#!/usr/bin/env python3
"""
更新 Anki 卡片中的 Telegram Bot Deep Link 資訊。

當 .env 中的 TG_BOT_USERNAME 改變時，
需要更新 Anki 中已建立卡片的 TG_Bot 欄位，
確保卡片上的 Deep Link 能跳轉到正確的機器人。
會自動過濾掉沒有 TG_Bot 欄位的筆記。

使用方式:
    python update_tg_bot_links.py --deck "Your Deck Name"
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# 將 backend 目錄加入 sys.path，以便載入 app 模組
backend_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(backend_dir))

# 切換工作目錄到 backend_dir，確保 pydantic-settings 能夠正確讀取 .env
os.chdir(str(backend_dir))

from app.core.config import settings
from app.infrastructure.anki.client import AnkiClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="更新指定牌組下卡片的 Telegram Bot Username (TG_Bot 欄位)，會自動略過沒有該欄位的卡片。"
    )
    parser.add_argument(
        "--deck",
        required=True,
        help="目標牌組名稱 (例如: 'Default' 或 'English::Spoken')",
    )

    args = parser.parse_args()
    deck_name = args.deck
    new_username = settings.TG_BOT_USERNAME

    if not new_username:
        logger.error(
            "❌ 找不到 TG_BOT_USERNAME，請確認 .env 檔案中是否已設定。"
        )
        return

    logger.info("=========================================")
    logger.info(f"🎯 目標牌組: {deck_name}")
    logger.info(f"🤖 新的 TG_BOT_USERNAME: {new_username}")
    logger.info("=========================================")

    client = AnkiClient()
    try:
        # 只根據牌組搜尋所有筆記
        query = f'"deck:{deck_name}"'
        logger.info(f"🔍 正在 Anki 中搜尋: {query}")
        
        note_ids = await client.find_notes(query)

        if not note_ids:
            logger.warning("⚠️ 找不到符合條件的筆記。")
            return

        logger.info(f"✅ 找到 {len(note_ids)} 筆筆記，開始進行檢查與更新...")

        success_count = 0
        skip_count = 0
        chunk_size = 500  # 批次處理，避免一次取得過多資訊導致超時
        
        for i in range(0, len(note_ids), chunk_size):
            chunk_ids = note_ids[i:i + chunk_size]
            note_infos = await client.get_notes_info(notes=chunk_ids)
            
            for note_info in note_infos:
                if "TG_Bot" not in note_info.fields:
                    logger.warning(
                        f"⚠️ 筆記 ID {note_info.noteId} (模型: {note_info.modelName}) "
                        "沒有 TG_Bot 欄位，略過更新。"
                    )
                    skip_count += 1
                    continue
                
                try:
                    await client.update_note_fields(
                        note_id=note_info.noteId,
                        fields={"TG_Bot": new_username},
                    )
                    success_count += 1
                except Exception as e:
                    logger.error(f"❌ 更新筆記 {note_info.noteId} 失敗: {e}")
            
            logger.info(f"🔄 進度: 已處理 {min(i + chunk_size, len(note_ids))}/{len(note_ids)} 筆筆記...")

        logger.info("=========================================")
        logger.info(
            f"🎉 執行完成！成功更新 {success_count} 筆，略過 {skip_count} 筆。"
        )

    except Exception as e:
        logger.error(f"❌ 發生未預期錯誤: {e}")
    finally:
        await client.close()

if __name__ == "__main__":
    asyncio.run(main())
