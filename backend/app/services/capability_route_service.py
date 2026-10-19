"""
能力路由設定的讀寫服務（能力路由計畫 capability_routing_FEAT_2026-10-19.md；前身為模型路由計畫
llm_model_routing_FEAT_2026-10-15.md D6）。

API（``/api/v1/capability-routes``）與 TG ``/setconfig`` 選單共用這一層：列出每個用途各能力目前
生效的設定、寫入一組選擇、恢復預設。每個操作都針對「用途鍵 × 能力」（能力見
:mod:`app.infrastructure.llm.capabilities`）。寫入前依序檢查：

1. 用途鍵已宣告（或為全域 ``*``），且宣告了這個能力
2. ``(provider, 模型, effort)`` 在白名單內（``audio_eval`` 另檢查用途鍵允許的策略）——與讀取端
   同一個 :func:`~app.infrastructure.llm.routing.check_choice`
3. 試建一次：``llm`` 建客戶端（CLI 找不找得到、金鑰有沒有設定、套件有沒有裝）、``audio_llm``
   建聽音檔的評分器、``stt`` 建 Whisper 客戶端；``audio_eval`` 只檢查策略名。不打 API（模型名本身
   是否存在仍要實際呼叫才知道），但 CLI 類會建立工作目錄

寫入成功後清空路由快取，下一次呼叫即生效。

錯誤分三種（都是 :class:`~app.core.exceptions.FluencyTidesError`，API 由全域 handler
轉成對應狀態碼）：

- :class:`~app.core.exceptions.RouteNotFoundError`（404）：用途鍵不存在（寫入時：未宣告）
- :class:`~app.core.exceptions.InvalidRouteError`（422）：設定不合格，或用途鍵沒有宣告這個能力（呼叫端可修正）
- :class:`~app.core.exceptions.RouteStoreUnavailableError`（503）：讀不到 DB，或寫入／刪除時
  資料庫異常（結果未知）

**讀取不沿用生成路徑的退回**：路由層讀不到 DB 時會回「沒有設定」好讓生成不中斷，但
管理介面若照單全收，就會把「讀不到」說成「不存在」。所以需要知道某個鍵有沒有存值的
操作（查單一用途、刪除孤兒鍵）在讀不到時拋 503（並記下原因）；列出全部則照常回傳、每列
標上 ``db_unavailable``。列出與查詢時每個請求只讀一次 DB，所有列都由同一份快照解析。
寫入時的資料庫錯誤同樣轉成 503（結果未知，提示重新查看）。

**清單不因單列壞掉而打不開**：算 ``llm`` 的標籤需要載入端點的客戶端類別；端點需要的第三方套件沒裝
或版本不符時，該列改用不去前綴的標籤並在 ``problem`` 說明，其餘照常列出——管理員正需要這個介面把
設定改到能用的端點。``.env`` 那一層的設定有誤（端點被跳過、金鑰或 STT 伺服器沒設、策略名無效）
同樣標在 ``problem``。

English summary:
    Read/write service for capability route settings, shared by the HTTP API
    and the Telegram ``/setconfig`` menu. Every operation targets a route key
    and a capability. Writes are validated against declared keys and
    capabilities, the whitelist and a trial build. Reads use one snapshot
    per request and never mistake an unreadable database for "not found".
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
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
    ResolvedRoute,
    RouteChoice,
    Snapshot,
    describe_import_error,
    is_internal_import,
)
from app.schemas.capability_route import CapabilityStateRead, ChoiceRead, RejectedLayerRead, RouteRead

logger = logging.getLogger(__name__)

# 全域設定在清單中的說明
GLOBAL_DESCRIPTION = "全域預設（各用途沒有設定時使用）"

_STORE_UNAVAILABLE_MESSAGE = (
    "讀不到能力路由設定（capability_routes），無法判斷這個用途的設定；詳見後端 log 的 WARNING，DB 恢復後再試。"
)
_WRITE_FAILED_MESSAGE = (
    "寫入能力路由設定（capability_routes）時資料庫異常，結果未知；請重新查看設定後再試，詳見後端 log。"
)


class RouteKeyEntry(NamedTuple):
    """清單中的一個用途鍵。One route key in display order.

    Attributes:
        key: 用途鍵；``*`` 為全域。The route key.
        description: 說明；孤兒鍵為 None。Description, None for orphans.
        declared: 是否為程式中宣告的鍵（或全域）；False＝DB 裡的孤兒列。
            False for orphan rows whose key is no longer declared.
        capabilities: 這個鍵可設定的能力：宣告的鍵為它宣告的能力，全域為所有能力，孤兒鍵為空。
            The capabilities that may be set for the key; empty for orphans.
    """

    key: str
    description: str | None
    declared: bool
    capabilities: frozenset[str] = frozenset()


@dataclass(frozen=True)
class RouteView:
    """一個用途鍵在一種能力上的完整狀態（給 API 與 TG 顯示）。One key's state for one capability.

    Attributes:
        entry: 用途鍵與說明。The key and its description.
        resolved: 實際生效的設定（含能力）。The effective setting.
        effective_effort: 實際生效的 effort。``llm`` 的 ``.env`` 那一層為 ``.env`` 的預設值
            （CLI 類），不是 None——與標籤一致。The effort actually used.
        label: 生效設定的顯示標籤（``llm`` 為寫進卡片的標籤）。The display label.
        stored: DB 中這個鍵在這個能力上存的選擇（可能已不在白名單）。The row stored
            for this key and capability, if any.
        problem: 生效設定目前無法使用的原因（端點需要的套件未安裝或版本不符、``.env`` 那一層的
            設定有誤）；有問題時 ``label`` 與 ``effective_effort`` 可能與實際不符。None 只表示
            沒有偵測到這些問題，不代表已驗證可用。Why the effective setting cannot be
            used right now; None does not mean it was verified.
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
        """用途鍵。The route key."""
        return self.entry.key

    @property
    def description(self) -> str | None:
        """說明。The description."""
        return self.entry.description

    @property
    def declared(self) -> bool:
        """用途鍵是否已宣告。Whether the key is declared."""
        return self.entry.declared

    @property
    def capability(self) -> str:
        """能力。The capability."""
        return self.resolved.capability

    @property
    def capability_declared(self) -> bool:
        """這個鍵是否宣告了這個能力；False＝孤兒設定（鍵或能力已不在程式中），只能刪除。

        Whether the key declares this capability; False rows can only be deleted.
        """
        return self.entry.declared and self.capability in self.entry.capabilities


@dataclass(frozen=True)
class KeyView:
    """一個用途鍵與它各能力的狀態。One route key and its per-capability states.

    Attributes:
        entry: 用途鍵與說明。The key.
        views: 各能力的狀態：宣告的能力依顯示順序，之後是 DB 中存了值、但這個鍵沒有宣告的能力。
            The states, declared capabilities first, then orphan ones.
    """

    entry: RouteKeyEntry
    views: tuple[RouteView, ...]

    @property
    def route_key(self) -> str:
        """用途鍵。The route key."""
        return self.entry.key

    @property
    def description(self) -> str | None:
        """說明。The description."""
        return self.entry.description

    @property
    def declared(self) -> bool:
        """用途鍵是否已宣告。Whether the key is declared."""
        return self.entry.declared

    @property
    def capabilities(self) -> tuple[str, ...]:
        """列出的能力（與 ``views`` 同順序）。The listed capabilities."""
        return tuple(view.capability for view in self.views)

    def view(self, capability: str) -> RouteView | None:
        """某個能力的狀態；沒有列出時為 None。The state of one capability."""
        return next((view for view in self.views if view.capability == capability), None)


def _session_factory() -> Callable[[], AsyncSession]:
    """主資料庫的 session 工廠。The main database's session factory.

    每次呼叫才讀 ``database.async_session_factory``（延遲 import，也不在 import 時綁定），
    所以測試 patch 該模組屬性即可同時影響路由層的讀取與本服務的寫入。

    Read on every call, so patching the database module attribute affects
    both the resolver's reads and this service's writes.
    """
    from app.infrastructure.database.database import async_session_factory

    return async_session_factory


def _client_class_for(endpoint_id: str) -> Any:
    """端點對應的客戶端類別；端點不存在時以 ID 當 provider 查。

    只有 ``.env`` 那一層會遇到端點不存在：``LLM_PROVIDER`` 指向的自訂端點因設定有誤被
    跳過（見 :func:`_env_endpoint_problem`）。DB 層的選擇已通過 ``check_choice``，端點一定存在。

    The client class for an endpoint; falls back to treating the ID as a
    provider value, which only happens on the ``.env`` layer when its custom
    endpoint was skipped as invalid.

    Raises:
        ImportError: 客戶端模組 import 失敗（套件未安裝或版本不符）。
    """
    endpoint = get_endpoint(endpoint_id)
    return factory.resolve_client_class(endpoint.type if endpoint is not None else endpoint_id)


def effective_effort(endpoint_id: str, effort: str | None, *, env_layer: bool) -> str | None:
    """``llm`` 實際會用的 effort。The effort an ``llm`` setting actually uses.

    effort 為 None 的意思依層而不同：DB 的選擇裡 None＝「此模型不使用 effort」（客戶端收到
    ``OMIT_EFFORT``）；``.env`` 那一層的 None＝「沿用 ``.env`` 預設」（CLI 類帶 ``LLM_*_EFFORT``）。

    Args:
        endpoint_id: 端點 ID。Endpoint ID.
        effort: effort 值或 None。The effort, or None.
        env_layer: 是否為 ``.env`` 那一層。Whether this is the ``.env`` layer.
    """
    if env_layer and effort is None:
        return _client_class_for(endpoint_id).default_effort()
    return effort


def preview_label(
    endpoint_id: str, model: str, effort: str | None, *, env_layer: bool = False, capability: str = CAPABILITY_LLM
) -> str:
    """不建立客戶端，算出這組設定的顯示標籤。Compute a setting's label without building a client.

    ``llm`` 的標籤與客戶端的 ``model_label`` 規則相同（去前綴與預設 effort 取自客戶端類別本身），
    就是寫進卡片的標籤；``audio_eval`` 為策略名；其他能力為 ``(provider)模型``。

    Args:
        endpoint_id: provider。The provider.
        model: 模型名。Model name.
        effort: effort 值或 None（意思見 :func:`effective_effort`）。The effort.
        env_layer: 是否為 ``.env`` 那一層。Whether this is the ``.env`` layer.
        capability: 能力。The capability.

    Returns:
        標籤，例如 ``(yinli)gemini-3.5-flash``、``(claude-code)opus-5@high``、``listen``。The label.
    """
    if capability == CAPABILITY_AUDIO_EVAL:
        return endpoint_id
    if capability != CAPABILITY_LLM:
        return build_model_label(endpoint_id, model, None)
    shown_effort = effective_effort(endpoint_id, effort, env_layer=env_layer)
    strip_prefix = _client_class_for(endpoint_id).MODEL_LABEL_STRIP_PREFIX
    return build_model_label(endpoint_id, model, shown_effort, strip_prefix=strip_prefix)


def _stored_keys(stored: Snapshot) -> set[str]:
    """DB 中存了任一能力的用途鍵。Keys with a row for any capability."""
    return {key for rows in stored.values() for key in rows}


def ordered_route_keys(stored: Snapshot) -> list[RouteKeyEntry]:
    """顯示順序：全域 ``*``、已宣告的鍵（依鍵排序，分支緊跟在流程鍵之後）、DB 中的孤兒鍵（依鍵排序）。

    Display order: ``*``, declared keys, then orphan keys found in the DB.
    """
    declared = {route.key: route for route in routing.list_route_keys()}
    entries = [RouteKeyEntry(GLOBAL_ROUTE_KEY, GLOBAL_DESCRIPTION, True, CAPABILITIES)]
    entries += [
        RouteKeyEntry(key, declared[key].description, True, declared[key].capabilities) for key in sorted(declared)
    ]
    entries += [
        RouteKeyEntry(key, None, False)
        for key in sorted(_stored_keys(stored))
        if key != GLOBAL_ROUTE_KEY and key not in declared
    ]
    return entries


def key_capabilities(entry: RouteKeyEntry, stored: Snapshot) -> tuple[str, ...]:
    """一個鍵要列出的能力：宣告的能力（依顯示順序），再加 DB 中存了值、但沒有宣告的能力。

    The capabilities listed for a key: declared ones in display order, then
    orphan rows stored for capabilities the key does not declare.
    """
    declared = [capability for capability in CAPABILITY_ORDER if capability in entry.capabilities]
    stored_caps = sorted(
        capability for capability, rows in stored.items() if entry.key in rows and capability not in entry.capabilities
    )
    return tuple(declared + stored_caps)


def _env_endpoint_problem() -> str | None:
    """``llm`` 的 ``.env`` 那一層的端點設定有誤、已被跳過時的說明；沒問題時為 None。

    ``LLM_PROVIDER`` 指向 ``LLM_ENDPOINTS`` 中寫錯的端點時，啟動時記過 ERROR，但這一層的
    每次生成都會失敗（工廠報「端點不存在」）。不標出來的話，清單會把它顯示成正常的預設。

    Why the ``.env`` layer's custom endpoint was skipped, or None.
    """
    if not env_endpoint_is_custom() or get_endpoint() is not None:
        return None
    endpoint_id = env_endpoint_id()
    reason = skip_reason(endpoint_id) or "設定有誤"
    return f".env 的 LLM_PROVIDER='{endpoint_id}' 已被跳過（{reason}），使用這一層的生成會失敗"


def _env_choice_problem(resolved: ResolvedRoute) -> str | None:
    """``.env`` 那一層的預設目前無法使用的原因；沒偵測到問題時為 None。

    Why the ``.env`` layer's default cannot be used right now, or None.
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
    """生效設定的 ``(effort, 標籤, 問題)``；``llm`` 端點需要的第三方套件沒裝或版本不符時退化並說明，
    不讓整份清單失敗。退化時標籤不去前綴，``.env`` 那一層的 effort 也取不到預設（為 None）。

    The effective effort, label and problem of a resolved route; a missing
    or incompatible third-party package degrades this one row instead of
    failing the list.

    Raises:
        ImportError: import 失敗的是專案自己的模組（程式錯誤，照常拋出）。
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
    """以一份快照組出一個用途在一種能力上的狀態。Build one key's state for one capability."""
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
    """以一份快照組出一個用途鍵各能力的狀態。Build one key's per-capability states."""
    return KeyView(
        entry, tuple(_view(entry, stored, read_ok, capability) for capability in key_capabilities(entry, stored))
    )


async def _strict_snapshot() -> Snapshot:
    """讀出全部設定；讀不到時記下原因並拋 503，不當成「沒有設定」。

    Read every stored choice, refusing to treat an unreadable DB as empty;
    the underlying error is logged with its traceback each time (management
    calls are rare, so this does not flood the log).

    Raises:
        RouteStoreUnavailableError: 讀不到 DB 時。
    """
    try:
        return await routing.stored_choices_strict()
    except DATABASE_ERRORS as error:
        logger.warning("管理操作讀取 capability_routes 失敗：%s: %s", type(error).__name__, error, exc_info=True)
        raise RouteStoreUnavailableError(_STORE_UNAVAILABLE_MESSAGE) from error


def views_from(stored: Snapshot, read_ok: bool) -> list[KeyView]:
    """以一份已讀出的快照組出全部用途鍵的狀態（依顯示順序）。All key views from one snapshot.

    TG 選單用它讓「選單版本」與「清單內容」來自同一次讀取。Lets the menu derive its
    version and its rows from the same read.
    """
    return [_key_view(entry, stored, read_ok) for entry in ordered_route_keys(stored)]


async def list_routes() -> tuple[list[KeyView], bool]:
    """全部用途鍵（含全域與孤兒鍵）各能力目前的狀態，依顯示順序。All route keys' states.

    讀不到 DB 時仍回傳（各列為 ``.env`` 預設、標上 ``db_unavailable``；孤兒鍵看不到），
    由呼叫端顯示警示。

    Returns:
        ``(各用途鍵狀態, DB 是否讀取成功)``。``(key views, read_ok)``.
    """
    stored, read_ok = await routing.stored_choices()
    return views_from(stored, read_ok), read_ok


async def describe(route_key: str) -> KeyView:
    """單一用途鍵各能力的狀態。The state of one route key.

    Raises:
        RouteNotFoundError: 用途鍵既未宣告、DB 中也沒有。
        RouteStoreUnavailableError: 讀不到 DB（無法判斷孤兒鍵是否存在、本身存了什麼）。
    """
    stored = await _strict_snapshot()
    for entry in ordered_route_keys(stored):
        if entry.key == route_key:
            return _key_view(entry, stored, True)
    raise RouteNotFoundError(f"用途鍵 '{route_key}' 不存在")


def _declared_entry(route_key: str) -> RouteKeyEntry | None:
    """已宣告的鍵（或全域）的清單項目；未宣告時為 None。The entry for a declared key."""
    if route_key == GLOBAL_ROUTE_KEY:
        return RouteKeyEntry(GLOBAL_ROUTE_KEY, GLOBAL_DESCRIPTION, True, CAPABILITIES)
    declared = routing.get_route(route_key)
    if declared is None:
        return None
    return RouteKeyEntry(declared.key, declared.description, True, declared.capabilities)


def _check_capability(capability: str) -> None:
    """能力必須是程式支援的。The capability must be a known one.

    Raises:
        InvalidRouteError: 不支援的能力。Unknown capability.
    """
    if capability not in CAPABILITIES:
        raise InvalidRouteError(f"不支援的能力 '{capability}'（可用：{', '.join(sorted(CAPABILITIES))}）")


def _trial_build(capability: str, choice: RouteChoice) -> None:
    """依能力試建一次（不打 API）。Build the tool once without calling any API.

    Raises:
        LLMServiceError / STTServiceError / AudioEvaluatorUnavailableError / ImportError / OSError:
            建不出來時。When it cannot be built.
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
    """白名單檢查加試建。Whitelist check plus a trial build.

    試建會攔下的部署問題：CLI 找不到、金鑰沒設（``LLMServiceError``）、STT 伺服器沒設
    （``STTServiceError``）、端點需要的第三方套件沒裝或版本不符（``ImportError``）、CLI 工作目錄
    無法建立（``OSError``）。import 失敗的若是專案自己的 ``app.*`` 模組（註冊表模組名、provider 內
    import 名稱打錯、provider 改名）則是程式錯誤，照常拋出——裝套件解決不了（見
    :func:`~app.infrastructure.llm.routing.is_internal_import`）。

    Args:
        choice: 要檢查的選擇。The choice.
        capability: 能力。The capability.
        route_key: 用途鍵（``audio_eval`` 據此檢查允許的策略，也用於 log）。Route key.
        updated_by: 修改者（只用於 log）。Who asked, for logging.

    Raises:
        InvalidRouteError: 不在白名單，或試建失敗（訊息含原因）。
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
    """``(用途鍵, 能力)`` 這一列的查詢條件。The filter for one key and capability."""
    return (col(CapabilityRouteSetting.route_key) == route_key) & (col(CapabilityRouteSetting.capability) == capability)


async def _row_exists(route_key: str, capability: str) -> bool:
    """該用途鍵在該能力上是否已有一列。Whether a row exists for the key and capability.

    Raises:
        DBAPIError / sqlalchemy.exc.TimeoutError / OSError: 資料庫異常。
    """
    async with _session_factory()() as session:
        result = await session.execute(
            select(CapabilityRouteSetting.route_key).where(_row_filter(route_key, capability))
        )
        return result.first() is not None


async def _upsert(route_key: str, capability: str, choice: RouteChoice, updated_by: str) -> None:
    """有就更新、沒有就新增一列（先查再寫，不是原子操作：並發的第一次寫入可能撞唯一鍵）。

    Update the row if present, insert it otherwise. Strategies store a NULL model.

    Raises:
        IntegrityError: 查的時候還沒有、提交時已被另一筆寫入建立。
        DBAPIError / sqlalchemy.exc.TimeoutError / OSError: 其他資料庫異常。
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
    """驗證並寫入一個用途鍵在一種能力上的選擇（有就更新、沒有就新增），寫入後立即生效。

    Validate and store the choice for a route key and capability (update or
    insert), then invalidate the routing cache so the next call uses it.

    回傳的狀態在寫入**之前**就由要寫的值組好、不再讀 DB：提交之後只剩清快取與記 log，
    寫入一旦提交就一定回報成功，不會因為讀回或組狀態失敗而被說成「操作失敗」。用途層的
    生效值就是要寫的值（已通過白名單）。

    The returned state is built from the value before writing and never
    re-read, so nothing that can fail runs after the commit.

    Args:
        route_key: 用途鍵或 ``*``。Route key or ``*``.
        endpoint: provider（去空白、不分大小寫）：``llm`` / ``audio_llm`` 為端點 ID、``stt`` 為
            ``whisper``、``audio_eval`` 為策略名。The provider, case-insensitive.
        model: 模型名（去空白、區分大小寫）；``audio_eval`` 不選模型（None 或空字串）。
            Model name, case-sensitive; empty for strategies.
        effort: 思考深度（去空白、不分大小寫）；不使用 effort 時為 None，空字串視同 None。
            Effort, case-insensitive; None or "" when the model takes none.
        updated_by: 修改者（``tg:<user id>`` 或 ``api``）。Who made the change.
        capability: 能力。The capability.

    Returns:
        寫入後的狀態。The state after the write.

    Raises:
        RouteNotFoundError: 用途鍵未宣告（DB 中有孤兒列也一樣：孤兒鍵只能刪除）。
        InvalidRouteError: 不支援的能力、用途鍵沒有宣告這個能力、不在白名單，或試建失敗。
        RouteStoreUnavailableError: 寫入時資料庫異常（結果未知，請重新查看）。
    """
    entry = _declared_entry(route_key)
    if entry is None:
        raise RouteNotFoundError(f"用途鍵 '{route_key}' 未在程式中宣告，不能設定")
    _check_capability(capability)
    if capability not in entry.capabilities:
        raise InvalidRouteError(f"用途鍵 '{route_key}' 沒有宣告能力 '{capability}'，不能設定")
    choice = RouteChoice(endpoint.strip().lower(), (model or "").strip(), (effort or "").strip().lower() or None)
    validate_choice(choice, capability=capability, route_key=route_key, updated_by=updated_by)
    source: routing.RouteSource = "global" if route_key == GLOBAL_ROUTE_KEY else "route"
    view = RouteView(
        entry=entry,
        resolved=ResolvedRoute(route_key, source, choice, capability=capability),
        effective_effort=choice.effort,
        label=preview_label(choice.endpoint, choice.model, choice.effort, capability=capability),
        stored=choice,
    )

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
                "寫入 capability_routes 時 %s / %s 剛被另一筆寫入建立，改為更新該列（%s）：%s",
                route_key, capability, updated_by, first_error.orig,
            )
            await _upsert(route_key, capability, choice, updated_by)
    except DATABASE_ERRORS as error:
        logger.exception(
            "寫入 capability_routes 失敗：%s / %s → %s / %s（%s）",
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
    """恢復預設的結果。The result of restoring the default.

    ``view`` 為 None 有兩種意思，以 ``view_unavailable`` 區分：孤兒設定刪除後本來就沒有狀態
    （False），或刪除已提交、但組不出刪除後的狀態（True，原因在後端 log）。

    Attributes:
        deleted: 是否真的刪了一列（原本沒有設定時為 False）。Whether a row was removed.
        view: 刪除後這個能力的狀態；孤兒設定、或組不出狀態時為 None。The state afterwards.
        view_unavailable: 刪除已提交，但組不出刪除後的狀態。True when the delete
            committed but the state afterwards could not be built.
    """

    deleted: bool
    view: RouteView | None
    view_unavailable: bool = False

    def __post_init__(self) -> None:
        if self.view_unavailable and self.view is not None:
            raise ValueError("view_unavailable 時不應有 view")


async def delete_route(route_key: str, *, updated_by: str, capability: str = CAPABILITY_LLM) -> DeleteResult:
    """刪除一個用途鍵在一種能力上的設定（恢復預設：退回上一層）。孤兒設定也可以刪（清理用）。

    Delete a route key's row for one capability so it falls back to the next
    layer; orphan rows may be deleted too.

    刪除後的狀態要再讀一次 DB（才知道退回到哪一層）；那次讀不到時不拋錯（刪除已提交），
    狀態標上 ``db_unavailable``。

    Args:
        route_key: 用途鍵或 ``*``。Route key or ``*``.
        updated_by: 操作者（只寫進 log，該列已刪除）。Who did it; logged only.
        capability: 能力。The capability.

    Raises:
        RouteNotFoundError: 用途鍵既未宣告、DB 中也沒有。
        InvalidRouteError: 不支援的能力。Unknown capability.
        RouteStoreUnavailableError: 讀不到 DB（無法判斷孤兒鍵是否存在），或刪除時資料庫
            異常（結果未知，請重新查看）。
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
        logger.exception("刪除 capability_routes 失敗：%s / %s（%s）", route_key, capability, updated_by)
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
        logger.exception("capability_routes 已刪除 %s / %s，但組出刪除後的狀態失敗", route_key, capability)
        return DeleteResult(deleted, None, view_unavailable=True)
    return DeleteResult(deleted, view)


def _choice_read(choice: RouteChoice) -> ChoiceRead:
    return ChoiceRead(provider=choice.endpoint, model=choice.model or None, effort=choice.effort)


def _state_read(view: RouteView) -> CapabilityStateRead:
    """一個能力的狀態轉成回應模型。Convert one capability's state."""
    resolved = view.resolved
    return CapabilityStateRead(
        source=resolved.source,
        effective=ChoiceRead(provider=resolved.endpoint, model=resolved.model or None, effort=view.effective_effort),
        label=view.label,
        stored=_choice_read(view.stored) if view.stored is not None else None,
        rejected=[RejectedLayerRead(route_key=layer.route_key, reason=layer.reason) for layer in resolved.rejected],
        db_unavailable=resolved.db_unavailable,
        problem=view.problem,
        declared=view.capability_declared,
    )


def to_read(view: KeyView | RouteView) -> RouteRead:
    """服務層的狀態轉成對外的回應模型（API 與診斷端點共用）。Convert a view into the response model.

    ``KeyView`` 列出這個鍵的所有能力；單一能力的 ``RouteView``（寫入或刪除的結果）只列那一個。
    A key view lists every capability; a single ``RouteView`` lists just its own.
    """
    views = view.views if isinstance(view, KeyView) else (view,)
    return RouteRead(
        route_key=view.route_key,
        description=view.description,
        declared=view.declared,
        capabilities={item.capability: _state_read(item) for item in views},
    )


async def refresh_global_client(app_state: Any) -> None:
    """依「全域 ``*``（DB）→ ``.env``」重建 ``app_state.llm_client``（模型路由計畫 D9）。

    Rebuild ``app_state.llm_client`` from the global route, so the
    diagnostics endpoint's label and CLI probe match the actual global
    setting.

    啟動時、以及寫入或刪除全域 ``*`` 之後呼叫——不論寫入成敗（結果未知時 DB 可能已改）。
    這個客戶端只給診斷端點用（標籤、CLI 探測）；各用途生成時仍每次依自己的用途鍵建立。
    診斷端點另會比對它與全域設定是否一致（``llm_client_matches_global``），例如啟動時讀不到
    DB 而用了 ``.env``、之後 DB 恢復的情況。

    建立失敗不拋出（與啟動時相同，其他功能不受影響），設為 None：設定問題
    （``LLMServiceError``，例如金鑰沒設、找不到 CLI）記 WARNING；其他例外可能是程式或環境
    錯誤，記 ERROR 附 traceback。DB 層設定或套件問題時 ``client_for`` 會先另記一次 ERROR。

    Called at startup and after any write to the global row. Failures leave
    ``None``: configuration errors are warnings, anything else is logged as
    an error with its traceback.
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
