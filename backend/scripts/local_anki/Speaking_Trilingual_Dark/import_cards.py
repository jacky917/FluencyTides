"""專屬 Speaking_Trilingual_Dark 的 Anki 卡片匯入腳本。

Anki card import script dedicated to the Speaking_Trilingual_Dark model,
creating cards in bulk from JSON files (native JSON arrays are serialized).

由 ``scripts/common/samples/speaking_trilingual_sample.json`` 同構的 JSON
檔批量建卡（欄位值可為原生 JSON 陣列，會自動序列化為字串）。

用法：
    cd backend
    python scripts/local_anki/Speaking_Trilingual_Dark/import_cards.py --file <path> [--dry-run]
    # 省略 --file 時使用 samples/speaking_trilingual_sample.json
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


async def import_cards(file_path: Path, dry_run: bool) -> None:
    """讀 JSON 檔並逐張建卡。

    Read the JSON file and create or update cards one by one.

    Args:
        file_path: JSON 檔案路徑。Path to the JSON file.
        dry_run: 預覽模式，不實際寫入 Anki。Preview mode; no writes to Anki.
    """
    logger.info(f"📂 開始載入 JSON 檔案: {file_path.name} ({file_path})")
    with open(file_path, "r", encoding="utf-8") as f:
        cards_data = json.load(f)
    logger.info(f"📄 成功載入，共包含 {len(cards_data)} 筆卡片資料。開始與 Anki 同步...")

    client = AnkiClient()
    success = 0
    try:
        for i, card in enumerate(cards_data, 1):
            fields = await _normalize_fields(client, card.get("fields", {}))
            if not fields.get("Prompt"):
                logger.warning(f"⚠️ [{i}] 缺少 Prompt 欄位，跳過。")
                continue
            base_deck_name = card.get("deckName", "FluencyTides::Speaking_Trilingual")
            
            try:
                jsons_dir = Path(__file__).parent / "jsons"
                rel_path = file_path.relative_to(jsons_dir)
                parts = list(rel_path.parent.parts)
                deck_suffix = "::".join(parts + [file_path.stem]) if parts else file_path.stem
            except ValueError:
                deck_suffix = file_path.stem

            deck_name = f"{base_deck_name}::{deck_suffix}" if not base_deck_name.endswith(f"::{deck_suffix}") else base_deck_name

            escaped_prompt = fields["Prompt"].replace('"', '\\"')
            escaped_deck = deck_name.replace('"', '\\"')
            query = f'deck:"{escaped_deck}" Prompt:"{escaped_prompt}"'
            existing_notes = await client.find_notes(query)

            if existing_notes:
                note_id = existing_notes[0]
                # 更新模式：排除會破壞使用者資料的欄位
                update_fields = {
                    k: v for k, v in fields.items()
                    if k not in ("Prompt_Audios", "Recordings_ZH", "Recordings_JA", "Recordings_EN", "Card_ID", "TG_Bot")
                }
                
                if dry_run:
                    logger.info(f"🧪 [DRY-RUN] [{i}] {deck_name} <- 更新現有卡片 note_id={note_id}: {fields['Prompt'][:40]}")
                    success += 1
                    continue
                    
                try:
                    await client.update_note_fields(note_id, update_fields)
                    logger.info(f"🔄 [{i}] 更新成功 note_id={note_id}")
                    success += 1
                except Exception as e:
                    logger.warning(f"⚠️ [{i}] 更新失敗 note_id={note_id}，原因: {e}")
            else:
                if dry_run:
                    logger.info(f"🧪 [DRY-RUN] [{i}] {deck_name} <- 新增卡片 {fields['Card_ID']}: {fields['Prompt'][:40]}")
                    success += 1
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
                    success += 1
                except Exception as e:
                    logger.warning(f"⚠️ [{i}] 建立失敗（可能重複）Card_ID={fields['Card_ID']}，原因: {e}")
            
        logger.info(f"📊 共處理 {len(cards_data)} 筆，成功 {success} 筆。")
    finally:
        await client.close()


async def main() -> None:
    """腳本主入口：解析參數並匯入單檔或整個 jsons 目錄。

    Script entry point: parse arguments and import either a single JSON file
    or every JSON file under the jsons directory.
    """
    parser = argparse.ArgumentParser(description="Speaking_Trilingual_Dark 卡片匯入腳本")
    parser.add_argument("--name", type=str, default=None, help="位於 jsons 目錄下的 JSON 檔名 (不含 .json)。不指定則掃描 jsons 目錄下所有檔案。")
    parser.add_argument("--dry-run", action="store_true", help="僅列印執行計畫，不寫入 Anki")
    args = parser.parse_args()
    
    jsons_dir = Path(__file__).parent / "jsons"
    
    if args.name:
        file_path = jsons_dir / f"{args.name}.json"
        if not file_path.exists():
            fallback_path = Path(__file__).parent / f"{args.name}.json"
            if fallback_path.exists():
                file_path = fallback_path
            else:
                logger.error(f"❌ 找不到 JSON 檔案: {args.name}.json")
                return
        await import_cards(file_path, args.dry_run)
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
            await import_cards(file_path, args.dry_run)


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
