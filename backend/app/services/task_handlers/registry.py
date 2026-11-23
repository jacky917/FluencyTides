"""Handler registry module.

任務處理器註冊表（Handler Registry）模組。

Dynamically manages and dispatches task handlers at the application level. The registry is a lightweight
module-level singleton (``handler_registry``) created when this module is imported; handlers register
themselves through ``@register_handler`` when the ``task_handlers`` package is imported.

負責在應用程式層級動態管理與分派 Task Handlers。Registry 本身為輕量的模組層級 Singleton
（``handler_registry``），在 import 此模組時建立；各 handler 在 import ``task_handlers`` 套件時經
``@register_handler`` 註冊。
"""

import logging

from typing import Type

from app.core.exceptions import FluencyTidesError
from app.services.media_names import MEDIA_KINDS
from app.services.task_handlers.base import BaseHandler
from app.services.task_handlers.capabilities import ANY_FIELD_KEY, TAG_SEARCH_KEY
from app.services.task_handlers.model_fields import model_field_names
from app.services.task_handlers.relation_graph import RelationGraphMixin

logger = logging.getLogger(__name__)


class HandlerNotFoundError(FluencyTidesError):
    """The error raised when the requested handler is not present in the registry.

    指定的 Handler 不存在於 Registry 中時拋出的錯誤。
    """

    error_code = "HANDLER_NOT_FOUND"
    status_code = 404

    def __init__(self, handler_name: str) -> None:
        """Create the error with a message naming the missing handler.

        以指出找不到的處理器的訊息建立錯誤。

        Args:
            handler_name: the handler name that was not found
                找不到的處理器名稱
        """
        super().__init__(
            message=f"找不到指定的任務處理器: {handler_name}",
        )


class HandlerRegistry:
    """The handler registry.

    處理器註冊表。

    Centrally manages all registered handler instances and serves lookup and dispatch for the API layer and
    the Telegram bot layer.

    負責統一管理所有已註冊的 Handler 實例，並提供給 API 層與 Telegram Bot 層查詢與分派使用。

    Attributes:
        _handlers: mapping from ``handler_name`` to the handler
            ``handler_name`` → handler 的映射字典
        _by_note_type: mapping from note type to the handlers owning it, in registration order
            筆記類型 → 負責它的 handler，依註冊順序
    """

    def __init__(self) -> None:
        """Create an empty registry.

        建立空的 Registry。
        """
        self._handlers: dict[str, BaseHandler] = {}
        self._by_note_type: dict[str, list[BaseHandler]] = {}

    def register(self, handler: BaseHandler) -> None:
        """Check a handler instance's declarations, then register it.

        檢查宣告後註冊一個 Handler 實例。

        Nothing is registered when a check fails.

        檢查不過時不會留下任何註冊。

        Args:
            handler: the handler instance to register
                要註冊的 Handler 實例

        Raises:
            RuntimeError: if the capability declarations are not backed by the implementation, or conflict with
                a handler sharing a note type (D2, D4)
                若能力宣告沒有實作支撐，或與共用筆記類型的 handler 不一致（D2、D4）
            ValueError: if ``handler_name`` is already registered
                若 ``handler_name`` 已存在
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
        """Check that handlers sharing a note type offer the same capabilities on existing notes (D4).

        同一筆記類型有多個 handler 時，檢查對既有筆記的能力相同（D4）。

        Args:
            handler: the handler about to be registered
                即將註冊的 handler

        Raises:
            RuntimeError: if the capabilities differ
                若能力不同
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
        """Return the handler owning a note type; the first registered when several do.

        回傳負責這個筆記類型的 handler；有多個時取第一個註冊的。

        Several handlers owning one note type have the same capabilities (D4).

        多個 handler 負責同一筆記類型時，它們的能力相同（D4）。

        Args:
            note_type: the note type
                筆記類型

        Returns:
            the handler, or ``None`` when no handler owns it
            Handler；沒有 handler 負責時為 ``None``
        """
        handlers = self._by_note_type.get(note_type)
        return handlers[0] if handlers else None

    def handlers_for_note_type(self, note_type: str) -> list[BaseHandler]:
        """Return all handlers owning a note type, in registration order.

        回傳負責這個筆記類型的所有 handler，依註冊順序。

        Args:
            note_type: the note type
                筆記類型

        Returns:
            the handlers; empty when none owns it
            Handler 清單，可能為空
        """
        return list(self._by_note_type.get(note_type, []))

    def all_handlers(self) -> list[BaseHandler]:
        """Return all registered handlers, in registration order.

        回傳所有已註冊的 handler，依註冊順序。

        Returns:
            the handlers
            Handler 清單
        """
        return list(self._handlers.values())

    def get_handler(self, handler_name: str) -> BaseHandler:
        """Return the handler instance with the given name.

        根據名稱取得對應的 Handler 實例。

        Args:
            handler_name: the handler name
                處理器名稱

        Returns:
            the matching handler instance
            對應的 Handler 實例

        Raises:
            HandlerNotFoundError: if no handler is registered under the name
                若名稱不存在
        """
        if handler_name not in self._handlers:
            raise HandlerNotFoundError(handler_name)
        return self._handlers[handler_name]

    def list_all_handlers(self) -> list[dict[str, object]]:
        """Return information about all registered handlers, for the frontend.

        列出所有註冊的 Handler 資訊，供前端使用。

        Returns:
            one dict per handler with ``handler_name``, ``supported_models``, and ``input_schema``
            每個 Handler 一個字典，含 ``handler_name``、``supported_models``、``input_schema``
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
    """Check that a handler's declarations are backed by its implementation (the table in handlers plan D2).

    檢查 handler 的宣告有實作支撐（handlers 計畫書 D2 的表格）。

    Only "declared as offered → must be implemented" is checked: an implementation that is declared as not
    offered is allowed. The shape of the declaration itself (for example, a closed operation always has a
    reason) is already checked when the ``capabilities`` dataclasses are constructed.

    只檢查「宣告開放 → 必須有實作」：有實作但宣告不開放是允許的。宣告本身的形狀（例如不開放時
    必有原因）在 ``capabilities`` 的資料類別建立時就檢查過。

    Args:
        handler: the handler to check
            要檢查的 handler

    Raises:
        RuntimeError: if any inconsistency is found; the message lists all of them
            若有不一致之處；訊息列出全部
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

    # 網頁的預覽流程：先以 DRAFT_PARAMS 的表單要草稿（execute_draft），草稿就是 CREATE_PARAMS 的形狀
    web_preview = create.supported and create.flow == "preview" and "web" in create.channels
    overrides_draft = cls.execute_draft is not BaseHandler.execute_draft
    if web_preview and (handler.DRAFT_PARAMS is None or not overrides_draft or handler.CREATE_PARAMS is None):
        problems.append("網頁的預覽流程要宣告 DRAFT_PARAMS、CREATE_PARAMS，並覆寫 execute_draft")
    if (handler.DRAFT_PARAMS is not None or overrides_draft) and not web_preview:
        problems.append("宣告了草稿（DRAFT_PARAMS 或 execute_draft），但沒有開放網頁的預覽流程")

    # JSON 欄位的表單編輯：開放就要有格式；格式只能宣告在 JSON_FIELDS 之內
    if caps.update_json.supported and not handler.JSON_FIELD_SPECS:
        problems.append("開放 update_json，但沒有宣告 JSON_FIELD_SPECS")
    unknown_specs = [name for name in handler.JSON_FIELD_SPECS if name not in handler.JSON_FIELDS]
    if unknown_specs:
        problems.append(f"JSON_FIELD_SPECS 的 {unknown_specs} 不在 JSON_FIELDS 內")

    # 媒體欄位：種類只有 audio、image；同一個欄位不能又是 JSON 又是媒體
    bad_kinds = {name: kind for name, kind in handler.MEDIA_FIELDS.items() if kind not in MEDIA_KINDS}
    if bad_kinds:
        problems.append(f"MEDIA_FIELDS 的種類只能是 {list(MEDIA_KINDS)}：{bad_kinds}")
    both = [name for name in handler.MEDIA_FIELDS if name in handler.JSON_FIELDS]
    if both:
        problems.append(f"{both} 同時宣告在 JSON_FIELDS 與 MEDIA_FIELDS")

    # 欄位搜尋：開放就要有欄位按鈕；鍵不重複，「*」保留給「全部欄位」
    if caps.search.supported:
        search_fields = handler.search_fields()
        keys = [f.key for f in search_fields]
        if not search_fields:
            problems.append("開放 search，但沒有可搜尋的欄位（宣告 SEARCH_FIELDS 或 CREATE_PARAMS）")
        if len(set(keys)) != len(keys) or {ANY_FIELD_KEY, TAG_SEARCH_KEY} & set(keys):
            problems.append(f"SEARCH_FIELDS 的鍵重複或用了保留的「*」「#」：{keys}")
        if any(not f.fields for f in search_fields):
            problems.append("SEARCH_FIELDS 有欄位按鈕沒有對應的 Anki 欄位")

    # 基底的 execute_update 只做 update_fields；具名動作一定要覆寫
    if caps.actions and cls.execute_update is BaseHandler.execute_update:
        problems.append(f"宣告了動作 {list(caps.actions)}，但沒有覆寫 execute_update")

    if caps.graph.layout == "tree" and cls.execute_read_graph is BaseHandler.execute_read_graph:
        problems.append("樹狀圖譜（graph.layout='tree'）必須覆寫 execute_read_graph")

    uses_relation_graph = isinstance(handler, RelationGraphMixin)
    if caps.graph.supported and caps.graph.links != uses_relation_graph:
        problems.append("graph.links 必須與是否使用 RelationGraphMixin 一致")
    if uses_relation_graph and not handler.RELATION_LABEL_FIELD:
        problems.append("使用 RelationGraphMixin 時必須宣告 RELATION_LABEL_FIELD")

    problems.extend(_unknown_declared_fields(handler))

    if problems:
        raise RuntimeError(f"Handler '{handler.handler_name}' 的宣告與實作不一致: {'; '.join(problems)}")


def _unknown_declared_fields(handler: BaseHandler) -> list[str]:
    """Check that declared field names exist in at least one of the handler's note types.

    檢查宣告的欄位名存在於至少一個負責的筆記類型。

    The repo's definition files are authoritative. When a note type has no definition file, the fields
    cannot be checked, and that is reported instead.

    以 repo 的定義檔為準。筆記類型沒有定義檔時無法檢查欄位，改為指出缺的檔案。

    Args:
        handler: the handler to check
            要檢查的 handler

    Returns:
        descriptions of the problems found; empty when there are none
        問題描述；沒有問題時為空
    """
    declared = {
        "RELATION_LABEL_FIELD": (handler.RELATION_LABEL_FIELD,) if handler.RELATION_LABEL_FIELD else (),
        "JSON_FIELDS": handler.JSON_FIELDS,
        "MEDIA_FIELDS": tuple(handler.MEDIA_FIELDS),
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
    """Instantiate a handler class and register it in the global registry; used as a class decorator.

    裝飾器：將 Handler 類別實例化並註冊至全域 Registry。

    Handlers are stateless, so they can be instantiated without arguments.

    由於 Handler 已被重構為無狀態（Stateless），可以直接無參數實例化。

    Args:
        cls: the handler class to register
            要註冊的 Handler 類別

    Returns:
        the original handler class, unmodified
        原始的 Handler 類別（不修改）

    Raises:
        RuntimeError: if the handler's declarations fail the checks of :meth:`HandlerRegistry.register`
            若 handler 的宣告沒通過 ``HandlerRegistry.register`` 的檢查
        ValueError: if a handler with the same name is already registered
            若同名的 handler 已註冊
    """
    handler_instance = cls()
    handler_registry.register(handler_instance)
    return cls
