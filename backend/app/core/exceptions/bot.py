"""
Telegram Bot 模組的異常定義。

Exception definitions for the Telegram Bot module.
"""

from .base import FluencyTidesError


class BotBaseError(FluencyTidesError):
    """Telegram Bot 模組的基礎錯誤。

    Base error for the Telegram Bot module.
    """
    error_code = "BOT_ERROR"
    status_code = 400


class BotStateError(BotBaseError):
    """Bot 狀態機異常。

    Bot finite-state-machine error.
    """
    error_code = "BOT_STATE_ERROR"


class BotInputError(BotBaseError):
    """使用者輸入或指令錯誤。

    Invalid user input or command error.
    """
    error_code = "BOT_INPUT_ERROR"


class BotActionError(BotBaseError):
    """Bot 動作執行失敗。

    Bot action execution failure.
    """
    error_code = "BOT_ACTION_ERROR"
