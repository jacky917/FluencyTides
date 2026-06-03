"""
Telegram Bot Dispatcher 配置模組。

本模組負責初始化 aiogram 的 Dispatcher 與 Bot 實例，
並註冊所有的 Router (Handlers) 與 Middlewares。
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

    註冊全域的 Middlewares 以及所有業務 Handlers。

    Returns:
        配置好的 Dispatcher 實例。
    """
    dp = Dispatcher()

    # 1. 註冊 Middlewares (全域)
    # WhitelistMiddleware 必須在最外層，阻擋未授權者
    dp.update.outer_middleware(WhitelistMiddleware())
    # ServiceInjectionMiddleware 確保 Handler 擁有 CardService
    dp.update.middleware(ServiceInjectionMiddleware())

    # 2. 註冊 Routers (Handlers)
    # 這裡採用延遲匯入，避免模組循環依賴
    from app.bot.handlers import commands, messages, voice

    dp.include_router(commands.router)
    dp.include_router(voice.router)  # 語音處理必須在文字訊息之前註冊
    dp.include_router(messages.router)

    logger.info("Telegram Bot Dispatcher 初始化完成。")
    return dp


def create_bot() -> Bot | None:
    """建立 aiogram Bot 實例。

    若環境變數中未設定 TG_BOT_TOKEN，將回傳 None，
    表示不啟用 Bot 服務。

    Returns:
        Bot 實例或 None。
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
