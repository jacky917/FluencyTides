"""Handler registry module.

任務處理器註冊表（Handler Registry）模組。

Dynamically manages and dispatches handlers and creators at the application level. The registry is a lightweight
module-level singleton (``handler_registry``) created when this module is imported; handlers and creators register
themselves through ``@register_handler`` and ``@register_creator`` when the ``task_handlers`` package is imported,
and the package then calls :meth:`HandlerRegistry.check_creators` once.

負責在應用程式層級動態管理與分派 handler 與 creator。Registry 本身為輕量的模組層級 Singleton
（``handler_registry``），在 import 此模組時建立；handler 與 creator 在 import ``task_handlers`` 套件時經
``@register_handler``、``@register_creator`` 註冊，套件最後呼叫一次 ``HandlerRegistry.check_creators``。

Each note type has exactly one handler, and registering a second one fails, so the handler found from a note
type is never ambiguous; a note type may have several creators (handler/creator split plan,
``handler_note_type_split_FEAT_2026-11-28.md``, D4 and D8).

每種筆記類型恰好一個 handler，註冊第二個就失敗，所以從筆記類型找 handler 一定只有一個答案；
同一種筆記類型可以有好幾個 creator（handler 拆分計畫書 ``handler_note_type_split_FEAT_2026-11-28.md`` D4、D8）。
"""

import logging

from typing import Type

from app.core.exceptions import FluencyTidesError
from app.services.media_names import MEDIA_KINDS
from app.services.task_handlers.base import BaseHandler
from app.services.task_handlers.capabilities import ANY_FIELD_KEY, TAG_SEARCH_KEY
from app.services.task_handlers.creator import BaseCreator
from app.services.task_handlers.model_fields import model_field_names
from app.services.task_handlers.relation_graph import RelationGraphMixin

logger = logging.getLogger(__name__)


class HandlerNotFoundError(FluencyTidesError):
    """The error raised when the requested handler is not present in the registry.

    指定的 Handler 不存在於 Registry 中時拋出的錯誤。
    """

    error_code = "HANDLER_NOT_FOUND"
    status_code = 404

    def __init__(self, handler_name: str, *, kind: str = "任務處理器") -> None:
        """Create the error with a message naming what was not found.

        以指出找不到什麼的訊息建立錯誤。

        Args:
            handler_name: the name that was not found (a creator's name for ``CreatorNotFoundError``)
                找不到的名稱（``CreatorNotFoundError`` 時是 creator 名稱）
            kind: what was looked up, in Chinese, for the message
                找的是什麼（中文），放進訊息裡
        """
        super().__init__(
            message=f"找不到指定的{kind}: {handler_name}",
        )


class CreatorNotFoundError(HandlerNotFoundError):
    """The error raised when the requested creator is not present in the registry.

    指定的 creator 不存在於 Registry 中時拋出的錯誤。

    It is a ``HandlerNotFoundError``, so the create URLs (``/handlers/{name}/notes`` and so on) keep the error code
    ``HANDLER_NOT_FOUND`` and status 404; only the message says a creator is missing.

    它是 ``HandlerNotFoundError``，所以建卡網址（``/handlers/{name}/notes`` 等）的錯誤碼 ``HANDLER_NOT_FOUND`` 與
    狀態 404 不變；只有訊息改說找不到建卡流程。
    """

    def __init__(self, creator_name: str) -> None:
        """Create the error with a message naming the missing creator.

        以指出找不到的 creator 的訊息建立錯誤。

        Args:
            creator_name: the creator name that was not found
                找不到的 creator 名稱
        """
        super().__init__(creator_name, kind="建卡流程")


class HandlerRegistry:
    """The handler and creator registry.

    handler 與 creator 的註冊表。

    Centrally manages all registered handler and creator instances and serves lookup and dispatch for the API
    layer and the Telegram bot layer.

    負責統一管理所有已註冊的 handler 與 creator 實例，並提供給 API 層與 Telegram Bot 層查詢與分派使用。

    Attributes:
        _handlers: mapping from ``handler_name`` to the handler
            ``handler_name`` → handler 的映射字典
        _by_note_type: mapping from note type to the one handler owning it
            筆記類型 → 負責它的唯一 handler
        _creators: mapping from ``creator_name`` to the creator
            ``creator_name`` → creator 的映射字典
    """

    def __init__(self) -> None:
        """Create an empty registry.

        建立空的 Registry。
        """
        self._handlers: dict[str, BaseHandler] = {}
        self._by_note_type: dict[str, BaseHandler] = {}
        self._creators: dict[str, BaseCreator] = {}
        # check_creators 通過過一次之後，再註冊的 creator 要當場檢查、綁定，不能留下沒綁定的
        self._creators_checked = False

    def register(self, handler: BaseHandler) -> None:
        """Check a handler instance's declarations, then register it.

        檢查宣告後註冊一個 Handler 實例。

        Nothing is registered when a check fails.

        檢查不過時不會留下任何註冊。

        Args:
            handler: the handler instance to register
                要註冊的 Handler 實例

        Raises:
            RuntimeError: if the capability declarations are not backed by the implementation (D2)
                若能力宣告沒有實作支撐（D2）
            ValueError: if ``handler_name`` is already registered, or one of its note types already has a handler
                若 ``handler_name`` 已存在，或它的某個筆記類型已經有 handler
        """
        if handler.handler_name in self._handlers:
            raise ValueError(f"Handler '{handler.handler_name}' 已經被註冊過了。")
        models = tuple(handler.supported_models)
        taken = {
            note_type: self._by_note_type[note_type].handler_name
            for note_type in models
            if note_type in self._by_note_type
        }
        if taken:
            owners = "、".join(f"{note_type}（已歸 '{owner}'）" for note_type, owner in taken.items())
            raise ValueError(f"Handler '{handler.handler_name}' 的筆記類型已經有 handler：{owners}")
        validate_handler(handler)
        self._handlers[handler.handler_name] = handler
        for note_type in models:
            self._by_note_type[note_type] = handler
        logger.info("已註冊 Handler: %s (支援: %s)", handler.handler_name, handler.supported_models)

    def register_creator(self, creator: BaseCreator) -> None:
        """Check a creator instance's declarations, then register it.

        檢查宣告後註冊一個 creator 實例。

        Checks that need its handler wait for :meth:`check_creators`. A creator registered after
        ``check_creators`` has already passed is checked and bound on the spot instead, so no registered creator is
        left unbound. Nothing is registered when a check fails.

        需要 handler 才能做的檢查留給 ``check_creators``。``check_creators`` 已經通過之後才註冊的 creator，改為
        當場檢查並綁定，不會留下沒綁定的 creator。檢查不過時不會留下任何註冊。

        Args:
            creator: the creator instance to register
                要註冊的 creator 實例

        Raises:
            RuntimeError: if the declarations are not backed by the implementation, or (after ``check_creators``)
                do not match the registered handlers
                若宣告沒有實作支撐，或（``check_creators`` 之後）與已註冊的 handler 不一致
            ValueError: if ``creator_name`` is already registered
                若 ``creator_name`` 已存在
        """
        if creator.creator_name in self._creators:
            raise ValueError(f"Creator '{creator.creator_name}' 已經被註冊過了。")
        validate_creator(creator)
        self._creators[creator.creator_name] = creator
        if self._creators_checked:
            try:
                self.check_creators()
            except RuntimeError:
                del self._creators[creator.creator_name]
                raise
        logger.info("已註冊 Creator: %s (handler: %s)", creator.creator_name, creator.HANDLER)

    def check_creators(self) -> None:
        """Check every creator against the registered handlers, then bind each to its handler.

        以已註冊的 handler 檢查每個 creator，再把它綁到自己的 handler。

        Call it once after every handler and creator module is imported (``task_handlers/__init__.py`` does),
        since a creator may write note types owned by handlers in other modules. Calling it again is harmless.
        While the API still uses one name per URL (plan D7, first stage), each handler may have at most one
        creator, named the same as the handler.

        要在所有 handler 與 creator 的模組都 import 之後呼叫一次（``task_handlers/__init__.py`` 會呼叫），因為
        creator 可能寫出別的模組的 handler 負責的筆記類型。重複呼叫沒有副作用。在 API 的網址還只有一個名稱時
        （handler 拆分計畫書 D7 第一階段），每個 handler 最多一個 creator，而且和 handler 同名。

        Checked for each creator: ``HANDLER`` is registered; the creator is named after it and is its only creator;
        ``create.note_type``, when set, belongs to it and is among ``PRODUCES`` when that is declared; and every
        produced note type has a handler.

        每個 creator 檢查：``HANDLER`` 已註冊；creator 和它同名、是它唯一的 creator；``create.note_type`` 有值時
        歸它，宣告了 ``PRODUCES`` 時也要在其中；會寫出的每種筆記類型都有 handler。

        Raises:
            RuntimeError: if any creator fails; the message lists all problems and the binding is left as it was
                若有 creator 不通過；訊息列出全部問題，綁定維持原狀
        """
        problems: list[str] = []
        listed: dict[str, list[str]] = {}
        for creator in self._creators.values():
            name = creator.creator_name
            handler = self._handlers.get(creator.HANDLER)
            if handler is None:
                problems.append(f"Creator '{name}' 的 HANDLER={creator.HANDLER!r} 沒有註冊")
                continue
            listed.setdefault(handler.handler_name, []).append(name)
            if name != handler.handler_name:
                problems.append(
                    f"Creator '{name}' 必須和它的 handler '{handler.handler_name}' 同名（handler 拆分計畫書 D7 第一階段）"
                )
            note_type = creator.CAPABILITIES.create.note_type
            if note_type is not None and note_type not in handler.supported_models:
                problems.append(f"Creator '{name}' 的 create.note_type={note_type!r} 不歸 '{handler.handler_name}'")
            if note_type is not None and creator.PRODUCES and note_type not in creator.PRODUCES:
                problems.append(f"Creator '{name}' 宣告了 PRODUCES，但沒有包含 create.note_type={note_type!r}")
            orphans = [nt for nt in creator.produced_note_types() if nt not in self._by_note_type]
            if orphans:
                problems.append(f"Creator '{name}' 會寫出 {orphans}，但沒有 handler 負責")
        for handler_name, names in listed.items():
            if len(names) > 1:
                problems.append(f"Handler '{handler_name}' 有多個 creator {names}（handler 拆分計畫書 D7 第一階段只允許一個）")
        if problems:
            raise RuntimeError(f"Creator 的宣告與 handler 不一致: {'; '.join(problems)}")
        for creator in self._creators.values():
            creator.bind(self._handlers[creator.HANDLER])
        self._creators_checked = True

    def handler_for_note_type(self, note_type: str) -> BaseHandler | None:
        """Return the one handler owning a note type.

        回傳負責這個筆記類型的唯一 handler。

        Args:
            note_type: the note type
                筆記類型

        Returns:
            the handler, or ``None`` when no handler owns it
            Handler；沒有 handler 負責時為 ``None``
        """
        return self._by_note_type.get(note_type)

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

    def get_creator(self, creator_name: str) -> BaseCreator:
        """Return the creator instance with the given name.

        根據名稱取得對應的 creator 實例。

        Args:
            creator_name: the creator name
                creator 名稱

        Returns:
            the matching creator instance
            對應的 creator 實例

        Raises:
            CreatorNotFoundError: if no creator is registered under the name
                若名稱不存在
        """
        if creator_name not in self._creators:
            raise CreatorNotFoundError(creator_name)
        return self._creators[creator_name]

    def all_creators(self) -> list[BaseCreator]:
        """Return all registered creators, in registration order.

        回傳所有已註冊的 creator，依註冊順序。

        Returns:
            the creators
            creator 清單
        """
        return list(self._creators.values())

    def creators_for_handler(self, handler_name: str) -> list[BaseCreator]:
        """Return the creators listed under a handler (``HANDLER``), in registration order.

        回傳列在某個 handler 底下（``HANDLER``）的 creator，依註冊順序。

        Args:
            handler_name: the handler name
                handler 名稱

        Returns:
            the creators; empty when none is listed under it
            creator 清單，可能為空
        """
        return [creator for creator in self._creators.values() if creator.HANDLER == handler_name]

    def creator_for_handler(self, handler_name: str) -> BaseCreator | None:
        """Return the one creator listed under a handler, as ``GET /handlers`` shows it.

        回傳列在某個 handler 底下的那一個 creator，``GET /handlers`` 就是這樣列的。

        During the first stage of handler/creator split plan D7, :meth:`check_creators` allows at most one creator
        per handler. This raises instead of quietly picking one, so lifting that limit forces the callers to be
        revisited.

        在 handler 拆分計畫書 D7 第一階段，``check_creators`` 只允許每個 handler 最多一個 creator。這裡遇到多個
        時拋錯，不默默挑一個，所以解除那個限制時一定會回頭改呼叫端。

        Args:
            handler_name: the handler name
                handler 名稱

        Returns:
            the creator, or ``None`` when none is listed under it
            creator；沒有時為 ``None``

        Raises:
            RuntimeError: if several creators are listed under it
                若有多個 creator 列在它底下
        """
        creators = self.creators_for_handler(handler_name)
        if len(creators) > 1:
            names = [creator.creator_name for creator in creators]
            raise RuntimeError(f"Handler '{handler_name}' 有多個 creator {names}，第一階段的 GET /handlers 不支援")
        return creators[0] if creators else None

    def producers_of(self, note_type: str) -> list[BaseCreator]:
        """Return the creators that write a note type, in registration order.

        回傳會寫出某種筆記類型的 creator，依註冊順序。

        Args:
            note_type: the note type
                筆記類型

        Returns:
            the creators; empty when none writes it
            creator 清單，可能為空
        """
        return [creator for creator in self._creators.values() if note_type in creator.produced_note_types()]


def validate_handler(handler: BaseHandler) -> None:
    """Check that a handler's declarations are backed by its implementation (the table in handlers plan D2).

    檢查 handler 的宣告有實作支撐（handlers 計畫書 D2 的表格）。

    Only "declared as offered → must be implemented" is checked: an implementation that is declared as not
    offered is allowed. The shape of the declaration itself (for example, a closed operation always has a
    reason) is already checked when the ``capabilities`` dataclasses are constructed. Creation is checked by
    :func:`validate_creator`.

    只檢查「宣告開放 → 必須有實作」：有實作但宣告不開放是允許的。宣告本身的形狀（例如不開放時
    必有原因）在 ``capabilities`` 的資料類別建立時就檢查過。建卡由 ``validate_creator`` 檢查。

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

    # 欄位搜尋：開放就要宣告欄位按鈕；鍵不重複，「*」保留給「全部欄位」
    if caps.search.supported:
        search_fields = handler.search_fields()
        keys = [f.key for f in search_fields]
        if not search_fields:
            problems.append("開放 search，但沒有宣告 SEARCH_FIELDS")
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


def validate_creator(creator: BaseCreator) -> None:
    """Check that a creator's declarations are backed by its implementation.

    檢查 creator 的宣告有實作支撐。

    Only what can be checked without the handler is checked here; the rest waits for
    :meth:`HandlerRegistry.check_creators`.

    這裡只檢查不需要 handler 的部分，其餘留給 ``HandlerRegistry.check_creators``。

    Args:
        creator: the creator to check
            要檢查的 creator

    Raises:
        RuntimeError: if any inconsistency is found; the message lists all of them
            若有不一致之處；訊息列出全部
    """
    create = creator.CAPABILITIES.create
    cls = type(creator)
    problems: list[str] = []

    if not creator.HANDLER:
        problems.append("沒有宣告 HANDLER")
    if "script" in create.channels and not callable(getattr(creator, "execute_generate", None)):
        problems.append("create.channels 含 script，但沒有 execute_generate")

    # 覆寫建立的參數不是筆記欄位，要有 CREATE_PARAMS 才能驗證與產生表單；標準建立則不該宣告它
    overrides_create = cls.execute_create is not BaseCreator.execute_create
    if overrides_create and create.supported and creator.CREATE_PARAMS is None:
        problems.append("開放建卡且覆寫了 execute_create，但沒有宣告 CREATE_PARAMS")
    if creator.CREATE_PARAMS is not None and not overrides_create:
        problems.append("宣告了 CREATE_PARAMS，但用的是標準建立（參數就是筆記欄位）")

    # 網頁的預覽流程：先以 DRAFT_PARAMS 的表單要草稿（execute_draft），草稿就是 CREATE_PARAMS 的形狀
    web_preview = create.supported and create.flow == "preview" and "web" in create.channels
    overrides_draft = cls.execute_draft is not BaseCreator.execute_draft
    if web_preview and (creator.DRAFT_PARAMS is None or not overrides_draft or creator.CREATE_PARAMS is None):
        problems.append("網頁的預覽流程要宣告 DRAFT_PARAMS、CREATE_PARAMS，並覆寫 execute_draft")
    if (creator.DRAFT_PARAMS is not None or overrides_draft) and not web_preview:
        problems.append("宣告了草稿（DRAFT_PARAMS 或 execute_draft），但沒有開放網頁的預覽流程")

    if problems:
        raise RuntimeError(f"Creator '{creator.creator_name}' 的宣告與實作不一致: {'; '.join(problems)}")


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
        ValueError: if a handler with the same name, or one owning one of its note types, is already registered
            若同名的 handler，或負責它某個筆記類型的 handler 已註冊
    """
    handler_instance = cls()
    handler_registry.register(handler_instance)
    return cls


def register_creator(cls: Type[BaseCreator]) -> Type[BaseCreator]:
    """Instantiate a creator class and register it in the global registry; used as a class decorator.

    裝飾器：將 creator 類別實例化並註冊至全域 Registry。

    Creators hold no state besides the handler the registry binds, so they can be instantiated without arguments.

    creator 除了註冊表綁定的 handler 之外沒有狀態，可以直接無參數實例化。

    Args:
        cls: the creator class to register
            要註冊的 creator 類別

    Returns:
        the original creator class, unmodified
        原始的 creator 類別（不修改）

    Raises:
        RuntimeError: if the creator's declarations fail the checks of :meth:`HandlerRegistry.register_creator`
            若 creator 的宣告沒通過 ``HandlerRegistry.register_creator`` 的檢查
        ValueError: if a creator with the same name is already registered
            若同名的 creator 已註冊
    """
    handler_registry.register_creator(cls())
    return cls
