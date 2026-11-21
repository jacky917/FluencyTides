"""Global error handler for the Telegram bot.

Telegram Bot 的全域錯誤處理。

Each handler catches only expected failures (for example Anki being unreachable) and lets programming errors
propagate. Without this layer aiogram only writes a log entry: the user gets nothing on Telegram and the button keeps
spinning. This layer logs the full traceback, stops the button's spinner, and sends a separate notice telling the user
the action did not complete.

Limitations: this layer cannot reach the "⏳ processing" message a handler already sent, so that message stays; nor
can it reach the FSM state (the state middleware sits inside the error handling), so it does not clear the flow.
Instead the notice asks the user to start over, rather than encouraging a plain retry.

各 handler 只攔截預期內的錯誤（例如連不上 Anki），程式錯誤照常往上拋；沒有這一層的話，
aiogram 只會記一筆 log，使用者在 TG 上什麼都收不到、按鈕一直轉圈。這裡記下完整的 traceback、
結束按鈕的轉圈，並另送一則通知告訴使用者這次操作沒有完成。

限制：這一層拿不到 handler 先送出的「⏳ 處理中」訊息，那則訊息會留著；也拿不到 FSM 狀態（狀態
middleware 在錯誤處理的內層），所以不清除流程，改在提示裡請使用者重新開始，不鼓勵原樣重試。
"""

import logging
from contextlib import suppress

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import ErrorEvent

from app.bot.utils.texts import error_detail

logger = logging.getLogger(__name__)

UNEXPECTED_ERROR = "❌ 發生未預期的錯誤，這次操作沒有完成"
RETRY_HINT = "如果在建立卡片途中，請用 /newcard 重新開始；其他操作可以重新執行一次。"


async def on_unhandled_error(event: ErrorEvent, bot: Bot) -> bool:
    """Log an exception no handler caught and notify the user who triggered it.

    記錄沒被 handler 攔下的例外，並通知觸發它的使用者。

    The notice is sent only for message and callback-query updates; a failure to send it is logged as a warning.

    只有訊息與回調查詢的 update 會送出通知；通知送不出去時記一筆 warning。

    Args:
        event: aiogram's error event, carrying the original update and the exception
            aiogram 的錯誤事件（含原本的 update 與例外）
        bot: the bot used to send the notice
            送出通知用的 Bot

    Returns:
        always ``True``, marking the error as handled so aiogram does not propagate it further
        一律為 ``True``：已處理，aiogram 不再往上拋
    """
    update = event.update
    error = event.exception

    chat_id: int | None = None
    payload: str | None = None
    if update.callback_query:
        query = update.callback_query
        chat_id = query.message.chat.id if query.message else query.from_user.id
        payload = query.data
    elif update.message:
        chat_id = update.message.chat.id
        payload = (update.message.text or update.message.caption or "")[:50]
    # 記下是哪個聊天室、哪個按鈕或指令，事後才查得到是哪個流程出錯
    logger.error(
        "Telegram Bot 未處理的例外（update %s，%s，chat %s，data=%r）",
        update.update_id, update.event_type, chat_id, payload, exc_info=error,
    )

    if update.callback_query:
        # 先結束按鈕的轉圈；handler 已回應過的 callback 再回應會出錯，不影響後面的通知
        with suppress(TelegramAPIError):
            await update.callback_query.answer()

    if chat_id is not None:
        # 附上例外類別：程式錯誤的訊息常只有一個鍵名（例如 KeyError 的 'fields'），單看看不出是什麼錯
        detail = f"{type(error).__name__}：{error_detail(error)}"
        try:
            await bot.send_message(chat_id, f"{UNEXPECTED_ERROR}（{detail}）\n{RETRY_HINT}")
        except TelegramAPIError:
            logger.warning("未處理例外的通知送不出去（chat %s）", chat_id, exc_info=True)
    return True
