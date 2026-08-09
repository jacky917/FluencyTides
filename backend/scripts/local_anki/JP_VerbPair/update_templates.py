"""更新 JP_VerbPair 系列模型的 Anki 樣板腳本。

Update the HTML/CSS templates of the JP_VerbPair family of Anki note models
(Master, Context, Cloze) from local template files.
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

async def update_all_templates(client: AnkiClient, backend_directory: str):
    """更新所有指定模型的樣板。

    Update templates of all listed Anki note models.

    Args:
        client: AnkiConnect 客戶端。AnkiConnect client instance.
        backend_directory: backend 根目錄路徑。Path to the backend root directory.
    """
    schema_builder = AnkiSchemaBuilder(client)
    model_dir = os.path.join(backend_directory, "app", "anki_models")
    
    models_to_update = [
        "JP_VerbPair_Master_Dark",
        "JP_Context_Dark",
        "JP_VerbPair_Cloze_Dark"
    ]
    
    print("🔧 開始更新 Anki 現有筆記模型的 HTML/CSS 樣板...")
    for model in models_to_update:
        print(f"▶ 處理模型: {model} ... ")
        try:
            await schema_builder.update_model_templates_from_files(model, model_dir)
            print("   ✅ 更新成功！\n")
        except Exception as e:
            print(f"   ⚠️ 發生錯誤 (原因: {e})\n")
            
    print("🎉 所有模型樣板更新完畢！你不需要在 Anki 內刪除舊模型，變更已經自動套用。")

async def main():
    """腳本主入口：建立客戶端並執行樣板更新。

    Script entry point: create the client and run the template update.
    """
    client = AnkiClient()
    try:
        await update_all_templates(client, backend_dir)
            
    finally:
        await client.close()

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
