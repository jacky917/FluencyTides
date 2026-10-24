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
from app.services.task_handlers.model_fields import model_field_names
from app.services.task_handlers.relation_graph import RelationGraphMixin

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
        self._by_note_type: dict[str, list[BaseHandler]] = {}

    def register(self, handler: BaseHandler) -> None:
        """檢查宣告後註冊一個 Handler 實例；檢查不過時不會留下任何註冊。

        Validate and register a handler instance; nothing is registered on
        failure.

        Args:
            handler: 要註冊的 Handler 實例。Handler instance to register.

        Raises:
            ValueError: 若 handler_name 已存在。If handler_name already
                exists.
            RuntimeError: 能力宣告沒有實作支撐，或與共用筆記類型的 handler 不一致（D2、D4）。
                When the declarations are not backed by the implementation
                or conflict with a handler sharing a note type.
        """
        if handler.handler_name in self._handlers:
            raise ValueError(f"Handler '{handler.handler_name}' 已經被註冊過了。")
        validate_handler(handler)
        self._check_shared_note_types(handler)
        self._handlers[handler.handler_name] = handler
        for note_type in handler.supported_models:
            self._by_note_type.setdefault(note_type, []).append(handler)
        logger.info("已註冊 Handler: %s (支援: %s)", handler.handler_name, handler.supported_models)

    def _check_shared_note_types(self, handler: BaseHandler) -> None:
        """同一筆記類型有多個 handler 時，對既有筆記的能力必須相同（D4）。

        Handlers sharing a note type must offer the same operations on
        existing notes.

        Raises:
            RuntimeError: 能力不同。When they differ.
        """
        ops = handler.CAPABILITIES.existing_note_ops()
        for note_type in handler.supported_models:
            for other in self._by_note_type.get(note_type, []):
                if other.CAPABILITIES.existing_note_ops() != ops:
                    raise RuntimeError(
                        f"Handler '{handler.handler_name}' 與 '{other.handler_name}' 共用筆記類型 {note_type}，"
                        "但讀取、改欄位、刪除、動作的能力不同"
                    )

    def handler_for_note_type(self, note_type: str) -> BaseHandler | None:
        """負責這個筆記類型的 handler；有多個時取第一個註冊的（它們的能力相同，D4）。

        The handler owning a note type; the first registered when several do.

        Args:
            note_type: 筆記類型。The note type.

        Returns:
            Handler；沒有 handler 負責時為 None。None when no handler owns it.
        """
        handlers = self._by_note_type.get(note_type)
        return handlers[0] if handlers else None

    def handlers_for_note_type(self, note_type: str) -> list[BaseHandler]:
        """負責這個筆記類型的所有 handler，依註冊順序。All handlers owning a note type.

        Args:
            note_type: 筆記類型。The note type.

        Returns:
            Handler 清單（可能為空）。The handlers, possibly none.
        """
        return list(self._by_note_type.get(note_type, []))

    def all_handlers(self) -> list[BaseHandler]:
        """所有已註冊的 handler，依註冊順序。All registered handlers, in order."""
        return list(self._handlers.values())

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


def validate_handler(handler: BaseHandler) -> None:
    """檢查 handler 的宣告有實作支撐（handlers 計畫書 D2 的表格）。

    Check that a handler's declarations are backed by its implementation.

    只檢查「宣告開放 → 必須有實作」：有實作但宣告不開放是允許的。宣告本身的形狀（例如不開放時
    必有原因）在 ``capabilities`` 的資料類別建立時就檢查過。

    Only "declared → implemented" is checked; implemented-but-closed is fine.

    Args:
        handler: 要檢查的 handler。The handler to check.

    Raises:
        RuntimeError: 列出所有不一致之處。Listing every problem found.
    """
    caps = handler.CAPABILITIES
    cls = type(handler)
    problems: list[str] = []

    if not handler.title:
        problems.append("title 為空")
    if not handler.supported_models:
        problems.append("supported_models 為空")

    create = caps.create
    if create.supported and create.note_type not in handler.supported_models:
        problems.append(f"create.note_type={create.note_type!r} 不在 supported_models 內")
    if "script" in create.channels and not callable(getattr(handler, "execute_generate", None)):
        problems.append("create.channels 含 script，但沒有 execute_generate")

    # 覆寫建立的參數不是筆記欄位，要有 CREATE_PARAMS 才能驗證與產生表單；標準建立則不該宣告它
    overrides_create = cls.execute_create is not BaseHandler.execute_create
    if overrides_create and create.supported and handler.CREATE_PARAMS is None:
        problems.append("開放建卡且覆寫了 execute_create，但沒有宣告 CREATE_PARAMS")
    if handler.CREATE_PARAMS is not None and not overrides_create:
        problems.append("宣告了 CREATE_PARAMS，但用的是標準建立（參數就是筆記欄位）")

    # JSON 欄位的表單編輯：開放就要有格式；格式只能宣告在 JSON_FIELDS 之內
    if caps.update_json.supported and not handler.JSON_FIELD_SPECS:
        problems.append("開放 update_json，但沒有宣告 JSON_FIELD_SPECS")
    unknown_specs = [name for name in handler.JSON_FIELD_SPECS if name not in handler.JSON_FIELDS]
    if unknown_specs:
        problems.append(f"JSON_FIELD_SPECS 的 {unknown_specs} 不在 JSON_FIELDS 內")

    # 基底的 execute_update 只做 update_fields；具名動作一定要覆寫
    if caps.actions and cls.execute_update is BaseHandler.execute_update:
        problems.append(f"宣告了動作 {list(caps.actions)}，但沒有覆寫 execute_update")

    uses_relation_graph = isinstance(handler, RelationGraphMixin)
    if caps.graph.supported and caps.graph.links != uses_relation_graph:
        problems.append("graph.links 必須與是否使用 RelationGraphMixin 一致")
    if uses_relation_graph and not handler.RELATION_LABEL_FIELD:
        problems.append("使用 RelationGraphMixin 時必須宣告 RELATION_LABEL_FIELD")

    problems.extend(_unknown_declared_fields(handler))

    if problems:
        raise RuntimeError(f"Handler '{handler.handler_name}' 的宣告與實作不一致: {'; '.join(problems)}")


def _unknown_declared_fields(handler: BaseHandler) -> list[str]:
    """宣告的欄位名要存在於至少一個負責的筆記類型（以 repo 的定義檔為準）。

    Declared field names must exist in at least one of the handler's note
    types, per the repo's definition files.

    Returns:
        問題描述。The problems found.
    """
    declared = {
        "RELATION_LABEL_FIELD": (handler.RELATION_LABEL_FIELD,) if handler.RELATION_LABEL_FIELD else (),
        "JSON_FIELDS": handler.JSON_FIELDS,
        "MEDIA_FIELDS": handler.MEDIA_FIELDS,
        "GRAPH": handler.GRAPH.field_names(),
    }
    if not any(declared.values()):
        return []
    known: set[str] = set()
    missing_files: list[str] = []
    for model in handler.supported_models:
        names = model_field_names(model)
        if names is None:
            missing_files.append(model)
        else:
            known.update(names)
    if missing_files:
        # 少了定義檔就無法判斷欄位對不對，直接指出缺的檔案，不誤報成欄位寫錯
        return [f"宣告了欄位，但 {missing_files} 在 app/anki_models 沒有定義檔，無法檢查"]
    return [
        f"{source} 的欄位 {name!r} 不存在於 {handler.supported_models} 的定義檔"
        for source, names in declared.items()
        for name in names
        if name not in known
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
