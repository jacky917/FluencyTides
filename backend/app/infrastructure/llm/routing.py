"""
能力路由：依用途鍵取得工具（能力路由計畫 capability_routing_FEAT_2026-10-19.md；前身為模型路由計畫
llm_model_routing_FEAT_2026-10-15.md D1 / D2 / D4）。

會用到工具的模組在模組層宣告用途鍵與它用到的能力，呼叫時以它取客戶端::

    CHILD_CARD = route("jp_verb_pair", "自他動詞子卡生成", capabilities={"llm"})
    ...
    llm = await CHILD_CARD.client()

用途鍵的命名規範見計畫書 §4（英數字與 ``. _ -``，長度 ≤ 64，最多兩層 ``<流程>.<分支>``）。
宣告時即登記，:func:`list_route_keys` 就是管理介面的合法鍵清單。設定存在 ``capability_routes``，
每列是一個用途鍵在一種能力上的選擇；能力與白名單見 :mod:`app.infrastructure.llm.capabilities`。

每個能力**各自**依下列順序解析（計畫書 §5）::

    <流程>.<分支>（DB）→ <流程>（DB）→ 全域 ``*``（DB）→ .env

DB 的各層必須在白名單內，否則跳過並記 WARNING；.env 那一層一律生效。``llm`` 的 .env 那一層
以不帶任何參數的 ``create_llm_client()`` 建立客戶端——與導入路由前的呼叫完全相同。其他能力的
.env 預設見 :func:`~app.infrastructure.llm.capabilities.env_default`。

用途鍵可在宣告時為某個能力另給預設（``env_defaults``，分支沿用流程鍵的），例如錄音評分的 ``llm``
預設為 audio 端點加上 ``STT_LLM_MODEL_NAME``。**這種預設排在全域 ``*`` 之前**：解析到它時不再看
``*``——全域 ``*`` 的 ``llm`` 是建卡用的預設（例如 Claude Code），不該連帶改掉錄音評分用的便宜
模型。要讓這個用途改用別的設定，就直接設定它本身（或它的流程鍵）。

設定列讀出後放在程序內快取，寫入方呼叫 :func:`invalidate_cache` 後下一次呼叫即
生效（後端只跑一個 worker，不處理跨程序同步）；客戶端仍每次呼叫才建立。資料庫
異常時退回 ``.env``：故障開始記 WARNING（附 traceback）、恢復時記 INFO，期間每次
呼叫都重試，解析結果標上 ``db_unavailable``。「資料庫異常」指 DB 回報的錯誤
（``DBAPIError``：連線、缺表、缺欄位——包括模型改了卻沒跑 migration）、連線池
逾時與網路錯誤；ORM 設定錯誤、import 錯誤、屬性錯誤等程式問題不攔截，照常往上拋。

English summary:
    Route-key based tool resolution. Modules declare route keys and the
    capabilities they use with :func:`route` and obtain clients through the
    returned handle. Each capability resolves on its own: branch row →
    flow row → global ``*`` row → ``.env``; rows outside the whitelist are
    skipped with a warning. With no rows the LLM client is built exactly as
    before routing existed. Only database errors fall back to ``.env``;
    programming errors propagate.
"""

import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.core.config import settings
from app.core.exceptions import LLMServiceError
from app.infrastructure.llm import factory
from app.infrastructure.llm.base import OMIT_EFFORT, BaseLLMClient
from app.infrastructure.llm.capabilities import (
    CAPABILITIES,
    CAPABILITY_AUDIO_EVAL,
    CAPABILITY_LLM,
    CAPABILITY_ORDER,
    STRATEGIES,
    RouteChoice,
    env_default,
)
from app.infrastructure.llm.capabilities import check_choice as check_capability_choice
from app.infrastructure.llm.endpoints import env_endpoint_id

logger = logging.getLogger(__name__)

__all__ = [
    "CAPABILITIES",
    "CAPABILITY_LLM",
    "DATABASE_ERRORS",
    "GLOBAL_ROUTE_KEY",
    "RejectedLayer",
    "ResolvedRoute",
    "Route",
    "RouteChoice",
    "RouteSource",
    "Snapshot",
    "route",
]

# 全域設定的用途鍵
GLOBAL_ROUTE_KEY = "*"

# 用途鍵規則；長度與 capability_routes.route_key 欄位一致。最多兩層（<流程>.<分支>）
ROUTE_KEY_MAX_LENGTH = 64
_ROUTE_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)?$")

# 生效的層：route＝查詢的鍵本身、parent＝分支往上的流程鍵、global＝*、env＝.env
RouteSource = Literal["route", "parent", "global", "env"]

# 能力 → 用途鍵 → 選擇
Snapshot = dict[str, dict[str, RouteChoice]]

# 用途鍵為某個能力另給的 .env 預設（呼叫時才讀設定；回 None＝沿用該能力的一般預設）
EnvDefault = Callable[[], RouteChoice | None]

# 視為「資料庫異常」的例外（DB 回報的錯誤、連線池逾時、網路錯誤）；其餘是程式錯誤
DATABASE_ERRORS: tuple[type[BaseException], ...] = (DBAPIError, PoolTimeoutError, OSError)

# 同一則「設定已不在白名單」只記一次；快取失效時重置
_reported: set[str] = set()

# 資料庫目前是否處於讀取失敗狀態（只在狀態轉換時記 log，與 _reported 分開）
_db_failing = False


@dataclass(frozen=True)
class RejectedLayer:
    """被跳過的一層與原因。A skipped layer and why.

    Attributes:
        route_key: 該層的鍵（用途鍵或 ``*``）。The layer's key.
        reason: 不合格的原因。Why it was skipped.
    """

    route_key: str
    reason: str


@dataclass(frozen=True)
class ResolvedRoute:
    """一個用途鍵在一種能力上實際生效的設定。The effective setting for a route key.

    Attributes:
        route_key: 查詢的用途鍵。The queried route key.
        source: 生效的層：``route``（用途鍵本身）/ ``parent``（分支往上的流程鍵）/
            ``global``（全域）/ ``env``。The layer that won.
        choice: 生效的 DB 選擇；``env`` 層為 None。The winning stored choice,
            or None on the env layer.
        rejected: 被跳過的層與原因（已不在白名單），依嘗試順序。Skipped
            layers, in the order tried.
        db_unavailable: DB 讀取失敗、DB 的各層沒有參與解析（與「DB 沒有任何
            設定」區分）。True when the stored settings could not be read.
        capability: 解析的能力。The capability resolved.
        env_choice: ``env`` 層的預設選擇；``llm`` 沒有另給預設時為 None（客戶端讀
            ``LLM_*``）。The env layer's default, None for the plain ``llm`` default.
    """

    route_key: str
    source: RouteSource
    choice: RouteChoice | None
    rejected: tuple[RejectedLayer, ...] = field(default=())
    db_unavailable: bool = False
    capability: str = CAPABILITY_LLM
    env_choice: RouteChoice | None = None

    def __post_init__(self) -> None:
        if (self.source == "env") != (self.choice is None):
            raise ValueError("只有 env 層沒有 DB 選擇")
        if self.choice is not None and self.env_choice is not None:
            raise ValueError("DB 層不帶 env_choice")

    @property
    def effective_choice(self) -> RouteChoice | None:
        """生效的選擇（DB 層或 ``env`` 層的預設）；``llm`` 的一般 ``.env`` 預設為 None。

        The effective choice; None only for the plain ``llm`` env default.
        """
        return self.choice if self.choice is not None else self.env_choice

    @property
    def endpoint(self) -> str:
        """生效的 provider（端點 ID、``whisper`` 或策略）；``llm`` 的 ``env`` 層為 ``LLM_PROVIDER``。

        The effective provider.
        """
        choice = self.effective_choice
        return choice.endpoint if choice is not None else env_endpoint_id()

    @property
    def provider(self) -> str:
        """同 :attr:`endpoint`。Same as :attr:`endpoint`."""
        return self.endpoint

    @property
    def model(self) -> str:
        """生效的模型名；``llm`` 的 ``env`` 層為 ``LLM_MODEL_NAME``。The effective model name."""
        choice = self.effective_choice
        if choice is not None:
            return choice.model
        return (settings.LLM_MODEL_NAME or "").strip()

    @property
    def effort(self) -> str | None:
        """DB 層選定的 effort。``env`` 層為 None——意思是「客戶端沿用 ``.env`` 預設」，
        不是「不使用 effort」（CLI 類仍會帶 ``LLM_*_EFFORT``）。

        The chosen effort. None on the env layer means "the client's
        configured default", not "no effort".
        """
        return self.choice.effort if self.choice is not None else None


@dataclass(frozen=True)
class Route:
    """已宣告的用途鍵與它用到的能力；以 :meth:`client` 取得該用途的 LLM 客戶端。

    A declared route key and the capabilities it uses; :meth:`client`
    returns its LLM client. Create through :func:`route` so the key is
    registered; the format is also checked here, so a direct construction
    cannot hold an invalid key.

    Attributes:
        key: 用途鍵。The route key.
        description: 給人看的說明（管理介面顯示）。Human-readable purpose.
        capabilities: 這個用途會用到的能力；設定只接受這些能力。The capabilities
            it uses; settings for other capabilities are refused.
        strategies: 宣告 ``audio_eval`` 時允許的評分策略；None 為全部（分支沒有限縮時
            沿用流程鍵的）。The recording-evaluation strategies allowed, or None.
        env_defaults: 能力 → 這個鍵（與它的分支）在 ``.env`` 那一層的預設。Per-capability
            ``.env`` defaults for this key and its branches.
    """

    key: str
    description: str
    capabilities: frozenset[str] = frozenset({CAPABILITY_LLM})
    strategies: frozenset[str] | None = None
    env_defaults: Mapping[str, EnvDefault] | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if (
            self.key == GLOBAL_ROUTE_KEY
            or len(self.key) > ROUTE_KEY_MAX_LENGTH
            or not _ROUTE_KEY_PATTERN.fullmatch(self.key)
        ):
            raise ValueError(
                f"用途鍵 {self.key!r} 不合規：只能用英數字與 . _ -、長度 1–{ROUTE_KEY_MAX_LENGTH}、"
                "最多兩層（<流程>.<分支>），且 * 保留給全域。"
            )
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))
        unknown = self.capabilities - CAPABILITIES
        if not self.capabilities or unknown:
            raise ValueError(
                f"用途鍵 {self.key!r} 的能力不合規：{sorted(unknown) or '（空）'}；可用：{sorted(CAPABILITIES)}。"
            )
        if self.strategies is not None:
            strategies = frozenset(self.strategies)
            object.__setattr__(self, "strategies", strategies)
            if CAPABILITY_AUDIO_EVAL not in self.capabilities or not strategies or not strategies <= set(STRATEGIES):
                raise ValueError(
                    f"用途鍵 {self.key!r} 的策略限縮不合規：需宣告 audio_eval，且為 {list(STRATEGIES)} 的非空子集。"
                )
        if self.env_defaults is not None and not set(self.env_defaults) <= self.capabilities:
            raise ValueError(f"用途鍵 {self.key!r} 的 env_defaults 含未宣告的能力。")

    @property
    def ordered_capabilities(self) -> tuple[str, ...]:
        """依顯示順序排列的能力。The capabilities in display order."""
        return tuple(capability for capability in CAPABILITY_ORDER if capability in self.capabilities)

    async def resolve(self, capability: str = CAPABILITY_LLM) -> ResolvedRoute:
        """此用途某個能力目前生效的設定。The effective setting for this route."""
        return await resolve(self.key, capability)

    async def client(self) -> BaseLLMClient:
        """依目前生效的設定建立 LLM 客戶端。Build the LLM client for this route.

        Raises:
            ValueError: 這個用途沒有宣告 ``llm`` 能力（程式錯誤）。The route does
                not declare the ``llm`` capability.
            LLMServiceError: 客戶端初始化失敗時（與 ``create_llm_client`` 相同）。
        """
        if CAPABILITY_LLM not in self.capabilities:
            raise ValueError(f"用途鍵 {self.key!r} 沒有宣告 llm 能力，不能取 LLM 客戶端。")
        return await client_for(self.key)


_DECLARED: dict[str, Route] = {}


def route(
    key: str,
    description: str,
    capabilities: set[str] | frozenset[str],
    *,
    strategies: set[str] | frozenset[str] | None = None,
    env_defaults: Mapping[str, EnvDefault] | None = None,
) -> Route:
    """宣告並登記一個用途鍵與它用到的能力。Declare and register a route key.

    在模組層呼叫：import 時即登記，格式不合規或重複宣告當場拋錯（拼錯但格式合規的鍵
    只會多登記一個新鍵，對應的 DB 設定不會生效——管理介面會列出它）。重新 import
    同一模組（如 ``importlib.reload``）也會因重複宣告拋錯。

    Args:
        key: 用途鍵（英數字與 ``. _ -``，長度 ≤ 64，最多兩層，不可為 ``*``）。Route key.
        description: 給人看的說明。Human-readable purpose.
        capabilities: 用到的能力（:data:`CAPABILITIES` 的非空子集）。The capabilities used.
        strategies: 宣告 ``audio_eval`` 時允許的評分策略（None 為全部）。Allowed strategies.
        env_defaults: 能力 → ``.env`` 那一層的預設（分支沿用流程鍵的）。Per-capability
            ``.env`` defaults.

    Returns:
        用途鍵物件。The route handle.

    Raises:
        ValueError: 鍵或能力不合規，或鍵已被宣告。When the key or capabilities are
            invalid, or the key is taken.
    """
    declared = Route(
        key,
        description,
        frozenset(capabilities),
        frozenset(strategies) if strategies is not None else None,
        dict(env_defaults) if env_defaults is not None else None,
    )
    if key in _DECLARED:
        raise ValueError(f"用途鍵 {key!r} 重複宣告。")
    _DECLARED[key] = declared
    return declared


def list_route_keys() -> list[Route]:
    """所有已宣告的用途鍵，依鍵排序（分支緊跟在流程鍵之後）。All declared route keys, sorted."""
    return [_DECLARED[key] for key in sorted(_DECLARED)]


def get_route(key: str) -> Route | None:
    """已宣告的用途鍵；未宣告時為 None。The declared route, or None."""
    return _DECLARED.get(key)


def parent_key(key: str) -> str | None:
    """分支鍵的流程鍵（``a.b`` → ``a``）；不是分支時為 None。The flow key of a branch."""
    head, separator, _ = key.partition(".")
    return head if separator else None


def route_layers(route_key: str) -> list[tuple[str, RouteSource]]:
    """解析時依序查的 DB 層（不含 ``.env``）。The DB layers tried, in order.

    ``a.b`` → ``a.b``、``a``、``*``；``a`` → ``a``、``*``；``*`` → ``*``。
    """
    if route_key == GLOBAL_ROUTE_KEY:
        return [(GLOBAL_ROUTE_KEY, "global")]
    layers: list[tuple[str, RouteSource]] = [(route_key, "route")]
    parent = parent_key(route_key)
    if parent is not None:
        layers.append((parent, "parent"))
    layers.append((GLOBAL_ROUTE_KEY, "global"))
    return layers


def _declared_chain(route_key: str) -> list[Route]:
    """用途鍵本身與它的流程鍵中已宣告的（依查詢順序）。Declared routes along the chain."""
    keys = [key for key, _ in route_layers(route_key) if key != GLOBAL_ROUTE_KEY]
    return [_DECLARED[key] for key in keys if key in _DECLARED]


def allowed_strategies(route_key: str) -> frozenset[str] | None:
    """用途鍵允許的評分策略：鍵本身有限縮就用它的，否則看流程鍵；都沒有為 None（全部）。

    The strategies a route key allows: its own restriction, else its flow
    key's, else None.
    """
    for declared in _declared_chain(route_key):
        if declared.strategies is not None:
            return declared.strategies
    return None


def declared_default_for(route_key: str, capability: str) -> RouteChoice | None:
    """用途鍵（或它的流程鍵）為這個能力宣告的預設；沒有宣告、或宣告的預設目前不適用時為 None。

    The default declared by the key or its flow key for a capability, or None.
    """
    for declared in _declared_chain(route_key):
        default = (declared.env_defaults or {}).get(capability)
        if default is not None:
            choice = default()
            if choice is not None:
                return choice
    return None


def env_choice_for(route_key: str, capability: str) -> RouteChoice | None:
    """``.env`` 那一層的選擇：用途鍵（或流程鍵）宣告的預設優先，否則為該能力的一般預設。

    The env layer's choice: a default declared by the key or its flow key,
    else the capability's general default (None for plain ``llm``).
    """
    declared = declared_default_for(route_key, capability)
    return declared if declared is not None else env_default(capability)


# ----------------------------------------------------------------------
# DB 讀取與快取（DB access and cache）
# ----------------------------------------------------------------------

_choices_cache: Snapshot | None = None

# 快取世代：每次 invalidate_cache 遞增。讀取開始後世代變了（期間有寫入），讀到的就可能是
# 寫入前的舊快照，不能放進快取——否則舊值會一直留到下次寫入或重啟
_cache_generation = 0


def invalidate_cache() -> None:
    """清空設定快取；寫入或刪除設定後呼叫。Drop the cached rows after a write.

    同時遞增快取世代，讓寫入前就開始、寫入後才讀完的讀取不會把舊快照寫回快取。
    Also bumps the generation so a read that started before the write cannot
    put its stale snapshot back into the cache.
    """
    global _choices_cache, _cache_generation
    _choices_cache = None
    _cache_generation += 1
    _reported.clear()


def cache_generation() -> int:
    """目前的快取世代：每次寫入或刪除設定都會變（評分器等依設定建立的物件據此清快取）。

    The cache generation; changes on every write, so caches built from the
    settings can tell they are outdated.
    """
    return _cache_generation


def copy_snapshot(snapshot: Snapshot) -> Snapshot:
    """快照的複本（兩層都複製）。A copy of a snapshot."""
    return {capability: dict(rows) for capability, rows in snapshot.items()}


def warn_once(message: str) -> None:
    """同一則警告只記一次（快取失效時重置，設定改過後會再提醒）。

    Log a warning once per message; reset whenever the cache is invalidated.
    """
    if message in _reported:
        return
    _reported.add(message)
    logger.warning(message)


async def _fetch_choices() -> Snapshot:
    """從 ``capability_routes`` 讀出全部設定（能力 → 用途鍵 → 選擇）。Read every row.

    ``model`` 欄位可為空（策略類能力），讀成空字串；需要模型的能力若是空的，比對白名單時會被
    跳過。

    延遲 import 資料庫模組：只在第一次需要時才建立引擎，LLM 層本身不依賴 DB。
    """
    from sqlalchemy import select

    from app.infrastructure.database.database import async_session_factory
    from app.infrastructure.database.models import CapabilityRouteSetting

    async with async_session_factory() as session:
        result = await session.execute(select(CapabilityRouteSetting))
        rows = result.scalars().all()
    snapshot: Snapshot = {}
    for row in rows:
        snapshot.setdefault(row.capability, {})[row.route_key] = RouteChoice(row.provider, row.model or "", row.effort)
    return snapshot


async def _fetch_and_cache() -> Snapshot:
    """讀 DB，成功時結束故障狀態並放進快取（讀取期間有寫入則不放）。

    Read the table; on success leave the failure state and cache the result,
    unless a write invalidated the cache while the read was in flight.

    Raises:
        DBAPIError / sqlalchemy.exc.TimeoutError / OSError: 讀不到 DB 時（見
            :data:`DATABASE_ERRORS`），快取與故障狀態都不動。
    """
    global _choices_cache, _db_failing
    generation = _cache_generation
    choices = await _fetch_choices()
    if _db_failing:
        logger.info("能力路由設定（capability_routes）已恢復讀取，DB 設定重新生效。")
        _db_failing = False
    if generation == _cache_generation:
        _choices_cache = choices
    return choices


async def _load_choices() -> tuple[Snapshot, bool]:
    """取設定（有快取用快取）。Get the stored choices, cached.

    只攔資料庫異常（``DBAPIError``、連線池逾時、``OSError``）：回空、不快取（下次
    重試），只在「正常 → 故障」時記 WARNING 附 traceback、「故障 → 恢復」時記 INFO。
    其他例外（程式錯誤）照常往上拋。

    Only database errors fall back (uncached, logged on state transitions);
    anything else is a bug and propagates.

    Returns:
        ``(設定, 是否讀取成功)``。``(choices, read_ok)``.
    """
    global _db_failing
    if _choices_cache is not None:
        return _choices_cache, True
    try:
        return await _fetch_and_cache(), True
    except DATABASE_ERRORS as error:  # D4：只有 DB 異常才退回 .env
        if not _db_failing:
            logger.warning(
                "讀取能力路由設定（capability_routes）失敗，改用 .env 設定直到 DB 恢復：%s: %s",
                type(error).__name__,
                error,
                exc_info=True,
            )
        _db_failing = True
        return {}, False


async def stored_choices() -> tuple[Snapshot, bool]:
    """DB 中目前存的全部設定（含不在白名單、或用途鍵已不存在的孤兒列），與解析共用快取。

    Every stored choice, including rows outside the whitelist and orphan
    keys; shares the resolution cache.

    讀取失敗時回 ``({}, False)``——那是生成路徑「不中斷」的退回，**管理路徑必須檢查
    ``read_ok``**，不能把空字典當成「沒有設定」。回傳的是複本，呼叫端改了也不影響快取。

    On a read failure this returns ``({}, False)``; management callers must
    check ``read_ok`` rather than treat the empty dict as "nothing stored".

    Returns:
        ``(能力 → 用途鍵 → 選擇, 是否讀取成功)``。``(snapshot, read_ok)``.
    """
    choices, read_ok = await _load_choices()
    return copy_snapshot(choices), read_ok


async def stored_choices_strict() -> Snapshot:
    """與 :func:`stored_choices` 共用快取，但讀不到時拋出原始的資料庫例外（不退回）。

    Shares the cache with :func:`stored_choices` but raises the original
    database error instead of falling back, so management callers can log
    the real cause.

    讀取成功時同樣寫入快取並結束「DB 故障」狀態；失敗時不記 log、也不進入故障狀態
    （原因交給呼叫端記錄），所以不影響生成路徑「故障開始才記 WARNING」的節奏。
    回傳的是複本。

    Raises:
        DBAPIError / sqlalchemy.exc.TimeoutError / OSError: 讀不到 DB 時（見
            :data:`DATABASE_ERRORS`）。
    """
    if _choices_cache is not None:
        return copy_snapshot(_choices_cache)
    return copy_snapshot(await _fetch_and_cache())


# ----------------------------------------------------------------------
# 客戶端模組 import 失敗的分類（Import failures；生成路徑與管理介面共用）
# ----------------------------------------------------------------------


def is_internal_import(error: ImportError) -> bool:
    """import 失敗的是專案自己的模組（``app.*``）：那是程式錯誤（註冊表模組名、import 名稱打錯、
    provider 改名），不是部署少裝套件或版本不符。

    ``name`` 由 import 機制填入（缺的模組，或 ``from X import Y`` 失敗時的 ``X``）；為 None 表示
    例外是手動拋出的——通常是第三方套件自己說「缺某個依賴」，視為部署問題。

    Whether the failed import belongs to this project, i.e. a bug rather
    than a missing or incompatible dependency. ``name`` is None only for a
    hand-raised error, usually from a third-party package.
    """
    return (error.name or "").split(".")[0] == "app"


def describe_import_error(endpoint_id: str, error: ImportError) -> str:
    """第三方套件 import 失敗的說明：沒裝（``ModuleNotFoundError``），或版本不符、無法載入
    （其他 ``ImportError``，例如 ``cannot import name 'X' from 'google.genai'``）。

    Describe a third-party import failure: not installed, or an
    incompatible / unloadable version.
    """
    if isinstance(error, ModuleNotFoundError):
        return f"端點 '{endpoint_id}' 需要的套件未安裝（{error.name or error}）"
    return f"端點 '{endpoint_id}' 需要的套件版本不符或無法載入（{error}）"


# ----------------------------------------------------------------------
# 解析（Resolution）
# ----------------------------------------------------------------------


def check_choice(
    choice: RouteChoice, capability: str = CAPABILITY_LLM, *, route_key: str = GLOBAL_ROUTE_KEY
) -> str | None:
    """檢查一組選擇是否在白名單內（``audio_eval`` 另檢查用途鍵允許的策略）。

    Check a choice against the whitelist; for ``audio_eval`` also against
    the strategies the route key allows.

    Returns:
        不合格的原因；合格時為 None。Why it is invalid, or None.
    """
    return check_capability_choice(capability, choice, strategies=allowed_strategies(route_key))


async def resolve(route_key: str, capability: str = CAPABILITY_LLM) -> ResolvedRoute:
    """依「分支 → 流程 → 全域 → .env」解析用途鍵某個能力實際生效的設定。

    Resolve the effective setting of one capability: branch row, flow row,
    the global row, then ``.env``. Rows outside the whitelist are skipped
    with a warning.

    Args:
        route_key: 用途鍵（``*`` 查全域）。Route key, or ``*``.
        capability: 能力。The capability.

    Returns:
        生效的設定與來源。The effective setting and its source.
    """
    choices, read_ok = await _load_choices()
    return resolve_with(route_key, choices, read_ok, capability)


def resolve_with(
    route_key: str, choices: Snapshot, read_ok: bool, capability: str = CAPABILITY_LLM
) -> ResolvedRoute:
    """以一份已讀出的設定快照解析（不碰 DB）。Resolve against a given snapshot.

    管理介面一次列出所有用途時用同一份快照，DB 只讀一次，各列也不會來自不同次讀取。

    Lets a listing resolve every key from one snapshot: one DB read, and no
    row built from a different read than its neighbours.

    Args:
        route_key: 用途鍵（``*`` 查全域）。Route key, or ``*``.
        choices: 能力 → 用途鍵 → 選擇。The stored choices.
        read_ok: 該快照是否讀取成功（失敗時結果標上 ``db_unavailable``）。
            Whether the snapshot was read successfully.
        capability: 能力。The capability.

    Returns:
        生效的設定與來源。The effective setting and its source.
    """
    rows = choices.get(capability, {})
    rejected: list[RejectedLayer] = []
    # 用途鍵宣告的預設排在全域 * 之前（見模組說明）：有宣告時不看 *
    declared_default = declared_default_for(route_key, capability) if route_key != GLOBAL_ROUTE_KEY else None
    for key, source in route_layers(route_key):
        if source == "global" and declared_default is not None:
            break
        choice = rows.get(key)
        if choice is None:
            continue
        problem = check_choice(choice, capability, route_key=route_key)
        if problem is not None:
            warn_once(f"能力路由設定 '{key}' 的 {capability} 已不在白名單（{problem}），改用下一層。")
            rejected.append(RejectedLayer(key, problem))
            continue
        return ResolvedRoute(route_key, source, choice, tuple(rejected), capability=capability)

    return ResolvedRoute(
        route_key,
        "env",
        None,
        tuple(rejected),
        db_unavailable=not read_ok,
        capability=capability,
        env_choice=declared_default if declared_default is not None else env_default(capability),
    )


async def client_for(route_key: str) -> BaseLLMClient:
    """依用途鍵建立 LLM 客戶端。Build the LLM client for a route key.

    ``env`` 層以不帶參數的 ``create_llm_client()`` 建立，與導入路由前完全相同（用途鍵另給了
    ``.env`` 預設時改用它的端點與模型）；DB 層指定端點、模型與 effort（模型不使用 effort 時
    傳 ``OMIT_EFFORT``）。DB 層建立失敗時不退回下一層（那會把設定錯誤藏起來）：記 ERROR，
    並以 ``add_note`` 把路由脈絡附在例外上，型別與訊息不變地拋出。

    端點需要的第三方套件沒裝或版本不符（``ImportError``）、CLI 工作目錄建不起來（``OSError``）
    時，不論哪一層都轉成 ``LLMServiceError``，訊息帶用途、層與原因——生成端才看得出是哪個
    設定、能怎麼修，也走與其他 LLM 錯誤相同的處理。import 失敗的若是專案自己的模組（程式
    錯誤）照常拋出。

    Raises:
        LLMServiceError: 客戶端初始化失敗、端點需要的第三方套件無法載入，或 CLI 工作目錄
            建不起來。When client construction fails, a third-party package cannot
            be imported, or the CLI working directory cannot be created.
    """
    return client_from(await resolve(route_key))


def client_from(resolved: ResolvedRoute) -> BaseLLMClient:
    """依 ``llm`` 能力的解析結果建立客戶端（錯誤處理見 :func:`client_for`）。

    Build the LLM client for an already-resolved ``llm`` route.

    Raises:
        ValueError: 解析結果不是 ``llm`` 能力（程式錯誤）。
        LLMServiceError: 同 :func:`client_for`。
    """
    if resolved.capability != CAPABILITY_LLM:
        raise ValueError(f"解析結果是 {resolved.capability}，不是 llm，不能建立 LLM 客戶端。")
    route_key = resolved.route_key
    logger.debug(
        "LLM 用途 %s → %s（%s / %s / %s），跳過：%s，DB 不可用：%s",
        route_key, resolved.source, resolved.endpoint, resolved.model, resolved.effort,
        resolved.rejected or "無", resolved.db_unavailable,
    )
    choice = resolved.effective_choice
    try:
        if choice is None:
            return factory.create_llm_client()
        if resolved.choice is None:
            # 用途鍵宣告的 .env 預設：effort 沿用該 provider 的預設
            return factory.create_llm_client(endpoint=choice.endpoint, model=choice.model)
        return factory.create_llm_client(
            endpoint=choice.endpoint,
            model=choice.model,
            effort=choice.effort if choice.effort is not None else OMIT_EFFORT,
        )
    except LLMServiceError as error:
        if choice is None:
            raise
        context = (
            f"LLM 用途 '{route_key}' 依 {resolved.source} 層設定（{choice.endpoint} / {choice.model} / "
            f"{choice.effort}）建立客戶端失敗；可到管理介面改設或恢復預設。"
        )
        logger.error(context)
        error.add_note(context)
        raise
    except ImportError as error:
        if is_internal_import(error):
            raise
        context = (
            f"LLM 用途 '{route_key}' 依 {resolved.source} 層設定（{resolved.endpoint} / {resolved.model}）"
            f"無法建立客戶端：{describe_import_error(resolved.endpoint, error)}；"
            "請補裝套件，或到管理介面改用其他端點。"
        )
        logger.error(context, exc_info=True)
        raise LLMServiceError(context) from error
    except OSError as error:
        # CLI 類客戶端建構時會建立工作目錄（cli_base），掛載或權限有誤時拋 OSError
        context = (
            f"LLM 用途 '{route_key}' 依 {resolved.source} 層設定（{resolved.endpoint} / {resolved.model}）"
            f"無法建立客戶端：無法建立 CLI 工作目錄或檔案（{error}）；請檢查工作目錄的路徑與權限。"
        )
        logger.error(context, exc_info=True)
        raise LLMServiceError(context) from error
