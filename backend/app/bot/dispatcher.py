"""
Telegram Bot Dispatcher 配置模組。

Telegram Bot dispatcher configuration module.

本模組負責初始化 aiogram 的 Dispatcher 與 Bot 實例，
並註冊所有的 Router (Handlers) 與 Middlewares。

This module initializes the aiogram Dispatcher and Bot instances,
and registers all routers (handlers) and middlewares.
"""

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.bot.dependencies import ServiceInjectionMiddleware, WhitelistMiddleware
from app.core.config import settings

logger = logging.getLogger(__name__)


def setup_dispatcher() -> Dispatcher:
    """初始化並配置 aiogram Dispatcher。

    Initialize and configure the aiogram Dispatcher.

    註冊全域的 Middlewares 以及所有業務 Handlers。

    Registers global middlewares and all business handlers.

    Returns:
        配置好的 Dispatcher 實例。The configured Dispatcher instance.
    """
    dp = Dispatcher()

    # 1. 註冊 Middlewares (全域)
    # WhitelistMiddleware 必須在最外層，阻擋未授權者
    dp.update.outer_middleware(WhitelistMiddleware())
    # ServiceInjectionMiddleware 確保 Handler 擁有 CardService
    dp.update.middleware(ServiceInjectionMiddleware())

    # 2. 註冊 Routers (Handlers)
    # 這裡採用延遲匯入，避免模組循環依賴
    from app.bot.handlers import commands, messages, voice, callbacks, media
    from app.bot.handlers.fsm import expression_fsm, speaking_fsm, vocabulary_fsm
    from app.bot.handlers import callbacks_config, newcard_menu
    
    dp.include_router(commands.router)
    dp.include_router(newcard_menu.router)  # 統一 /newcard 入口與卡片類型選單
    dp.include_router(callbacks_config.router) # 動態設定選單回呼
    dp.include_router(callbacks.router) # 回呼處理
    dp.include_router(media.router)     # 圖片處理
    dp.include_router(voice.router)     # 語音處理必須在文字訊息之前註冊
    
    # 註冊所有 FSM 狀態機流程 (必須在 messages 之前)
    dp.include_router(expression_fsm.router)
    dp.include_router(speaking_fsm.router)
    dp.include_router(vocabulary_fsm.router)
    
    dp.include_router(messages.router)

    logger.info("Telegram Bot Dispatcher 初始化完成。")
    return dp


def create_bot() -> Bot | None:
    """建立 aiogram Bot 實例。

    Create the aiogram Bot instance.

    若環境變數中未設定 TG_BOT_TOKEN，將回傳 None，
    表示不啟用 Bot 服務。

    Returns None when TG_BOT_TOKEN is not set in the environment,
    meaning the bot service is disabled.

    Returns:
        Bot 實例或 None。The Bot instance, or None.
    """
    token = settings.TG_BOT_TOKEN
    if not token:
        logger.warning(
            "TG_BOT_TOKEN 未設定，Telegram Bot 服務將不會啟動。"
        )
        return None

    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    return bot
