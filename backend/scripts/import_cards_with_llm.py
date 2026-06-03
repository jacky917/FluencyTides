"""
通用 LLM 卡片生成與匯入工具 (CLI)。

此腳本允許使用者透過標準化的 JSON 檔案，批次提供提示詞 (User Prompt) 與額外欄位，
自動呼叫 LLM 進行深度分析與卡片生成，並將最終結果匯入 Anki。

設計決策：
- 不重新造輪子，直接使用核心業務邏輯 `CardService.generate_card`。
- 完全相容 `CardGenerateRequest` 的 Pydantic Schema 進行資料驗證。
- 自動初始化資料庫與所有基礎設施依賴。

用法：
    cd backend
    python -m scripts.import_cards_with_llm --file scripts/samples/llm_import_sample.json
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.core.config import settings
from app.core.exceptions import FluencyTidesError
from app.infrastructure.anki.client import AnkiClient
from app.infrastructure.database.database import async_session_factory, create_db_and_tables
from app.infrastructure.llm.client import LLMClient
from app.schemas.card import CardGenerateRequest
from app.services.anki_model_manager import AnkiModelManager
from app.services.card_service import CardService
from app.services.prompt_manager import PromptManager
from app.services.relation_service import RelationService

# 設定日誌格式
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("LLMImporter")


async def main() -> None:
    parser = argparse.ArgumentParser(description="通用的 LLM 卡片生成與匯入工具")
    parser.add_argument(
        "--file",
        type=str,
        required=True,
        help="包含 CardGenerateRequest 資料的 JSON 檔案路徑",
    )
    parser.add_argument(
        "--db-url",
        type=str,
        default=None,
        help="自訂的資料庫連線字串 (如 sqlite+aiosqlite:///./my_db.db)。若未提供，預設使用環境變數的 DATABASE_URL。",
    )
    args = parser.parse_args()

    file_path = Path(args.file)
    if not file_path.is_file():
        logger.error(f"找不到檔案: {file_path}")
        return

    # 讀取 JSON 檔案
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            requests_data: list[dict[str, Any]] = json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"JSON 格式無效: {e}")
        return

    if not isinstance(requests_data, list):
        logger.error("JSON 檔案的最外層必須是一個陣列 (List)。")
        return

    # 初始化所有基礎設施
    logger.info("正在初始化基礎設施與資料庫連線...")
    
    # 根據是否傳入 --db-url 決定要使用的 Session Factory
    if args.db_url:
        logger.info(f"使用自訂的資料庫連線: {args.db_url}")
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from sqlmodel import SQLModel
        import app.infrastructure.database.conventions  # noqa: F401
        import app.infrastructure.database.models  # noqa: F401
        
        custom_engine = create_async_engine(args.db_url)
        async with custom_engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        session_factory = async_sessionmaker(custom_engine, class_=AsyncSession, expire_on_commit=False)
    else:
        from app.core.config import settings
        logger.info(f"使用預設資料庫連線 (DATABASE_URL): {settings.DATABASE_URL}")
        from app.infrastructure.database.database import async_session_factory as session_factory, create_db_and_tables
        # 確保資料表存在
        await create_db_and_tables()

    anki_client = AnkiClient()
    
    try:
        llm_client = LLMClient()
    except Exception as e:
        logger.error(f"LLMClient 初始化失敗，請檢查環境變數 (LLM_API_KEY): {e}")
        await anki_client.close()
        return

    model_manager = AnkiModelManager(
        anki_client=anki_client,
        model_dir=Path(__file__).resolve().parent.parent / "app" / "anki_models",
    )
    prompt_manager = PromptManager(
        template_dir=Path(__file__).resolve().parent.parent / "app" / "services" / "prompts"
    )

    try:
        async with session_factory() as session:
            relation_service = RelationService(session)
            card_service = CardService(
                anki_client=anki_client,
                llm_client=llm_client,
                model_manager=model_manager,
                prompt_manager=prompt_manager,
                relation_service=relation_service,
            )

            success_count = 0
            fail_count = 0

            for index, req_dict in enumerate(requests_data):
                logger.info("-" * 40)
                logger.info(f"👉 正在處理第 {index + 1}/{len(requests_data)} 筆請求...")

                # 使用 Pydantic 進行嚴格驗證
                try:
                    request = CardGenerateRequest.model_validate(req_dict)
                except ValidationError as e:
                    logger.error(f"❌ 請求 {index + 1} 的 JSON 格式不符合 Schema 規範:\n{e}")
                    fail_count += 1
                    continue

                try:
                    # 呼叫核心 Service
                    response = await card_service.generate_card(request)
                    logger.info(f"✅ {response.message} (Note ID: {response.note_id})")
                    success_count += 1
                except FluencyTidesError as e:
                    logger.error(f"❌ 發生業務邏輯錯誤: [{e.error_code}] {e.message}")
                    fail_count += 1
                except Exception as e:
                    logger.error(f"❌ 發生未預期錯誤: {e}", exc_info=True)
                    fail_count += 1
                    
                # 每次呼叫後暫停，避免觸發 API 頻率限制 (特別是 Gemini Free Tier)
                if index < len(requests_data) - 1:
                    logger.info("⏳ 等待 3 秒以防觸發 LLM API 頻率限制...")
                    await asyncio.sleep(3)

            logger.info("=" * 40)
            logger.info(
                f"🎉 LLM 匯入作業結束！共處理 {len(requests_data)} 筆請求。"
                f"成功: {success_count}, 失敗: {fail_count}"
            )

    finally:
        await anki_client.close()


if __name__ == "__main__":
    settings.setup_logging()
    asyncio.run(main())
