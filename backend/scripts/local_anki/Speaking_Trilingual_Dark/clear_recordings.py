"""
Speaking_Trilingual_Dark 專屬的語音清理腳本 (封裝自 common/clear_audio_fields.py)

Recording-cleanup script dedicated to Speaking_Trilingual_Dark, wrapping
common/clear_audio_fields.py: deletes recorded audio files from the Anki
media library and clears the Recordings_ZH/JA/EN fields.

此腳本設計用來輕鬆清除 Speaking_Trilingual_Dark 卡片的提交語音 (Recordings)。
這會刪除 Anki 媒體庫中的實體音檔，並清空 Recordings_ZH, Recordings_JA, Recordings_EN 欄位，
讓你可以重新錄音挑戰。

【使用方式】

1. 清除「所有」Speaking_Trilingual_Dark 卡片的語音:
   python scripts/local_anki/Speaking_Trilingual_Dark/clear_recordings.py

2. 僅清除特定「主牌組及所有子牌組」的語音 (例如買花情境):
   python scripts/local_anki/Speaking_Trilingual_Dark/clear_recordings.py --deck "FluencyTides::Speaking_Trilingual::お花屋さんで花を買う"

3. 僅清除「特定卡片 ID」的語音:
   python scripts/local_anki/Speaking_Trilingual_Dark/clear_recordings.py --card-id st-12345678

4. 預覽模式 (安全檢查，不實際刪除):
   加上 --dry-run 參數，例如:
   python scripts/local_anki/Speaking_Trilingual_Dark/clear_recordings.py --dry-run

5. 指定清除特定語言的語音 (支援 zh, ja, en，可多選):
   python scripts/local_anki/Speaking_Trilingual_Dark/clear_recordings.py --lang ja
   python scripts/local_anki/Speaking_Trilingual_Dark/clear_recordings.py --lang zh en
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# 確保能載入 backend 模組
_backend_dir = Path(__file__).resolve().parents[3]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

# 引入共用的 clear_audio_fields 邏輯
from scripts.local_anki.common.clear_audio_fields import main as clear_main

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)

async def run_wrapper() -> None:
    """解析參數、組合搜尋條件並轉呼叫共用清理腳本。

    Parse CLI arguments, build the Anki search query and delegate to the
    shared clear_audio_fields main routine via sys.argv rewriting.
    """
    parser = argparse.ArgumentParser(description="Speaking_Trilingual_Dark 專用: 清除語音錄音記錄")
    parser.add_argument("--deck", type=str, help="指定牌組名稱 (例如 'FluencyTides::Speaking_Trilingual::お花屋さんで花を買う')。自動支援萬用字元 '*'")
    parser.add_argument("--card-id", type=str, help="指定單一卡片 ID (例如 'st-123456')")
    parser.add_argument("--lang", nargs="+", type=str, help="指定要清除的語言簡稱 (支援 zh, ja, en，可傳入多個)。若未提供，則預設清除全部三個語言。")
    parser.add_argument("--dry-run", action="store_true", help="安全預覽模式：不實際刪除檔案與修改卡片，僅顯示將被影響的項目")
    
    args = parser.parse_args()
    
    # 建構 Anki 搜尋語法
    query_parts = ['"note:Speaking_Trilingual_Dark"']
    
    if args.deck:
        query_parts.append(f'"deck:{args.deck}"')
    if args.card_id:
        query_parts.append(f'Card_ID:{args.card_id}')
        
    final_query = " ".join(query_parts)
    
    # 決定目標要清理的欄位
    target_fields = []
    if args.lang:
        for l in args.lang:
            l_lower = l.lower()
            if l_lower == "zh":
                target_fields.append("Recordings_ZH")
            elif l_lower == "ja":
                target_fields.append("Recordings_JA")
            elif l_lower == "en":
                target_fields.append("Recordings_EN")
            else:
                logger.warning(f"⚠️ 未知的語言簡稱: {l}，將被忽略")
        
        # 去除重複
        target_fields = list(dict.fromkeys(target_fields))
        
        if not target_fields:
            logger.error("❌ 沒有任何有效的目標欄位可清理，程式結束")
            return
    else:
        target_fields = ["Recordings_ZH", "Recordings_JA", "Recordings_EN"]
    
    logger.info("=" * 60)
    logger.info("🚀 準備清理 Speaking_Trilingual_Dark 語音記錄")
    logger.info(f"🔍 搜尋條件: {final_query}")
    logger.info(f"📄 清理欄位: {', '.join(target_fields)}")
    if args.dry_run:
        logger.info("⚠️ 目前為 --dry-run 模式，不會進行實際刪除")
    logger.info("=" * 60)
    
    # 覆寫 sys.argv 傳遞給共用腳本
    sys.argv = ["clear_audio_fields.py", "--query", final_query, "--fields"] + target_fields
    if args.dry_run:
        sys.argv.append("--dry-run")
        
    await clear_main()

if __name__ == "__main__":
    try:
        asyncio.run(run_wrapper())
    except KeyboardInterrupt:
        logger.info("\n操作已取消")
