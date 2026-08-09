"""匯入 JP_VerbPair 系列筆記模型至本地 Anki。

Import the JP_VerbPair note models into the local Anki instance.

讀取 `app/anki_models/` 下的四件套資產（.json / _front.html / _back.html /
_style.css），透過 AnkiConnect 建立以下模型：

Reads the four-file asset set (.json / _front.html / _back.html /
_style.css) under `app/anki_models/` and creates the following models via
AnkiConnect:

- JP_VerbPair_Master_Dark（自他動詞對母卡 / transitivity-pair master card）
- JP_Context_Dark（通用文脈子卡，與 JP_CoreVerb 共用 /
  shared context child card, also used by JP_CoreVerb）
- JP_VerbPair_Cloze_Dark（自他動詞克漏字子卡 / transitivity cloze card）

補充（S063）：本檔案原本遺失，但 `migrate_master_cards.py` 與
`JP_CoreVerb/import_models.py` 的註解都假設它存在（前者直接 import
`import_all_models`），導致該遷移腳本必定 ModuleNotFoundError。此處依
`JP_CoreVerb/import_models.py` 的既有慣例補回。

Note (S063): this file was missing even though `migrate_master_cards.py`
imports `import_all_models` from it and `JP_CoreVerb/import_models.py`
documents it as the owner of `JP_Context_Dark`; its absence made the
migration script fail with ModuleNotFoundError. It is restored here
following the existing `JP_CoreVerb/import_models.py` convention.

使用方式 / Usage:
    python scripts/local_anki/JP_VerbPair/import_models.py
"""

import os
import sys
from pathlib import Path

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

# 統一 bootstrap：向上尋找第一個含 app/ 的目錄即為 backend 根，與檔案深度無關；
# 腳本搬移目錄層級時不需再調整硬編碼的上層層數，避免匯入路徑失準。
# Unified bootstrap: walk up the parent chain and take the first directory
# containing app/ as the backend root. Depth-independent, so relocating this
# script never breaks the import path.
_BACKEND_DIR = next(
    p for p in Path(__file__).resolve().parents if (p / "app").is_dir()
)
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
import scripts.common.env  # noqa

import asyncio
from app.infrastructure.anki.client import AnkiClient
from scripts.local_anki.schema_builder import AnkiSchemaBuilder


async def import_all_models(client: AnkiClient, backend_directory: str) -> None:
    """依序匯入所有 JP_VerbPair 相關模型。

    Sequentially import all JP_VerbPair-related models into Anki.

    Args:
        client: AnkiConnect 客戶端實例。AnkiConnect client instance.
        backend_directory: backend 根目錄的絕對路徑，用於定位模型資產。
            Absolute path of the backend root, used to locate model assets.
    """
    schema_builder = AnkiSchemaBuilder(client)
    model_dir = os.path.join(backend_directory, "app", "anki_models")

    models_to_import = [
        "JP_VerbPair_Master_Dark",
        "JP_Context_Dark",
        "JP_VerbPair_Cloze_Dark",
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


async def main() -> None:
    """腳本進入點：建立客戶端、執行匯入、確保連線關閉。

    Script entry point: create the client, run the import, and ensure the
    connection is closed.
    """
    client = AnkiClient()
    try:
        await import_all_models(client, str(_BACKEND_DIR))
    finally:
        await client.close()


if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
