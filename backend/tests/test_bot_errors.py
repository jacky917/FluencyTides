"""Telegram Bot 全域錯誤處理的測試。

Tests for the bot's global error handler: an exception no handler caught is
logged and the user is told the action did not complete, for messages and
for button callbacks (whose spinner must stop).
"""

import asyncio
import logging
from datetime import datetime
from unittest import mock

from aiogram import Dispatcher, Router
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Chat, ErrorEvent, Message, Update, User

from app.bot.handlers import errors

USER = User(id=7, is_bot=False, first_name="u")
CHAT = Chat(id=42, type="private")


def _message(text: str = "/boom") -> Message:
    return Message(message_id=1, date=datetime(2026, 1, 1), chat=CHAT, from_user=USER, text=text)


def _bot():
    bot = mock.MagicMock()
    bot.send_message = mock.AsyncMock()
    return bot


def _sent_text(bot) -> str:
    [call] = bot.send_message.await_args_list
    chat_id, text = call.args
    assert chat_id == CHAT.id
    return text


def test_message_error_is_reported_to_the_user(caplog):
    """訊息觸發的例外：記下 traceback，並告訴使用者這次沒有完成（錯誤原文轉義）。"""
    bot = _bot()
    event = ErrorEvent(update=Update(update_id=1, message=_message()), exception=KeyError("<fields> & more"))
    with caplog.at_level(logging.ERROR, logger=errors.__name__):
        handled = asyncio.run(errors.on_unhandled_error(event, bot))

    assert handled is True
    text = _sent_text(bot)
    assert text.startswith(errors.UNEXPECTED_ERROR) and "&lt;fields&gt; &amp; more" in text
    assert errors.RETRY_HINT in text
    assert any(record.exc_info for record in caplog.records)


def test_callback_error_stops_the_spinner_and_notifies():
    """按鈕觸發的例外：先回應 callback（結束轉圈），再到該聊天室送通知；已回應過也不影響通知。"""
    bot = _bot()
    query = CallbackQuery(id="q", from_user=USER, chat_instance="c", message=_message("menu"), data="x")
    event = ErrorEvent(update=Update(update_id=2, callback_query=query), exception=RuntimeError("boom"))
    answer = mock.AsyncMock(side_effect=TelegramBadRequest(method=mock.MagicMock(), message="query is too old"))
    with mock.patch.object(CallbackQuery, "answer", answer):
        asyncio.run(errors.on_unhandled_error(event, bot))

    answer.assert_awaited_once()
    assert _sent_text(bot).startswith(errors.UNEXPECTED_ERROR)


def test_failed_notice_is_logged_not_raised(caplog):
    """通知本身送不出去：記 WARNING，不再拋出（避免錯誤處理再出錯）。"""
    bot = _bot()
    bot.send_message.side_effect = TelegramNetworkError(method=mock.MagicMock(), message="offline")
    event = ErrorEvent(update=Update(update_id=3, message=_message()), exception=RuntimeError("boom"))
    with caplog.at_level(logging.WARNING, logger=errors.__name__):
        assert asyncio.run(errors.on_unhandled_error(event, bot)) is True
    assert any("送不出去" in record.getMessage() for record in caplog.records)


def test_dispatcher_routes_unhandled_errors_to_the_handler():
    """實際經過 Dispatcher：handler 拋出的例外會走到全域錯誤處理，使用者收到通知。"""
    router = Router()

    @router.message(Command("boom"))
    async def boom(_message: Message) -> None:
        raise KeyError("fields")

    dp = Dispatcher()
    dp.include_router(router)
    dp.errors.register(errors.on_unhandled_error)
    bot = mock.MagicMock(id=1)
    bot.send_message = mock.AsyncMock()

    asyncio.run(dp.feed_update(bot, Update(update_id=4, message=_message("/boom"))))
    assert _sent_text(bot).startswith(errors.UNEXPECTED_ERROR)


def test_setup_dispatcher_registers_the_global_handler():
    """正式的 Dispatcher 有註冊全域錯誤處理。

    aiogram 的 router 只能掛一次，測試後解除掛載，避免影響之後的 setup。"""
    from app.bot.dispatcher import setup_dispatcher

    dp = setup_dispatcher()
    try:
        assert any(handler.callback is errors.on_unhandled_error for handler in dp.errors.handlers)
    finally:
        for sub_router in dp.sub_routers:
            sub_router._parent_router = None

