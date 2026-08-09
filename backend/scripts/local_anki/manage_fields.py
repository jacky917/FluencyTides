"""手動維護 Anki 筆記模型欄位結構的開發者腳本。

Developer script for manually maintaining Anki note model field
schemas (add / remove / rename fields via AnkiSchemaBuilder).
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

# 確保 sys.path 包含 backend 根目錄並載入 .env
backend_dir = str(Path(__file__).resolve().parents[2])
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)
import scripts.common.env  # noqa

from app.infrastructure.anki.client import AnkiClient
from scripts.local_anki.schema_builder import AnkiSchemaBuilder

logging.basicConfig(level=logging.INFO, format="%(message)s")


async def main():
    """腳本進入點：執行手動編寫的欄位維護操作並確保連線關閉。

    Script entry point: run the manually written field maintenance
    operations and ensure the Anki client connection is closed.
    """
    client = AnkiClient()
    schema_builder = AnkiSchemaBuilder(client)

    print("🔧 開始進行欄位結構維護...")
    
    # 這裡可以寫入你想要手動執行的欄位操作。
    # 以下為將 Context_Note_ID 新增到 Cloze 模型的範例：
    
    try:
        await schema_builder.rename_field_if_exists(
            model_name="JP_VerbPair_Cloze_Dark",
            old_field_name="Target_Particle_Verb",
            new_field_name="Verb_Pair_JSON"
        )
    except Exception as e:
        print(f"⚠️ 發生錯誤: {e}")
    finally:
        await client.close()

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
