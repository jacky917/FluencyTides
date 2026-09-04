"""專屬 Speaking_Trilingual_Dark 的 Anki 卡片匯入腳本。

Anki card import script dedicated to the Speaking_Trilingual_Dark model,
creating cards in bulk from JSON files (native JSON arrays are serialized).

由 ``scripts/common/samples/speaking_trilingual_sample.json`` 同構的 JSON
檔批量建卡（欄位值可為原生 JSON 陣列，會自動序列化為字串）。

用法：
    cd backend
    python scripts/local_anki/Speaking_Trilingual_Dark/import_cards.py [--name <相對路徑>] [--dry-run]
    # 省略 --name 時遞迴掃描 jsons/ 下所有 JSON

存在判斷**只看身分**（JSON 內的 ``cardId`` + ``noteId``），完全不看
``Prompt``——編輯卡片內容不會再讓腳本誤判成新卡。建卡後身分會自動寫回
JSON，成為該卡的永久識別。詳見
``docs/archive/card_identity_writeback_FEAT_2026-08-11.md`` §3.2。

Existence checks look **only at the identity** (``cardId`` + ``noteId`` in
the JSON) and never at ``Prompt``, so editing card content no longer makes
the script treat it as a new card. The identity is written back to the JSON
after creation and becomes that card's permanent handle. See the plan
document, §3.2.

四種身分狀態的處理：

- 兩者皆無 → ⚠️ 視為卡片不存在，警告後建卡並寫回身分
- 兩者皆有且與 Anki 一致 → 依 ``--update-existing`` 決定更新或跳過
- 兩者皆有但對不上 → ❌ 印診斷並跳過，不建卡不更新（需人工處理）
- 只有其一 → ❌ 視為損毀身分，同上

Handling of the four identity states: neither present -> treated as a new
card (warned, created, identity written back); both present and consistent
with Anki -> updated or skipped per ``--update-existing``; both present but
mismatched, or only one present -> diagnosed and skipped for manual
handling.
"""

import argparse
import asyncio
import base64
import copy
import html
import json
import logging
import sys
from pathlib import Path
from typing import NamedTuple

# 確保 sys.path 包含 backend 根目錄並載入 .env
backend_dir = Path(__file__).resolve().parents[3]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))
import scripts.common.env  # noqa

from app.core.config import settings
from app.core.exceptions import AnkiFieldCorruptedError
from app.infrastructure.anki.client import AnkiClient
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager
from app.infrastructure.utils.id_generator import generate_unique_card_id
from app.schemas.anki import AnkiNote, AnkiNoteOptions
from scripts.local_anki.common.card_identity import (
    KEY_CARD_ID,
    KEY_NOTE_ID,
    identity_state,
    load_cards,
    read_identity,
    save_cards,
    set_identity,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

MODEL_NAME = "Speaking_Trilingual_Dark"
#: 七個 JSON 欄位（值為 list/dict 時自動序列化）
JSON_FIELDS = (
    "Prompt_Audios",
    "Recordings_ZH", "Recordings_JA", "Recordings_EN",
    "References_ZH", "References_JA", "References_EN",
)
DEFAULT_SAMPLE = (
    backend_dir / "scripts" / "common" / "samples" / "speaking_trilingual_sample.json"
)
#: 卡片 JSON 的存放根目錄；牌組階層由檔案在此目錄下的相對路徑推導
JSONS_DIR = Path(__file__).parent / "jsons"


def resolve_deck_name(file_path: Path, card_deck_name: str | None) -> str:
    """組合最終牌組名稱：``<根牌組>::<相對路徑>::<檔名>::<deckName>``。

    Build the final Anki deck name from the configured root deck, the
    file's path relative to ``jsons/``, and an optional trailing
    sub-deck declared by the JSON's ``deckName``.

    階層一律由**檔案位置**決定：根牌組來自
    ``settings.SPEAKING_TRILINGUAL_ROOT_DECK``（單一事實來源，改該設定即可
    整批搬家），``jsons/`` 下的每層子資料夾各成一層子牌組。``deckName``
    為選填，若有值則**再往下追加一層**。

    Args:
        file_path: 卡片 JSON 的路徑。Path to the card JSON file.
        card_deck_name: JSON 中的 ``deckName``，選填；有值時追加在路徑
            之後成為最深一層。可為 ``None`` 或空字串。The JSON's optional
            ``deckName``, appended as the deepest level when present;
            may be ``None`` or empty.

    Returns:
        以 ``::`` 串接的完整牌組名稱。The full ``::``-joined deck name.

    Examples:
        根牌組為 ``日常會話`` 時：

        - ``jsons/お花屋さんで花を買う.json`` + ``deckName=""``
          → ``日常會話::お花屋さんで花を買う``
        - ``jsons/日本語面接/Queen Bee Capital株式会社/志望動機.json`` + ``deckName=""``
          → ``日常會話::日本語面接::Queen Bee Capital株式会社::志望動機``
        - 同上但 ``deckName="Step1"``
          → ``日常會話::日本語面接::Queen Bee Capital株式会社::志望動機::Step1``
    """
    segments: list[str] = []

    root = (settings.SPEAKING_TRILINGUAL_ROOT_DECK or "").strip().strip(":")
    if root:
        segments.append(root)

    # 子資料夾 → 子牌組（不在 jsons/ 底下時退化為僅用檔名）
    try:
        rel_path = file_path.resolve().relative_to(JSONS_DIR.resolve())
        segments.extend(rel_path.parent.parts)
    except ValueError:
        logger.warning("⚠️ %s 不在 jsons/ 目錄下，牌組階層僅使用檔名。", file_path.name)

    segments.append(file_path.stem)

    # deckName 為選填的額外末層
    trailing = (card_deck_name or "").strip().strip(":")
    # 向後相容：舊 JSON 的 deckName 可能重複寫了根牌組
    if root and trailing == root:
        # 恰等於根牌組 → 視為未填（沿用路徑推導，避免掉失檔名層或重複一層）
        logger.warning(
            "⚠️ %s 的 deckName 與根牌組相同，已忽略（階層由檔案位置決定）。",
            file_path.name,
        )
        trailing = ""
    elif root and trailing.startswith(f"{root}::"):
        # 含根牌組前綴的完整路徑 → 直接沿用整串
        logger.warning(
            "⚠️ %s 的 deckName 含根牌組前綴，已視為完整牌組名沿用: %s",
            file_path.name, trailing,
        )
        return trailing
    if trailing:
        segments.append(trailing)

    return "::".join(segments)


class NoteResolution(NamedTuple):
    """存在判斷的結果。

    The outcome of an existence check.

    Attributes:
        note_id: 命中的 note ID；``None`` 代表應建立新卡。The matched note ID;
            ``None`` means a new card should be created.
        diagnostic: 需人工處理時的診斷訊息；``None`` 代表可繼續。A diagnostic
            message when manual handling is required; ``None`` means proceed.
        source: 命中來源——``"identity"``（身分）、``"adopted"``（以 Prompt
            接管）、``"new"``（新卡）、``"blocked"``（有診斷，須跳過）。
            How the note was matched: ``"identity"``, ``"adopted"``, ``"new"``,
            or ``"blocked"``.
    """

    note_id: int | None
    diagnostic: str | None
    source: str


def _recovery_hint(file_path: Path | None, index: int) -> str:
    """組出可直接複製執行的 ``clear_identity.py`` 復原指令。

    Build a copy-pasteable ``clear_identity.py`` recovery command.

    診斷訊息若只說「用 clear_identity.py 清除」，使用者還得自行推算該檔在
    ``jsons/`` 下的相對路徑——那正是容易出錯的一步。

    A diagnostic that merely says "use clear_identity.py" still leaves the user
    to derive the file's path relative to ``jsons/`` by hand, which is exactly
    the error-prone step.

    Args:
        file_path: 卡片 JSON 的路徑；``None`` 時給出通用提示。Path to the card
            JSON; ``None`` yields a generic hint.
        index: 卡片序號（1-based）。The card index (1-based).

    Returns:
        指令字串。The command string.
    """
    if file_path is None:
        return "clear_identity.py --name <相對路徑> --index <N>"
    try:
        rel = file_path.resolve().relative_to(JSONS_DIR.resolve()).with_suffix("")
        return f'clear_identity.py --name "{rel.as_posix()}" --index {index}'
    except ValueError:
        return f'clear_identity.py --name "{file_path.stem}" --index {index}'


async def resolve_existing_note(
    client: AnkiClient,
    card: dict,
    deck_name: str,
    prompt_text: str,
    card_label: str,
    adopt_by_prompt: bool,
    recovery_hint: str = "clear_identity.py --name <相對路徑> --index <N>",
) -> NoteResolution:
    """依身分判斷卡片是否已存在於 Anki。

    Decide whether a card already exists in Anki, based on its identity.

    完全不以 ``Prompt`` 作為存在依據（除非明確傳入 ``adopt_by_prompt``），
    因此編輯卡片內容不會產生重複卡。四種身分狀態的處理見模組 docstring。

    ``Prompt`` is never used as an existence key unless ``adopt_by_prompt``
    is explicitly requested, so editing card content cannot create
    duplicates. See the module docstring for the four identity states.

    身分「有但對不上」時**不回退**至 ``Prompt`` 比對：那代表這張卡曾綁定某張
    note 而現在綁不上，可能是卡片被刻意刪除、JSON 被複製、或 ``Card_ID`` 被
    手動改過——每種情況的正確處理都不同，腳本無從分辨，因此交還給人決定。

    When the identity is present but does not match, there is deliberately
    **no fallback** to ``Prompt``: it means the card was once bound to a note
    and no longer is, which could be an intentional deletion, a copied JSON
    file, or a hand-edited ``Card_ID``. Each needs different handling and the
    script cannot tell them apart, so the decision is handed back to a human.

    Args:
        client: AnkiConnect 客戶端。AnkiConnect client instance.
        card: JSON 中的單張卡片物件。A single card object from the JSON file.
        deck_name: 推導後的完整牌組名稱。The resolved full deck name.
        prompt_text: 卡片的 Prompt 內容，僅供接管查詢與診斷使用。The card's
            Prompt, used only for adoption lookup and diagnostics.
        card_label: 診斷訊息中的卡片標示（如 ``逆質問.json #2``）。Card label
            shown in diagnostics, e.g. ``逆質問.json #2``.
        adopt_by_prompt: 是否啟用一次性的 Prompt 接管（僅遷移時）。Whether to
            enable the one-off Prompt-based adoption (migration only).

    Returns:
        NoteResolution: 判斷結果。The resolution outcome.
    """
    state = identity_state(card)
    card_id, note_id = read_identity(card)

    if state == "partial":
        return NoteResolution(
            None,
            f"❌ [{card_label}] 身分不完整，已跳過\n"
            f"   JSON  : {KEY_NOTE_ID}={note_id!r}  {KEY_CARD_ID}={card_id!r}\n"
            f"   原因  : 兩者必須同時存在才算有效身分\n"
            f"   Prompt: {prompt_text[:40]}\n"
            f"   處理  : 補齊缺少的一方，或清除身分後重跑：\n"
            f"           {recovery_hint}",
            "blocked",
        )

    if state == "complete":
        assert card_id is not None and note_id is not None  # identity_state 已保證
        notes = await client.get_notes_info([note_id])
        if not notes:
            return NoteResolution(
                None,
                f"❌ [{card_label}] 身分與 Anki 不一致，已跳過\n"
                f"   JSON  : {KEY_NOTE_ID}={note_id}  {KEY_CARD_ID}={card_id}\n"
                f"   Anki  : 查無此 note（可能已被刪除）\n"
                f"   Prompt: {prompt_text[:40]}\n"
                f"   處理  : 確認該卡是否為刻意刪除。若要重新建立，先執行：\n"
                f"           {recovery_hint}",
                "blocked",
            )

        note = notes[0]
        if note.modelName != MODEL_NAME:
            return NoteResolution(
                None,
                f"❌ [{card_label}] 身分指向的 note 模型不符，已跳過\n"
                f"   JSON  : {KEY_NOTE_ID}={note_id}（預期模型 {MODEL_NAME}）\n"
                f"   Anki  : 該 note 實際模型為 {note.modelName}\n"
                f"   Prompt: {prompt_text[:40]}\n"
                f"   處理  : 沿用會寫進錯誤的欄位集合。請確認 note 類型是否被改過；\n"
                f"           若確定要與該 note 脫鉤：\n"
                f"           {recovery_hint}",
                "blocked",
            )

        anki_card_id = str(note.fields.get("Card_ID", {}).get("value", "")).strip()
        if anki_card_id != card_id:
            return NoteResolution(
                None,
                f"❌ [{card_label}] 身分與 Anki 不一致，已跳過\n"
                f"   JSON  : {KEY_CARD_ID}={card_id}\n"
                f"   Anki  : note {note_id} 的 Card_ID 為 {anki_card_id or '（空）'}\n"
                f"   Prompt: {prompt_text[:40]}\n"
                f"   處理  : 兩邊指向不同卡片。確認是誤改還是 JSON 被複製，\n"
                f"           再決定要修正 JSON 的 {KEY_NOTE_ID}，或清除身分重來：\n"
                f"           {recovery_hint}",
                "blocked",
            )

        return NoteResolution(note_id, None, "identity")

    # state == "absent"：身分完全不存在
    if adopt_by_prompt and prompt_text:
        escaped_prompt = prompt_text.replace('"', '\\"')
        escaped_deck = deck_name.replace('"', '\\"')
        # 必須限定模型：Prompt 也是 Speaking_Coach_Dark / Notion_SRS_Dark 的欄位名，
        # Anki 的欄位搜尋跨模型，不限定會接管到別種卡片。
        # The model filter is required: Prompt is also a field on other models and
        # Anki's field search is model-agnostic, so without it adoption can bind to
        # a note of the wrong type.
        adopted = await client.find_notes(
            f'"note:{MODEL_NAME}" deck:"{escaped_deck}" Prompt:"{escaped_prompt}"'
        )
        if len(adopted) > 1:
            # 同牌組同 Prompt 的重複卡正是本計劃的成因，任選一張會讓另一張上的
            # 錄音變成孤兒。交還給人決定要接管哪一張。
            # Duplicates sharing a deck and Prompt are the very problem this plan
            # addresses; picking one arbitrarily would strand the other's
            # recordings. Hand the choice back to a human.
            return NoteResolution(
                None,
                f"❌ [{card_label}] Prompt 接管命中 {len(adopted)} 張卡片，"
                f"無法判斷該接管哪一張，已跳過\n"
                f"   候選  : {', '.join(str(n) for n in adopted)}\n"
                f"   Prompt: {prompt_text[:40]}\n"
                f"   處理  : 這是同牌組同 Prompt 的重複卡。請在 Anki 中確認要保留哪一張\n"
                f"           （注意各自的錄音），刪除其餘後重跑；或手動把正確的\n"
                f"           noteId / cardId 填入 JSON。",
                "blocked",
            )
        if adopted:
            return NoteResolution(adopted[0], None, "adopted")

    return NoteResolution(None, None, "new")


async def _process_media_paths(client: AnkiClient, data: any, dry_run: bool = False) -> any:
    """遞迴掃描字典/陣列，若遇到 audio 或 avatar 為絕對路徑，上傳至 Anki 並替換為純檔名。

    Recursively scan dicts/lists; when an audio or avatar value is an absolute
    path, upload it to Anki media and replace it with the bare filename.

    Args:
        client: AnkiConnect 客戶端。AnkiConnect client instance.
        data: 任意巢狀資料。Arbitrary nested data structure.
        dry_run: 預覽模式。僅檢查檔案是否存在並列印，**不實際上傳**——
            上傳會寫入 Anki 媒體庫, 與 ``--dry-run`` 的承諾牴觸。
            Preview mode: only checks that files exist and logs them, without
            uploading — an upload writes to Anki's media library and would
            break the promise made by ``--dry-run``.

    Returns:
        處理後的資料。The processed data structure.
    """
    if isinstance(data, dict):
        for k, v in list(data.items()):
            if k in ("audio", "avatar") and isinstance(v, str) and (":/" in v or ":\\" in v or v.startswith("/")):
                path = Path(v)
                if not path.exists():
                    logger.warning(f"   ⚠️ 媒體檔案不存在: {path}")
                elif dry_run:
                    logger.info(f"   🧪 [DRY-RUN] 將上傳媒體: {path.name}")
                else:
                    try:
                        with open(path, "rb") as f:
                            b64_data = base64.b64encode(f.read()).decode("utf-8")
                        filename = path.name
                        await client._invoke("storeMediaFile", filename=filename, data=b64_data)
                        logger.info(f"   ✔️ 成功上傳媒體: {filename}")
                        data[k] = filename
                    except Exception as e:
                        logger.error(f"   ❌ 無法上傳媒體 {path}: {e}")
            else:
                data[k] = await _process_media_paths(client, v, dry_run)
        return data
    elif isinstance(data, list):
        for i in range(len(data)):
            data[i] = await _process_media_paths(client, data[i], dry_run)
        return data
    else:
        return data


async def _normalize_fields(
    client: AnkiClient, fields: dict, card_id: str | None = None, dry_run: bool = False
) -> dict[str, str]:
    """把 JSON 欄位的原生陣列序列化為字串，自動上傳媒體，並填入 Card_ID 與 TG_Bot。

    Serialize native arrays in JSON fields to strings, auto-upload media, and
    fill in Card_ID and TG_Bot.

    JSON 欄位一律經 ``html.escape`` 後寫入，與語音流程（``AnkiJsonFieldManager``）
    的格式一致。兩邊格式若分裂，任何以裸 ``json.loads`` 讀取的呼叫點都會對其中
    一種靜默失效（S065 即為此類問題）。讀取端 ``parse_field_string`` 對轉義與
    未轉義皆相容，故存量資料不需回頭改寫。

    JSON fields are written through ``html.escape`` so the format matches the
    voice flow (``AnkiJsonFieldManager``). A split format silently breaks any
    caller that parses with a bare ``json.loads`` for one of the two variants —
    which is exactly the S065 class of bug. The reader
    (``parse_field_string``) accepts both, so existing data needs no rewrite.

    Args:
        client: AnkiConnect 客戶端。AnkiConnect client instance.
        fields: 原始欄位字典。Raw field dictionary from the JSON file.
        card_id: JSON 已記錄的 ``cardId``；``None`` 時生成新的。The ``cardId``
            already recorded in the JSON; a new one is generated when ``None``.
        dry_run: 預覽模式，媒體只檢查不上傳。Preview mode; media is checked but
            not uploaded.

    Returns:
        全為字串值的欄位字典。Field dictionary with all values as strings.
    """
    out: dict[str, str] = {}
    for key, value in fields.items():
        if isinstance(value, (list, dict)):
            processed_value = await _process_media_paths(client, value, dry_run)
            out[key] = html.escape(json.dumps(processed_value, ensure_ascii=False))
        else:
            out[key] = str(value)

    for key in JSON_FIELDS:
        out.setdefault(key, "[]")

    # Card_ID 以 JSON 的身分為準，僅在尚無身分時生成；TG_Bot 一律以執行環境為準
    # Card_ID follows the JSON identity and is only generated when absent;
    # TG_Bot always comes from the runtime environment.
    out["Card_ID"] = card_id or generate_unique_card_id(prefix="st")
    out["TG_Bot"] = settings.TG_BOT_USERNAME or ""
    return out


async def import_cards(
    file_path: Path,
    dry_run: bool,
    update_existing: bool = False,
    adopt_by_prompt: bool = False,
    client: AnkiClient | None = None,
) -> dict[str, int]:
    """讀 JSON 檔並逐張建卡，建卡後把身分寫回 JSON。

    Import one JSON file card by card, writing the identity back to the JSON
    after creation.

    Args:
        file_path: JSON 檔案路徑。Path to the JSON file.
        dry_run: 預覽模式，不寫入 Anki 也不改 JSON 檔。Preview mode; writes
            neither to Anki nor to the JSON file.
        update_existing: 身分有效的卡片是否更新。預設 ``False``（跳過），
            傳 ``True`` 才會覆寫 Prompt / Context / References_×3
            （Recordings 等使用者資料一律不動）。Whether to update cards whose
            identity resolves. Defaults to ``False`` (skip); pass ``True`` to
            overwrite Prompt / Context / References (user data such as
            Recordings is never touched).
        adopt_by_prompt: 一次性遷移用——無身分的卡片改以 ``Prompt`` 查找既有
            卡並接管。One-off migration: cards without an identity are matched
            against existing notes by ``Prompt`` and adopted.
        client: 既有的 AnkiConnect 客戶端；``None`` 時自行建立並負責關閉。
            An existing AnkiConnect client; when ``None`` one is created and
            closed by this function.

    Returns:
        各類結果的計數字典，鍵為 ``created`` / ``updated`` / ``skipped`` /
        ``adopted`` / ``blocked`` / ``failed`` / ``identity_written``。
        Counts per outcome, keyed by ``created`` / ``updated`` / ``skipped`` /
        ``adopted`` / ``blocked`` / ``failed`` / ``identity_written``.
    """
    logger.info(f"📂 開始載入 JSON 檔案: {file_path.name} ({file_path})")
    cards_data = load_cards(file_path)
    mode_hint = "更新" if update_existing else "跳過"
    adopt_hint = "，並以 Prompt 接管無身分的卡片" if adopt_by_prompt else ""
    logger.info(
        f"📄 成功載入，共包含 {len(cards_data)} 筆卡片資料。"
        f"（身分有效的卡片將{mode_hint}{adopt_hint}）開始與 Anki 同步..."
    )

    owns_client = client is None
    client = client or AnkiClient()
    stats = dict.fromkeys(
        ("created", "updated", "skipped", "adopted", "blocked", "failed", "identity_written"), 0
    )
    identity_dirty = False

    try:
        for i, card in enumerate(cards_data, 1):
            card_label = f"{file_path.name} #{i}"
            existing_card_id, _ = read_identity(card)
            # 深拷貝再正規化：_process_media_paths 會就地把絕對路徑改寫成純檔名，
            # 若直接傳入 cards_data 內的 dict，身分寫回時會連帶把使用者手寫的
            # 素材路徑覆寫掉——而 jsons/ 未進版控，那是唯一的一份。
            # Deep-copy before normalising: _process_media_paths rewrites absolute
            # paths to bare filenames in place, so passing the dict from cards_data
            # would make the identity write-back destroy the user's hand-written
            # media paths — and jsons/ is git-ignored, so that is the only copy.
            fields = await _normalize_fields(
                client, copy.deepcopy(card.get("fields", {})), existing_card_id, dry_run
            )
            prompt_text = fields.get("Prompt", "")
            if not prompt_text:
                logger.warning(f"⚠️ [{card_label}] 缺少 Prompt 欄位，跳過。")
                stats["failed"] += 1
                continue

            deck_name = resolve_deck_name(file_path, card.get("deckName"))
            resolution = await resolve_existing_note(
                client, card, deck_name, prompt_text, card_label, adopt_by_prompt,
                _recovery_hint(file_path, i),
            )

            if resolution.diagnostic:
                logger.error(resolution.diagnostic)
                stats["blocked"] += 1
                continue

            # ── 已存在（身分命中或以 Prompt 接管）──────────────────────────
            if resolution.note_id is not None:
                note_id = resolution.note_id

                if resolution.source == "adopted":
                    # 接管：把既有 note 的身分收編進 JSON，之後不再需要 Prompt 比對
                    # Adoption: pull the existing note's identity into the JSON so
                    # Prompt matching is never needed again.
                    notes = await client.get_notes_info([note_id])
                    anki_card_id = (
                        str(notes[0].fields.get("Card_ID", {}).get("value", "")).strip()
                        if notes else ""
                    )
                    adopted_card_id = anki_card_id or fields["Card_ID"]
                    logger.info(
                        f"🔗 [{card_label}] 以 Prompt 接管既有卡片 note_id={note_id} "
                        f"Card_ID={adopted_card_id}"
                    )
                    stats["adopted"] += 1
                    # 身分照常記到記憶體中的卡片物件；是否落地由下方單一的
                    # dry_run 判斷決定，避免兩處各判一次而漏掉其中一條路徑。
                    # The identity is always recorded on the in-memory card; whether
                    # it reaches disk is decided by the single dry_run check below,
                    # so no path can slip past a second, separate guard.
                    if set_identity(card, adopted_card_id, note_id):
                        identity_dirty = True
                        stats["identity_written"] += 1
                    fields["Card_ID"] = adopted_card_id

                if not update_existing:
                    if resolution.source != "adopted":
                        logger.info(
                            f"⏭️ [{card_label}] 身分有效，跳過 note_id={note_id}: {prompt_text[:40]}"
                        )
                        stats["skipped"] += 1
                    continue

                # 更新模式：排除會破壞使用者資料的欄位
                update_fields = {
                    k: v for k, v in fields.items()
                    if k not in ("Prompt_Audios", "Recordings_ZH", "Recordings_JA", "Recordings_EN", "Card_ID", "TG_Bot")
                }
                if dry_run:
                    logger.info(
                        f"🧪 [DRY-RUN] [{card_label}] {deck_name} <- 更新 note_id={note_id}: {prompt_text[:40]}"
                    )
                    stats["updated"] += 1
                    continue
                try:
                    await client.update_note_fields(note_id, update_fields)
                    logger.info(f"🔄 [{card_label}] 更新成功 note_id={note_id}")
                    stats["updated"] += 1
                except Exception as e:
                    logger.warning(f"⚠️ [{card_label}] 更新失敗 note_id={note_id}，原因: {e}")
                    stats["failed"] += 1
                continue

            # ── 不存在：建立新卡 ────────────────────────────────────────────
            logger.warning(
                f"⚠️ [{card_label}] 無身分（{KEY_CARD_ID}/{KEY_NOTE_ID} 皆缺），視為新卡建立: "
                f"{prompt_text[:40]}"
            )
            if dry_run:
                logger.info(
                    f"🧪 [DRY-RUN] [{card_label}] {deck_name} <- 新增卡片 {fields['Card_ID']}"
                )
                stats["created"] += 1
                continue

            await client.create_deck(deck_name)
            note = AnkiNote(
                deckName=deck_name,
                modelName=card.get("modelName", MODEL_NAME),
                fields=fields,
                tags=list(card.get("tags") or ["Speaking_Trilingual"]),
                options=AnkiNoteOptions(allowDuplicate=False, duplicateScope="deck"),
            )
            try:
                note_id = await client.add_note(note)
            except Exception as e:
                logger.warning(f"⚠️ [{card_label}] 建立失敗（可能重複）Card_ID={fields['Card_ID']}，原因: {e}")
                stats["failed"] += 1
                continue

            if note_id is None:
                logger.warning(f"⚠️ [{card_label}] 建立未回傳 note_id，身分無法寫回。")
                stats["failed"] += 1
                continue

            logger.info(f"🎉 [{card_label}] 建立成功 Card_ID={fields['Card_ID']} note_id={note_id}")
            stats["created"] += 1
            if set_identity(card, fields["Card_ID"], note_id):
                identity_dirty = True
                stats["identity_written"] += 1

        logger.info(_format_summary(len(cards_data), stats, update_existing, dry_run))
    finally:
        # 身分寫回放在 finally：迴圈中途若因連線中斷等原因拋出例外，已經在 Anki
        # 建好的卡片其身分仍必須落地，否則下次重跑會把它們當成新卡再建一次，
        # 正是本計劃要消滅的重複卡問題。
        # The write-back lives in finally: if the loop raises midway (e.g. the
        # connection drops), identities for cards already created in Anki must
        # still reach disk — otherwise the next run treats them as new and
        # duplicates them, which is exactly the problem this design removes.
        if identity_dirty and not dry_run:
            try:
                save_cards(file_path, cards_data)
                logger.info(f"💾 已將 {stats['identity_written']} 筆身分寫回 {file_path.name}")
            except OSError as e:
                # 不可讓寫檔失敗掩蓋原本的例外
                # Never let a write failure mask the original exception.
                logger.error(f"❌ 身分寫回 {file_path.name} 失敗: {e}")
        if owns_client:
            await client.close()

    return stats


def _format_summary(
    total: int, stats: dict[str, int], update_existing: bool, dry_run: bool
) -> str:
    """組裝單一檔案的結果摘要行。

    Build the one-line result summary for a single file.

    Args:
        total: 該檔的卡片總數。Total cards in the file.
        stats: 各類結果計數。Counts per outcome.
        update_existing: 是否為更新模式。Whether update mode is on.
        dry_run: 是否為預覽模式。Whether this was a preview run.

    Returns:
        摘要字串。The summary string.
    """
    parts = ([f"📊 共 {total} 筆"] if total >= 0 else ["📊 合計"])
    parts += [f"新增 {stats['created']}", f"更新 {stats['updated']}"]
    if stats["adopted"]:
        parts.append(f"接管 {stats['adopted']}")
    parts.append(f"跳過 {stats['skipped']}")
    if stats["blocked"]:
        parts.append(f"身分不符跳過 {stats['blocked']}")
    if stats["failed"]:
        parts.append(f"失敗 {stats['failed']}")
    if stats["identity_written"]:
        parts.append(f"已寫回身分 {stats['identity_written']}")

    summary = "｜".join(parts)
    if dry_run:
        summary += "　(DRY-RUN，未寫入 Anki 也未改 JSON)"
    if stats["skipped"] and not update_existing:
        summary += "　(要覆寫既有卡片請加 --update-existing)"
    return summary


async def report_orphans(client: AnkiClient) -> int:
    """列出 Anki 有、但所有 JSON 檔都未持有其身分的卡片。

    List cards that exist in Anki but whose identity is held by no JSON file.

    孤兒卡通常是「改過 `Prompt` 而被重複建立」的舊卡。本函式**只報告不刪除**
    ——孤兒卡可能帶著使用者的錄音，刪除必須人工確認，因此輸出會標示各卡的錄音
    筆數作為判斷依據。

    Orphans are usually stale cards left behind when a ``Prompt`` edit caused a
    duplicate to be created. This only reports and never deletes: an orphan may
    carry user recordings, so the output includes each card's recording count
    to support a manual decision.

    Args:
        client: AnkiConnect 客戶端。AnkiConnect client instance.

    Returns:
        孤兒卡數量。The number of orphan cards.
    """
    known_note_ids: set[int] = set()
    # 除了 jsons/，也要含腳本同層的 JSON——main() 的 --name 支援該處作為
    # 後備位置（sabbat_of_the_witch.json 即在此），漏掉會讓那些卡永遠被誤報為孤兒，
    # 而本報告的用途是驅動人工刪卡，誤報方向的代價最高。
    # Besides jsons/, include JSONs alongside the script: main()'s --name accepts
    # that fallback location, and omitting it would flag those notes as orphans
    # forever — the costly direction of error for a report that drives manual
    # deletion.
    search_roots = list(JSONS_DIR.rglob("*.json")) + list(Path(__file__).parent.glob("*.json"))
    for json_file in sorted(set(search_roots)):
        try:
            cards = load_cards(json_file)
        except ValueError as e:
            logger.error(f"❌ 略過無法解析的檔案 {json_file.name}: {e}")
            logger.error("   （其身分無法納入比對，本次報告可能出現誤判的孤兒卡）")
            continue
        for card in cards:
            _, note_id = read_identity(card)
            if note_id:
                known_note_ids.add(note_id)

    note_ids = await client.find_notes(f'"note:{MODEL_NAME}"')
    orphan_ids = [nid for nid in note_ids if nid not in known_note_ids]

    logger.info("=" * 60)
    logger.info(
        f"🔍 孤兒卡報告：Anki 共 {len(note_ids)} 張，JSON 持有身分 {len(known_note_ids)} 張"
    )
    if not orphan_ids:
        logger.info("✅ 沒有孤兒卡。")
        logger.info("=" * 60)
        return 0

    notes = await client.get_notes_info(orphan_ids)
    logger.info(f"⚠️ 發現 {len(notes)} 張孤兒卡（Anki 有、JSON 無）：")
    for note in notes:
        recording_counts = []
        for lang in ("ZH", "JA", "EN"):
            raw = str(note.fields.get(f"Recordings_{lang}", {}).get("value", ""))
            try:
                count = len(AnkiJsonFieldManager.parse_field_string(raw))
            except AnkiFieldCorruptedError:
                count = -1  # 解析失敗，標示為未知 / unparseable, mark unknown
            recording_counts.append(f"{lang}={'?' if count < 0 else count}")
        prompt = str(note.fields.get("Prompt", {}).get("value", ""))[:40]
        logger.info(
            f"   nid={note.noteId}  錄音 {' '.join(recording_counts)}  {prompt}"
        )
    logger.info("ℹ️ 本工具不刪除任何卡片。錄音數不為 0 者請先確認再處理。")
    logger.info("=" * 60)
    return len(notes)


async def main() -> int:
    """腳本主入口：解析參數並匯入單檔或整個 jsons 目錄。

    Script entry point: parse arguments and import either a single JSON file
    or every JSON file under the jsons directory.

    Returns:
        行程結束碼。有卡片因身分不符被跳過、或有失敗時回傳 ``1``，讓批次腳本
        不會把「跳過一半」誤判成成功。Process exit code: ``1`` when any card
        was blocked by an identity mismatch or failed, so batch callers cannot
        mistake a half-skipped run for success.
    """
    parser = argparse.ArgumentParser(description="Speaking_Trilingual_Dark 卡片匯入腳本")
    parser.add_argument("--name", type=str, default=None, help="jsons 目錄下的 JSON 相對路徑 (不含 .json，子資料夾需一併給，如 '日本語面接/Q社/志望動機')。不指定則遞迴掃描 jsons 目錄下所有檔案。")
    parser.add_argument("--dry-run", action="store_true", help="僅列印執行計畫，不寫入 Anki 也不改 JSON 檔")
    parser.add_argument(
        "--update-existing",
        action="store_true",
        help=(
            "身分有效的卡片改為更新（覆寫 Prompt / Context / References_×3）。"
            "預設不加此參數時一律跳過。Recordings 等使用者資料在任何模式下都不會被動到。"
        ),
    )
    parser.add_argument(
        "--adopt-by-prompt",
        action="store_true",
        help=(
            "【一次性遷移用】無身分的卡片改以牌組+Prompt 查找既有卡並接管其身分。"
            "平時不要使用——Prompt 比對正是造成重複卡的原因。"
        ),
    )
    parser.add_argument(
        "--report-orphans",
        action="store_true",
        help="只列出 Anki 有、JSON 無身分的孤兒卡，不做任何匯入（唯讀）",
    )
    args = parser.parse_args()

    jsons_dir = JSONS_DIR

    if args.report_orphans:
        client = AnkiClient()
        try:
            orphan_count = await report_orphans(client)
        finally:
            await client.close()
        return 1 if orphan_count else 0

    if args.name:
        file_path = jsons_dir / f"{args.name}.json"
        if not file_path.exists():
            fallback_path = Path(__file__).parent / f"{args.name}.json"
            if fallback_path.exists():
                file_path = fallback_path
            else:
                logger.error(f"❌ 找不到 JSON 檔案: {args.name}.json")
                return 1
        targets = [file_path]
    else:
        if not jsons_dir.exists():
            logger.error(f"❌ 找不到 jsons 資料夾: {jsons_dir}")
            return 1
        targets = sorted(jsons_dir.rglob("*.json"))
        if not targets:
            logger.info("ℹ️ jsons 目錄下沒有找到任何 JSON 檔案。")
            return 0
        logger.info(f"🔍 準備批量匯入 {len(targets)} 個 JSON 檔案...")

    # 共用單一客戶端，避免每檔重新連線與重複觸發 Anki 同步
    # Share one client so each file does not reconnect and re-trigger a sync.
    client = AnkiClient()
    totals = dict.fromkeys(
        ("created", "updated", "skipped", "adopted", "blocked", "failed", "identity_written"), 0
    )
    try:
        for file_path in targets:
            try:
                stats = await import_cards(
                    file_path, args.dry_run, args.update_existing, args.adopt_by_prompt, client
                )
            except ValueError as e:
                # 單一檔案格式損毀不該讓其餘檔案陪葬，也不該以 raw traceback 收場
                # A single malformed file must not take the rest down with it, nor
                # end the run with a raw traceback.
                logger.error(f"❌ {file_path.name} 解析失敗，已略過該檔: {e}")
                totals["failed"] += 1
                continue
            for key, value in stats.items():
                totals[key] += value
    finally:
        await client.close()

    if len(targets) > 1:
        logger.info("=" * 60)
        logger.info(f"📦 全部檔案：{_format_summary(-1, totals, args.update_existing, args.dry_run)}")

    if totals["blocked"] or totals["failed"]:
        logger.error(
            f"⚠️ 有 {totals['blocked']} 筆因身分問題被跳過、{totals['failed']} 筆失敗，"
            f"請處理後重跑（exit code 1）。"
        )
        return 1
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    sys.exit(asyncio.run(main()))
