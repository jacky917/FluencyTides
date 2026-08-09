"""匯入通用系列筆記模型（如 Speaking_Trilingual_Dark）至本地 Anki。

Import common note models (e.g. Speaking_Trilingual_Dark) into the
local Anki instance from the app/anki_models asset files.
"""

import os
import sys
from pathlib import Path

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

# 確保 sys.path 包含 backend 根目錄並載入 .env
backend_dir = str(Path(__file__).resolve().parents[3])
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)
import scripts.common.env  # noqa

import asyncio
from app.infrastructure.anki.client import AnkiClient
from scripts.local_anki.schema_builder import AnkiSchemaBuilder

async def import_all_models(client: AnkiClient, backend_directory: str):
    """依序匯入所有通用模型至 Anki。

    Sequentially import all common models into Anki.

    Args:
        client: AnkiConnect 客戶端實例。AnkiConnect client instance.
        backend_directory: backend 根目錄的絕對路徑，用於定位模型資產。
            Absolute path of the backend root, used to locate model assets.
    """
    schema_builder = AnkiSchemaBuilder(client)
    model_dir = os.path.join(backend_directory, "app", "anki_models")
    
    models_to_import = [
        "Speaking_Trilingual_Dark"
    ]
    
    print("🔧 開始匯入/更新 Anki 筆記模型...")
    for model in models_to_import:
        print(f"▶ 處理模型: {model} ... ")
        try:
            await schema_builder.import_model_from_files(model, model_dir)
            print("   ✅ 建立/更新成功！\n")
        except Exception as e:
            print(f"   ⚠️ 略過 (原因: {e})\n")
            
    print("🎉 所有模型匯入流程完畢！如果你有更新 HTML/CSS 但模型已存在，請考慮先在 Anki 內將舊模型刪除後再執行此腳本。")

async def main():
    """腳本進入點：建立客戶端、執行匯入、確保連線關閉。

    Script entry point: create the client, run the import, and ensure
    the connection is closed.
    """
    client = AnkiClient()
    try:
        await import_all_models(client, backend_dir)
            
    finally:
        await client.close()

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
