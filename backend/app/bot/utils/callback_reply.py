"""Shared helpers for inline button callbacks (callback queries).

按鈕（callback query）相關的共用小工具。

When Telegram is slow and the user taps repeatedly, answering or editing buttons often fails, but those
failures do not mean the operation itself should be aborted:

- Answering a button (stopping the spinner) past the time limit yields ``query is too old``: only log a
  warning and let the rest of the logic run as usual.
- Editing a button whose content has not changed yields ``message is not modified``: this message has
  already been handled (the second tap of a double tap), so the caller should simply return instead of
  surfacing it as an unexpected error.

Telegram 變慢、使用者連點時，回應按鈕或改按鈕常會失敗，但那些失敗不代表操作本身該中斷：

- 回應按鈕（結束轉圈）超過時限會得到 ``query is too old``：只記 warning，後面的邏輯照常執行。
- 改按鈕時內容沒變會得到 ``message is not modified``：代表這則訊息已經被處理過（連點的第二下），
  呼叫端應直接結束，不要冒成未預期錯誤。
"""

import logging

from aiogram import types
from aiogram.exceptions import TelegramBadRequest

logger = logging.getLogger(__name__)

#: Errors Telegram returns when a callback is answered too late (matched in lowercase).
#: 回應按鈕太晚時 Telegram 回的錯誤（小寫比對）。
_EXPIRED_QUERY = ("query is too old", "query id is invalid")


async def answer_callback(callback: types.CallbackQuery, text: str | None = None, *, show_alert: bool = False) -> None:
    """Answer the button callback to stop the spinner; an expired query is logged, not raised.

    回應按鈕（結束轉圈）；過期的 query 只記 log，不拋出。

    Only "answered too late" (``query is too old``, ``query ID is invalid``) is tolerated: it logs a
    warning and does not interrupt the caller. Other errors (for example a hint text that is too long)
    are programming errors and propagate as usual; network timeouts (``TelegramNetworkError``) also
    propagate.

    只容忍「太晚回應」（``query is too old``、``query ID is invalid``）：記 warning，不中斷呼叫端。
    其他錯誤（例如提示文字太長）是程式問題，照常往外拋；網路逾時（``TelegramNetworkError``）也照常往外拋。

    Args:
        callback: the button callback to answer
            要回應的按鈕 callback
        text: a hint shown along with the answer; ``None`` shows nothing
            順便顯示的提示；``None`` 表示不顯示
        show_alert: show ``text`` as a dialog instead of a toast
            以對話框而非浮動提示顯示 ``text``

    Raises:
        TelegramBadRequest: if Telegram rejects the answer for any reason other than an expired query
            若 Telegram 因過期以外的原因拒絕回應
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
    """Return whether the error means the edit changed nothing (``message is not modified``).

    回傳是否為「內容沒變」（``message is not modified``）。

    Args:
        error: the error raised by an edit call
            編輯呼叫拋出的錯誤

    Returns:
        ``True`` if the message content was unchanged
        若訊息內容沒變則為 ``True``
    """
    return "message is not modified" in error.message.lower()
