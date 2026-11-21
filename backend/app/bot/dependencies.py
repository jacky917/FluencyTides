"""Telegram Bot dependency injection and middleware module.

Telegram Bot 依賴注入與中介層模組。

This module implements aiogram middlewares responsible for:
1. Whitelist check: blocks unauthorized user IDs from using the bot.
2. Service injection: pulls infrastructure singletons from the FastAPI app,
   instantiates CardService, HandlerRegistry, etc., and injects them into
   the aiogram handler data dict.

此模組實作了 aiogram 的 Middleware，負責：
1. 白名單攔截 (Whitelist Check)：防止未授權的 User ID 使用 Bot。
2. 服務注入 (Service Injection)：從 FastAPI app 取出基礎設施 Singletons，
   實例化 CardService、HandlerRegistry 並注入到 aiogram Handler 的 data 字典中。

Design decisions (Phase 9 update):
- CardService has been reduced to a pure CRUD repository and no longer needs
  LLM or prompt dependencies.
- HandlerRegistry is injected so bot handlers can dispatch task logic via
  the strategy pattern.
- UserStateManager is injected as well so the voice handler can access it
  directly.

設計決策 (Phase 9 更新)：
- CardService 已退化為純 CRUD Repository，不再需要 LLM 或 Prompt 依賴。
- 新增 HandlerRegistry 注入，讓 Bot Handler 可以透過策略模式呼叫對應任務邏輯。
- UserStateManager 一併注入，確保 Voice Handler 可直接取用。
"""

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User

from app.bot.state import user_state_manager
from app.core.config import settings
from app.core.dependencies import _ANKI_MODELS_DIR
from app.infrastructure.database.corpus_database import corpus_async_session_factory
from app.services.anki_model_manager import AnkiModelManager
from app.services.card_service import CardService
from app.services.note_service import NoteService
from app.services.relation_service import RelationService
from app.services.task_handlers.registry import handler_registry

if TYPE_CHECKING:
    from typing import Any

logger = logging.getLogger(__name__)


class WhitelistMiddleware(BaseMiddleware):
    """A whitelist access-control middleware.

    白名單存取控制中介層。

    Blocks users not present in the TG_ALLOWED_USER_IDS list; if the setting
    is empty, everyone is blocked (secure default).

    攔截不在 TG_ALLOWED_USER_IDS 列表中的使用者，
    若設定檔留空，則阻擋所有人（安全預設）。
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, "Any"]], Awaitable["Any"]],
        event: TelegramObject,
        data: dict[str, "Any"],
    ) -> "Any":
        """Pass the event on only if its user is whitelisted.

        只有使用者在白名單內時才把事件往下傳。

        Events without a user (system events) are passed on. A user who is
        not whitelisted gets a refusal message when the event can answer.

        沒有 User 資訊的事件（系統事件）放行。不在白名單的使用者，事件能回覆時會收到拒絕訊息。

        Args:
            handler: the next handler in the chain
                下一個處理函式
            event: the incoming Telegram event
                傳入的 Telegram 事件
            data: the aiogram context data dict
                aiogram 上下文資料字典

        Returns:
            the downstream handler's result, or ``None`` to stop processing
            when unauthorized
            下游 handler 的結果；未授權時回傳 ``None`` 終止處理
        """
        # 從 event 中提取 User 物件 (可能來自 Message, CallbackQuery 等)
        user: User | None = data.get("event_from_user")

        if not user:
            # 如果沒有 User 資訊，放行（系統事件）
            return await handler(event, data)

        allowed_users = settings.tg_allowed_users
        if not allowed_users:
            logger.warning(
                "TG_ALLOWED_USER_IDS 未設定，封鎖使用者 %d 的存取", user.id
            )
            return  # 終止處理

        if user.id not in allowed_users:
            logger.warning("未授權的使用者 %d 嘗試存取 Bot", user.id)
            # 可選：發送拒絕訊息
            if hasattr(event, "answer"):
                await event.answer("❌ 您沒有權限使用此 Bot。")
            elif hasattr(event, "message") and hasattr(
                event.message, "answer"
            ):
                await event.message.answer("❌ 您沒有權限使用此 Bot。")
            return  # 終止處理

        # 白名單檢查通過，繼續處理
        return await handler(event, data)


class ServiceInjectionMiddleware(BaseMiddleware):
    """A service injection middleware.

    服務注入中介層。

    Before each update is handled, pulls infrastructure from the FastAPI app,
    builds business objects such as CardService and HandlerRegistry, and
    injects them into the handler data dict.

    在每個 Update 處理前，從 app (FastAPI 實例) 取出 Infrastructure，
    建立 CardService、HandlerRegistry 等業務邏輯物件，並注入到 handler data 中。

    Phase 9 injected objects:
    - card_service: CardService (pure CRUD repository)
    - handler_registry: HandlerRegistry (task handler registry)
    - model_manager: AnkiModelManager
    - relation_service: RelationService
    - note_service: NoteService (handlers plan D1: Telegram card creation and
      actions go through the service layer)
    - anki_client: AnkiClient
    - user_state_manager: UserStateManager (singleton)

    Phase 9 注入清單：
    - card_service: CardService (純 CRUD Repository)
    - handler_registry: HandlerRegistry (任務處理器註冊表)
    - model_manager: AnkiModelManager
    - relation_service: RelationService
    - note_service: NoteService（handlers 計畫書 D1：TG 的建卡與動作經過服務層）
    - anki_client: AnkiClient
    - user_state_manager: UserStateManager (Singleton)
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, "Any"]], Awaitable["Any"]],
        event: TelegramObject,
        data: dict[str, "Any"],
    ) -> "Any":
        """Inject the services into ``data`` and call the next handler.

        將服務注入 ``data`` 後呼叫下一個處理函式。

        The corpus database session used by ``relation_service`` stays open
        only while the downstream handler runs.

        ``relation_service`` 用的語料庫 session 只在下游 handler 執行期間開著。

        Args:
            handler: the next handler in the chain
                下一個處理函式
            event: the incoming Telegram event
                傳入的 Telegram 事件
            data: the aiogram context data dict
                aiogram 上下文資料字典

        Returns:
            the downstream handler's result
            下游 handler 的結果

        Raises:
            RuntimeError: if the FastAPI app or AnkiClient is missing, or the
                corpus (MySQL) database is not configured
                若 FastAPI app 或 AnkiClient 未初始化，或語料庫 (MySQL) 連線尚未設定
        """
        app = data.get("app")
        if not app:
            logger.error("FastAPI app 實例未注入到 aiogram dispatcher 中！")
            raise RuntimeError("FastAPI app is missing in dispatcher data.")

        # 從 FastAPI app.state 取得 Singletons
        anki_client = app.state.anki_client

        if not anki_client:
            logger.error("AnkiClient 未在 app.state 中初始化！")
            raise RuntimeError("AnkiClient is missing.")

        # 實例化 Services
        model_manager = AnkiModelManager(
            anki_client=anki_client,
            model_dir=_ANKI_MODELS_DIR,
        )

        # 卡片關聯存在語料庫（MySQL）的 rel_links（corpus_db_rebuild D9 的 C）
        if not corpus_async_session_factory:
            logger.error("語料庫 (MySQL) 連線尚未設定，無法建立 RelationService！")
            raise RuntimeError("Corpus database is not configured.")

        async with corpus_async_session_factory() as session:
            relation_service = RelationService(session)

            # Phase 9: CardService 已退化，只需 anki_client + model_manager
            card_service = CardService(
                anki_client=anki_client,
                model_manager=model_manager,
            )


            # 注入到 data 中，Handler 即可透過 kwargs 取得
            data["card_service"] = card_service
            data["handler_registry"] = handler_registry
            data["model_manager"] = model_manager
            data["relation_service"] = relation_service
            data["note_service"] = NoteService(card_service, relation_service)
            data["anki_client"] = anki_client

            # 注入 UserStateManager Singleton
            data["user_state_manager"] = user_state_manager

            return await handler(event, data)
