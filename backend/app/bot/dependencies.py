"""
Telegram Bot 依賴注入與中介層模組。

此模組實作了 aiogram 的 Middleware，負責：
1. 白名單攔截 (Whitelist Check)：防止未授權的 User ID 使用 Bot。
2. 服務注入 (Service Injection)：從 FastAPI app 取出基礎設施 Singletons，
   實例化 CardService 並注入到 aiogram Handler 的 data 字典中。

設計決策：
- 嚴格遵守 Clean Architecture，Bot 的 Handler (Controller)
  絕不直接操作資料庫或 LLM，而是使用與 Web API 相同的 CardService。
- AudioEvaluator 與 UserStateManager 一併注入，
  確保 Voice Handler 可直接取用。
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User

from app.bot.state import user_state_manager
from app.core.config import settings
from app.core.dependencies import _ANKI_MODELS_DIR, _PROMPTS_DIR
from app.infrastructure.database.database import async_session_factory
from app.services.anki_model_manager import AnkiModelManager
from app.services.card_service import CardService
from app.services.prompt_manager import PromptManager
from app.services.relation_service import RelationService

logger = logging.getLogger(__name__)


class WhitelistMiddleware(BaseMiddleware):
    """白名單存取控制中介層。

    攔截不在 TG_ALLOWED_USER_IDS 列表中的使用者，
    若設定檔留空，則阻擋所有人（安全預設）。
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
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
    """服務注入中介層。

    在每個 Update 處理前，從 app (FastAPI 實例) 取出 Infrastructure，
    建立 CardService 等業務邏輯物件，並注入到 handler data 中。

    注入清單：
    - card_service: CardService
    - model_manager: AnkiModelManager
    - relation_service: RelationService
    - anki_client: AnkiClient
    - user_state_manager: UserStateManager (Singleton)
    - audio_evaluator: BaseAudioEvaluator (Singleton，若已初始化)
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        app = data.get("app")
        if not app:
            logger.error("FastAPI app 實例未注入到 aiogram dispatcher 中！")
            raise RuntimeError("FastAPI app is missing in dispatcher data.")

        # 從 FastAPI app.state 取得 Singletons
        anki_client = app.state.anki_client
        llm_client = app.state.llm_client

        if not anki_client or not llm_client:
            logger.error("Infrastructure clients 未在 app.state 中初始化！")
            raise RuntimeError("Infrastructure clients are missing.")

        # 實例化 Services
        model_manager = AnkiModelManager(
            anki_client=anki_client,
            model_dir=_ANKI_MODELS_DIR,
        )
        prompt_manager = PromptManager(template_dir=_PROMPTS_DIR)

        async with async_session_factory() as session:
            relation_service = RelationService(session)

            card_service = CardService(
                anki_client=anki_client,
                llm_client=llm_client,
                model_manager=model_manager,
                prompt_manager=prompt_manager,
                relation_service=relation_service,
            )

            # 注入到 data 中，Handler 即可透過 kwargs 取得
            data["card_service"] = card_service
            data["model_manager"] = model_manager
            data["relation_service"] = relation_service
            data["anki_client"] = anki_client

            # 注入 UserStateManager Singleton
            data["user_state_manager"] = user_state_manager

            # 注入 Audio Evaluator (若已在 app.state 中初始化)
            audio_evaluator = getattr(app.state, "audio_evaluator", None)
            if audio_evaluator:
                data["audio_evaluator"] = audio_evaluator

            return await handler(event, data)
