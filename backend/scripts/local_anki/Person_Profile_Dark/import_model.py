"""建立或更新本地 Anki 的人脈卡筆記類型（Person_Profile_Dark）。

Create or update the Person_Profile_Dark note type in the local Anki.

模型不存在時依 ``app/anki_models/Person_Profile_Dark.json`` 建立（兩種卡片：認人、見面前複習）；
已存在時只更新模板與 CSS（欄位有增減或順序改變時會先詢問）。

Creates the note type from the model definition when missing; otherwise
updates its templates and CSS (asking first before any field change).

用法 / Usage::

    python scripts/local_anki/Person_Profile_Dark/import_model.py
"""

import asyncio
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

# 確保 sys.path 包含 backend 根目錄並載入 .env
backend_dir = Path(__file__).resolve().parents[3]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))
import scripts.common.env  # noqa: E402,F401

from app.infrastructure.anki.client import AnkiClient  # noqa: E402
from scripts.local_anki.schema_builder import AnkiSchemaBuilder  # noqa: E402

MODEL_NAME = "Person_Profile_Dark"


async def main() -> None:
    """建立或更新模型。Create or update the model."""
    client = AnkiClient()
    try:
        builder = AnkiSchemaBuilder(client)
        model_dir = backend_dir / "app" / "anki_models"
        if MODEL_NAME in await client.get_model_names():
            print(f"▶ 模型 {MODEL_NAME} 已存在，更新模板與 CSS ...")
            await builder.update_model_templates_from_files(MODEL_NAME, model_dir)
        else:
            print(f"▶ 建立模型 {MODEL_NAME} ...")
            await builder.import_model_from_files(MODEL_NAME, model_dir)
        print("✅ 完成")
    finally:
        await client.close()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
