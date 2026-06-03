"""
FastAPI 依賴注入中心模組。

本模組集中管理所有 Infrastructure 與 Service 實例的建立與取得，
實現 Clean Architecture 的依賴反轉 (Dependency Inversion Principle)。

設計原則：
    - 所有 Infrastructure Client（AnkiClient、LLMClient、MinioClient）
      在 FastAPI lifespan 事件中初始化為 Singleton，存入 app.state。
    - Service 層實例透過 Depends() 按請求建立，注入對應的 Infrastructure Client。
    - Controller 層（api/、bot/）永遠不直接觸碰 Infrastructure，
      僅透過此模組取得 Service 實例。

Dependencies:
    - FastAPI: Request, Depends
    - Infrastructure: AnkiClient, LLMClient, MinioClient
    - Services: AnkiModelManager, CardService, StorageService, PromptManager
"""

import logging
from pathlib import Path

from fastapi import Depends, Request

from app.infrastructure.database.database import get_async_session

from app.infrastructure.anki.client import AnkiClient
from app.infrastructure.llm.client import LLMClient
from app.infrastructure.storage.minio_client import MinioClient
from app.services.anki_model_manager import AnkiModelManager
from app.services.card_service import CardService
from app.services.prompt_manager import PromptManager
from app.services.relation_service import RelationService
from app.services.storage_service import StorageService

logger = logging.getLogger(__name__)

# Anki 模型定義檔的基礎路徑（相對於 app/ 目錄）
_ANKI_MODELS_DIR = Path(__file__).parent.parent / "anki_models"

# Jinja2 Prompt 模板的基礎路徑（相對於 app/ 目錄）
_PROMPTS_DIR = Path(__file__).parent.parent / "services" / "prompts"


# ============================================================================
# Infrastructure Client 取得（Singleton，從 app.state 讀取）
# ============================================================================

# 將 get_async_session 重新匯出，供 Router 與 Service 層使用。
# 命名為 get_db_session 以符合專案的 get_xxx 命名慣例。
get_db_session = get_async_session


def get_anki_client(request: Request) -> AnkiClient:
    """從 app.state 取得 AnkiClient Singleton 實例。

    AnkiClient 在 lifespan startup 時初始化並存入 app.state，
    確保所有請求共用同一個 httpx.AsyncClient 連線池。

    Args:
        request: FastAPI Request 物件，用於存取 app.state。

    Returns:
        AnkiClient Singleton 實例。
    """
    return request.app.state.anki_client


def get_llm_client(request: Request) -> LLMClient:
    """從 app.state 取得 LLMClient Singleton 實例。

    LLMClient 在 lifespan startup 時初始化並存入 app.state，
    確保所有請求共用同一個 AsyncOpenAI 客戶端。

    Args:
        request: FastAPI Request 物件，用於存取 app.state。

    Returns:
        LLMClient Singleton 實例。
    """
    return request.app.state.llm_client


def get_minio_client(request: Request) -> MinioClient:
    """從 app.state 取得 MinioClient Singleton 實例。

    MinioClient 在 lifespan startup 時初始化並存入 app.state，
    確保所有請求共用同一個 MinIO SDK 客戶端。

    Args:
        request: FastAPI Request 物件，用於存取 app.state。

    Returns:
        MinioClient Singleton 實例。
    """
    return request.app.state.minio_client


# ============================================================================
# Service 層工廠函數
# ============================================================================


def get_prompt_manager() -> PromptManager:
    """建立 PromptManager 實例。

    PromptManager 內部使用 Jinja2 FileSystemLoader 載入模板，
    本身是輕量物件，每次請求建立不會有效能問題。

    Returns:
        PromptManager 實例。
    """
    return PromptManager(template_dir=_PROMPTS_DIR)


def get_model_manager(
    anki_client: AnkiClient = Depends(get_anki_client),
) -> AnkiModelManager:
    """建立 AnkiModelManager 實例。

    注入 AnkiClient Singleton，並指定 Anki 模型定義檔目錄。

    Args:
        anki_client: 注入的 AnkiClient Singleton。

    Returns:
        AnkiModelManager 實例。
    """
    return AnkiModelManager(
        anki_client=anki_client,
        model_dir=_ANKI_MODELS_DIR,
    )


def get_relation_service(
    db_session=Depends(get_db_session),
) -> RelationService:
    """建立 RelationService 實例。

    注入 AsyncSession，提供卡片關聯資料庫的操作功能。

    Args:
        db_session: 注入的 AsyncSession。

    Returns:
        RelationService 實例。
    """
    return RelationService(db_session=db_session)


def get_card_service(
    anki_client: AnkiClient = Depends(get_anki_client),
    llm_client: LLMClient = Depends(get_llm_client),
    model_manager: AnkiModelManager = Depends(get_model_manager),
    prompt_manager: PromptManager = Depends(get_prompt_manager),
    relation_service: RelationService = Depends(get_relation_service),
) -> CardService:
    """建立 CardService 實例。

    注入所有 Infrastructure 與中間層 Service 依賴，
    確保 CardService 可完整執行卡片生成流程（包含自動寫入關聯資料庫）。

    Args:
        anki_client: 注入的 AnkiClient Singleton。
        llm_client: 注入的 LLMClient Singleton。
        model_manager: 注入的 AnkiModelManager 實例。
        prompt_manager: 注入的 PromptManager 實例。
        relation_service: 注入的 RelationService 實例。

    Returns:
        CardService 實例。
    """
    return CardService(
        anki_client=anki_client,
        llm_client=llm_client,
        model_manager=model_manager,
        prompt_manager=prompt_manager,
        relation_service=relation_service,
    )


def get_storage_service(
    minio_client: MinioClient = Depends(get_minio_client),
) -> StorageService:
    """建立 StorageService 實例。

    注入 MinioClient Singleton，提供業務層的檔案存取操作。

    Args:
        minio_client: 注入的 MinioClient Singleton。

    Returns:
        StorageService 實例。
    """
    return StorageService(minio_client=minio_client)
