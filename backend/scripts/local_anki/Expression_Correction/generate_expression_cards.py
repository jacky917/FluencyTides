"""
外語糾錯卡片 (Expression Correction) 生成範例腳本。

Sample script that generates Expression Correction cards via the LLM
expression_correction task handler.

用法：
    cd backend
    python -m scripts.Expression_Correction.generate_expression_cards
"""

import asyncio
import logging
import sys
from pathlib import Path
import os
from pathlib import Path

# 確保 sys.path 包含 backend 根目錄並載入 .env
_backend_dir = Path(__file__).resolve().parents[4]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))
import scripts.common.env  # noqa

from app.infrastructure.anki.client import AnkiClient
from app.services.anki_model_manager import AnkiModelManager
from app.services.card_service import CardService
from app.services.task_handlers.registry import handler_registry
# 匯入以觸發 @register_handler 註冊
from app.services.task_handlers import expression_handler  # noqa

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("GenerateExpressionCards")


async def generate() -> None:
    """腳本主流程：初始化服務、匯入模型並執行卡片生成。

    Main flow: initialize services, ensure models are imported, then run
    the expression-correction card generation and trigger an Anki sync.
    """
    logger.info("=" * 60)
    logger.info("外語糾錯卡片生成工具 - 範例腳本")
    logger.info("=" * 60)

    # 1. 初始化相依服務
    try:
        anki_client = AnkiClient()
        # 本檔位於 backend/scripts/local_anki/Expression_Correction/，
        # parents[3] = backend/（原三層 parent 只到 scripts/，會錯指
        # backend/scripts/app/anki_models 並自動建立空資料夾）
        model_dir = Path(__file__).resolve().parents[3] / "app" / "anki_models"
        model_manager = AnkiModelManager(anki_client=anki_client, model_dir=model_dir)
        card_service = CardService(anki_client=anki_client, model_manager=model_manager)
    except Exception as e:
        logger.error("初始化相依服務失敗: %s", e)
        return

    # 2. 取得 Handler
    try:
        handler = handler_registry.get_handler("expression_correction")
    except Exception as e:
        logger.error("無法取得 Handler: %s", e)
        return

    # 3. 準備模擬輸入參數 (相當於從 Telegram 傳入的參數)
    parameters = {
        "native_language": "中文",
        "target_language": "日文",
        "original_text": """昨日また「劇本殺」というボドゲに遊びに行きました。

西暦「1492」何があったのかご存知ですか？そう、その時一番大きい事件は「哥倫布發現新大陸」です！今回のストリートはそれを元にして作られたストリートです！13時間のゲームですが、物語が面白くて、時間はあっという間に過ぎました。""",
        "user_grammar_correction": """昨日また「劇本殺（マーダーミステリー）」というボドゲをしに行きました！

西暦1492年に何があったかご存知ですか？そう、その年一番大きな出来事は「コロンブスの新大陸発見」です！今回のストーリーはそれを元にして作られたものです！

13時間もかかるゲームですが、物語が面白くて、時間はあっという間に過ぎました""",
        "user_reorganization": "",
        "source_tag": "HelloTalk",
        "context": "分享玩劇本殺的經驗",
        "tg_bot": "Jacky917_bot"
    }

    logger.info("準備送出的參數:")
    for k, v in parameters.items():
        logger.info("  - %s: %s", k, v)
    logger.info("-" * 60)

    # 4. 前置檢查與模型匯入
    try:
        existing_models = await anki_client.get_model_names()
        for required_model in ["Expression_Master_Dark", "Expression_Micro_Dark"]:
            if required_model not in existing_models:
                logger.warning("模型 '%s' 不存在，即將自動匯入...", required_model)
                await model_manager.import_model_from_files(required_model)
    except Exception as e:
        logger.error("前置模型匯入失敗: %s", e)
        return

    # 5. 執行生成流程
    try:
        logger.info("⏳ 正在呼叫 LLM 進行分析與拆解... (可能需要幾秒鐘)")
        created_ids = await handler.execute_create(
            card_service=card_service,
            relation_service=None,  # 暫不使用 relation graph
            deck_name="日本語::外語糾錯::母卡片",
            model_name="Expression_Master_Dark",
            parameters=parameters
        )
        logger.info("✅ 建立成功！共產生 %d 張卡片。", len(created_ids))
        logger.info("👉 Note IDs: %s", created_ids)

        # 6. 強制觸發 Anki 同步
        await anki_client.sync(force=True)
        logger.info("🔄 已觸發 Anki 同步。請至 Anki 桌面端查看。")

    except Exception as e:
        logger.exception("❌ 生成失敗: %s", e)
    finally:
        await anki_client.close()


if __name__ == "__main__":
    asyncio.run(generate())
