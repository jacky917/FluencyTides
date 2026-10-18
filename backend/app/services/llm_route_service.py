"""
LLM 用途設定的讀寫服務（模型路由計畫 llm_model_routing_FEAT_2026-10-15.md D6）。

API（``/api/v1/llm-routes``）與 TG ``/models`` 選單共用這一層：列出每個用途目前
生效的設定、寫入一組選擇、恢復預設。寫入前依序檢查：

1. 用途鍵已宣告（或為全域 ``*``）
2. ``(端點, 模型, effort)`` 在 ``.env`` 白名單內——與讀取端同一個
   :func:`~app.infrastructure.llm.routing.check_choice`
3. 試建一次客戶端：CLI 找不找得到、金鑰有沒有設定、套件有沒有裝，在存檔當下就擋下。
   不打 API（模型名本身是否存在仍要實際呼叫才知道），但 CLI 類會建立工作目錄

寫入成功後清空路由快取，下一次呼叫即生效。

錯誤分三種（都是 :class:`~app.core.exceptions.FluencyTidesError`，API 由全域 handler
轉成對應狀態碼）：

- :class:`~app.core.exceptions.LLMRouteNotFoundError`（404）：用途鍵不存在（寫入時：未宣告）
- :class:`~app.core.exceptions.InvalidLLMRouteError`（422）：設定不合格（呼叫端可修正）
- :class:`~app.core.exceptions.LLMRouteStoreUnavailableError`（503）：讀不到 DB，或寫入／刪除時
  資料庫異常（結果未知）

**讀取不沿用生成路徑的退回**：路由層讀不到 DB 時會回「沒有設定」好讓生成不中斷，但
管理介面若照單全收，就會把「讀不到」說成「不存在」。所以需要知道某個鍵有沒有存值的
操作（查單一用途、刪除孤兒鍵）在讀不到時拋 503（並記下原因）；列出全部則照常回傳、每列
標上 ``db_unavailable``。列出與查詢時每個請求只讀一次 DB，所有列都由同一份快照解析。
寫入時的資料庫錯誤同樣轉成 503（結果未知，提示重新查看）。

**清單不因單列壞掉而打不開**：算標籤需要載入端點的客戶端類別；端點需要的第三方套件沒裝或版本不符時，
該列改用不去前綴的標籤並在 ``problem`` 說明，其餘照常列出——管理員正需要這個介面把
設定改到能用的端點。``.env`` 那一層的端點設定有誤被跳過時同樣標在 ``problem``。

English summary:
    Read/write service for LLM route settings, shared by the HTTP API and the
    Telegram ``/models`` menu. Writes are validated against declared keys,
    the ``.env`` whitelist and a trial client build. Reads use one snapshot
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

from app.core.exceptions import (
    InvalidLLMRouteError,
    LLMRouteNotFoundError,
    LLMRouteStoreUnavailableError,
    LLMServiceError,
)
from app.infrastructure.database.models import LLMRouteSetting
from app.infrastructure.llm import factory, routing
from app.infrastructure.llm.base import OMIT_EFFORT
from app.infrastructure.llm.endpoints import env_endpoint_id, env_endpoint_is_custom, get_endpoint, skip_reason
from app.infrastructure.llm.labels import build_model_label
from app.infrastructure.llm.routing import (
    DATABASE_ERRORS,
    GLOBAL_ROUTE_KEY,
    ResolvedRoute,
    RouteChoice,
    describe_import_error,
    is_internal_import,
)
from app.schemas.llm_route import LLMRouteRead, RejectedLayerRead, RouteChoiceRead

logger = logging.getLogger(__name__)

# 全域設定在清單中的說明
GLOBAL_DESCRIPTION = "全域預設（各用途沒有設定時使用）"

_STORE_UNAVAILABLE_MESSAGE = (
    "讀不到 LLM 用途設定（llm_routes），無法判斷這個用途的設定；詳見後端 log 的 WARNING，DB 恢復後再試。"
)
_WRITE_FAILED_MESSAGE = (
    "寫入 LLM 用途設定（llm_routes）時資料庫異常，結果未知；請重新查看設定後再試，詳見後端 log。"
)


class RouteKeyEntry(NamedTuple):
    """清單中的一個用途鍵。One route key in display order.

    Attributes:
        key: 用途鍵；``*`` 為全域。The route key.
        description: 說明；孤兒鍵為 None。Description, None for orphans.
        declared: 是否為程式中宣告的鍵（或全域）；False＝DB 裡的孤兒列。
            False for orphan rows whose key is no longer declared.
    """

    key: str
    description: str | None
    declared: bool


@dataclass(frozen=True)
class RouteView:
    """一個用途鍵的完整狀態（給 API 與 TG 顯示）。One route key's full state.

    Attributes:
        entry: 用途鍵與說明。The key and its description.
        resolved: 實際生效的設定。The effective setting.
        effective_effort: 實際生效的 effort。``.env`` 那一層為 ``.env`` 的預設值（CLI 類），
            不是 None——與標籤一致。The effort actually used, the configured
            default on the ``.env`` layer.
        label: 生效設定對應的卡片標籤。The label the effective setting yields.
        stored: DB 中這個鍵本身存的選擇（可能已不在白名單）。The row stored for
            this key, if any (possibly outside the whitelist).
        problem: 生效設定目前無法使用的原因（端點需要的套件未安裝或版本不符、``.env`` 的
            端點設定有誤已被跳過）；有問題時 ``label`` 與 ``effective_effort`` 可能與實際不符。
            None 只表示沒有偵測到這兩類問題，不代表已驗證可用。Why the effective
            setting cannot be used right now; None does not mean it was verified.
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
        if not self.entry.declared and self.stored is None:
            raise ValueError("孤兒鍵一定有存值（否則不會出現在清單）")
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
        """是否為已宣告的鍵。Whether the key is declared."""
        return self.entry.declared


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
    """實際會用的 effort。The effort a setting actually uses.

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


def preview_label(endpoint_id: str, model: str, effort: str | None, *, env_layer: bool = False) -> str:
    """不建立客戶端，算出這組設定會產生的卡片標籤（規則與客戶端的 ``model_label`` 相同）。

    Compute the label a setting would produce without building a client;
    the strip prefix and default effort come from the client class itself.

    Args:
        endpoint_id: 端點 ID。Endpoint ID.
        model: 模型名。Model name.
        effort: effort 值或 None（意思見 :func:`effective_effort`）。The effort.
        env_layer: 是否為 ``.env`` 那一層。Whether this is the ``.env`` layer.

    Returns:
        標籤，例如 ``(yinli)gemini-3.5-flash``、``(claude-code)opus-5@high``。The label.
    """
    shown_effort = effective_effort(endpoint_id, effort, env_layer=env_layer)
    strip_prefix = _client_class_for(endpoint_id).MODEL_LABEL_STRIP_PREFIX
    return build_model_label(endpoint_id, model, shown_effort, strip_prefix=strip_prefix)


def ordered_route_keys(stored: dict[str, RouteChoice]) -> list[RouteKeyEntry]:
    """顯示順序：全域 ``*``、已宣告的鍵（依鍵排序）、DB 中的孤兒鍵（依鍵排序）。

    Display order: ``*``, declared keys, then orphan keys found in the DB.
    """
    declared = {route.key: route.description for route in routing.list_route_keys()}
    entries = [RouteKeyEntry(GLOBAL_ROUTE_KEY, GLOBAL_DESCRIPTION, True)]
    entries += [RouteKeyEntry(key, declared[key], True) for key in sorted(declared)]
    entries += [
        RouteKeyEntry(key, None, False)
        for key in sorted(stored)
        if key != GLOBAL_ROUTE_KEY and key not in declared
    ]
    return entries


def _env_endpoint_problem() -> str | None:
    """``.env`` 那一層的端點設定有誤、已被跳過時的說明；沒問題時為 None。

    ``LLM_PROVIDER`` 指向 ``LLM_ENDPOINTS`` 中寫錯的端點時，啟動時記過 ERROR，但這一層的
    每次生成都會失敗（工廠報「端點不存在」）。不標出來的話，清單會把它顯示成正常的預設。

    Why the ``.env`` layer's custom endpoint was skipped, or None.
    """
    if not env_endpoint_is_custom() or get_endpoint() is not None:
        return None
    endpoint_id = env_endpoint_id()
    reason = skip_reason(endpoint_id) or "設定有誤"
    return f".env 的 LLM_PROVIDER='{endpoint_id}' 已被跳過（{reason}），使用這一層的生成會失敗"


def _label_for(resolved: ResolvedRoute) -> tuple[str | None, str, str | None]:
    """生效設定的 ``(effort, 標籤, 問題)``；端點需要的第三方套件沒裝或版本不符時退化並說明，
    不讓整份清單失敗。退化時標籤不去前綴，``.env`` 那一層的 effort 也取不到預設（為 None）。

    The effective effort, label and problem of a resolved route; a missing
    or incompatible third-party package degrades this one row instead of
    failing the list.

    Raises:
        ImportError: import 失敗的是專案自己的模組（程式錯誤，照常拋出）。
    """
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


def _view(entry: RouteKeyEntry, stored: dict[str, RouteChoice], read_ok: bool) -> RouteView:
    """以一份快照組出一個用途的狀態。Build one route's state from a snapshot."""
    resolved = routing.resolve_with(entry.key, stored, read_ok)
    effort, label, problem = _label_for(resolved)
    if problem is None and resolved.choice is None:
        problem = _env_endpoint_problem()
    return RouteView(
        entry=entry,
        resolved=resolved,
        effective_effort=effort,
        label=label,
        stored=stored.get(entry.key),
        problem=problem,
    )


async def _strict_snapshot() -> dict[str, RouteChoice]:
    """讀出全部設定；讀不到時記下原因並拋 503，不當成「沒有設定」。

    Read every stored choice, refusing to treat an unreadable DB as empty;
    the underlying error is logged with its traceback each time (management
    calls are rare, so this does not flood the log).

    Raises:
        LLMRouteStoreUnavailableError: 讀不到 DB 時。
    """
    try:
        return await routing.stored_choices_strict()
    except DATABASE_ERRORS as error:
        logger.warning("管理操作讀取 llm_routes 失敗：%s: %s", type(error).__name__, error, exc_info=True)
        raise LLMRouteStoreUnavailableError(_STORE_UNAVAILABLE_MESSAGE) from error


def views_from(stored: dict[str, RouteChoice], read_ok: bool) -> list[RouteView]:
    """以一份已讀出的快照組出全部用途的狀態（依顯示順序）。All views from one snapshot.

    TG 選單用它讓「選單版本」與「清單內容」來自同一次讀取。Lets the menu derive its
    version and its rows from the same read.
    """
    return [_view(entry, stored, read_ok) for entry in ordered_route_keys(stored)]


async def list_routes() -> tuple[list[RouteView], bool]:
    """全部用途鍵（含全域與孤兒鍵）目前的狀態，依顯示順序。All route keys' states.

    讀不到 DB 時仍回傳（各列為 ``.env`` 預設、標上 ``db_unavailable``；孤兒鍵看不到），
    由呼叫端顯示警示。

    Returns:
        ``(各用途狀態, DB 是否讀取成功)``。``(views, read_ok)``.
    """
    stored, read_ok = await routing.stored_choices()
    return views_from(stored, read_ok), read_ok


async def describe(route_key: str) -> RouteView:
    """單一用途鍵的狀態。The state of one route key.

    Raises:
        LLMRouteNotFoundError: 用途鍵既未宣告、DB 中也沒有。
        LLMRouteStoreUnavailableError: 讀不到 DB（無法判斷孤兒鍵是否存在、本身存了什麼）。
    """
    stored = await _strict_snapshot()
    for entry in ordered_route_keys(stored):
        if entry.key == route_key:
            return _view(entry, stored, True)
    raise LLMRouteNotFoundError(f"用途鍵 '{route_key}' 不存在")


def _declared_entry(route_key: str) -> RouteKeyEntry | None:
    """已宣告的鍵（或全域）的清單項目；未宣告時為 None。The entry for a declared key."""
    if route_key == GLOBAL_ROUTE_KEY:
        return RouteKeyEntry(GLOBAL_ROUTE_KEY, GLOBAL_DESCRIPTION, True)
    for route in routing.list_route_keys():
        if route.key == route_key:
            return RouteKeyEntry(route.key, route.description, True)
    return None


def validate_choice(choice: RouteChoice, *, route_key: str = "?", updated_by: str = "?") -> None:
    """白名單檢查加試建客戶端。Whitelist check plus a trial client build.

    試建會攔下的部署問題：CLI 找不到、金鑰沒設（``LLMServiceError``）、端點需要的第三方套件
    沒裝或版本不符（``ImportError``）、CLI 工作目錄無法建立（``OSError``）。import 失敗的若是
    專案自己的 ``app.*`` 模組（註冊表模組名、provider 內 import 名稱打錯、provider 改名）則是
    程式錯誤，照常拋出——裝套件解決不了（見 :func:`~app.infrastructure.llm.routing.is_internal_import`）。

    Args:
        choice: 要檢查的選擇。The choice.
        route_key: 用途鍵（只用於 log）。Route key, for logging.
        updated_by: 修改者（只用於 log）。Who asked, for logging.

    Raises:
        InvalidLLMRouteError: 不在白名單，或試建失敗（訊息含原因）。
    """
    problem = routing.check_choice(choice)
    if problem is not None:
        raise InvalidLLMRouteError(problem)
    try:
        factory.create_llm_client(
            endpoint=choice.endpoint,
            model=choice.model,
            effort=choice.effort if choice.effort is not None else OMIT_EFFORT,
        )
    except (LLMServiceError, ImportError, OSError) as error:
        if isinstance(error, ImportError) and is_internal_import(error):
            raise
        if isinstance(error, ImportError):
            reason = describe_import_error(choice.endpoint, error)
        elif isinstance(error, OSError):
            reason = f"無法建立 CLI 工作目錄或檔案（{error}）"
        else:
            reason = str(error)
        logger.warning(
            "LLM 用途 '%s' 的設定試建客戶端失敗（%s / %s / %s，%s）：%s",
            route_key, choice.endpoint, choice.model, choice.effort, updated_by, reason,
            exc_info=not isinstance(error, LLMServiceError),
        )
        raise InvalidLLMRouteError(f"試建客戶端失敗：{reason}") from error


async def _row_exists(route_key: str) -> bool:
    """該用途鍵是否已有一列。Whether a row exists for the route key.

    Raises:
        DBAPIError / sqlalchemy.exc.TimeoutError / OSError: 資料庫異常。
    """
    async with _session_factory()() as session:
        result = await session.execute(
            select(LLMRouteSetting.route_key).where(col(LLMRouteSetting.route_key) == route_key)
        )
        return result.first() is not None


async def _upsert(route_key: str, choice: RouteChoice, updated_by: str) -> None:
    """有就更新、沒有就新增一列（先查再寫，不是原子操作：並發的第一次寫入可能撞唯一鍵）。

    Update the row if present, insert it otherwise.

    Raises:
        IntegrityError: 查的時候還沒有、提交時已被另一筆寫入建立。
        DBAPIError / sqlalchemy.exc.TimeoutError / OSError: 其他資料庫異常。
    """
    async with _session_factory()() as session:
        result = await session.execute(
            select(LLMRouteSetting).where(col(LLMRouteSetting.route_key) == route_key)
        )
        row = result.scalar_one_or_none()
        if row is None:
            session.add(LLMRouteSetting(
                route_key=route_key,
                endpoint=choice.endpoint,
                model=choice.model,
                effort=choice.effort,
                updated_by=updated_by,
            ))
        else:
            row.endpoint, row.model, row.effort, row.updated_by = (
                choice.endpoint, choice.model, choice.effort, updated_by,
            )
        await session.commit()


async def set_route(
    route_key: str, endpoint: str, model: str, effort: str | None, *, updated_by: str
) -> RouteView:
    """驗證並寫入一個用途鍵的選擇（有就更新、沒有就新增），寫入後立即生效。

    Validate and store the choice for a route key (update or insert), then
    invalidate the routing cache so the next call uses it.

    回傳的狀態在寫入**之前**就由要寫的值組好、不再讀 DB：提交之後只剩清快取與記 log，
    寫入一旦提交就一定回報成功，不會因為讀回或組狀態失敗而被說成「操作失敗」。用途層的
    生效值就是要寫的值（已通過白名單）。

    The returned state is built from the value before writing and never
    re-read, so nothing that can fail runs after the commit.

    Args:
        route_key: 用途鍵或 ``*``。Route key or ``*``.
        endpoint: 端點 ID（去空白、不分大小寫）。Endpoint ID, case-insensitive.
        model: 模型名（去空白、區分大小寫）。Model name, case-sensitive.
        effort: 思考深度（去空白、不分大小寫）；模型不使用 effort 時為 None，空字串視同
            None。Effort, case-insensitive; None or "" when the model takes none.
        updated_by: 修改者（``tg:<user id>`` 或 ``api``）。Who made the change.

    Returns:
        寫入後的狀態。The state after the write.

    Raises:
        LLMRouteNotFoundError: 用途鍵未宣告（DB 中有孤兒列也一樣：孤兒鍵只能刪除）。
        InvalidLLMRouteError: 不在白名單，或試建客戶端失敗。
        LLMRouteStoreUnavailableError: 寫入時資料庫異常（結果未知，請重新查看）。
    """
    entry = _declared_entry(route_key)
    if entry is None:
        raise LLMRouteNotFoundError(f"用途鍵 '{route_key}' 未在程式中宣告，不能設定")
    choice = RouteChoice(endpoint.strip().lower(), model.strip(), (effort or "").strip().lower() or None)
    validate_choice(choice, route_key=route_key, updated_by=updated_by)
    source: routing.RouteSource = "global" if route_key == GLOBAL_ROUTE_KEY else "route"
    view = RouteView(
        entry=entry,
        resolved=ResolvedRoute(route_key, source, choice),
        effective_effort=choice.effort,
        label=preview_label(choice.endpoint, choice.model, choice.effort),
        stored=choice,
    )

    try:
        try:
            await _upsert(route_key, choice, updated_by)
        except IntegrityError as first_error:
            # 同一用途鍵的第一次寫入同時發生（API 與 TG 同時、連按兩下）：查的時候還沒有、提交時
            # 另一筆剛插入而撞到唯一鍵。先確認那一列真的存在了才重做（會走「更新那一列」）；不存在
            # 就是別的約束（NOT NULL、schema 沒跑 migration…），原樣往外拋。重做或往外拋的 IntegrityError
            # 都是 DBAPIError，由外層 DATABASE_ERRORS 照一般資料庫錯誤處理（記 traceback、回 503）
            if not await _row_exists(route_key):
                raise
            logger.info(
                "寫入 llm_routes 時 %s 剛被另一筆寫入建立，改為更新該列（%s）：%s",
                route_key, updated_by, first_error.orig,
            )
            await _upsert(route_key, choice, updated_by)
    except DATABASE_ERRORS as error:
        logger.exception(
            "寫入 llm_routes 失敗：%s → %s / %s（%s）", route_key, choice.endpoint, choice.model, updated_by
        )
        raise LLMRouteStoreUnavailableError(_WRITE_FAILED_MESSAGE) from error
    finally:
        # 不論成敗都清：提交後才出錯（例如關閉 session 時被取消、非 DBAPI 的 SQLAlchemy 錯誤）時
        # DB 可能已寫入，不能留著舊快取讓生成一直用舊設定
        routing.invalidate_cache()
    logger.info(
        "LLM 用途設定已更新：%s → %s / %s / %s（%s）",
        route_key, choice.endpoint, choice.model, choice.effort, updated_by,
    )
    return view


@dataclass(frozen=True)
class DeleteResult:
    """恢復預設的結果。The result of restoring the default.

    ``view`` 為 None 有兩種意思，以 ``view_unavailable`` 區分：孤兒鍵刪除後本來就沒有狀態
    （False），或刪除已提交、但組不出刪除後的狀態（True，原因在後端 log）。

    Attributes:
        deleted: 是否真的刪了一列（原本沒有設定時為 False）。Whether a row was removed.
        view: 刪除後的狀態；孤兒鍵、或組不出狀態時為 None。The state afterwards.
        view_unavailable: 刪除已提交，但組不出刪除後的狀態。True when the delete
            committed but the state afterwards could not be built.
    """

    deleted: bool
    view: RouteView | None
    view_unavailable: bool = False

    def __post_init__(self) -> None:
        if self.view_unavailable and self.view is not None:
            raise ValueError("view_unavailable 時不應有 view")


async def delete_route(route_key: str, *, updated_by: str) -> DeleteResult:
    """刪除一個用途鍵的設定（恢復預設：退回全域，或 ``.env``）。孤兒鍵也可以刪（清理用）。

    Delete a route key's row so it falls back to the next layer; orphan keys
    may be deleted too.

    刪除後的狀態要再讀一次 DB（才知道退回到哪一層）；那次讀不到時不拋錯（刪除已提交），
    狀態標上 ``db_unavailable``。

    Args:
        route_key: 用途鍵或 ``*``。Route key or ``*``.
        updated_by: 操作者（只寫進 log，該列已刪除）。Who did it; logged only.

    Raises:
        LLMRouteNotFoundError: 用途鍵既未宣告、DB 中也沒有。
        LLMRouteStoreUnavailableError: 讀不到 DB（無法判斷孤兒鍵是否存在），或刪除時資料庫
            異常（結果未知，請重新查看）。
    """
    entry = _declared_entry(route_key)
    if entry is None:
        stored = await _strict_snapshot()
        if route_key not in stored:
            raise LLMRouteNotFoundError(f"用途鍵 '{route_key}' 不存在")

    try:
        async with _session_factory()() as session:
            # aiosqlite / aiomysql 的 DELETE 都回報實際刪除的列數（不會是 -1）
            result = cast(
                CursorResult[Any],
                await session.execute(delete(LLMRouteSetting).where(col(LLMRouteSetting.route_key) == route_key)),
            )
            await session.commit()
    except DATABASE_ERRORS as error:
        logger.exception("刪除 llm_routes 失敗：%s（%s）", route_key, updated_by)
        raise LLMRouteStoreUnavailableError(_WRITE_FAILED_MESSAGE) from error
    finally:
        routing.invalidate_cache()  # 理由同 set_route：提交後才出錯時 DB 可能已刪除
    deleted = bool(result.rowcount)
    if deleted:
        logger.info("LLM 用途設定已刪除（恢復預設）：%s（%s）", route_key, updated_by)
    if entry is None:
        return DeleteResult(deleted, None)
    try:
        stored_after, read_ok = await routing.stored_choices()
        view = _view(entry, stored_after, read_ok)
    except Exception:  # noqa: BLE001 - 刪除已提交：組不出刪除後狀態時只少回報狀態，不報成失敗
        logger.exception("llm_routes 已刪除 %s，但組出刪除後的狀態失敗", route_key)
        return DeleteResult(deleted, None, view_unavailable=True)
    return DeleteResult(deleted, view)


def to_read(view: RouteView) -> LLMRouteRead:
    """服務層的狀態轉成對外的回應模型（API 與診斷端點共用）。Convert a view into the response model."""
    resolved = view.resolved
    return LLMRouteRead(
        route_key=view.route_key,
        description=view.description,
        declared=view.declared,
        source=resolved.source,
        effective=RouteChoiceRead(endpoint=resolved.endpoint, model=resolved.model, effort=view.effective_effort),
        label=view.label,
        stored=(
            RouteChoiceRead(endpoint=view.stored.endpoint, model=view.stored.model, effort=view.stored.effort)
            if view.stored is not None
            else None
        ),
        rejected=[RejectedLayerRead(route_key=layer.route_key, reason=layer.reason) for layer in resolved.rejected],
        db_unavailable=resolved.db_unavailable,
        problem=view.problem,
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
