"""
LLM 模型路由：依用途鍵取得客戶端（模型路由計畫 llm_model_routing_FEAT_2026-10-15.md D1 / D2 / D4）。

用到 LLM 的模組在模組層宣告用途鍵，呼叫時以它取客戶端::

    CHILD_CARD_LLM = llm_route("jp_verb_pair", "自他動詞子卡生成")
    ...
    llm = await CHILD_CARD_LLM.client()

用途鍵由寫程式的人自由命名（英數字與 ``. _ -``，長度 ≤ 64；``.`` 只是名字的一部分，
沒有階層語意），與 handler 脫鉤：一個模組可宣告多個鍵，不用 LLM 的模組什麼都不必寫。
宣告時即登記，:func:`list_route_keys` 就是管理介面的合法鍵清單。

解析順序::

    用途鍵（DB）→ 全域 ``*``（DB）→ .env（LLM_PROVIDER / LLM_MODEL_NAME / 預設 effort）

DB 的兩層必須在 ``.env`` 白名單內，否則跳過並記 WARNING；.env 那一層一律生效，
以不帶任何參數的 ``create_llm_client()`` 建立客戶端——與導入路由前的呼叫完全相同。

設定列讀出後放在程序內快取，寫入方呼叫 :func:`invalidate_cache` 後下一次呼叫即
生效（後端只跑一個 worker，不處理跨程序同步）；客戶端仍每次呼叫才建立。資料庫
異常時退回 ``.env``：故障開始記 WARNING（附 traceback）、恢復時記 INFO，期間每次
呼叫都重試，解析結果標上 ``db_unavailable``。「資料庫異常」指 DB 回報的錯誤
（``DBAPIError``：連線、缺表、缺欄位——包括模型改了卻沒跑 migration）、連線池
逾時與網路錯誤；ORM 設定錯誤、import 錯誤、屬性錯誤等程式問題不攔截，照常往上拋。

English summary:
    Route-key based LLM client resolution. Modules declare free-form route
    keys with :func:`llm_route` and obtain clients through the returned
    handle. Resolution goes route row → global ``*`` row → ``.env``; rows
    outside the ``.env`` whitelist are skipped with a warning. With no rows
    the client is built exactly as before routing existed. Only database
    errors fall back to ``.env``; programming errors propagate.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.core.config import settings
from app.core.exceptions import LLMServiceError
from app.infrastructure.llm import factory
from app.infrastructure.llm.base import OMIT_EFFORT, BaseLLMClient
from app.infrastructure.llm.endpoints import env_endpoint_id, get_endpoint

logger = logging.getLogger(__name__)

# 全域設定的用途鍵
GLOBAL_ROUTE_KEY = "*"

# 用途鍵規則；長度與 llm_routes.route_key 欄位一致
ROUTE_KEY_MAX_LENGTH = 64
_ROUTE_KEY_PATTERN = re.compile(rf"^[A-Za-z0-9._-]{{1,{ROUTE_KEY_MAX_LENGTH}}}$")

RouteSource = Literal["route", "global", "env"]

# 視為「資料庫異常」的例外（DB 回報的錯誤、連線池逾時、網路錯誤）；其餘是程式錯誤
DATABASE_ERRORS: tuple[type[BaseException], ...] = (DBAPIError, PoolTimeoutError, OSError)

# 同一則「設定已不在白名單」只記一次；快取失效時重置
_reported: set[str] = set()

# 資料庫目前是否處於讀取失敗狀態（只在狀態轉換時記 log，與 _reported 分開）
_db_failing = False


@dataclass(frozen=True)
class RouteChoice:
    """DB 中一列的選擇（尚未比對白名單）。One stored choice, not yet checked.

    Attributes:
        endpoint: 端點 ID。Endpoint ID.
        model: 模型名。Model name.
        effort: 思考深度；None＝該模型不使用 effort（空字串正規化為 None）。
            Effort, or None when the model takes none ("" normalizes to None).
    """

    endpoint: str
    model: str
    effort: str | None

    def __post_init__(self) -> None:
        if self.effort == "":
            object.__setattr__(self, "effort", None)


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
    """一個用途鍵實際生效的設定。The effective setting for a route key.

    Attributes:
        route_key: 查詢的用途鍵。The queried route key.
        source: 生效的層：``route``（用途）/ ``global``（全域）/ ``env``。
            The layer that won.
        choice: 生效的 DB 選擇；``env`` 層為 None（客戶端讀 ``.env``）。The
            winning stored choice, or None on the env layer.
        rejected: 被跳過的層與原因（已不在白名單），依嘗試順序。Skipped
            layers, in the order tried.
        db_unavailable: DB 讀取失敗、兩個 DB 層沒有參與解析（與「DB 沒有任何
            設定」區分）。True when the stored settings could not be read.
    """

    route_key: str
    source: RouteSource
    choice: RouteChoice | None
    rejected: tuple[RejectedLayer, ...] = field(default=())
    db_unavailable: bool = False

    @property
    def endpoint(self) -> str:
        """生效的端點 ID；``env`` 層為 ``LLM_PROVIDER``。The effective endpoint ID."""
        return self.choice.endpoint if self.choice is not None else env_endpoint_id()

    @property
    def model(self) -> str:
        """生效的模型名；``env`` 層為 ``LLM_MODEL_NAME``。The effective model name."""
        if self.choice is not None:
            return self.choice.model
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
class LLMRoute:
    """已宣告的用途鍵；以 :meth:`client` 取得該用途的客戶端。

    A declared route key; :meth:`client` returns the client for it. Create
    through :func:`llm_route` so the key is registered; the format is also
    checked here, so a direct construction cannot hold an invalid key.

    Attributes:
        key: 用途鍵。The route key.
        description: 給人看的說明（管理介面顯示）。Human-readable purpose.
    """

    key: str
    description: str

    def __post_init__(self) -> None:
        if self.key == GLOBAL_ROUTE_KEY or not _ROUTE_KEY_PATTERN.fullmatch(self.key):
            raise ValueError(
                f"LLM 用途鍵 {self.key!r} 不合規：只能用英數字與 . _ -、長度 1–{ROUTE_KEY_MAX_LENGTH}，"
                "且 * 保留給全域。"
            )

    async def resolve(self) -> ResolvedRoute:
        """此用途目前生效的設定。The effective setting for this route."""
        return await resolve(self.key)

    async def client(self) -> BaseLLMClient:
        """依目前生效的設定建立客戶端。Build the client for this route.

        Raises:
            LLMServiceError: 客戶端初始化失敗時（與 ``create_llm_client`` 相同）。
        """
        return await client_for(self.key)


_DECLARED: dict[str, LLMRoute] = {}


def llm_route(key: str, description: str) -> LLMRoute:
    """宣告並登記一個用途鍵。Declare and register a route key.

    在模組層呼叫：import 時即登記，格式不合規或重複宣告當場拋錯（拼錯但格式合規的鍵
    只會多登記一個新鍵，對應的 DB 設定不會生效——管理介面會列出它）。重新 import
    同一模組（如 ``importlib.reload``）也會因重複宣告拋錯。

    Args:
        key: 用途鍵（英數字與 ``. _ -``，長度 ≤ 64，不可為 ``*``）。Route key.
        description: 給人看的說明。Human-readable purpose.

    Returns:
        用途鍵物件。The route handle.

    Raises:
        ValueError: 鍵不合規或已被宣告。When the key is invalid or taken.
    """
    route = LLMRoute(key, description)
    if key in _DECLARED:
        raise ValueError(f"LLM 用途鍵 {key!r} 重複宣告。")
    _DECLARED[key] = route
    return route


def list_route_keys() -> list[LLMRoute]:
    """所有已宣告的用途鍵，依鍵排序。All declared route keys, sorted."""
    return [_DECLARED[key] for key in sorted(_DECLARED)]


# ----------------------------------------------------------------------
# DB 讀取與快取（DB access and cache）
# ----------------------------------------------------------------------

_choices_cache: dict[str, RouteChoice] | None = None

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


def _warn_once(message: str) -> None:
    """同一則警告只記一次。Log a warning once per message."""
    if message in _reported:
        return
    _reported.add(message)
    logger.warning(message)


async def _fetch_choices() -> dict[str, RouteChoice]:
    """從 ``llm_routes`` 讀出全部設定。Read every row of ``llm_routes``.

    延遲 import 資料庫模組：只在第一次需要時才建立引擎，LLM 層本身不依賴 DB。
    """
    from sqlalchemy import select

    from app.infrastructure.database.database import async_session_factory
    from app.infrastructure.database.models import LLMRouteSetting

    async with async_session_factory() as session:
        result = await session.execute(select(LLMRouteSetting))
        rows = result.scalars().all()
    return {row.route_key: RouteChoice(row.endpoint, row.model, row.effort) for row in rows}


async def _fetch_and_cache() -> dict[str, RouteChoice]:
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
        logger.info("LLM 用途設定（llm_routes）已恢復讀取，DB 設定重新生效。")
        _db_failing = False
    if generation == _cache_generation:
        _choices_cache = choices
    return choices


async def _load_choices() -> tuple[dict[str, RouteChoice], bool]:
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
                "讀取 LLM 用途設定（llm_routes）失敗，改用 .env 設定直到 DB 恢復：%s: %s",
                type(error).__name__,
                error,
                exc_info=True,
            )
        _db_failing = True
        return {}, False


async def stored_choices() -> tuple[dict[str, RouteChoice], bool]:
    """DB 中目前存的全部設定（含不在白名單、或用途鍵已不存在的孤兒列），與解析共用快取。

    Every stored choice, including rows outside the whitelist and orphan
    keys; shares the resolution cache.

    讀取失敗時回 ``({}, False)``——那是生成路徑「不中斷」的退回，**管理路徑必須檢查
    ``read_ok``**，不能把空字典當成「沒有設定」。回傳的是複本，呼叫端改了也不影響快取。

    On a read failure this returns ``({}, False)``; management callers must
    check ``read_ok`` rather than treat the empty dict as "nothing stored".

    Returns:
        ``(用途鍵 → 選擇, 是否讀取成功)``。``(choices, read_ok)``.
    """
    choices, read_ok = await _load_choices()
    return dict(choices), read_ok


async def stored_choices_strict() -> dict[str, RouteChoice]:
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
        return dict(_choices_cache)
    return dict(await _fetch_and_cache())


# ----------------------------------------------------------------------
# 解析（Resolution）
# ----------------------------------------------------------------------


def check_choice(choice: RouteChoice) -> str | None:
    """檢查一組選擇是否在白名單內。Check a choice against the whitelist.

    Returns:
        不合格的原因；合格時為 None。Why it is invalid, or None.
    """
    endpoint = get_endpoint(choice.endpoint)
    if endpoint is None:
        return f"端點 '{choice.endpoint}' 不存在"
    if not endpoint.listed:
        return f"端點 '{choice.endpoint}' 僅供 .env 那一層使用，不可選擇"
    option = endpoint.find_model(choice.model)
    if option is None:
        return f"模型 '{choice.model}' 不在端點 '{endpoint.id}' 的白名單"
    if option.allows(choice.effort):
        return None
    if not option.efforts:
        return f"模型 '{choice.model}' 不使用 effort，卻設定了 '{choice.effort}'"
    return (
        f"effort '{choice.effort or '（未設定）'}' 不在模型 '{choice.model}' 的白名單"
        f"（可用：{'|'.join(option.efforts)}）"
    )


async def resolve(route_key: str) -> ResolvedRoute:
    """依「用途 → 全域 → .env」解析用途鍵實際生效的設定。

    Resolve the effective setting: route row, then the global row, then
    ``.env``. Rows outside the whitelist are skipped with a warning.

    Args:
        route_key: 用途鍵（``*`` 查全域）。Route key, or ``*``.

    Returns:
        生效的設定與來源。The effective setting and its source.
    """
    choices, read_ok = await _load_choices()
    return resolve_with(route_key, choices, read_ok)


def resolve_with(route_key: str, choices: dict[str, RouteChoice], read_ok: bool) -> ResolvedRoute:
    """以一份已讀出的設定快照解析（不碰 DB）。Resolve against a given snapshot.

    管理介面一次列出所有用途時用同一份快照，DB 只讀一次，各列也不會來自不同次讀取。

    Lets a listing resolve every key from one snapshot: one DB read, and no
    row built from a different read than its neighbours.

    Args:
        route_key: 用途鍵（``*`` 查全域）。Route key, or ``*``.
        choices: 用途鍵 → 選擇。The stored choices.
        read_ok: 該快照是否讀取成功（失敗時結果標上 ``db_unavailable``）。
            Whether the snapshot was read successfully.

    Returns:
        生效的設定與來源。The effective setting and its source.
    """
    layers: list[tuple[str, RouteSource]] = [(GLOBAL_ROUTE_KEY, "global")]
    if route_key != GLOBAL_ROUTE_KEY:
        layers.insert(0, (route_key, "route"))

    rejected: list[RejectedLayer] = []
    for key, source in layers:
        choice = choices.get(key)
        if choice is None:
            continue
        problem = check_choice(choice)
        if problem is not None:
            _warn_once(f"LLM 用途設定 '{key}' 已不在白名單（{problem}），改用下一層。")
            rejected.append(RejectedLayer(key, problem))
            continue
        return ResolvedRoute(route_key, source, choice, tuple(rejected))

    return ResolvedRoute(route_key, "env", None, tuple(rejected), db_unavailable=not read_ok)


async def client_for(route_key: str) -> BaseLLMClient:
    """依用途鍵建立客戶端。Build the client for a route key.

    ``env`` 層以不帶參數的 ``create_llm_client()`` 建立，與導入路由前完全相同；
    DB 層指定端點、模型與 effort（模型不使用 effort 時傳 ``OMIT_EFFORT``）。DB 層
    建立失敗時不退回下一層（那會把設定錯誤藏起來）：記 ERROR，並以 ``add_note`` 把
    路由脈絡附在例外上，型別與訊息不變地拋出。

    Raises:
        LLMServiceError: 客戶端初始化失敗時。When client construction fails.
    """
    resolved = await resolve(route_key)
    logger.debug(
        "LLM 用途 %s → %s（%s / %s / %s），跳過：%s，DB 不可用：%s",
        route_key, resolved.source, resolved.endpoint, resolved.model, resolved.effort,
        resolved.rejected or "無", resolved.db_unavailable,
    )
    choice = resolved.choice
    if choice is None:
        return factory.create_llm_client()
    try:
        return factory.create_llm_client(
            endpoint=choice.endpoint,
            model=choice.model,
            effort=choice.effort if choice.effort is not None else OMIT_EFFORT,
        )
    except LLMServiceError as error:
        context = (
            f"LLM 用途 '{route_key}' 依 {resolved.source} 層設定（{choice.endpoint} / {choice.model} / "
            f"{choice.effort}）建立客戶端失敗；可到管理介面改設或恢復預設。"
        )
        logger.error(context)
        error.add_note(context)
        raise
