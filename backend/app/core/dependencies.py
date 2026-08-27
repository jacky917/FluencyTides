"""
FastAPI 依賴注入中心模組。

Central FastAPI dependency-injection module.

本模組集中管理所有 Infrastructure 與 Service 實例的建立與取得，
實現 Clean Architecture 的依賴反轉 (Dependency Inversion Principle)。

This module centralizes creation and retrieval of all infrastructure and
service instances, implementing the Dependency Inversion Principle of
Clean Architecture.

設計原則：
    - 所有 Infrastructure Client（AnkiClient、LLMClient、MinioClient）
      在 FastAPI lifespan 事件中初始化為 Singleton，存入 app.state。
    - Service 層實例透過 Depends() 按請求建立，注入對應的 Infrastructure Client。
    - Controller 層（api/、bot/）永遠不直接觸碰 Infrastructure，
      僅透過此模組取得 Service 實例。

Design principles:
    - All infrastructure clients (AnkiClient, LLMClient, MinioClient) are
      initialized as singletons in the FastAPI lifespan and stored in
      app.state.
    - Service-layer instances are created per request via Depends(), with
      the corresponding infrastructure client injected.
    - Controllers (api/, bot/) never touch infrastructure directly; they
      obtain service instances only through this module.

Dependencies:
    - FastAPI: Request, Depends
    - Infrastructure: AnkiClient, LLMClient, MinioClient
    - Services: AnkiModelManager, CardService, StorageService, PromptManager
"""

import logging
from pathlib import Path

from fastapi import Depends, Request

from app.infrastructure.database.database import get_async_session

from typing import TYPE_CHECKING

from app.infrastructure.anki.client import AnkiClient
from app.infrastructure.llm.client import LLMClient

if TYPE_CHECKING:
    # 僅供型別標註：API 模式下不應在 import 期載入 claude-code provider
    # （連帶不需要 jsonschema 套件）。Type-only import; API mode must not
    # load the claude-code provider at import time.
    from app.infrastructure.llm.claude_code_client import ClaudeCodeLLMClient
from app.infrastructure.storage.minio_client import MinioClient
from app.services.anki_model_manager import AnkiModelManager
from app.services.card_service import CardService
from app.core.template_engine import TemplateEngine
from app.services.relation_service import RelationService
from app.services.storage_service import StorageService

from app.core.config import settings

logger = logging.getLogger(__name__)

# 從 settings 取得相對路徑，組合出絕對路徑 (以 backend 目錄為基準)
_ANKI_MODELS_DIR = Path(__file__).parent.parent.parent / settings.ANKI_MODELS_DIR

# 統一集中管理的模板目錄
_TEMPLATE_DIR = Path(__file__).parent.parent / "templates"

# 全域單例
_template_engine_instance: TemplateEngine | None = None


# ============================================================================
# Infrastructure Client 取得（Singleton，從 app.state 讀取）
# ============================================================================

# 將 get_async_session 重新匯出，供 Router 與 Service 層使用。
# 命名為 get_db_session 以符合專案的 get_xxx 命名慣例。
get_db_session = get_async_session


def get_anki_client(request: Request) -> AnkiClient:
    """從 app.state 取得 AnkiClient Singleton 實例。

    Get the AnkiClient singleton from app.state.

    AnkiClient 在 lifespan startup 時初始化並存入 app.state，
    確保所有請求共用同一個 httpx.AsyncClient 連線池。

    Initialized at lifespan startup so every request shares the same
    httpx.AsyncClient connection pool.

    Args:
        request: FastAPI Request 物件，用於存取 app.state。
            FastAPI Request object used to access app.state.

    Returns:
        AnkiClient Singleton 實例。The AnkiClient singleton instance.
    """
    return request.app.state.anki_client


def get_llm_client(request: Request) -> "LLMClient | ClaudeCodeLLMClient":
    """從 app.state 取得 LLM 客戶端 Singleton 實例。

    Get the LLM client singleton from app.state.

    客戶端在 lifespan startup 時由 ``create_llm_client()`` 依
    ``LLM_PROVIDER`` 建立並存入 app.state：預設為共用同一個 AsyncOpenAI
    連線的 ``LLMClient``，``claude-code`` 模式下則為驅動本機 CLI 的
    ``ClaudeCodeLLMClient``。兩者介面相同。

    Created at lifespan startup by ``create_llm_client()`` according to
    ``LLM_PROVIDER``; both clients share the same interface.

    Args:
        request: FastAPI Request 物件，用於存取 app.state。
            FastAPI Request object used to access app.state.

    Returns:
        LLM 客戶端 Singleton 實例。The LLM client singleton instance.
    """
    return request.app.state.llm_client


def get_minio_client(request: Request) -> MinioClient:
    """從 app.state 取得 MinioClient Singleton 實例。

    Get the MinioClient singleton from app.state.

    MinioClient 在 lifespan startup 時初始化並存入 app.state，
    確保所有請求共用同一個 MinIO SDK 客戶端。

    Initialized at lifespan startup so every request shares the same
    MinIO SDK client.

    Args:
        request: FastAPI Request 物件，用於存取 app.state。
            FastAPI Request object used to access app.state.

    Returns:
        MinioClient Singleton 實例。The MinioClient singleton instance.
    """
    return request.app.state.minio_client


# ============================================================================
# Service 層工廠函數
# ============================================================================


def get_template_engine() -> TemplateEngine:
    """取得 TemplateEngine 全域單例。

    Get the global TemplateEngine singleton.

    Returns:
        TemplateEngine 實例。The TemplateEngine instance.
    """
    global _template_engine_instance
    if _template_engine_instance is None:
        _template_engine_instance = TemplateEngine(template_dir=_TEMPLATE_DIR)
    return _template_engine_instance


def get_model_manager(
    anki_client: AnkiClient = Depends(get_anki_client),
) -> AnkiModelManager:
    """建立 AnkiModelManager 實例。

    Create an AnkiModelManager instance.

    注入 AnkiClient Singleton，並指定 Anki 模型定義檔目錄。

    Injects the AnkiClient singleton and points at the Anki model
    definition directory.

    Args:
        anki_client: 注入的 AnkiClient Singleton。Injected AnkiClient
            singleton.

    Returns:
        AnkiModelManager 實例。An AnkiModelManager instance.
    """
    return AnkiModelManager(
        anki_client=anki_client,
        model_dir=_ANKI_MODELS_DIR,
    )


def get_relation_service(
    db_session=Depends(get_db_session),
) -> RelationService:
    """建立 RelationService 實例。

    Create a RelationService instance.

    注入 AsyncSession，提供卡片關聯資料庫的操作功能。

    Injects an AsyncSession to provide card-relation database operations.

    Args:
        db_session: 注入的 AsyncSession。Injected AsyncSession.

    Returns:
        RelationService 實例。A RelationService instance.
    """
    return RelationService(db_session=db_session)


def get_card_service(
    anki_client: AnkiClient = Depends(get_anki_client),
    model_manager: AnkiModelManager = Depends(get_model_manager),
) -> CardService:
    """建立 CardService 實例。

    Create a CardService instance.

    Phase 9 重構後，CardService 退化為純粹的 Anki CRUD Repository，
    僅需要 AnkiClient 與 AnkiModelManager 兩個依賴。
    LLM、Prompt、Relation 等業務依賴已移至 Task Handlers。

    After the Phase 9 refactor, CardService is a pure Anki CRUD repository
    needing only AnkiClient and AnkiModelManager; LLM, prompt, and relation
    dependencies moved to the task handlers.

    Args:
        anki_client: 注入的 AnkiClient Singleton。Injected AnkiClient
            singleton.
        model_manager: 注入的 AnkiModelManager 實例。Injected
            AnkiModelManager instance.

    Returns:
        CardService 實例。A CardService instance.
    """
    return CardService(
        anki_client=anki_client,
        model_manager=model_manager,
    )


def get_storage_service(
    minio_client: MinioClient = Depends(get_minio_client),
) -> StorageService:
    """建立 StorageService 實例。

    Create a StorageService instance.

    注入 MinioClient Singleton，提供業務層的檔案存取操作。

    Injects the MinioClient singleton to provide business-level file access.

    Args:
        minio_client: 注入的 MinioClient Singleton。Injected MinioClient
            singleton.

    Returns:
        StorageService 實例。A StorageService instance.
    """
    return StorageService(minio_client=minio_client)
