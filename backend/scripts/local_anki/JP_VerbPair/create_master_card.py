"""手動建立一對自他動詞母卡片的命令列工具。

CLI tool for manually creating a single intransitive/transitive verb-pair
master card (JP_VerbPair_Master_Dark) in Anki.
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# 確保 sys.path 包含 backend 根目錄並載入 .env
_backend_dir = Path(__file__).resolve().parents[3]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))
import scripts.common.env  # noqa

from app.infrastructure.anki.client import AnkiClient
from app.infrastructure.utils.id_generator import generate_unique_card_id

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

async def main():
    """腳本主入口：解析參數並在 Anki 中建立母卡片。

    Script entry point: parse CLI arguments and create the master card
    in Anki via AnkiConnect.
    """
    parser = argparse.ArgumentParser(description="手動建立一對自他動詞母卡片")
    parser.add_argument("-i", "--intransitive", required=True, help="自動詞 (例如: 止まる)")
    parser.add_argument("-t", "--transitive", required=True, help="他動詞 (例如: 止める)")
    parser.add_argument("-d", "--deck", default="日本語::自他動詞::Master", help="目標牌組 (預設: 日本語::自他動詞::Master)")
    
    args = parser.parse_args()

    client = AnkiClient()
    try:
        master_card_id = generate_unique_card_id(prefix="vp-m")
        logger.info(f"✨ 產生的 Card_ID: {master_card_id}")
        
        note = {
            "deckName": args.deck,
            "modelName": "JP_VerbPair_Master_Dark",
            "fields": {
                "Card_ID": master_card_id,
                "Intransitive_Word": args.intransitive,
                "Transitive_Word": args.transitive,
                "Intransitive_Data_JSON": "[]",
                "Transitive_Data_JSON": "[]"
            },
            "options": {
                "allowDuplicate": True
            },
            "tags": ["VerbPair", "ManualCreated"]
        }
        
        logger.info(f"🚀 準備寫入 Anki (自動詞: {args.intransitive}, 他動詞: {args.transitive})")
        results = await client._invoke("addNotes", notes=[note])
        
        if results and results[0]:
            logger.info(f"✅ 母卡片建立成功！ Note ID: {results[0]}")
            logger.info(f"   (請記錄此 Note ID，這就是 migrate_child_cards.json 中所需要的 target_master_nid)")
        else:
            logger.error("❌ 建立失敗，可能是 AnkiConnect 問題或牌組/模型不存在。")
            
    except Exception as e:
        logger.error(f"❌ 發生未預期的錯誤: {e}")
    finally:
        await client.close()

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
