"""重新讀寫母卡片 JSON 欄位以修復 HTML 逸出格式的腳本。

Re-read and re-write master-card JSON fields so the html.escape protection
is applied, fixing broken red-text/underline rendering.
"""

import os
import sys
from pathlib import Path
import asyncio
import logging

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

backend_dir = str(Path(__file__).resolve().parents[3])
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)
import scripts.common.env  # noqa

from app.infrastructure.anki.client import AnkiClient
from app.services.card_service import CardService
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

async def main():
    """腳本主入口：搜尋母卡片並重寫其 JSON 欄位。

    Script entry point: find all master notes and rewrite their JSON fields
    to reapply the escape protection.
    """
    client = AnkiClient()
    card_service = CardService(client, None)

    try:
        query = 'note:"JP_VerbPair_Master_Dark"'
        logger.info(f"🔍 開始搜尋需要修復的母卡片 (Query: {query})")
        cards = await client.find_cards(query)
        
        if not cards:
            logger.info("❌ 找不到任何符合條件的卡片！")
            return
            
        logger.info(f"✅ 共找到 {len(cards)} 張母卡片，準備開始執行修復...\n")
        
        # 將卡片轉為 note IDs
        cards_info = await client.get_cards_info(cards)
        note_ids = list(set([c["note"] for c in cards_info]))
        
        logger.info(f"📌 共有 {len(note_ids)} 個獨立的筆記需要修復。")
        
        success_count = 0
        error_count = 0
        
        for idx, note_id in enumerate(note_ids, 1):
            try:
                # 讀取自動詞與他動詞的 JSON
                # 這裡的 safe_read_list 會觸發我們剛剛更新過的解析邏輯，完美還原出含有 <u> 的原本資料
                in_list = await AnkiJsonFieldManager.safe_read_list(card_service, note_id, "Intransitive_Data_JSON")
                tr_list = await AnkiJsonFieldManager.safe_read_list(card_service, note_id, "Transitive_Data_JSON")
                
                # 重新寫回，這會觸發 html.escape 的保護機制
                await AnkiJsonFieldManager.update_field(card_service, note_id, "Intransitive_Data_JSON", in_list)
                await AnkiJsonFieldManager.update_field(card_service, note_id, "Transitive_Data_JSON", tr_list)
                
                success_count += 1
                if idx % 10 == 0:
                    logger.info(f"⏳ 進度: {idx}/{len(note_ids)}")
                    
            except Exception as e:
                logger.error(f"⚠️ 處理筆記 {note_id} 時發生錯誤: {e}")
                error_count += 1
                
        logger.info("\n🎉 修復任務完成！")
        logger.info(f"   ✅ 成功修復: {success_count} 個筆記")
        if error_count > 0:
            logger.info(f"   ❌ 失敗: {error_count} 個筆記")
            
        logger.info("\n💡 請打開 Anki，卡片的紅字與底線效果現在應該已經正常顯示了！")
        
    finally:
        await client.close()

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
