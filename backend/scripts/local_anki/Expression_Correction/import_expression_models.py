"""
匯入 Expression_Master_Dark 與 Expression_Micro_Dark 模型至 Anki。

Import the Expression_Master_Dark and Expression_Micro_Dark note models
into Anki from local template files.

用法：
    cd backend
    python -m scripts.Expression_Correction.import_expression_models
"""

import asyncio
import logging
import sys
from pathlib import Path

from app.infrastructure.anki.client import AnkiClient
from app.services.anki_model_manager import AnkiModelManager

# 日誌設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("ImportExpressionModels")


async def main() -> None:
    """腳本主入口：匯入兩個模型並觸發 Anki 同步。

    Script entry point: import both note models and trigger an Anki sync.
    """
    logger.info("=" * 60)
    logger.info("開始匯入外語糾錯卡片模型至 Anki")
    logger.info("=" * 60)

    try:
        # 1. 初始化相依服務
        anki_client = AnkiClient()
        # 本檔位於 backend/scripts/local_anki/Expression_Correction/，
        # parents[3] = backend/（原三層 parent 只到 scripts/，會錯指
        # backend/scripts/app/anki_models 並自動建立空資料夾）
        model_dir = Path(__file__).resolve().parents[3] / "app" / "anki_models"
        model_manager = AnkiModelManager(anki_client=anki_client, model_dir=model_dir)

        # 2. 匯入母卡片模型
        logger.info("正在匯入: Expression_Master_Dark")
        await model_manager.import_model_from_files("Expression_Master_Dark")
        
        # 3. 匯入子卡片模型
        logger.info("正在匯入: Expression_Micro_Dark")
        await model_manager.import_model_from_files("Expression_Micro_Dark")

        # 4. 強制觸發 Anki 同步
        await anki_client.sync(force=True)
        logger.info("🔄 模型匯入完成，並已觸發 Anki 同步！")

    except Exception as e:
        logger.exception("❌ 匯入失敗: %s", e)
    finally:
        await anki_client.close()


if __name__ == "__main__":
    asyncio.run(main())
