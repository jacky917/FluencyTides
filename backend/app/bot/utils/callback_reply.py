"""
按鈕（callback query）相關的共用小工具。

Shared helpers for inline button callbacks.

Telegram 變慢、使用者連點時，回應按鈕或改按鈕常會失敗，但那些失敗不代表操作本身該中斷：

- 回應按鈕（結束轉圈）超過時限會得到 ``query is too old``：只記 warning，後面的邏輯照常執行。
- 改按鈕時內容沒變會得到 ``message is not modified``：代表這則訊息已經被處理過（連點的第二下），
  呼叫端應直接結束，不要冒成未預期錯誤。

When Telegram is slow and the user taps repeatedly, answering or editing buttons often fails;
these helpers keep such failures from aborting the handler or surfacing as unexpected errors.
"""

import logging

from aiogram import types
from aiogram.exceptions import TelegramBadRequest

logger = logging.getLogger(__name__)

#: 回應按鈕太晚時 Telegram 回的錯誤（小寫比對）。Errors for a callback answered too late.
_EXPIRED_QUERY = ("query is too old", "query id is invalid")


async def answer_callback(callback: types.CallbackQuery, text: str | None = None, *, show_alert: bool = False) -> None:
    """回應按鈕（結束轉圈；``text`` 是順便顯示的提示，``show_alert`` 時跳成對話框）。

    Answer the callback to stop the spinner; an expired query is logged, not raised.

    只容忍「太晚回應」（``query is too old``、``query ID is invalid``）：記 warning，不中斷呼叫端。
    其他錯誤（例如提示文字太長）是程式問題，照常往外拋；網路逾時（``TelegramNetworkError``）也照常往外拋。
    """
    try:
        if text is None:
            await callback.answer()
        elif show_alert:
            await callback.answer(text, show_alert=True)
        else:
            await callback.answer(text)
    except TelegramBadRequest as e:
        if not any(marker in e.message.lower() for marker in _EXPIRED_QUERY):
            raise
        logger.warning("回應按鈕太晚，照常處理（data=%r）: %s", callback.data, e.message)


def is_not_modified(error: TelegramBadRequest) -> bool:
    """是否為「內容沒變」（``message is not modified``）。Whether the edit changed nothing."""
    return "message is not modified" in error.message.lower()
