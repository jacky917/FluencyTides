"""
Task Handlers 模組。
提供任務驅動架構 (Task-Driven Architecture) 的處理器實作。

Task handlers package.

Provides the handler implementations of the task-driven architecture.
"""

# 必須在此匯入所有的 handler 模組，這樣 @register_handler 才會在應用啟動時執行
from app.services.task_handlers import (
    vocabulary_mining_handler,
    expression_handler,
    speaking_coach_handler,
    speaking_trilingual_handler,
)
