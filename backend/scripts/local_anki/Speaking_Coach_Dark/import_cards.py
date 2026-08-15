"""專屬 Speaking_Coach_Dark 的 Anki 卡片匯入腳本。

Anki card import script dedicated to the Speaking_Coach_Dark model.

用法：
    cd backend
    python scripts/local_anki/Speaking_Coach_Dark/import_cards.py [--name <相對路徑>] [--dry-run]
    # 省略 --name 時遞迴掃描 jsons/ 下所有 JSON

存在判斷**只看身分**（JSON 內的 ``cardId`` + ``noteId``），完全不看
``Prompt``——編輯卡片內容不會再讓腳本誤判成新卡。建卡後身分會自動寫回
JSON，成為該卡的永久識別。設計與 ``Speaking_Trilingual_Dark`` 的同名腳本
一致，詳見 ``docs/wip/speaking_coach_identity_FEAT_2026-08-11.md`` §3.3。

Existence checks look **only at the identity** (``cardId`` + ``noteId`` in the
JSON) and never at ``Prompt``, so editing card content no longer makes the
script treat it as a new card. The identity is written back after creation and
becomes that card's permanent handle. The design mirrors the script of the same
name under ``Speaking_Trilingual_Dark``; see the plan document, §3.3.

四種身分狀態的處理：

- 兩者皆無 → ⚠️ 視為卡片不存在，警告後建卡並寫回身分
- 兩者皆有且與 Anki 一致 → 依 ``--update-existing`` 決定更新或跳過
- 兩者皆有但對不上 → ❌ 印診斷並跳過，不建卡不更新（需人工處理）
- 只有其一 → ❌ 視為損毀身分，同上

Handling of the four identity states: neither present -> treated as a new card
(warned, created, identity written back); both present and consistent with Anki
-> updated or skipped per ``--update-existing``; both present but mismatched, or
only one present -> diagnosed and skipped for manual handling.

與三語卡的差異：本 model 只有 8 個欄位，錄音欄位是**單數**的 ``Recordings``，
且語言由 ``Target_Language`` 欄位決定（三語卡是由欄位名後綴決定）。

Differences from the trilingual model: 8 fields instead of 11, a **singular**
``Recordings`` field, and the language comes from the ``Target_Language`` field
rather than a field-name suffix.
"""

import argparse
import asyncio
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

MODEL_NAME = "Speaking_Coach_Dark"
#: 三個 JSON 欄位（值為 list/dict 時自動序列化）
JSON_FIELDS = ("Prompt_Audios", "Recordings", "References")
#: 更新模式下不覆寫的欄位——使用者資料與由環境決定的值
#:
#: ``Recordings`` 是**單數**（三語卡是 ``Recordings_ZH/JA/EN`` 三個），
#: 直接照抄三語卡的清單會漏掉它，使用者的錄音就會被清空。
#:
#: ``Recordings`` is **singular** here (the trilingual model has three suffixed
#: fields); copying that model's list verbatim would miss it and wipe the user's
#: recordings.
PROTECTED_FIELDS = ("Prompt_Audios", "Recordings", "Card_ID", "TG_Bot")
#: ``Target_Language`` 未提供時的預設值
DEFAULT_TARGET_LANGUAGE = "ja-JP"
#: 卡片 JSON 的存放根目錄；牌組階層由檔案在此目錄下的相對路徑推導
JSONS_DIR = Path(__file__).parent / "jsons"


def resolve_deck_name(file_path: Path, card_deck_name: str | None) -> str:
    """組合最終牌組名稱：``<根牌組>::<相對路徑>::<檔名>::<deckName>``。

    Build the final Anki deck name from the configured root deck, the file's
    path relative to ``jsons/``, and an optional trailing sub-deck.

    根牌組來自 ``settings.SPEAKING_COACH_ROOT_DECK``（與三語卡各自獨立，
    可分別搬家）；``jsons/`` 下的每層子資料夾各成一層子牌組。

    The root deck comes from ``settings.SPEAKING_COACH_ROOT_DECK``, kept
    separate from the trilingual one so each can be relocated independently.

    Args:
        file_path: 卡片 JSON 的路徑。Path to the card JSON file.
        card_deck_name: JSON 中的 ``deckName``，選填；有值時追加為最深一層。
            The JSON's optional ``deckName``, appended as the deepest level.

    Returns:
        以 ``::`` 串接的完整牌組名稱。The full ``::``-joined deck name.

    Examples:
        根牌組為 ``封存::日本語::AI點評`` 時：

        - ``jsons/面接（2026-06-07）.json`` + ``deckName=""``
          → ``封存::日本語::AI點評::面接（2026-06-07）``
    """
    segments: list[str] = []

    root = (settings.SPEAKING_COACH_ROOT_DECK or "").strip().strip(":")
    if root:
        segments.append(root)

    try:
        rel_path = file_path.resolve().relative_to(JSONS_DIR.resolve())
        segments.extend(rel_path.parent.parts)
    except ValueError:
        logger.warning("⚠️ %s 不在 jsons/ 目錄下，牌組階層僅使用檔名。", file_path.name)

    segments.append(file_path.stem)

    trailing = (card_deck_name or "").strip().strip(":")
    if root and trailing == root:
        logger.warning(
            "⚠️ %s 的 deckName 與根牌組相同，已忽略（階層由檔案位置決定）。",
            file_path.name,
        )
        trailing = ""
    elif root and trailing.startswith(f"{root}::"):
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
            message when manual handling is required.
        source: 命中來源——``"identity"`` / ``"adopted"`` / ``"new"`` /
            ``"blocked"``。How the note was matched.
    """

    note_id: int | None
    diagnostic: str | None
    source: str


def _recovery_hint(file_path: Path | None, index: int) -> str:
    """組出可直接複製執行的 ``clear_identity.py`` 復原指令。

    Build a copy-pasteable ``clear_identity.py`` recovery command.

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

    身分「有但對不上」時**不回退**至 ``Prompt`` 比對：那代表這張卡曾綁定某張
    note 而現在綁不上，可能是卡片被刻意刪除、JSON 被複製、或 ``Card_ID`` 被
    手動改過——每種情況的正確處理都不同，腳本無從分辨，因此交還給人決定。

    When the identity is present but does not match, there is deliberately no
    fallback to ``Prompt``: it means the card was once bound to a note and no
    longer is, which could be an intentional deletion, a copied JSON file, or a
    hand-edited ``Card_ID``. Each needs different handling and the script cannot
    tell them apart, so the decision is handed back to a human.

    Args:
        client: AnkiConnect 客戶端。AnkiConnect client instance.
        card: JSON 中的單張卡片物件。A single card object from the JSON file.
        deck_name: 推導後的完整牌組名稱。The resolved full deck name.
        prompt_text: 卡片的 Prompt 內容，僅供接管查詢與診斷使用。The card's
            Prompt, used only for adoption lookup and diagnostics.
        card_label: 診斷訊息中的卡片標示。Card label shown in diagnostics.
        adopt_by_prompt: 是否啟用一次性的 Prompt 接管（僅遷移時）。Whether to
            enable the one-off Prompt-based adoption (migration only).
        recovery_hint: 診斷訊息中附上的復原指令。The recovery command shown in
            diagnostics.

    Returns:
        NoteResolution: 判斷結果。The resolution outcome.
    """
    state = identity_state(card)
    card_id, note_id = read_identity(card)

    if state == "partial":
        return NoteResolution(
            None,
            f"❌ [{card_label}] 身分不完整，已跳過\n"
            f"   JSON  : {KEY_NOTE_ID}={card.get(KEY_NOTE_ID)!r}  "
            f"{KEY_CARD_ID}={card.get(KEY_CARD_ID)!r}\n"
            f"   原因  : 兩者必須同時存在才算有效身分\n"
            f"   Prompt: {prompt_text[:40]}\n"
            f"   處理  : 補齊缺少的一方，或清除身分後重跑：\n"
            f"           {recovery_hint}",
            "blocked",
        )

    if state == "complete":
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
        adopted = await client.find_notes(
            f'"note:{MODEL_NAME}" deck:"{escaped_deck}" Prompt:"{escaped_prompt}"'
        )
        if len(adopted) > 1:
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
        dry_run: 預覽模式。僅檢查檔案是否存在並列印，**不實際上傳**——上傳會
            寫入 Anki 媒體庫，與 ``--dry-run`` 的承諾牴觸。Preview mode: only
            checks that files exist, without uploading.

    Returns:
        處理後的資料。The processed data structure.
    """
    import base64

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
                        await client._invoke("storeMediaFile", filename=path.name, data=b64_data)
                        logger.info(f"   ✔️ 成功上傳媒體: {path.name}")
                        data[k] = path.name
                    except Exception as e:
                        logger.error(f"   ❌ 無法上傳媒體 {path}: {e}")
            else:
                data[k] = await _process_media_paths(client, v, dry_run)
        return data
    if isinstance(data, list):
        for i in range(len(data)):
            data[i] = await _process_media_paths(client, data[i], dry_run)
        return data
    return data


async def _normalize_fields(
    client: AnkiClient, fields: dict, card_id: str | None = None, dry_run: bool = False
) -> dict[str, str]:
    """把 JSON 欄位的原生陣列序列化為字串，並填入 Card_ID / TG_Bot / Target_Language。

    Serialize native arrays to strings and fill in Card_ID, TG_Bot and
    Target_Language.

    JSON 欄位一律經 ``html.escape`` 後寫入，與語音流程
    （``AnkiJsonFieldManager``）的格式一致。讀取端 ``parse_field_string`` 對
    轉義與未轉義皆相容，故存量資料不需回頭改寫。

    JSON fields are written through ``html.escape`` so the format matches the
    voice flow. The reader accepts both, so existing data needs no rewrite.

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
            processed = await _process_media_paths(client, value, dry_run)
            out[key] = html.escape(json.dumps(processed, ensure_ascii=False))
        else:
            out[key] = str(value)

    for key in JSON_FIELDS:
        out.setdefault(key, "[]")

    # Target_Language 是評分時的語言基準；留空會讓 STT 退化為自動偵測，
    # 且評分樣板的「目標語言」硬性門檻形同虛設，因此一定要有值。
    # Target_Language is the scoring language baseline; leaving it empty makes
    # STT fall back to auto-detection and voids the target-language threshold in
    # the prompt template, so it must always carry a value.
    if not out.get("Target_Language", "").strip():
        out["Target_Language"] = DEFAULT_TARGET_LANGUAGE

    out["Card_ID"] = card_id or generate_unique_card_id(prefix="sc")
    out["TG_Bot"] = settings.TG_BOT_USERNAME or ""
    return out


def _format_summary(
    total: int, stats: dict[str, int], update_existing: bool, dry_run: bool
) -> str:
    """組裝單一檔案的結果摘要行。

    Build the one-line result summary for a single file.

    Args:
        total: 該檔的卡片總數；負值表示這是跨檔合計。Total cards in the file; a
            negative value marks a cross-file aggregate.
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


async def import_cards(
    file_path: Path,
    dry_run: bool,
    update_existing: bool = False,
    adopt_by_prompt: bool = False,
    client: AnkiClient | None = None,
) -> dict[str, int]:
    """讀 JSON 檔並逐張建卡，建卡後把身分寫回 JSON。

    Import one JSON file card by card, writing the identity back afterwards.

    Args:
        file_path: JSON 檔案路徑。Path to the JSON file.
        dry_run: 預覽模式，不寫入 Anki 也不改 JSON 檔。Preview mode.
        update_existing: 身分有效的卡片是否更新。預設 ``False``（跳過）。
            Whether to update cards whose identity resolves.
        adopt_by_prompt: 一次性遷移用——無身分的卡片改以 ``Prompt`` 查找既有卡
            並接管。One-off migration by ``Prompt``.
        client: 既有的 AnkiConnect 客戶端；``None`` 時自行建立並負責關閉。An
            existing client; when ``None`` one is created and closed here.

    Returns:
        各類結果的計數字典。Counts per outcome.
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
            # Deep-copy before normalising: the media rewrite happens in place, and
            # without the copy the identity write-back would destroy the user's
            # hand-written media paths. jsons/ is git-ignored, so it is the only copy.
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

                update_fields = {
                    k: v for k, v in fields.items() if k not in PROTECTED_FIELDS
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
                tags=list(card.get("tags") or ["Speaking_Coach"]),
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
        # 建好的卡片其身分仍必須落地，否則下次重跑會把它們當成新卡再建一次。
        # The write-back lives in finally so cards already created in Anki are not
        # orphaned by a mid-run failure and duplicated on the next run.
        if identity_dirty and not dry_run:
            try:
                save_cards(file_path, cards_data)
                logger.info(f"💾 已將 {stats['identity_written']} 筆身分寫回 {file_path.name}")
            except OSError as e:
                logger.error(f"❌ 身分寫回 {file_path.name} 失敗: {e}")
        if owns_client:
            await client.close()

    return stats


async def report_orphans(client: AnkiClient) -> int:
    """列出 Anki 有、但所有 JSON 檔都未持有其身分的卡片。

    List cards that exist in Anki but whose identity is held by no JSON file.

    本函式**只報告不刪除**——孤兒卡可能帶著使用者的錄音，刪除必須人工確認。

    This only reports and never deletes: an orphan may carry user recordings.

    Args:
        client: AnkiConnect 客戶端。AnkiConnect client instance.

    Returns:
        孤兒卡數量。The number of orphan cards.
    """
    known_note_ids: set[int] = set()
    search_roots = list(JSONS_DIR.rglob("*.json")) + list(Path(__file__).parent.glob("*.json"))
    for json_file in sorted(set(search_roots)):
        try:
            cards = load_cards(json_file)
        except ValueError as e:
            logger.error(f"❌ 略過無法解析的檔案 {json_file.name}: {e}")
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
        raw = str(note.fields.get("Recordings", {}).get("value", ""))
        try:
            count = len(AnkiJsonFieldManager.parse_field_string(raw))
        except AnkiFieldCorruptedError:
            count = -1
        prompt = str(note.fields.get("Prompt", {}).get("value", ""))[:40]
        logger.info(f"   nid={note.noteId}  錄音 {'?' if count < 0 else count} 筆  {prompt}")
    logger.info("ℹ️ 本工具不刪除任何卡片。錄音數不為 0 者請先確認再處理。")
    logger.info("=" * 60)
    return len(notes)


async def main() -> int:
    """腳本主入口：解析參數並匯入單檔或整個 jsons 目錄。

    Script entry point: parse arguments and import one JSON file or the whole
    jsons directory.

    Returns:
        行程結束碼。有卡片因身分不符被跳過、或有失敗時回傳 ``1``。Process exit
        code: ``1`` when any card was blocked or failed.
    """
    parser = argparse.ArgumentParser(description="Speaking_Coach_Dark 卡片匯入腳本")
    parser.add_argument("--name", type=str, default=None, help="jsons 目錄下的 JSON 相對路徑 (不含 .json)。不指定則遞迴掃描全部。")
    parser.add_argument("--dry-run", action="store_true", help="僅列印執行計畫，不寫入 Anki 也不改 JSON 檔")
    parser.add_argument(
        "--update-existing",
        action="store_true",
        help=(
            "身分有效的卡片改為更新（覆寫 Prompt / Context / References / Target_Language）。"
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

    if args.report_orphans:
        client = AnkiClient()
        try:
            orphan_count = await report_orphans(client)
        finally:
            await client.close()
        return 1 if orphan_count else 0

    if args.name:
        file_path = JSONS_DIR / f"{args.name}.json"
        if not file_path.exists():
            logger.error(f"❌ 找不到 JSON 檔案: {file_path}")
            return 1
        targets = [file_path]
    else:
        if not JSONS_DIR.exists():
            logger.error(f"❌ 找不到 jsons 資料夾: {JSONS_DIR}")
            return 1
        targets = sorted(JSONS_DIR.rglob("*.json"))
        if not targets:
            logger.info("ℹ️ jsons 目錄下沒有找到任何 JSON 檔案。")
            return 0
        logger.info(f"🔍 準備批量匯入 {len(targets)} 個 JSON 檔案...")

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
