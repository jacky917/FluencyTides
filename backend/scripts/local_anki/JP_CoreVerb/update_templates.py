"""更新 JP_CoreVerb 系列既有筆記模型的 HTML / CSS 樣板。

Update the HTML/CSS templates of existing JP_CoreVerb note models
without overwriting field settings; structural field changes prompt
for interactive confirmation.

不會覆蓋欄位設定或重置模型；若偵測到欄位結構變更（新增/調序）會先要求
互動式確認。目標模型：

- JP_CoreVerb_Master_Dark（核心動詞母卡）
- JP_CoreVerb_Cloze_Dark（核心動詞克漏字子卡）

註：Context 卡共用通用模型 `JP_Context_Dark`，其樣板由 JP_VerbPair 側的
update_templates.py 管理，此處不重複處理。

使用方式：
    python scripts/local_anki/JP_CoreVerb/update_templates.py
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


async def update_all_templates(client: AnkiClient, backend_directory: str) -> None:
    """依序更新所有 JP_CoreVerb 模型的樣板與樣式。

    Sequentially update templates and styling for all JP_CoreVerb models.

    Args:
        client: AnkiConnect 客戶端實例。AnkiConnect client instance.
        backend_directory: backend 根目錄的絕對路徑，用於定位模型資產。
            Absolute path of the backend root, used to locate model assets.
    """
    schema_builder = AnkiSchemaBuilder(client)
    model_dir = os.path.join(backend_directory, "app", "anki_models")

    models_to_update = [
        "JP_CoreVerb_Master_Dark",
        "JP_CoreVerb_Cloze_Dark"
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


async def main() -> None:
    """腳本進入點：建立客戶端、執行更新、確保連線關閉。

    Script entry point: create the client, run the updates, and
    ensure the connection is closed.
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
