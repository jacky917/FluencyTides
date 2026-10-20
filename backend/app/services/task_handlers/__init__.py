"""
Task Handlers 模組。
提供任務驅動架構 (Task-Driven Architecture) 的處理器實作。

Task handlers package.

Provides the handler implementations of the task-driven architecture.
"""

# 所有正式 handler 都在這裡 import，讓 @register_handler 在 import 套件時就執行。
# 註冊順序由這裡決定：同一筆記類型有多個 handler 時取第一個註冊的（handlers 計畫書 D4）。
# Every handler is imported here so registration happens on package import;
# this order decides which handler wins for a shared note type.
from app.services.task_handlers import (  # noqa: F401
    vocabulary_mining_handler,
    expression_handler,
    speaking_coach_handler,
    speaking_trilingual_handler,
    jp_verb_pair_handler,
    jp_core_verb_handler,
)
