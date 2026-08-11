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

from app.core.exceptions import AnkiFieldCorruptedError
from app.infrastructure.anki.client import AnkiClient, AnkiConnectError
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# 配對 Anki 的音檔標籤，例如 [sound:file.mp3] 或 [sound:file.ogg]
SOUND_TAG_PATTERN = re.compile(r"\[sound:[^\]]+\]")


def _collect_audio(items: list) -> list[str]:
    """遞迴收集陣列中所有 ``audio`` 值。

    Recursively collect every ``audio`` value found in a list.

    兩種結構都要涵蓋：``Recordings_*`` / ``Prompt_Audios`` 的 ``audio`` 在
    項目頂層，而 ``References_*`` 的音檔藏在項目的 ``audios`` 子陣列裡。
    只看頂層會讓 References 的媒體檔清不掉卻回報「沒有找到」。

    Both shapes must be covered: ``Recordings_*`` and ``Prompt_Audios`` carry
    ``audio`` at the item's top level, while ``References_*`` nest theirs in an
    ``audios`` sub-list. Looking only at the top level would leave References
    media on disk while reporting "nothing found".

    Args:
        items: 已解析的 JSON 陣列。The parsed JSON array.

    Returns:
        所有 audio 檔名。Every audio filename found.
    """
    files: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("audio"):
            files.append(str(item["audio"]))
        nested = item.get("audios")
        if isinstance(nested, list):
            files.extend(_collect_audio(nested))
    return files


def extract_audio_from_json_field(
    field_name: str, raw_value: str
) -> tuple[list[str], bool, bool]:
    """從 JSON 陣列欄位取出所有 audio 檔名，判斷是否需要清空該欄位。

    Extract every audio filename from a JSON array field and report whether
    the field needs to be cleared.

    欄位的儲存格式並不一致：經語音流程寫入的（如 ``Recordings_*``）是
    HTML 轉義過的（``&quot;``），而匯入腳本直寫的（如 ``References_*``）
    是未轉義的原始 JSON。因此一律經 ``AnkiJsonFieldManager`` 解析，它會
    先剝除 Anki 插入的 HTML 並還原實體，對兩種格式都成立（S065）。

    The stored format is not uniform: fields written by the voice flow
    (e.g. ``Recordings_*``) are HTML-escaped (``&quot;``), while fields
    written directly by import scripts (e.g. ``References_*``) are raw
    JSON. Parsing therefore goes through ``AnkiJsonFieldManager``, which
    strips Anki-injected HTML and unescapes entities, so it handles both
    stored formats (S065).

    Args:
        field_name: 欄位名稱，僅用於日誌。Field name, used only for logging.
        raw_value: Anki 欄位的原始字串內容。Raw Anki field string.

    Returns:
        ``(audio 檔名列表, 是否為含音檔的 JSON 陣列, 是否應完全跳過該欄位)``。
        第二個值為 ``False`` 代表該欄位不是含音檔的 JSON 陣列，呼叫端應改走
        ``[sound:...]`` 標籤路徑；第三個值為 ``True`` 時代表欄位已損毀，呼叫端
        **不得對該欄位做任何寫入**。``(audio filenames, whether it is a JSON
        array holding audio, whether the field must be skipped entirely)``. The
        third value distinguishes "corrupted, do not touch" from "not a JSON
        array, try the sound-tag path".
    """
    if not raw_value.strip():
        return [], False, False

    try:
        items = AnkiJsonFieldManager.parse_field_string(raw_value)
    except AnkiFieldCorruptedError as e:
        # 不再靜默吞掉：欄位有內容卻無法解析，代表資料可能已損毀，
        # 必須讓使用者看見，否則會誤以為「這張卡沒有錄音」。
        # No longer swallowed: a non-empty field that cannot be parsed may be
        # corrupted, and hiding it would read as "this card has no recording".
        logger.error("❌ 欄位 '%s' 解析失敗，已跳過該欄位: %s", field_name, e)
        return [], False, True

    files = _collect_audio(items)
    return files, bool(files), False

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

                    # 1. 嘗試解析為 JSON 陣列 (處理 Recordings / References 等欄位)
                    json_files, is_json_audio, must_skip = extract_audio_from_json_field(
                        field_name, original_val
                    )
                    if must_skip:
                        # 已判定損毀：任何寫入都可能讓情況更糟，整個欄位跳過。
                        # 若在此往下走，即使只是 .strip() 也會改動欄位內容並印出
                        # 「音訊已清除」，與上面的錯誤訊息自相矛盾。
                        # Corrupted: any write risks making it worse, so skip the
                        # field entirely. Falling through would rewrite it (even a
                        # bare .strip()) and log "audio cleared", contradicting the
                        # error just emitted.
                        continue
                    if is_json_audio:
                        field_files.extend(json_files)
                        new_val = "[]"  # 清空該 JSON 陣列

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
