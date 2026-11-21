"""Central FastAPI dependency-injection module.

FastAPI 依賴注入中心模組。

This module centralizes creation and retrieval of all infrastructure and
service instances, implementing the Dependency Inversion Principle of
Clean Architecture.

本模組集中管理所有 Infrastructure 與 Service 實例的建立與取得，
實現 Clean Architecture 的依賴反轉 (Dependency Inversion Principle)。

Design principles:
    - All infrastructure clients (AnkiClient, LLMClient, MinioClient) are
      initialized as singletons in the FastAPI lifespan and stored in
      app.state. The LLM client is not provided here: each use case gets
      its own client through its route key.
    - Service-layer instances are created per request via Depends(), with
      the corresponding infrastructure client injected.
    - Controllers (api/, bot/) never touch infrastructure directly; they
      obtain service instances only through this module.

設計原則：
    - 所有 Infrastructure Client（AnkiClient、LLMClient、MinioClient）
      在 FastAPI lifespan 事件中初始化為 Singleton，存入 app.state。
      LLM 客戶端不由本模組提供：各用途以用途鍵取得自己的客戶端。
    - Service 層實例透過 Depends() 按請求建立，注入對應的 Infrastructure Client。
    - Controller 層（api/、bot/）永遠不直接觸碰 Infrastructure，
      僅透過此模組取得 Service 實例。

Dependencies:
    - FastAPI: Request, Depends
    - Infrastructure: AnkiClient, MinioClient, database sessions
    - Core: TemplateEngine
    - Services: AnkiModelManager, CardService, RelationService,
      StorageService, NoteService
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import Depends, Request

from app.infrastructure.database.corpus_database import get_corpus_async_session
from app.infrastructure.database.database import get_async_session

from app.infrastructure.anki.client import AnkiClient
from app.infrastructure.storage.minio_client import MinioClient
from app.services.anki_model_manager import AnkiModelManager
from app.services.card_service import CardService
from app.core.template_engine import TemplateEngine
from app.services.relation_service import RelationService
from app.services.storage_service import StorageService

from app.core.config import settings

if TYPE_CHECKING:
    from app.services.note_service import NoteService

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
    """Get the AnkiClient singleton from app.state.

    從 app.state 取得 AnkiClient Singleton 實例。

    Initialized at lifespan startup and stored in app.state, so every
    request shares the same httpx.AsyncClient connection pool.

    AnkiClient 在 lifespan startup 時初始化並存入 app.state，
    確保所有請求共用同一個 httpx.AsyncClient 連線池。

    Args:
        request: the FastAPI request, used to access app.state
            FastAPI Request 物件，用於存取 app.state

    Returns:
        the AnkiClient singleton instance
        AnkiClient Singleton 實例
    """
    return request.app.state.anki_client


# LLM 客戶端不經依賴注入：各用途以宣告的用途鍵（app.infrastructure.llm.routing.route）
# 每次取得自己的客戶端。app.state.llm_client 只給診斷端點用（全域設定，見 capability_route_service.
# refresh_global_client），不可拿來生成——那會略過各用途的設定。


def get_minio_client(request: Request) -> MinioClient:
    """Get the MinioClient singleton from app.state.

    從 app.state 取得 MinioClient Singleton 實例。

    Initialized at lifespan startup and stored in app.state, so every
    request shares the same MinIO SDK client.

    MinioClient 在 lifespan startup 時初始化並存入 app.state，
    確保所有請求共用同一個 MinIO SDK 客戶端。

    Args:
        request: the FastAPI request, used to access app.state
            FastAPI Request 物件，用於存取 app.state

    Returns:
        the MinioClient singleton instance
        MinioClient Singleton 實例
    """
    return request.app.state.minio_client


# ============================================================================
# Service 層工廠函數
# ============================================================================


def get_template_engine() -> TemplateEngine:
    """Get the global TemplateEngine singleton, creating it on first use.

    取得 TemplateEngine 全域單例，第一次使用時建立。

    Returns:
        the TemplateEngine instance
        TemplateEngine 實例
    """
    global _template_engine_instance
    if _template_engine_instance is None:
        _template_engine_instance = TemplateEngine(template_dir=_TEMPLATE_DIR)
    return _template_engine_instance


def get_model_manager(
    anki_client: AnkiClient = Depends(get_anki_client),
) -> AnkiModelManager:
    """Create an AnkiModelManager instance.

    建立 AnkiModelManager 實例。

    Injects the AnkiClient singleton and points at the Anki model
    definition directory.

    注入 AnkiClient Singleton，並指定 Anki 模型定義檔目錄。

    Args:
        anki_client: the injected AnkiClient singleton
            注入的 AnkiClient Singleton

    Returns:
        an AnkiModelManager instance
        AnkiModelManager 實例
    """
    return AnkiModelManager(
        anki_client=anki_client,
        model_dir=_ANKI_MODELS_DIR,
    )


def get_relation_service(
    db_session=Depends(get_corpus_async_session),
) -> RelationService:
    """Create a RelationService instance.

    建立 RelationService 實例。

    Injects the **corpus** (MySQL) AsyncSession: card relations live in
    ``rel_links`` / ``rel_types`` (option C of D9 in
    docs/wip/corpus_db_rebuild_FEAT_2026-10-31.md), no longer in the
    website's SQLite.

    注入**語料庫**（MySQL）的 AsyncSession：卡片關聯存在 ``rel_links`` / ``rel_types``
    （docs/wip/corpus_db_rebuild_FEAT_2026-10-31.md D9 的 C），不再用網站的 SQLite。

    Args:
        db_session: the injected corpus AsyncSession
            注入的語料庫 AsyncSession

    Returns:
        a RelationService instance
        RelationService 實例
    """
    return RelationService(db_session=db_session)


def get_card_service(
    anki_client: AnkiClient = Depends(get_anki_client),
    model_manager: AnkiModelManager = Depends(get_model_manager),
) -> CardService:
    """Create a CardService instance.

    建立 CardService 實例。

    After the Phase 9 refactor, CardService is a pure Anki CRUD repository
    needing only AnkiClient and AnkiModelManager; LLM, prompt, and relation
    dependencies moved to the task handlers.

    Phase 9 重構後，CardService 退化為純粹的 Anki CRUD Repository，
    僅需要 AnkiClient 與 AnkiModelManager 兩個依賴。
    LLM、Prompt、Relation 等業務依賴已移至 Task Handlers。

    Args:
        anki_client: the injected AnkiClient singleton
            注入的 AnkiClient Singleton
        model_manager: the injected AnkiModelManager instance
            注入的 AnkiModelManager 實例

    Returns:
        a CardService instance
        CardService 實例
    """
    return CardService(
        anki_client=anki_client,
        model_manager=model_manager,
    )


def get_storage_service(
    minio_client: MinioClient = Depends(get_minio_client),
) -> StorageService:
    """Create a StorageService instance.

    建立 StorageService 實例。

    Injects the MinioClient singleton to provide business-level file access.

    注入 MinioClient Singleton，提供業務層的檔案存取操作。

    Args:
        minio_client: the injected MinioClient singleton
            注入的 MinioClient Singleton

    Returns:
        a StorageService instance
        StorageService 實例
    """
    return StorageService(minio_client=minio_client)


def get_note_service(
    card_service: CardService = Depends(get_card_service),
    relation_service: RelationService = Depends(get_relation_service),
) -> "NoteService":
    """Create a NoteService instance (handlers plan D1).

    建立 NoteService 實例（handlers 計畫書 D1）。

    Imported inside the function: ``note_service`` loads every handler
    through the registry, and some handlers import this module's
    ``get_template_engine`` at module level; importing at the top of the
    file would create a circular import.

    在函式內 import：``note_service`` 經註冊表載入所有 handler，而有些 handler 在模組層 import 本模組的
    ``get_template_engine``；放在檔案開頭會形成循環 import。

    Args:
        card_service: the injected CardService
            注入的 CardService
        relation_service: the injected RelationService
            注入的 RelationService

    Returns:
        a NoteService instance
        NoteService 實例
    """
    from app.services.note_service import NoteService

    return NoteService(card_service=card_service, relation_service=relation_service)
