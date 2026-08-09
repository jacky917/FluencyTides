"""Task Handlers 共用工具模組。

存放多個卡片類型 Handler 之間共用的純函數邏輯
（例如 Cloze 挖空定位與 LINE 風格對話組裝），
避免各 Handler 各自複製一份導致行為分岔。

Shared utilities for task handlers.

Holds pure-function logic shared by multiple card-type handlers (e.g.
cloze positioning and LINE-style dialog assembly), so behavior does not
diverge across per-handler copies.
"""

from app.services.task_handlers.shared.cloze_positioning import (
    assemble_dialog_turns,
    position_cloze,
)

__all__ = [
    "assemble_dialog_turns",
    "position_cloze",
]
