"""
任務處理器註冊表 (Handler Registry) 模組。

負責在應用程式層級動態管理與分派 Task Handlers。
Registry 本身為輕量 Singleton，在 FastAPI lifespan 初始化。

Handler registry module.

Dynamically manages and dispatches task handlers at the application
level. The registry is a lightweight singleton initialized during the
FastAPI lifespan.
"""

import logging

from typing import Type

from app.core.exceptions import FluencyTidesError
from app.services.task_handlers.base import BaseHandler

logger = logging.getLogger(__name__)


class HandlerNotFoundError(FluencyTidesError):
    """指定的 Handler 不存在於 Registry 中。

    Raised when the requested handler is not present in the registry.
    """

    error_code = "HANDLER_NOT_FOUND"
    status_code = 404

    def __init__(self, handler_name: str) -> None:
        """初始化錯誤訊息。

        Initialize the error message.

        Args:
            handler_name: 找不到的處理器名稱。The missing handler name.
        """
        super().__init__(
            message=f"找不到指定的任務處理器: {handler_name}",
        )


class HandlerRegistry:
    """處理器註冊表。

    Handler registry.

    負責統一管理所有已註冊的 Handler 實例，
    並提供給 API 層與 Telegram Bot 層查詢與分派使用。

    Centrally manages all registered handler instances and serves lookup
    and dispatch for the API layer and the Telegram bot layer.

    Attributes:
        _handlers: handler_name -> BaseHandler 的映射字典。Mapping from
            handler_name to BaseHandler.
    """

    def __init__(self) -> None:
        """初始化空的 Registry。

        Initialize an empty registry.
        """
        self._handlers: dict[str, BaseHandler] = {}

    def register(self, handler: BaseHandler) -> None:
        """註冊一個 Handler 實例。

        Register a handler instance.

        Args:
            handler: 要註冊的 Handler 實例。Handler instance to register.

        Raises:
            ValueError: 若 handler_name 已存在。If handler_name already
                exists.
        """
        if handler.handler_name in self._handlers:
            raise ValueError(f"Handler '{handler.handler_name}' 已經被註冊過了。")
        self._handlers[handler.handler_name] = handler
        logger.info("已註冊 Handler: %s (支援: %s)", handler.handler_name, handler.supported_models)

    def get_handler(self, handler_name: str) -> BaseHandler:
        """根據名稱取得對應的 Handler 實例。

        Get the handler instance by name.

        Args:
            handler_name: 處理器名稱。Handler name.

        Returns:
            對應的 BaseHandler 實例。The matching BaseHandler instance.

        Raises:
            HandlerNotFoundError: 若名稱不存在。If the name is not
                registered.
        """
        if handler_name not in self._handlers:
            raise HandlerNotFoundError(handler_name)
        return self._handlers[handler_name]

    def list_all_handlers(self) -> list[dict[str, object]]:
        """列出所有註冊的 Handler 資訊，供前端使用。

        List info of all registered handlers for frontend use.

        Returns:
            包含每個 Handler 的 name, models, schema 的字典列表。List of
            dicts with each handler's name, models, and schema.
        """
        return [
            {
                "handler_name": name,
                "supported_models": handler.supported_models,
                "input_schema": handler.get_input_schema(),
            }
            for name, handler in self._handlers.items()
        ]

# 全域的 Singleton 實例
handler_registry = HandlerRegistry()

def register_handler(cls: Type[BaseHandler]) -> Type[BaseHandler]:
    """裝飾器：將 Handler 類別實例化並註冊至全域 Registry。

    Decorator: instantiate the handler class and register it globally.

    由於 Handler 已被重構為無狀態 (Stateless)，可以直接無參數實例化。
    Handlers are stateless, so they can be instantiated without args.

    Args:
        cls: 要註冊的 Handler 類別。Handler class to register.

    Returns:
        原始的 Handler 類別（不修改）。The original class, unmodified.
    """
    handler_instance = cls()
    handler_registry.register(handler_instance)
    return cls
