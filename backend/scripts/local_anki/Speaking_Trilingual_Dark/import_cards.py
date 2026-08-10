"""專屬 Speaking_Trilingual_Dark 的 Anki 卡片匯入腳本。

Anki card import script dedicated to the Speaking_Trilingual_Dark model,
creating cards in bulk from JSON files (native JSON arrays are serialized).

由 ``scripts/common/samples/speaking_trilingual_sample.json`` 同構的 JSON
檔批量建卡（欄位值可為原生 JSON 陣列，會自動序列化為字串）。

用法：
    cd backend
    python scripts/local_anki/Speaking_Trilingual_Dark/import_cards.py [--name <相對路徑>] [--dry-run]
    # 省略 --name 時遞迴掃描 jsons/ 下所有 JSON

已存在的卡片（同牌組 + 同 Prompt）**預設跳過**；加 ``--update-existing``
才會覆寫 Prompt / Context / References_×3。Recordings 等使用者資料在任何
模式下都不會被動到。

Existing cards (same deck + same Prompt) are skipped by default; pass
``--update-existing`` to overwrite Prompt / Context / References. User
data such as Recordings is never touched in either mode.
"""

import argparse
import asyncio
import base64
import json
import logging
import sys
from pathlib import Path

# 確保 sys.path 包含 backend 根目錄並載入 .env
backend_dir = Path(__file__).resolve().parents[3]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))
import scripts.common.env  # noqa

from app.core.config import settings
from app.infrastructure.anki.client import AnkiClient
from app.infrastructure.utils.id_generator import generate_unique_card_id
from app.schemas.anki import AnkiNote, AnkiNoteOptions

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


async def _process_media_paths(client: AnkiClient, data: any) -> any:
    """遞迴掃描字典/陣列，若遇到 audio 或 avatar 為絕對路徑，上傳至 Anki 並替換為純檔名。

    Recursively scan dicts/lists; when an audio or avatar value is an absolute
    path, upload it to Anki media and replace it with the bare filename.

    Args:
        client: AnkiConnect 客戶端。AnkiConnect client instance.
        data: 任意巢狀資料。Arbitrary nested data structure.

    Returns:
        處理後的資料。The processed data structure.
    """
    if isinstance(data, dict):
        for k, v in list(data.items()):
            if k in ("audio", "avatar") and isinstance(v, str) and (":/" in v or ":\\" in v or v.startswith("/")):
                path = Path(v)
                if path.exists():
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
                    logger.warning(f"   ⚠️ 媒體檔案不存在: {path}")
            else:
                data[k] = await _process_media_paths(client, v)
        return data
    elif isinstance(data, list):
        for i in range(len(data)):
            data[i] = await _process_media_paths(client, data[i])
        return data
    else:
        return data


async def _normalize_fields(client: AnkiClient, fields: dict) -> dict[str, str]:
    """把 JSON 欄位的原生陣列序列化為字串，自動上傳媒體，並強制使用通用工具類生成 Card_ID 與讀取 .env 的 TG_Bot。

    Serialize native arrays in JSON fields to strings, auto-upload media, and
    force-generate Card_ID via the shared utility and TG_Bot from .env.

    Args:
        client: AnkiConnect 客戶端。AnkiConnect client instance.
        fields: 原始欄位字典。Raw field dictionary from the JSON file.

    Returns:
        全為字串值的欄位字典。Field dictionary with all values as strings.
    """
    out: dict[str, str] = {}
    for key, value in fields.items():
        if isinstance(value, (list, dict)):
            processed_value = await _process_media_paths(client, value)
            out[key] = json.dumps(processed_value, ensure_ascii=False)
        else:
            out[key] = str(value)
            
    for key in JSON_FIELDS:
        out.setdefault(key, "[]")
        
    # 強制覆寫：不論 JSON 範例中是否提供，都以實際執行環境為主
    out["Card_ID"] = generate_unique_card_id(prefix="st")
    out["TG_Bot"] = settings.TG_BOT_USERNAME or ""
    return out


async def import_cards(file_path: Path, dry_run: bool, update_existing: bool = False) -> None:
    """讀 JSON 檔並逐張建卡；已存在的卡片預設跳過。

    Read the JSON file and create cards one by one; existing cards are
    skipped by default.

    Args:
        file_path: JSON 檔案路徑。Path to the JSON file.
        dry_run: 預覽模式，不實際寫入 Anki。Preview mode; no writes to Anki.
        update_existing: 已存在的卡片是否更新。預設 ``False``（跳過），
            傳 ``True`` 才會覆寫 Prompt / Context / References_×3
            （Recordings 等使用者資料一律不動）。Whether to update cards
            that already exist. Defaults to ``False`` (skip); pass ``True``
            to overwrite Prompt / Context / References (user data such as
            Recordings is never touched).
    """
    logger.info(f"📂 開始載入 JSON 檔案: {file_path.name} ({file_path})")
    with open(file_path, "r", encoding="utf-8") as f:
        cards_data = json.load(f)
    mode_hint = "更新" if update_existing else "跳過"
    logger.info(
        f"📄 成功載入，共包含 {len(cards_data)} 筆卡片資料。"
        f"（已存在的卡片將{mode_hint}）開始與 Anki 同步..."
    )

    client = AnkiClient()
    created = updated = skipped = failed = 0
    try:
        for i, card in enumerate(cards_data, 1):
            fields = await _normalize_fields(client, card.get("fields", {}))
            if not fields.get("Prompt"):
                logger.warning(f"⚠️ [{i}] 缺少 Prompt 欄位，跳過。")
                continue
            deck_name = resolve_deck_name(file_path, card.get("deckName"))

            escaped_prompt = fields["Prompt"].replace('"', '\\"')
            escaped_deck = deck_name.replace('"', '\\"')
            query = f'deck:"{escaped_deck}" Prompt:"{escaped_prompt}"'
            existing_notes = await client.find_notes(query)

            if existing_notes:
                note_id = existing_notes[0]

                # 預設行為：卡片已存在就跳過，完全不碰（需 --update-existing 才更新）
                if not update_existing:
                    logger.info(
                        f"⏭️ [{i}] 已存在，跳過 note_id={note_id}: {fields['Prompt'][:40]}"
                    )
                    skipped += 1
                    continue

                # 更新模式：排除會破壞使用者資料的欄位
                update_fields = {
                    k: v for k, v in fields.items()
                    if k not in ("Prompt_Audios", "Recordings_ZH", "Recordings_JA", "Recordings_EN", "Card_ID", "TG_Bot")
                }

                if dry_run:
                    logger.info(f"🧪 [DRY-RUN] [{i}] {deck_name} <- 更新現有卡片 note_id={note_id}: {fields['Prompt'][:40]}")
                    updated += 1
                    continue

                try:
                    await client.update_note_fields(note_id, update_fields)
                    logger.info(f"🔄 [{i}] 更新成功 note_id={note_id}")
                    updated += 1
                except Exception as e:
                    logger.warning(f"⚠️ [{i}] 更新失敗 note_id={note_id}，原因: {e}")
                    failed += 1
            else:
                if dry_run:
                    logger.info(f"🧪 [DRY-RUN] [{i}] {deck_name} <- 新增卡片 {fields['Card_ID']}: {fields['Prompt'][:40]}")
                    created += 1
                    continue

                await client.create_deck(deck_name)
                note = AnkiNote(
                    deckName=deck_name,
                    modelName=card.get("modelName", MODEL_NAME),
                    fields=fields,
                    tags=list(card.get("tags", ["Speaking_Trilingual"])),
                    options=AnkiNoteOptions(allowDuplicate=False, duplicateScope="deck"),
                )
                try:
                    note_id = await client.add_note(note)
                    logger.info(f"🎉 [{i}] 建立成功 Card_ID={fields['Card_ID']} note_id={note_id}")
                    created += 1
                except Exception as e:
                    logger.warning(f"⚠️ [{i}] 建立失敗（可能重複）Card_ID={fields['Card_ID']}，原因: {e}")
                    failed += 1

        summary = f"📊 共 {len(cards_data)} 筆｜新增 {created}｜更新 {updated}｜跳過 {skipped}"
        if failed:
            summary += f"｜失敗 {failed}"
        if skipped and not update_existing:
            summary += "　(要覆寫既有卡片請加 --update-existing)"
        logger.info(summary)
    finally:
        await client.close()


async def main() -> None:
    """腳本主入口：解析參數並匯入單檔或整個 jsons 目錄。

    Script entry point: parse arguments and import either a single JSON file
    or every JSON file under the jsons directory.
    """
    parser = argparse.ArgumentParser(description="Speaking_Trilingual_Dark 卡片匯入腳本")
    parser.add_argument("--name", type=str, default=None, help="jsons 目錄下的 JSON 相對路徑 (不含 .json，子資料夾需一併給，如 '日本語面接/Q社/志望動機')。不指定則遞迴掃描 jsons 目錄下所有檔案。")
    parser.add_argument("--dry-run", action="store_true", help="僅列印執行計畫，不寫入 Anki")
    parser.add_argument(
        "--update-existing",
        action="store_true",
        help=(
            "已存在的卡片改為更新（覆寫 Prompt / Context / References_×3）。"
            "預設不加此參數時一律跳過既有卡片。Recordings 等使用者資料在任何模式下都不會被動到。"
        ),
    )
    args = parser.parse_args()
    
    jsons_dir = JSONS_DIR

    if args.name:
        file_path = jsons_dir / f"{args.name}.json"
        if not file_path.exists():
            fallback_path = Path(__file__).parent / f"{args.name}.json"
            if fallback_path.exists():
                file_path = fallback_path
            else:
                logger.error(f"❌ 找不到 JSON 檔案: {args.name}.json")
                return
        await import_cards(file_path, args.dry_run, args.update_existing)
    else:
        if not jsons_dir.exists():
            logger.error(f"❌ 找不到 jsons 資料夾: {jsons_dir}")
            return
            
        json_files = list(jsons_dir.rglob("*.json"))
        if not json_files:
            logger.info("ℹ️ jsons 目錄下沒有找到任何 JSON 檔案。")
            return
            
        logger.info(f"🔍 準備批量匯入 {len(json_files)} 個 JSON 檔案...")
        for file_path in json_files:
            await import_cards(file_path, args.dry_run, args.update_existing)


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
