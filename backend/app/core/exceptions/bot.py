"""Exception definitions for the Telegram Bot module.

Telegram Bot 模組的異常定義。
"""

from .base import FluencyTidesError


class BotBaseError(FluencyTidesError):
    """The base error for the Telegram Bot module.

    Telegram Bot 模組的基礎錯誤。
    """
    error_code = "BOT_ERROR"
    status_code = 400


class BotStateError(BotBaseError):
    """A bot finite-state-machine error.

    Bot 狀態機異常。
    """
    error_code = "BOT_STATE_ERROR"


class BotInputError(BotBaseError):
    """An invalid user input or command error.

    使用者輸入或指令錯誤。
    """
    error_code = "BOT_INPUT_ERROR"


class BotActionError(BotBaseError):
    """A bot action execution failure.

    Bot 動作執行失敗。
    """
    error_code = "BOT_ACTION_ERROR"
