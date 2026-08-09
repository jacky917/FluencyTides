"""
清除特定卡牌特定欄位中的語音標籤 ([sound:...])。

Clear audio tags ([sound:...]) from specific fields of matched
notes, optionally deleting the underlying media files; only the
audio parts are removed and other field text is preserved.

此腳本允許使用者指定一或多個欄位，並將這些欄位中的語音標籤移除。
如果欄位中只有語音標籤，移除後欄位將變為空白。
這能確保「不動其他內容」，只會移除 [sound:...] 的部分，保留欄位內原本的文字（如果有的話）。

用法範例:
    python scripts/clear_audio_fields.py --query "Card_ID:sc-12345" --fields Pronunciation SentenceAudio
    python scripts/clear_audio_fields.py --query "nid:123456789" --fields VocabAudio --dry-run
"""

import argparse
import asyncio
import logging
import re
import sys
from pathlib import Path

# 確保 sys.path 包含 backend 根目錄並載入 .env
_backend_dir = Path(__file__).resolve().parents[3]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))
import scripts.common.env  # noqa

from app.infrastructure.anki.client import AnkiClient, AnkiConnectError

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# 配對 Anki 的音檔標籤，例如 [sound:file.mp3] 或 [sound:file.ogg]
SOUND_TAG_PATTERN = re.compile(r"\[sound:[^\]]+\]")

async def main() -> None:
    """腳本進入點：搜尋筆記、清除指定欄位音訊並刪除媒體檔案。

    Script entry point: find matching notes, strip audio from the
    given fields, and delete the associated media files.
    """
    parser = argparse.ArgumentParser(description="清除特定卡牌特定欄位中的語音標籤 ([sound:...])")
    parser.add_argument("--query", required=True, help="Anki 搜尋語法 (例如 'Card_ID:sc-1234' 或 'nid:123456')")
    parser.add_argument("--fields", nargs="+", required=True, help="要清除語音的欄位名稱列表 (例如 Pronunciation SentenceAudio)")
    parser.add_argument("--dry-run", action="store_true", help="不實際修改，僅預覽結果")
    args = parser.parse_args()

    # 初始化 AnkiConnect 客戶端 (會自動讀取 .env 中的設定)
    client = AnkiClient()
    
    try:
        # 1. 根據查詢字串搜尋筆記
        note_ids = await client.find_notes(args.query)
        if not note_ids:
            logger.warning(f"找不到符合條件的筆記: {args.query}")
            return
            
        logger.info(f"找到 {len(note_ids)} 筆筆記，準備處理欄位: {args.fields}")
        
        # 2. 取得筆記詳細資訊
        notes_info = await client.get_notes_info(note_ids)
        
        for note in notes_info:
            updated_fields: dict[str, str] = {}
            files_to_delete: list[str] = []
            
            for field_name in args.fields:
                if field_name in note.fields:
                    field_data = note.fields[field_name]
                    original_val = str(field_data.get("value", ""))
                    new_val = original_val
                    field_files = []
                    
                    # 1. 嘗試解析為 JSON (處理 Recordings 欄位)
                    import json
                    try:
                        json_data = json.loads(original_val)
                        if isinstance(json_data, list):
                            has_audio_record = False
                            for item in json_data:
                                if isinstance(item, dict) and "audio" in item:
                                    field_files.append(item["audio"])
                                    has_audio_record = True
                            if has_audio_record:
                                new_val = "[]"  # 清空該 JSON 陣列
                    except json.JSONDecodeError:
                        pass
                    
                    # 2. 處理標準 Anki [sound:...] 標籤
                    sound_tags = SOUND_TAG_PATTERN.findall(new_val)
                    for tag in sound_tags:
                        filename = tag[7:-1]
                        field_files.append(filename)
                    new_val = SOUND_TAG_PATTERN.sub("", new_val).strip()
                    
                    if original_val != new_val or field_files:
                        updated_fields[field_name] = new_val
                        files_to_delete.extend(field_files)
                        logger.info(f"筆記 {note.noteId} 的欄位 '{field_name}' 音訊已清除。將刪除 {len(field_files)} 個實體檔案。")
                    else:
                        logger.info(f"筆記 {note.noteId} 的欄位 '{field_name}' 中沒有找到音訊標籤或記錄。")
                else:
                    logger.warning(f"筆記 {note.noteId} 沒有欄位 '{field_name}'")
            
            # 3. 執行更新與實體檔案刪除
            if updated_fields:
                if args.dry_run:
                    logger.info(f"[Dry Run] 預計更新筆記 {note.noteId} 的欄位: {list(updated_fields.keys())}")
                    logger.info(f"[Dry Run] 預計刪除實體檔案: {files_to_delete}")
                else:
                    await client.update_note_fields(note.noteId, updated_fields)
                    logger.info(f"✅ 成功更新筆記 {note.noteId}")
                    
                    for filename in files_to_delete:
                        if filename:
                            try:
                                await client.delete_media_file(filename)
                                logger.info(f"🗑️ 已從 Anki 媒體庫刪除真實檔案: {filename}")
                            except Exception as e:
                                logger.error(f"❌ 刪除實體檔案 {filename} 失敗: {e}")
            else:
                logger.info(f"筆記 {note.noteId} 無需更新。")

    except AnkiConnectError as e:
        logger.error(f"Anki API 錯誤: {e}")
    finally:
        await client.close()

if __name__ == "__main__":
    asyncio.run(main())
