"""Read/write service for capability route settings.

能力路由設定的讀寫服務。

Capability routing plan capability_routing_FEAT_2026-10-19.md; its predecessor is the model routing plan
llm_model_routing_FEAT_2026-10-15.md D6.

能力路由計畫 capability_routing_FEAT_2026-10-19.md；前身為模型路由計畫
llm_model_routing_FEAT_2026-10-15.md D6。

The API (``/api/v1/capability-routes``) and the TG ``/setconfig`` menu share this layer: list the setting
currently in effect for each capability of each route, write a set of choices, and restore the default. Every
operation targets a "route key × capability" (for capabilities see :mod:`app.infrastructure.llm.capabilities`).
Before writing, these checks run in order:

API（``/api/v1/capability-routes``）與 TG ``/setconfig`` 選單共用這一層：列出每個用途各能力目前
生效的設定、寫入一組選擇、恢復預設。每個操作都針對「用途鍵 × 能力」（能力見
:mod:`app.infrastructure.llm.capabilities`）。寫入前依序檢查：

1. The route key is declared (or is the global ``*``) and declares this capability.
2. ``(provider, model, effort)`` is on the whitelist (``audio_eval`` also checks the strategies the route key
   allows), using the same :func:`~app.infrastructure.llm.routing.check_choice` as the reading side.
3. A trial build: ``llm`` builds a client (whether the CLI can be found, the key is set, the package is
   installed), ``audio_llm`` builds the listening evaluator, ``stt`` builds the Whisper client; ``audio_eval`` only
   checks the strategy name. No API is called (whether the model name itself exists is only known from a real
   call), but CLI-type clients do create their working directory.

1. 用途鍵已宣告（或為全域 ``*``），且宣告了這個能力
2. ``(provider, 模型, effort)`` 在白名單內（``audio_eval`` 另檢查用途鍵允許的策略）——與讀取端
   同一個 :func:`~app.infrastructure.llm.routing.check_choice`
3. 試建一次：``llm`` 建客戶端（CLI 找不找得到、金鑰有沒有設定、套件有沒有裝）、``audio_llm``
   建聽音檔的評分器、``stt`` 建 Whisper 客戶端；``audio_eval`` 只檢查策略名。不打 API（模型名本身
   是否存在仍要實際呼叫才知道），但 CLI 類會建立工作目錄

After a successful write the route cache is cleared, so the next call uses the new setting.

寫入成功後清空路由快取，下一次呼叫即生效。

There are three kinds of errors (all :class:`~app.core.exceptions.FluencyTidesError`; the API's global handler
turns them into the matching status codes):

錯誤分三種（都是 :class:`~app.core.exceptions.FluencyTidesError`，API 由全域 handler
轉成對應狀態碼）：

- :class:`~app.core.exceptions.RouteNotFoundError` (404): the route key does not exist (when writing: not
  declared)
- :class:`~app.core.exceptions.InvalidRouteError` (422): the setting is invalid, or the route key does not declare
  this capability (the caller can fix it)
- :class:`~app.core.exceptions.RouteStoreUnavailableError` (503): the DB cannot be read, or the database failed
  while writing or deleting (outcome unknown)

- :class:`~app.core.exceptions.RouteNotFoundError`（404）：用途鍵不存在（寫入時：未宣告）
- :class:`~app.core.exceptions.InvalidRouteError`（422）：設定不合格，或用途鍵沒有宣告這個能力（呼叫端可修正）
- :class:`~app.core.exceptions.RouteStoreUnavailableError`（503）：讀不到 DB，或寫入／刪除時
  資料庫異常（結果未知）

**Reads do not reuse the generation path's fallback**: when the routing layer cannot read the DB it returns "no
setting" so generation is not interrupted, but if the management interface accepted that, it would report
"unreadable" as "does not exist". So operations that need to know whether a key has a stored value (describing a
single route, deleting an orphan key) raise 503 when the DB cannot be read (and log the reason); listing everything
still returns, with every row marked ``db_unavailable``. Listing and describing read the DB only once per request,
and every row is resolved from the same snapshot. Database errors while writing are likewise turned into 503
(outcome unknown; the user is told to check again).

**讀取不沿用生成路徑的退回**：路由層讀不到 DB 時會回「沒有設定」好讓生成不中斷，但
管理介面若照單全收，就會把「讀不到」說成「不存在」。所以需要知道某個鍵有沒有存值的
操作（查單一用途、刪除孤兒鍵）在讀不到時拋 503（並記下原因）；列出全部則照常回傳、每列
標上 ``db_unavailable``。列出與查詢時每個請求只讀一次 DB，所有列都由同一份快照解析。
寫入時的資料庫錯誤同樣轉成 503（結果未知，提示重新查看）。

**The list does not fail to open because one row is broken**: computing an ``llm`` label requires loading the
endpoint's client class; when the third-party package the endpoint needs is missing or the wrong version, that row
uses a label without prefix stripping and explains in ``problem``, and the rest are listed as usual, because the
administrator needs exactly this interface to switch the setting to a working endpoint. Mistakes in the ``.env``
layer (endpoint skipped, key or STT server not set, invalid strategy name) are also marked in ``problem``.

**清單不因單列壞掉而打不開**：算 ``llm`` 的標籤需要載入端點的客戶端類別；端點需要的第三方套件沒裝
或版本不符時，該列改用不去前綴的標籤並在 ``problem`` 說明，其餘照常列出——管理員正需要這個介面把
設定改到能用的端點。``.env`` 那一層的設定有誤（端點被跳過、金鑰或 STT 伺服器沒設、策略名無效）
同樣標在 ``problem``。
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, NamedTuple, cast

from sqlalchemy import CursorResult, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

# 宣告錄音評分的用途鍵（recording_eval…）：清單與寫入都以已宣告的鍵為準，不能等到第一則語音才 import
import app.services.recording_eval  # noqa: F401
from app.core.exceptions import (
    AudioEvaluatorUnavailableError,
    InvalidRouteError,
    LLMServiceError,
    RouteNotFoundError,
    RouteStoreUnavailableError,
    STTServiceError,
)
from app.infrastructure.audio_evaluator import factory as audio_factory
from app.infrastructure.database.models import CapabilityRouteSetting
from app.infrastructure.llm import factory, routing
from app.infrastructure.llm.base import OMIT_EFFORT
from app.infrastructure.llm.capabilities import (
    CAPABILITIES,
    CAPABILITY_AUDIO_EVAL,
    CAPABILITY_AUDIO_LLM,
    CAPABILITY_LLM,
    CAPABILITY_ORDER,
    CAPABILITY_STT,
    STRATEGIES,
    stt_configured,
)
from app.infrastructure.llm.endpoints import (
    TYPE_ANTIGRAVITY,
    TYPE_CLAUDE_CODE,
    env_endpoint_id,
    env_endpoint_is_custom,
    get_endpoint,
    skip_reason,
)
from app.infrastructure.llm.labels import build_model_label
from app.infrastructure.llm.routing import (
    DATABASE_ERRORS,
    GLOBAL_ROUTE_KEY,
    GLOBAL_ROUTE_KEYS,
    SWITCH_CAPABILITY,
    SWITCH_ON,
    VOICE_GLOBAL_ROUTE_KEY,
    ResolvedRoute,
    RouteChoice,
    Snapshot,
    describe_import_error,
    is_internal_import,
)
from app.schemas.capability_route import CapabilityStateRead, ChoiceRead, RejectedLayerRead, RouteRead

logger = logging.getLogger(__name__)

# 兩個全域在清單中的說明
GLOBAL_DESCRIPTIONS: dict[str, str] = {
    GLOBAL_ROUTE_KEY: "全域（文字）：文字用途沒有自己的設定時使用",
    VOICE_GLOBAL_ROUTE_KEY: "全域（語音）：錄音評分沒有自己的設定時使用",
}

_STORE_UNAVAILABLE_MESSAGE = (
    "讀不到能力路由設定（cfg_capability_routes），無法判斷這個用途的設定；詳見後端 log 的 WARNING，DB 恢復後再試。"
)
_SWITCH_UNKNOWN_MESSAGE = "讀不到目前的設定，無法確認全局生效開關是否蓋過這組設定；請重新查看。"
_WRITE_FAILED_MESSAGE = (
    "寫入能力路由設定（cfg_capability_routes）時資料庫異常，結果未知；請重新查看設定後再試，詳見後端 log。"
)


class RouteKeyEntry(NamedTuple):
    """One route key in the list.

    清單中的一個用途鍵。

    Attributes:
        key: the route key; ``*`` and ``*voice`` are the text and voice globals
            用途鍵；``*`` 與 ``*voice`` 為文字與語音全域
        description: the description; ``None`` for orphan keys
            說明；孤兒鍵為 ``None``
        declared: whether the key is declared in the code (or is the global one); ``False`` means an orphan row in
            the DB
            是否為程式中宣告的鍵（或全域）；``False``＝DB 裡的孤兒列
        capabilities: the capabilities that may be set for this key: a declared key's own declared capabilities,
            the global's capabilities for a global key (``routing.global_capabilities``), and none for orphan keys
            這個鍵可設定的能力：宣告的鍵為它宣告的能力，全域鍵為該全域的能力（``routing.global_capabilities``），
            孤兒鍵為空
    """

    key: str
    description: str | None
    declared: bool
    capabilities: frozenset[str] = frozenset()


@dataclass(frozen=True)
class RouteView:
    """The complete state of one route key for one capability (for display by the API and TG).

    一個用途鍵在一種能力上的完整狀態（給 API 與 TG 顯示）。

    Attributes:
        entry: the route key and its description
            用途鍵與說明
        resolved: the setting actually in effect (capability included)
            實際生效的設定（含能力）
        effective_effort: the effort actually in effect. For the ``llm`` ``.env`` layer it is the ``.env`` default
            (CLI types), not ``None``, consistent with the label.
            實際生效的 effort。``llm`` 的 ``.env`` 那一層為 ``.env`` 的預設值
            （CLI 類），不是 ``None``——與標籤一致。
        label: the display label of the effective setting (for ``llm``, the label written into cards)
            生效設定的顯示標籤（``llm`` 為寫進卡片的標籤）
        stored: the choice stored in the DB for this key and capability (it may no longer be on the whitelist)
            DB 中這個鍵在這個能力上存的選擇（可能已不在白名單）
        problem: why the effective setting cannot be used right now (a package the endpoint needs is missing or
            the wrong version, or the ``.env`` layer is misconfigured); when there is a problem, ``label`` and
            ``effective_effort`` may not match reality. ``None`` only means none of these problems was detected,
            not that the setting was verified to work.
            生效設定目前無法使用的原因（端點需要的套件未安裝或版本不符、``.env`` 那一層的
            設定有誤）；有問題時 ``label`` 與 ``effective_effort`` 可能與實際不符。``None`` 只表示
            沒有偵測到這些問題，不代表已驗證可用。
    """

    entry: RouteKeyEntry
    resolved: ResolvedRoute
    effective_effort: str | None
    label: str
    stored: RouteChoice | None
    problem: str | None = None

    def __post_init__(self) -> None:
        if self.resolved.route_key != self.entry.key:
            raise ValueError("RouteView 的 resolved 與 entry 不是同一個用途鍵")
        if not self.capability_declared and self.stored is None:
            raise ValueError("孤兒設定一定有存值（否則不會出現在清單）")
        if self.resolved.source == "route" and self.resolved.choice != self.stored:
            raise ValueError("來源為用途層時，生效的選擇必須就是該鍵存的值")

    @property
    def route_key(self) -> str:
        """The route key.

        用途鍵。
        """
        return self.entry.key

    @property
    def description(self) -> str | None:
        """The description.

        說明。
        """
        return self.entry.description

    @property
    def declared(self) -> bool:
        """Whether the route key is declared.

        用途鍵是否已宣告。
        """
        return self.entry.declared

    @property
    def capability(self) -> str:
        """The capability.

        能力。
        """
        return self.resolved.capability

    @property
    def capability_declared(self) -> bool:
        """Whether this key declares this capability.

        這個鍵是否宣告了這個能力。

        ``False`` means an orphan setting (the key or the capability is no longer in the code), which can only be
        deleted.

        ``False``＝孤兒設定（鍵或能力已不在程式中），只能刪除。
        """
        return self.entry.declared and self.capability in self.entry.capabilities


@dataclass(frozen=True)
class KeyView:
    """One route key and the states of its capabilities.

    一個用途鍵與它各能力的狀態。

    Attributes:
        entry: the route key and its description
            用途鍵與說明
        views: the state of each capability: declared capabilities in display order, followed by capabilities the
            DB stores a value for but this key does not declare
            各能力的狀態：宣告的能力依顯示順序，之後是 DB 中存了值、但這個鍵沒有宣告的能力
        switch: whether the global's "global switch" is on; ``None`` for a key that is not a global, and for a
            global while the stored settings cannot be read (unknown, not "off")
            全域的「全局生效」開關是否開著；不是全域鍵時為 ``None``，讀不到設定時全域也是 ``None``（不知道，
            不是「關」）
    """

    entry: RouteKeyEntry
    views: tuple[RouteView, ...]
    switch: bool | None = None

    @property
    def route_key(self) -> str:
        """The route key.

        用途鍵。
        """
        return self.entry.key

    @property
    def description(self) -> str | None:
        """The description.

        說明。
        """
        return self.entry.description

    @property
    def declared(self) -> bool:
        """Whether the route key is declared.

        用途鍵是否已宣告。
        """
        return self.entry.declared

    @property
    def capabilities(self) -> tuple[str, ...]:
        """The listed capabilities (in the same order as ``views``).

        列出的能力（與 ``views`` 同順序）。
        """
        return tuple(view.capability for view in self.views)

    def view(self, capability: str) -> RouteView | None:
        """Return the state of one capability.

        回傳某個能力的狀態。

        Args:
            capability: the capability
                能力

        Returns:
            the state, or ``None`` if the capability is not listed
            狀態；沒有列出時為 ``None``
        """
        return next((view for view in self.views if view.capability == capability), None)


def _session_factory() -> Callable[[], AsyncSession]:
    """Return the corpus database's session factory (the settings live in ``cfg_capability_routes``).

    回傳語料庫的 session 工廠（設定存在 ``cfg_capability_routes``）。

    ``corpus_database.corpus_async_session_factory`` is read only on each call (a deferred import, not bound at
    import time), so a test that patches that module attribute affects both the routing layer's reads and this
    service's writes.

    每次呼叫才讀 ``corpus_database.corpus_async_session_factory``（延遲 import，也不在 import 時綁定），
    所以測試 patch 該模組屬性即可同時影響路由層的讀取與本服務的寫入。

    Raises:
        ConnectionError: if the corpus connection is not configured; it is in ``DATABASE_ERRORS``, so callers
            report the store as unavailable (503)
            若語料庫連線沒有設定；它屬於 ``DATABASE_ERRORS``，呼叫端回報讀不到（503）
    """
    from app.infrastructure.database.corpus_database import require_corpus_session_factory

    return require_corpus_session_factory()


def _client_class_for(endpoint_id: str) -> Any:
    """Return the client class for an endpoint, looking the ID up as a provider when the endpoint does not exist.

    回傳端點對應的客戶端類別；端點不存在時以 ID 當 provider 查。

    Only the ``.env`` layer can meet a missing endpoint: the custom endpoint ``LLM_PROVIDER`` points to was skipped
    because it is misconfigured (see :func:`_env_endpoint_problem`). Choices in the DB layer have passed
    ``check_choice``, so their endpoint always exists.

    只有 ``.env`` 那一層會遇到端點不存在：``LLM_PROVIDER`` 指向的自訂端點因設定有誤被
    跳過（見 ``_env_endpoint_problem``）。DB 層的選擇已通過 ``check_choice``，端點一定存在。

    Raises:
        ImportError: if importing the client module fails (the package is not installed or is the wrong version)
            若客戶端模組 import 失敗（套件未安裝或版本不符）
    """
    endpoint = get_endpoint(endpoint_id)
    return factory.resolve_client_class(endpoint.type if endpoint is not None else endpoint_id)


def effective_effort(endpoint_id: str, effort: str | None, *, env_layer: bool) -> str | None:
    """Return the effort an ``llm`` setting actually uses.

    回傳 ``llm`` 實際會用的 effort。

    What an effort of ``None`` means depends on the layer: in a DB choice, ``None`` means "this model takes no
    effort" (the client receives ``OMIT_EFFORT``); in the ``.env`` layer, ``None`` means "use the ``.env`` default"
    (CLI types carry ``LLM_*_EFFORT``).

    effort 為 ``None`` 的意思依層而不同：DB 的選擇裡 ``None``＝「此模型不使用 effort」（客戶端收到
    ``OMIT_EFFORT``）；``.env`` 那一層的 ``None``＝「沿用 ``.env`` 預設」（CLI 類帶 ``LLM_*_EFFORT``）。

    Args:
        endpoint_id: the endpoint ID
            端點 ID
        effort: the effort value, or ``None``
            effort 值或 ``None``
        env_layer: whether this is the ``.env`` layer
            是否為 ``.env`` 那一層

    Returns:
        the effort actually used; ``None`` when no effort is used
        實際使用的 effort；不使用 effort 時為 ``None``

    Raises:
        ImportError: if the ``.env`` default is needed and the client module cannot be imported
            若需要 ``.env`` 預設，而客戶端模組 import 失敗
    """
    if env_layer and effort is None:
        return _client_class_for(endpoint_id).default_effort()
    return effort


def preview_label(
    endpoint_id: str, model: str, effort: str | None, *, env_layer: bool = False, capability: str = CAPABILITY_LLM
) -> str:
    """Compute the display label of a setting without building a client.

    不建立客戶端，算出這組設定的顯示標籤。

    The ``llm`` label follows the same rules as the client's ``model_label`` (prefix stripping and the default
    effort come from the client class itself), so it is exactly the label written into cards; ``audio_eval`` uses
    the strategy name; other capabilities use ``(provider)model``.

    ``llm`` 的標籤與客戶端的 ``model_label`` 規則相同（去前綴與預設 effort 取自客戶端類別本身），
    就是寫進卡片的標籤；``audio_eval`` 為策略名；其他能力為 ``(provider)模型``。

    Args:
        endpoint_id: the provider
            provider
        model: the model name
            模型名
        effort: the effort value or ``None`` (for its meaning see :func:`effective_effort`)
            effort 值或 ``None``（意思見 ``effective_effort``）
        env_layer: whether this is the ``.env`` layer
            是否為 ``.env`` 那一層
        capability: the capability
            能力

    Returns:
        the label, for example ``(yinli)gemini-3.5-flash``, ``(claude-code)opus-5@high`` or ``listen``
        標籤，例如 ``(yinli)gemini-3.5-flash``、``(claude-code)opus-5@high``、``listen``

    Raises:
        ImportError: for ``llm``, if the client module cannot be imported
            ``llm`` 時，若客戶端模組 import 失敗
    """
    if capability == CAPABILITY_AUDIO_EVAL:
        return endpoint_id
    if capability != CAPABILITY_LLM:
        return build_model_label(endpoint_id, model, None)
    shown_effort = effective_effort(endpoint_id, effort, env_layer=env_layer)
    strip_prefix = _client_class_for(endpoint_id).MODEL_LABEL_STRIP_PREFIX
    return build_model_label(endpoint_id, model, shown_effort, strip_prefix=strip_prefix)


def _global_entry(global_key: str) -> RouteKeyEntry:
    """Return the list entry of a global key.

    回傳全域鍵的清單項目。
    """
    return RouteKeyEntry(global_key, GLOBAL_DESCRIPTIONS[global_key], True, routing.global_capabilities(global_key))


def _stored_keys(stored: Snapshot) -> set[str]:
    """Return the route keys that have a stored value in the DB for any capability (switch rows excluded).

    回傳 DB 中存了任一能力的用途鍵（不含開關那一列）。
    """
    return {key for capability, rows in stored.items() if capability != SWITCH_CAPABILITY for key in rows}


def ordered_route_keys(stored: Snapshot) -> list[RouteKeyEntry]:
    """List every route key in display order.

    依顯示順序列出所有用途鍵。

    Display order: the text global ``*``, the voice global ``*voice``, the declared keys (sorted by key), then
    orphan keys in the DB (sorted by key).

    顯示順序：文字全域 ``*``、語音全域 ``*voice``、已宣告的鍵（依鍵排序）、DB 中的孤兒鍵（依鍵排序）。

    Args:
        stored: the snapshot of stored settings
            已存設定的快照

    Returns:
        the route key entries in display order
        依顯示順序的用途鍵項目
    """
    declared = {route.key: route for route in routing.list_route_keys()}
    entries = [_global_entry(key) for key in GLOBAL_ROUTE_KEYS]
    entries += [
        RouteKeyEntry(key, declared[key].description, True, declared[key].capabilities) for key in sorted(declared)
    ]
    entries += [
        RouteKeyEntry(key, None, False)
        for key in sorted(_stored_keys(stored))
        if key not in GLOBAL_ROUTE_KEYS and key not in declared
    ]
    return entries


def key_capabilities(entry: RouteKeyEntry, stored: Snapshot) -> tuple[str, ...]:
    """Return the capabilities to list for a key.

    回傳一個鍵要列出的能力。

    The declared capabilities (in display order), followed by capabilities the DB stores a value for but the key
    does not declare (sorted).

    宣告的能力（依顯示順序），再加 DB 中存了值、但沒有宣告的能力（排序）。

    Args:
        entry: the route key entry
            用途鍵項目
        stored: the snapshot of stored settings
            已存設定的快照

    Returns:
        the capabilities to list
        要列出的能力
    """
    declared = [capability for capability in CAPABILITY_ORDER if capability in entry.capabilities]
    stored_caps = sorted(
        capability
        for capability, rows in stored.items()
        if entry.key in rows and capability not in entry.capabilities and capability != SWITCH_CAPABILITY
    )
    return tuple(declared + stored_caps)


def _env_endpoint_problem() -> str | None:
    """Explain why the ``llm`` ``.env`` layer's endpoint was skipped as misconfigured, or return ``None`` if it was not.

    ``llm`` 的 ``.env`` 那一層的端點設定有誤、已被跳過時的說明；沒問題時為 ``None``。

    When ``LLM_PROVIDER`` points to a malformed endpoint in ``LLM_ENDPOINTS``, an ERROR was logged at startup, but
    every generation in this layer fails (the factory reports "endpoint does not exist"). Without marking it, the
    list would show it as a normal default.

    ``LLM_PROVIDER`` 指向 ``LLM_ENDPOINTS`` 中寫錯的端點時，啟動時記過 ERROR，但這一層的
    每次生成都會失敗（工廠報「端點不存在」）。不標出來的話，清單會把它顯示成正常的預設。
    """
    if not env_endpoint_is_custom() or get_endpoint() is not None:
        return None
    endpoint_id = env_endpoint_id()
    reason = skip_reason(endpoint_id) or "設定有誤"
    return f".env 的 LLM_PROVIDER='{endpoint_id}' 已被跳過（{reason}），使用這一層的生成會失敗"


def _env_choice_problem(resolved: ResolvedRoute) -> str | None:
    """Return why the ``.env`` layer's default cannot be used right now, or ``None`` if no problem is detected.

    回傳 ``.env`` 那一層的預設目前無法使用的原因；沒偵測到問題時為 ``None``。
    """
    choice = resolved.env_choice
    capability = resolved.capability
    if choice is None:
        return _env_endpoint_problem() if capability == CAPABILITY_LLM else None
    if capability in (CAPABILITY_LLM, CAPABILITY_AUDIO_LLM):
        endpoint = get_endpoint(choice.endpoint)
        if endpoint is None:
            return f".env 預設的端點 '{choice.endpoint}' 不存在，使用這一層會失敗"
        if endpoint.api_key is None and endpoint.type not in (TYPE_CLAUDE_CODE, TYPE_ANTIGRAVITY):
            return f"{endpoint.api_key_setting} 未設定，使用這一層會失敗"
        if not choice.model:
            return ".env 預設的模型是空的，使用這一層會失敗"
        return None
    if capability == CAPABILITY_STT:
        if not stt_configured():
            return "STT_SERVER_URL 未設定，使用這一層的語音轉文字會失敗"
        return None if choice.model else "STT_MODEL_NAME 是空的，使用這一層的語音轉文字會失敗"
    if capability == CAPABILITY_AUDIO_EVAL:
        if choice.endpoint not in STRATEGIES:
            return f".env 的 AUDIO_EVALUATOR_PROVIDER 換不成可用的策略（'{choice.endpoint}'），錄音評分會失敗"
        allowed = routing.allowed_strategies(resolved.route_key)
        if allowed is not None and choice.endpoint not in allowed:
            return f".env 預設的策略 '{choice.endpoint}' 不在這個用途允許的策略內，請另外設定"
    return None


def _label_for(resolved: ResolvedRoute) -> tuple[str | None, str, str | None]:
    """Return the ``(effort, label, problem)`` of the effective setting.

    回傳生效設定的 ``(effort, 標籤, 問題)``。

    When the third-party package an ``llm`` endpoint needs is missing or the wrong version, this row degrades and
    explains why, instead of failing the whole list. When degraded, the label is not prefix-stripped and the
    ``.env`` layer's effort default cannot be obtained either (it is ``None``).

    ``llm`` 端點需要的第三方套件沒裝或版本不符時退化並說明，
    不讓整份清單失敗。退化時標籤不去前綴，``.env`` 那一層的 effort 也取不到預設（為 ``None``）。

    Raises:
        ImportError: if the failed import is one of the project's own modules (a programming error, raised as
            usual)
            若 import 失敗的是專案自己的模組（程式錯誤，照常拋出）
    """
    if resolved.capability != CAPABILITY_LLM:
        label = preview_label(resolved.endpoint, resolved.model, None, capability=resolved.capability)
        return resolved.effort, label, None
    # .env 那一層的 effort=None 是「沿用預設」；DB 層的 None 是「不帶 effort」（見 effective_effort）
    env_layer = resolved.choice is None
    try:
        return (
            effective_effort(resolved.endpoint, resolved.effort, env_layer=env_layer),
            preview_label(resolved.endpoint, resolved.model, resolved.effort, env_layer=env_layer),
            None,
        )
    except ImportError as error:
        if is_internal_import(error):
            raise
        reason = describe_import_error(resolved.endpoint, error)
        # 每次列清單、每一列都會走到這裡：同一個原因只記一次（設定改過、快取失效後才會再提醒）
        routing.warn_once(f"{reason}，清單中使用這個端點的用途標籤可能不準確。")
        label = build_model_label(resolved.endpoint, resolved.model, resolved.effort)
        return resolved.effort, label, f"{reason}，這組設定目前無法建立客戶端"


def _view(entry: RouteKeyEntry, stored: Snapshot, read_ok: bool, capability: str = CAPABILITY_LLM) -> RouteView:
    """Build the state of one route for one capability from a snapshot.

    以一份快照組出一個用途在一種能力上的狀態。
    """
    resolved = routing.resolve_with(entry.key, stored, read_ok, capability)
    effort, label, problem = _label_for(resolved)
    if problem is None and resolved.choice is None:
        problem = _env_choice_problem(resolved)
    return RouteView(
        entry=entry,
        resolved=resolved,
        effective_effort=effort,
        label=label,
        stored=stored.get(capability, {}).get(entry.key),
        problem=problem,
    )


def _key_view(entry: RouteKeyEntry, stored: Snapshot, read_ok: bool) -> KeyView:
    """Build the states of every capability of one route key from a snapshot.

    以一份快照組出一個用途鍵各能力的狀態。

    When the snapshot could not be read, a global's switch is ``None`` (unknown): an empty snapshot would otherwise
    read as "off".

    快照讀不到時，全域的開關為 ``None``（不知道）：否則空快照會被讀成「關」。
    """
    switch = routing.switch_enabled(stored, entry.key) if entry.key in GLOBAL_ROUTE_KEYS and read_ok else None
    return KeyView(
        entry,
        tuple(_view(entry, stored, read_ok, capability) for capability in key_capabilities(entry, stored)),
        switch,
    )


async def _strict_snapshot() -> Snapshot:
    """Read every stored setting; when the DB cannot be read, log the reason and raise 503 instead of "no setting".

    讀出全部設定；讀不到時記下原因並拋 503，不當成「沒有設定」。

    The underlying error is logged with its traceback every time (management calls are rare, so this does not
    flood the log).

    每次都會記下底層錯誤與 traceback（管理操作很少，不會洗版）。

    Raises:
        RouteStoreUnavailableError: if the DB cannot be read
            若讀不到 DB
    """
    try:
        return await routing.stored_choices_strict()
    except DATABASE_ERRORS as error:
        logger.warning("管理操作讀取 cfg_capability_routes 失敗：%s: %s", type(error).__name__, error, exc_info=True)
        raise RouteStoreUnavailableError(_STORE_UNAVAILABLE_MESSAGE) from error


def views_from(stored: Snapshot, read_ok: bool) -> list[KeyView]:
    """Build the states of every route key (in display order) from an already-read snapshot.

    以一份已讀出的快照組出全部用途鍵的狀態（依顯示順序）。

    The TG menu uses this so the "menu version" and the "list content" come from the same read.

    TG 選單用它讓「選單版本」與「清單內容」來自同一次讀取。

    Args:
        stored: the snapshot of stored settings
            已存設定的快照
        read_ok: whether the snapshot was read from the DB successfully
            快照是否成功從 DB 讀出

    Returns:
        the state of every route key, in display order
        各用途鍵的狀態，依顯示順序
    """
    return [_key_view(entry, stored, read_ok) for entry in ordered_route_keys(stored)]


async def list_routes() -> tuple[list[KeyView], bool]:
    """List the current state of every capability of every route key (global and orphan keys included).

    列出全部用途鍵（含全域與孤兒鍵）各能力目前的狀態，依顯示順序。

    Results are in display order. When the DB cannot be read, this still returns (every row shows the ``.env``
    default and is marked ``db_unavailable``; orphan keys are not visible), and the caller shows a warning.

    依顯示順序。讀不到 DB 時仍回傳（各列為 ``.env`` 預設、標上 ``db_unavailable``；孤兒鍵看不到），
    由呼叫端顯示警示。

    Returns:
        ``(key views, whether the DB was read successfully)``
        ``(各用途鍵狀態, DB 是否讀取成功)``
    """
    stored, read_ok = await routing.stored_choices()
    return views_from(stored, read_ok), read_ok


async def describe(route_key: str) -> KeyView:
    """Return the state of every capability of a single route key.

    回傳單一用途鍵各能力的狀態。

    Args:
        route_key: the route key or ``*``
            用途鍵或 ``*``

    Returns:
        the key's states
        這個鍵的狀態

    Raises:
        RouteNotFoundError: if the route key is neither declared nor present in the DB
            若用途鍵既未宣告、DB 中也沒有
        RouteStoreUnavailableError: if the DB cannot be read (so it cannot tell whether an orphan key exists or what
            the key itself stores)
            若讀不到 DB（無法判斷孤兒鍵是否存在、本身存了什麼）
    """
    stored = await _strict_snapshot()
    for entry in ordered_route_keys(stored):
        if entry.key == route_key:
            return _key_view(entry, stored, True)
    raise RouteNotFoundError(f"用途鍵 '{route_key}' 不存在")


def _declared_entry(route_key: str) -> RouteKeyEntry | None:
    """Return the list entry for a declared key (or the global key), or ``None`` if it is not declared.

    回傳已宣告的鍵（或全域）的清單項目；未宣告時為 ``None``。
    """
    if route_key in GLOBAL_ROUTE_KEYS:
        return _global_entry(route_key)
    declared = routing.get_route(route_key)
    if declared is None:
        return None
    return RouteKeyEntry(declared.key, declared.description, True, declared.capabilities)


def _check_capability(capability: str) -> None:
    """Require the capability to be one the code supports.

    能力必須是程式支援的。

    Raises:
        InvalidRouteError: if the capability is not supported
            若為不支援的能力
    """
    if capability not in CAPABILITIES:
        raise InvalidRouteError(f"不支援的能力 '{capability}'（可用：{', '.join(sorted(CAPABILITIES))}）")


def _trial_build(capability: str, choice: RouteChoice) -> None:
    """Build the tool for the capability once, without calling any API.

    依能力試建一次（不打 API）。

    ``audio_eval`` builds nothing. When the tool cannot be built, this raises ``LLMServiceError``,
    ``STTServiceError``, ``AudioEvaluatorUnavailableError``, ``ImportError`` or ``OSError``.

    ``audio_eval`` 不建立任何東西。建不出來時拋 ``LLMServiceError``、``STTServiceError``、
    ``AudioEvaluatorUnavailableError``、``ImportError`` 或 ``OSError``。
    """
    if capability == CAPABILITY_LLM:
        factory.create_llm_client(
            endpoint=choice.endpoint,
            model=choice.model,
            effort=choice.effort if choice.effort is not None else OMIT_EFFORT,
        )
    elif capability == CAPABILITY_AUDIO_LLM:
        audio_factory.build_listen_evaluator(choice.endpoint, choice.model)
    elif capability == CAPABILITY_STT:
        audio_factory.build_whisper(choice.endpoint, choice.model)


def validate_choice(
    choice: RouteChoice,
    *,
    capability: str = CAPABILITY_LLM,
    route_key: str = GLOBAL_ROUTE_KEY,
    updated_by: str = "?",
) -> None:
    """Check the choice against the whitelist, then do a trial build.

    白名單檢查加試建。

    Deployment problems the trial build catches: the CLI cannot be found or the key is not set
    (``LLMServiceError``), the STT server is not set (``STTServiceError``), a third-party package the endpoint needs
    is missing or the wrong version (``ImportError``), the CLI working directory cannot be created (``OSError``). If
    the failed import is one of the project's own ``app.*`` modules (a wrong registry module name, a mistyped import
    inside a provider, a renamed provider), it is a programming error and is raised as usual, since installing a
    package will not fix it (see :func:`~app.infrastructure.llm.routing.is_internal_import`).

    試建會攔下的部署問題：CLI 找不到、金鑰沒設（``LLMServiceError``）、STT 伺服器沒設
    （``STTServiceError``）、端點需要的第三方套件沒裝或版本不符（``ImportError``）、CLI 工作目錄
    無法建立（``OSError``）。import 失敗的若是專案自己的 ``app.*`` 模組（註冊表模組名、provider 內
    import 名稱打錯、provider 改名）則是程式錯誤，照常拋出——裝套件解決不了（見
    ``app.infrastructure.llm.routing.is_internal_import``）。

    Args:
        choice: the choice to check
            要檢查的選擇
        capability: the capability
            能力
        route_key: the route key (``audio_eval`` uses it to check the allowed strategies; also used in the log)
            用途鍵（``audio_eval`` 據此檢查允許的策略，也用於 log）
        updated_by: who made the change (used only in the log)
            修改者（只用於 log）

    Raises:
        InvalidRouteError: if the choice is not on the whitelist, or the trial build fails (the message includes
            the reason)
            若不在白名單，或試建失敗（訊息含原因）
    """
    problem = routing.check_choice(choice, capability, route_key=route_key)
    if problem is not None:
        raise InvalidRouteError(problem)
    try:
        _trial_build(capability, choice)
    except (LLMServiceError, STTServiceError, AudioEvaluatorUnavailableError, ImportError, OSError) as error:
        if isinstance(error, ImportError) and is_internal_import(error):
            raise
        if isinstance(error, ImportError):
            reason = describe_import_error(choice.endpoint, error)
        elif isinstance(error, OSError):
            reason = f"無法建立 CLI 工作目錄或檔案（{error}）"
        else:
            reason = error.message
        noun = "客戶端" if capability == CAPABILITY_LLM else "工具"
        logger.warning(
            "用途 '%s' 的 %s 設定試建%s失敗（%s / %s / %s，%s）：%s",
            route_key, capability, noun, choice.endpoint, choice.model, choice.effort, updated_by, reason,
            exc_info=isinstance(error, (ImportError, OSError)),
        )
        raise InvalidRouteError(f"試建{noun}失敗：{reason}") from error


def _row_filter(route_key: str, capability: str) -> Any:
    """Return the query filter for the ``(route key, capability)`` row.

    回傳 ``(用途鍵, 能力)`` 這一列的查詢條件。
    """
    return (col(CapabilityRouteSetting.route_key) == route_key) & (col(CapabilityRouteSetting.capability) == capability)


async def _row_exists(route_key: str, capability: str) -> bool:
    """Return whether a row already exists for the route key and capability.

    回傳該用途鍵在該能力上是否已有一列。

    Raises ``DBAPIError``, ``sqlalchemy.exc.TimeoutError`` or ``OSError`` when the database fails.

    資料庫異常時拋 ``DBAPIError``、``sqlalchemy.exc.TimeoutError`` 或 ``OSError``。
    """
    async with _session_factory()() as session:
        result = await session.execute(
            select(CapabilityRouteSetting.route_key).where(_row_filter(route_key, capability))
        )
        return result.first() is not None


async def _upsert(route_key: str, capability: str, choice: RouteChoice, updated_by: str) -> None:
    """Update the row if it exists, otherwise insert one.

    有就更新、沒有就新增一列。

    It queries before writing, so it is not atomic: concurrent first writes may collide on the unique key.
    Strategies (an empty model) store a NULL model. Other database failures raise ``DBAPIError``,
    ``sqlalchemy.exc.TimeoutError`` or ``OSError``.

    先查再寫，不是原子操作：並發的第一次寫入可能撞唯一鍵。策略（模型為空）存成 NULL。其他資料庫異常時拋
    ``DBAPIError``、``sqlalchemy.exc.TimeoutError`` 或 ``OSError``。

    Raises:
        IntegrityError: if the row did not exist when queried but was created by another write before the commit
            若查的時候還沒有、提交時已被另一筆寫入建立
    """
    model = choice.model or None
    async with _session_factory()() as session:
        result = await session.execute(select(CapabilityRouteSetting).where(_row_filter(route_key, capability)))
        row = result.scalar_one_or_none()
        if row is None:
            session.add(CapabilityRouteSetting(
                route_key=route_key,
                capability=capability,
                provider=choice.endpoint,
                model=model,
                effort=choice.effort,
                updated_by=updated_by,
            ))
        else:
            row.provider, row.model, row.effort, row.updated_by = choice.endpoint, model, choice.effort, updated_by
        await session.commit()


async def set_route(
    route_key: str,
    endpoint: str,
    model: str | None,
    effort: str | None,
    *,
    updated_by: str,
    capability: str = CAPABILITY_LLM,
) -> RouteView:
    """Validate and write the choice for a route key and capability; it takes effect immediately.

    驗證並寫入一個用途鍵在一種能力上的選擇（有就更新、沒有就新增），寫入後立即生效。

    The row is updated if it exists and inserted otherwise. The returned state is built **before** writing, from
    the current settings with the value to write put in place, and the DB is not read again afterwards: after the
    commit only clearing the cache and logging remain, so once the write is committed it is always reported as
    successful and never called "operation failed" because reading back or building the state failed. While the
    global's switch is on, the returned state shows that the global is in effect (``resolved.forced``), so the
    caller can tell the user that the stored value does not take effect for now.

    回傳的狀態在寫入**之前**就組好：以目前的設定、換上要寫的值來解析，寫入後不再讀 DB。提交之後只剩清快取與
    記 log，寫入一旦提交就一定回報成功，不會因為讀回或組狀態失敗而被說成「操作失敗」。全域的開關開著時，
    回傳的狀態會顯示生效的是全域（``resolved.forced``），呼叫端可以提醒使用者存的值目前不生效。

    Args:
        route_key: the route key or a global key
            用途鍵或全域鍵
        endpoint: the provider (trimmed, case-insensitive): the endpoint ID for ``llm`` / ``audio_llm``,
            ``whisper`` for ``stt``, and the strategy name for ``audio_eval``
            provider（去空白、不分大小寫）：``llm`` / ``audio_llm`` 為端點 ID、``stt`` 為
            ``whisper``、``audio_eval`` 為策略名
        model: the model name (trimmed, case-sensitive); ``audio_eval`` takes no model (``None`` or an empty string)
            模型名（去空白、區分大小寫）；``audio_eval`` 不選模型（``None`` 或空字串）
        effort: the thinking depth (trimmed, case-insensitive); ``None`` when no effort is used, and an empty
            string is treated as ``None``
            思考深度（去空白、不分大小寫）；不使用 effort 時為 ``None``，空字串視同 ``None``
        updated_by: who made the change (``tg:<user id>`` or ``api``)
            修改者（``tg:<user id>`` 或 ``api``）
        capability: the capability
            能力

    Returns:
        the state after the write
        寫入後的狀態

    Raises:
        InvalidRouteError: if the capability is not supported, the route key does not declare it, the choice is not
            on the whitelist, or the trial build fails
            若為不支援的能力、用途鍵沒有宣告這個能力、不在白名單，或試建失敗
        RouteNotFoundError: if the route key is not declared (even if the DB has an orphan row: orphan keys can only
            be deleted)
            若用途鍵未宣告（DB 中有孤兒列也一樣：孤兒鍵只能刪除）
        RouteStoreUnavailableError: if the database fails while writing (outcome unknown; check again)
            若寫入時資料庫異常（結果未知，請重新查看）
    """
    entry = _declared_entry(route_key)
    if entry is None:
        raise RouteNotFoundError(f"用途鍵 '{route_key}' 未在程式中宣告，不能設定")
    _check_capability(capability)
    if capability not in entry.capabilities:
        raise InvalidRouteError(f"用途鍵 '{route_key}' 沒有宣告能力 '{capability}'，不能設定")
    choice = RouteChoice(endpoint.strip().lower(), (model or "").strip(), (effort or "").strip().lower() or None)
    validate_choice(choice, capability=capability, route_key=route_key, updated_by=updated_by)
    # 以目前的設定換上要寫的值來組狀態（看得出全域開關是否蓋過它）
    current, read_ok = await routing.stored_choices()
    current.setdefault(capability, {})[route_key] = choice
    view = _view(entry, current, read_ok, capability)
    if not read_ok:
        # 讀不到目前的設定：組出來的狀態只算了要寫的這一筆，看不出全局生效開關，要明說
        view = replace(view, problem=_SWITCH_UNKNOWN_MESSAGE)

    try:
        try:
            await _upsert(route_key, capability, choice, updated_by)
        except IntegrityError as first_error:
            # 同一用途鍵的第一次寫入同時發生（API 與 TG 同時、連按兩下）：查的時候還沒有、提交時
            # 另一筆剛插入而撞到唯一鍵。先確認那一列真的存在了才重做（會走「更新那一列」）；不存在
            # 就是別的約束（NOT NULL、schema 沒跑 migration…），原樣往外拋。重做或往外拋的 IntegrityError
            # 都是 DBAPIError，由外層 DATABASE_ERRORS 照一般資料庫錯誤處理（記 traceback、回 503）
            if not await _row_exists(route_key, capability):
                raise
            logger.info(
                "寫入 cfg_capability_routes 時 %s / %s 剛被另一筆寫入建立，改為更新該列（%s）：%s",
                route_key, capability, updated_by, first_error.orig,
            )
            await _upsert(route_key, capability, choice, updated_by)
    except DATABASE_ERRORS as error:
        logger.exception(
            "寫入 cfg_capability_routes 失敗：%s / %s → %s / %s（%s）",
            route_key, capability, choice.endpoint, choice.model, updated_by,
        )
        raise RouteStoreUnavailableError(_WRITE_FAILED_MESSAGE) from error
    finally:
        # 不論成敗都清：提交後才出錯（例如關閉 session 時被取消、非 DBAPI 的 SQLAlchemy 錯誤）時
        # DB 可能已寫入，不能留著舊快取讓生成一直用舊設定
        routing.invalidate_cache()
    logger.info(
        "能力路由設定已更新：%s / %s → %s / %s / %s（%s）",
        route_key, capability, choice.endpoint, choice.model, choice.effort, updated_by,
    )
    return view


@dataclass(frozen=True)
class DeleteResult:
    """The result of restoring the default.

    恢復預設的結果。

    A ``None`` ``view`` has two meanings, told apart by ``view_unavailable``: an orphan setting has no state after
    deletion anyway (``False``), or the deletion was committed but the state afterwards could not be built
    (``True``; the reason is in the backend log).

    ``view`` 為 ``None`` 有兩種意思，以 ``view_unavailable`` 區分：孤兒設定刪除後本來就沒有狀態
    （``False``），或刪除已提交、但組不出刪除後的狀態（``True``，原因在後端 log）。

    Attributes:
        deleted: whether a row was actually deleted (``False`` when there was no setting)
            是否真的刪了一列（原本沒有設定時為 ``False``）
        view: the state of this capability after deletion; ``None`` for an orphan setting or when the state cannot
            be built
            刪除後這個能力的狀態；孤兒設定、或組不出狀態時為 ``None``
        view_unavailable: whether the deletion was committed but the state afterwards could not be built
            刪除已提交，但組不出刪除後的狀態
    """

    deleted: bool
    view: RouteView | None
    view_unavailable: bool = False

    def __post_init__(self) -> None:
        if self.view_unavailable and self.view is not None:
            raise ValueError("view_unavailable 時不應有 view")


async def set_global_switch(global_key: str, enabled: bool, *, updated_by: str) -> KeyView:
    """Turn a global's "global switch" on or off; it takes effect immediately.

    打開或關掉全域的「全局生效」開關，立即生效。

    On stores a switch row for the global (``routing.SWITCH_CAPABILITY``); off deletes it. The settings of the
    individual routes are never touched, so turning the switch off brings them back. As with :func:`set_route`,
    the returned state is built **before** writing, so a committed write is never reported as failed because
    reading back failed.

    打開時存一列該全域的開關（``routing.SWITCH_CAPABILITY``）；關掉時刪除那一列。各用途自己的設定
    完全不動，所以關掉開關後就回到各自的設定。和 ``set_route`` 一樣，回傳的狀態在寫入**之前**就組好，
    寫入一旦提交，不會因為讀回失敗而被說成失敗。

    Args:
        global_key: ``*`` or ``*voice``
            ``*`` 或 ``*voice``
        enabled: ``True`` to turn it on, ``False`` to turn it off
            ``True`` 為打開，``False`` 為關掉
        updated_by: who made the change (``tg:<user id>`` or ``api``)
            修改者（``tg:<user id>`` 或 ``api``）

    Returns:
        the global's state after the change
        改完之後該全域的狀態

    Raises:
        RouteNotFoundError: if ``global_key`` is not a global key
            若 ``global_key`` 不是全域鍵
        RouteStoreUnavailableError: if the database fails while writing (outcome unknown; check again)
            若寫入時資料庫異常（結果未知，請重新查看）
    """
    if global_key not in GLOBAL_ROUTE_KEYS:
        raise RouteNotFoundError(f"'{global_key}' 不是全域，沒有全局生效開關")
    switch = RouteChoice(SWITCH_ON, "", None)
    current, read_ok = await routing.stored_choices()
    rows = current.setdefault(SWITCH_CAPABILITY, {})
    if enabled:
        rows[global_key] = switch
    else:
        rows.pop(global_key, None)
    view = _key_view(_global_entry(global_key), current, read_ok)
    if not read_ok:
        # 讀不到目前的設定：開關的狀態就是要寫的值，但各能力只看得到 .env，要明說
        view = replace(
            view,
            switch=enabled,
            views=tuple(replace(item, problem=item.problem or _STORE_UNAVAILABLE_MESSAGE) for item in view.views),
        )
    changed = True
    try:
        if enabled:
            try:
                await _upsert(global_key, SWITCH_CAPABILITY, switch, updated_by)
            except IntegrityError as first_error:
                # 同時打開兩次：另一筆剛插入。那一列已存在，改走更新
                if not await _row_exists(global_key, SWITCH_CAPABILITY):
                    raise
                logger.info(
                    "全局生效開關 %s 剛被另一筆寫入建立，改為更新該列（%s）：%s", global_key, updated_by, first_error.orig
                )
                await _upsert(global_key, SWITCH_CAPABILITY, switch, updated_by)
        else:
            async with _session_factory()() as session:
                result = cast(
                    CursorResult[Any],
                    await session.execute(
                        delete(CapabilityRouteSetting).where(_row_filter(global_key, SWITCH_CAPABILITY))
                    ),
                )
                await session.commit()
            changed = bool(result.rowcount)
    except DATABASE_ERRORS as error:
        logger.exception("寫入 cfg_capability_routes 的全局生效開關失敗：%s → %s（%s）", global_key, enabled, updated_by)
        raise RouteStoreUnavailableError(_WRITE_FAILED_MESSAGE) from error
    finally:
        routing.invalidate_cache()  # 理由同 set_route
    logger.info(
        "全局生效開關：%s → %s（%s%s）", global_key, "開" if enabled else "關", updated_by, "" if changed else "，原本就是關"
    )
    return view


async def delete_route(route_key: str, *, updated_by: str, capability: str = CAPABILITY_LLM) -> DeleteResult:
    """Delete a route key's setting for one capability (restore the default: fall back to the layer above).

    刪除一個用途鍵在一種能力上的設定（恢復預設：退回上一層）。

    Orphan settings can be deleted too (for cleanup). The state after deletion requires reading the DB again (to
    know which layer it fell back to); if that read fails, no error is raised (the deletion was committed) and the
    state is marked ``db_unavailable``.

    孤兒設定也可以刪（清理用）。刪除後的狀態要再讀一次 DB（才知道退回到哪一層）；那次讀不到時不拋錯（刪除已提交），
    狀態標上 ``db_unavailable``。

    Args:
        route_key: the route key or a global key
            用途鍵或全域鍵
        updated_by: who did it (written only to the log, since the row is deleted)
            操作者（只寫進 log，該列已刪除）
        capability: the capability
            能力

    Returns:
        the deletion result
        刪除結果

    Raises:
        InvalidRouteError: if the capability is not supported
            若為不支援的能力
        RouteNotFoundError: if the route key is neither declared nor present in the DB
            若用途鍵既未宣告、DB 中也沒有
        RouteStoreUnavailableError: if the DB cannot be read (so it cannot tell whether an orphan key exists), or
            the database fails while deleting (outcome unknown; check again)
            若讀不到 DB（無法判斷孤兒鍵是否存在），或刪除時資料庫異常（結果未知，請重新查看）
    """
    _check_capability(capability)
    entry = _declared_entry(route_key)
    if entry is None:
        stored = await _strict_snapshot()
        if route_key not in _stored_keys(stored):
            raise RouteNotFoundError(f"用途鍵 '{route_key}' 不存在")

    try:
        async with _session_factory()() as session:
            # aiosqlite / aiomysql 的 DELETE 都回報實際刪除的列數（不會是 -1）
            result = cast(
                CursorResult[Any],
                await session.execute(delete(CapabilityRouteSetting).where(_row_filter(route_key, capability))),
            )
            await session.commit()
    except DATABASE_ERRORS as error:
        logger.exception("刪除 cfg_capability_routes 失敗：%s / %s（%s）", route_key, capability, updated_by)
        raise RouteStoreUnavailableError(_WRITE_FAILED_MESSAGE) from error
    finally:
        routing.invalidate_cache()  # 理由同 set_route：提交後才出錯時 DB 可能已刪除
    deleted = bool(result.rowcount)
    if deleted:
        logger.info("能力路由設定已刪除（恢復預設）：%s / %s（%s）", route_key, capability, updated_by)
    if entry is None or capability not in entry.capabilities:
        # 孤兒設定（鍵或能力已不在程式中）刪除後就從清單消失，沒有「預設」可言
        return DeleteResult(deleted, None)
    try:
        stored_after, read_ok = await routing.stored_choices()
        view = _view(entry, stored_after, read_ok, capability)
    except Exception:  # noqa: BLE001 - 刪除已提交：組不出刪除後狀態時只少回報狀態，不報成失敗
        logger.exception("cfg_capability_routes 已刪除 %s / %s，但組出刪除後的狀態失敗", route_key, capability)
        return DeleteResult(deleted, None, view_unavailable=True)
    return DeleteResult(deleted, view)


def _choice_read(choice: RouteChoice) -> ChoiceRead:
    return ChoiceRead(provider=choice.endpoint, model=choice.model or None, effort=choice.effort)


def _state_read(view: RouteView) -> CapabilityStateRead:
    """Convert the state of one capability into the response model.

    把一個能力的狀態轉成回應模型。
    """
    resolved = view.resolved
    return CapabilityStateRead(
        source=resolved.source,
        global_key=resolved.global_key,
        forced=resolved.forced,
        effective=ChoiceRead(provider=resolved.endpoint, model=resolved.model or None, effort=view.effective_effort),
        label=view.label,
        stored=_choice_read(view.stored) if view.stored is not None else None,
        rejected=[RejectedLayerRead(route_key=layer.route_key, reason=layer.reason) for layer in resolved.rejected],
        db_unavailable=resolved.db_unavailable,
        problem=view.problem,
        declared=view.capability_declared,
    )


def to_read(view: KeyView | RouteView) -> RouteRead:
    """Convert a service-layer state into the external response model (shared by the API and diagnostics endpoint).

    把服務層的狀態轉成對外的回應模型（API 與診斷端點共用）。

    A ``KeyView`` lists every capability of the key; a single-capability ``RouteView`` (the result of a write or a
    deletion) lists only that one.

    ``KeyView`` 列出這個鍵的所有能力；單一能力的 ``RouteView``（寫入或刪除的結果）只列那一個。

    Args:
        view: the state to convert
            要轉換的狀態

    Returns:
        the response model
        回應模型
    """
    views = view.views if isinstance(view, KeyView) else (view,)
    return RouteRead(
        route_key=view.route_key,
        description=view.description,
        declared=view.declared,
        capabilities={item.capability: _state_read(item) for item in views},
        switch=view.switch if isinstance(view, KeyView) else None,
    )


async def refresh_global_client(app_state: Any) -> None:
    """Rebuild ``app_state.llm_client`` from "global ``*`` (DB) → ``.env``" (model routing plan D9).

    依「全域 ``*``（DB）→ ``.env``」重建 ``app_state.llm_client``（模型路由計畫 D9）。

    Called at startup and after writing or deleting the global ``*``, whether or not the write succeeded (when
    the outcome is unknown, the DB may have changed). This client is used only by the diagnostics endpoint (label,
    CLI probe); each route still builds its own client from its route key on every generation. The diagnostics
    endpoint also checks whether it matches the global setting (``llm_client_matches_global``), for example when the
    DB could not be read at startup so ``.env`` was used, and the DB recovered later.

    A build failure is not raised (as at startup, other features are unaffected); the client is set to ``None``.
    Configuration problems (``LLMServiceError``, for example the key is not set or the CLI cannot be found) are
    logged as WARNING; other exceptions may be programming or environment errors and are logged as ERROR with a
    traceback. For DB-layer setting or package problems, ``client_for`` first logs a separate ERROR.

    啟動時、以及寫入或刪除全域 ``*`` 之後呼叫——不論寫入成敗（結果未知時 DB 可能已改）。
    這個客戶端只給診斷端點用（標籤、CLI 探測）；各用途生成時仍每次依自己的用途鍵建立。
    診斷端點另會比對它與全域設定是否一致（``llm_client_matches_global``），例如啟動時讀不到
    DB 而用了 ``.env``、之後 DB 恢復的情況。

    建立失敗不拋出（與啟動時相同，其他功能不受影響），設為 None：設定問題
    （``LLMServiceError``，例如金鑰沒設、找不到 CLI）記 WARNING；其他例外可能是程式或環境
    錯誤，記 ERROR 附 traceback。DB 層設定或套件問題時 ``client_for`` 會先另記一次 ERROR。

    Args:
        app_state: the application state object whose ``llm_client`` attribute is replaced
            要替換 ``llm_client`` 屬性的應用程式狀態物件
    """
    try:
        resolved = await routing.resolve(GLOBAL_ROUTE_KEY)
        client = await routing.client_for(GLOBAL_ROUTE_KEY)
        label = client.model_label
    except LLMServiceError as error:
        logger.warning(
            "全域 LLM 客戶端建立失敗（設定問題；診斷端點將顯示沒有客戶端，各用途的生成不受影響）：%s", error
        )
        app_state.llm_client = None
        return
    except Exception:  # noqa: BLE001 - 不中斷啟動或寫入，但這可能是程式或環境錯誤
        logger.exception("全域 LLM 客戶端建立時發生非預期錯誤（可能是程式或環境錯誤；診斷端點將顯示沒有客戶端）")
        app_state.llm_client = None
        return
    app_state.llm_client = client
    logger.info(
        "全域 LLM 客戶端已建立：%s（%s，來源：%s%s）",
        label, type(client).__name__, resolved.source, "，讀不到 DB、暫用 .env" if resolved.db_unavailable else "",
    )
