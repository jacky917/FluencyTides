"""LLM 模型路由測試（模型路由計畫 D1 / D2 / D4）。

Tests for route-key declaration, the route → global → ``.env`` resolution
order, whitelist enforcement on stored rows, the in-process cache, the
DB-failure fallback (database errors only), and how resolved settings reach
the factory.
"""

import asyncio
import importlib
import logging
from contextlib import ExitStack
from unittest import mock

import pytest
from pydantic import SecretStr
from sqlalchemy.exc import InvalidRequestError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from app.core.config import LLMEndpointSettings, settings
from app.core.exceptions import LLMServiceError
from app.infrastructure.database.models import LLMRouteSetting
from app.infrastructure.llm import routing
from app.infrastructure.llm.base import OMIT_EFFORT
from app.infrastructure.llm.claude_code_client import ClaudeCodeLLMClient
from app.infrastructure.llm.client import LLMClient
from app.infrastructure.llm.routing import (
    GLOBAL_ROUTE_KEY,
    LLMRoute,
    RouteChoice,
    list_route_keys,
    llm_route,
)

# conftest 的 autouse fixture 會把 _fetch_choices 換成空結果；真表測試要用原函式
_REAL_FETCH_CHOICES = routing._fetch_choices

# 本計畫為現有呼叫點取的鍵（D2 表）與宣告它們的模組
_PRODUCTION_ROUTES = {
    "expression_correction": "app.services.task_handlers.expression_handler",
    "jp_verb_pair": "app.services.task_handlers.jp_verb_pair_handler",
    "jp_core_verb": "app.services.task_handlers.jp_core_verb_handler",
    "verb_pair_example": "app.services.task_handlers.example.verb_pair_example_handler",
    "jp_verb_reading": "app.services.jp_verb_reading_service",
}


@pytest.fixture(autouse=True)
def _isolated_declarations():
    """測試中宣告的鍵在結束後移除；正式模組先 import，它們的鍵不會被誤刪。"""
    for module in _PRODUCTION_ROUTES.values():
        importlib.import_module(module)
    before = set(routing._DECLARED)
    yield
    for key in set(routing._DECLARED) - before:
        del routing._DECLARED[key]


@pytest.fixture
def whitelist():
    """.env：google（舊式）＋中轉站 yinli＋claude-code 白名單。"""
    values = {
        "LLM_PROVIDER": "google",
        "LLM_MODEL_NAME": "gemini-3.5-flash",
        "LLM_API_KEY": "legacy-key",
        "LLM_BASE_URL": "https://legacy.example/v1",
        "LLM_ENDPOINTS": {
            "yinli": LLMEndpointSettings(
                base_url="https://yinli.example/v1",
                api_key=SecretStr("k"),
                models=("m1", "m2"),
            ),
        },
        "LLM_CLAUDE_CODE_MODELS": ("opus@low|high", "haiku"),
        "LLM_ANTIGRAVITY_MODELS": (),
    }
    with ExitStack() as stack:
        for name, value in values.items():
            stack.enter_context(mock.patch.object(settings, name, value))
        yield


def _rows(**choices: RouteChoice):
    """以 ``_fetch_choices`` 回傳指定的設定列（route_key → 選擇）。

    ``*`` 不是合法的關鍵字參數名，以 ``GLOBAL`` 代表全域。
    """
    stored = {(GLOBAL_ROUTE_KEY if key == "GLOBAL" else key): value for key, value in choices.items()}
    return mock.patch.object(routing, "_fetch_choices", mock.AsyncMock(return_value=stored))


def _resolve(key: str):
    return asyncio.run(routing.resolve(key))


# ----------------------------------------------------------------------
# 宣告（Declaration）
# ----------------------------------------------------------------------


@pytest.mark.parametrize("key", ["scenario.ask", "jp.verb_pair.child-card", "A1", "x" * 64])
def test_free_form_keys_are_accepted(key):
    """用途鍵可自由命名，含 . _ -。"""
    route = llm_route(key, "測試")
    assert route.key == key
    assert route in list_route_keys()


@pytest.mark.parametrize("key", ["*", "", "has space", "中文", "x" * 65, "a/b", "a\n"])
def test_invalid_keys_raise(key):
    """不合規的鍵在宣告當下拋錯；直接建構 LLMRoute 也一樣。"""
    with pytest.raises(ValueError, match="不合規"):
        llm_route(key, "測試")
    with pytest.raises(ValueError, match="不合規"):
        LLMRoute(key, "測試")


def test_duplicate_key_raises():
    """重複宣告在 import 時就會被發現。"""
    llm_route("dup.key", "第一次")
    with pytest.raises(ValueError, match="重複宣告"):
        llm_route("dup.key", "第二次")


def test_route_is_immutable():
    """用途鍵物件不可變，登記表的鍵與物件不會脫節。"""
    route = llm_route("frozen.key", "測試")
    with pytest.raises(AttributeError):
        route.key = "other"  # type: ignore[misc]


def test_list_route_keys_sorted():
    """清單依鍵排序。"""
    llm_route("zz.test", "z")
    llm_route("aa.test", "a")
    keys = [route.key for route in list_route_keys()]
    assert keys == sorted(keys)


@pytest.mark.parametrize(("key", "module"), sorted(_PRODUCTION_ROUTES.items()))
def test_call_sites_declare_their_keys(key, module):
    """現有呼叫點的模組在模組層宣告各自的用途鍵，且已登記。"""
    declared = [
        value for value in vars(importlib.import_module(module)).values()
        if isinstance(value, LLMRoute)
    ]
    assert [route.key for route in declared] == [key]
    assert routing._DECLARED[key] is declared[0]


# ----------------------------------------------------------------------
# 解析順序（Resolution order）
# ----------------------------------------------------------------------


def test_no_rows_resolves_to_env(whitelist):
    """DB 沒有任何設定：走 .env 那一層。"""
    with _rows():
        resolved = _resolve("jp_verb_pair")

    assert resolved.source == "env"
    assert resolved.choice is None
    assert (resolved.endpoint, resolved.model, resolved.effort) == ("google", "gemini-3.5-flash", None)
    assert resolved.rejected == ()
    assert resolved.db_unavailable is False


def test_global_row_applies_when_route_has_none(whitelist):
    """用途沒有設定時用全域。"""
    with _rows(GLOBAL=RouteChoice("yinli", "m1", None)):
        resolved = _resolve("jp_verb_pair")

    assert resolved.source == "global"
    assert (resolved.endpoint, resolved.model) == ("yinli", "m1")


def test_route_row_beats_global(whitelist):
    """用途有設定時優先於全域。"""
    with _rows(
        GLOBAL=RouteChoice("yinli", "m1", None),
        jp_verb_pair=RouteChoice("claude-code", "opus", "high"),
    ):
        resolved = _resolve("jp_verb_pair")
        other = _resolve("expression_correction")

    assert (resolved.source, resolved.endpoint, resolved.effort) == ("route", "claude-code", "high")
    assert other.source == "global"


def test_dot_in_key_has_no_hierarchy(whitelist):
    """``scenario.ask`` 沒設定時不會去找 ``scenario``，直接退回全域。"""
    with _rows(scenario=RouteChoice("yinli", "m2", None)):
        resolved = _resolve("scenario.ask")

    assert resolved.source == "env"


def test_global_key_resolves_global_then_env(whitelist):
    """查 ``*`` 本身：全域 → .env。"""
    with _rows(GLOBAL=RouteChoice("yinli", "m1", None)):
        assert _resolve(GLOBAL_ROUTE_KEY).source == "global"
    routing.invalidate_cache()
    with _rows():
        assert _resolve(GLOBAL_ROUTE_KEY).source == "env"


def test_empty_effort_normalizes_to_none():
    """DB 存的 effort 空字串視同 None（模型不使用 effort）。"""
    assert RouteChoice("yinli", "m1", "").effort is None


# ----------------------------------------------------------------------
# 白名單（Whitelist enforcement）
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("choice", "fragment"),
    [
        (RouteChoice("gone", "m1", None), "端點 'gone' 不存在"),
        (RouteChoice("yinli", "m9", None), "模型 'm9' 不在端點 'yinli' 的白名單"),
        (RouteChoice("claude-code", "opus", "max"), "effort 'max' 不在模型 'opus' 的白名單"),
        (RouteChoice("claude-code", "opus", None), "effort '（未設定）' 不在模型 'opus' 的白名單"),
        (RouteChoice("claude-code", "haiku", "high"), "不使用 effort"),
        (RouteChoice("yinli", "m1", "high"), "不使用 effort"),
        (RouteChoice("", "gemini-3.5-flash", None), "不存在"),
    ],
)
def test_row_outside_whitelist_falls_back(whitelist, choice, fragment, caplog):
    """不在白名單的設定不使用、退回下一層，記 WARNING 並列在 rejected。"""
    with _rows(GLOBAL=RouteChoice("yinli", "m1", None), jp_verb_pair=choice), \
         caplog.at_level(logging.WARNING, logger=routing.__name__):
        resolved = _resolve("jp_verb_pair")

    assert resolved.source == "global"
    assert [layer.route_key for layer in resolved.rejected] == ["jp_verb_pair"]
    assert fragment in resolved.rejected[0].reason
    assert fragment in caplog.text


def test_unlisted_endpoint_is_reported_distinctly(whitelist):
    """只供 .env 那一層的端點（LLM_PROVIDER 留空）與「不存在」分開報。"""
    with mock.patch.object(settings, "LLM_PROVIDER", ""), \
         _rows(jp_verb_pair=RouteChoice("", "gemini-3.5-flash", None)):
        resolved = _resolve("jp_verb_pair")

    assert resolved.source == "env"
    assert "僅供 .env 那一層使用" in resolved.rejected[0].reason


def test_both_layers_invalid_fall_back_to_env(whitelist):
    """用途與全域都失效時退回 .env，兩則原因依嘗試順序列出。"""
    with _rows(GLOBAL=RouteChoice("gone", "x", None), jp_verb_pair=RouteChoice("yinli", "m9", None)):
        resolved = _resolve("jp_verb_pair")

    assert resolved.source == "env"
    assert [layer.route_key for layer in resolved.rejected] == ["jp_verb_pair", "*"]


def test_env_layer_choice_is_always_whitelisted(whitelist):
    """「.env 那一層」的組合自動在白名單內，可被 DB 選用。"""
    with _rows(jp_verb_pair=RouteChoice("google", "gemini-3.5-flash", None)):
        assert _resolve("jp_verb_pair").source == "route"


def test_rejection_warned_once(whitelist, caplog):
    """同一則失效警告只記一次（每次生成都會解析）。"""
    with _rows(jp_verb_pair=RouteChoice("gone", "x", None)), \
         caplog.at_level(logging.WARNING, logger=routing.__name__):
        for _ in range(3):
            _resolve("jp_verb_pair")

    assert caplog.text.count("已不在白名單") == 1


# ----------------------------------------------------------------------
# 快取與 DB 失敗（Cache and DB failure）
# ----------------------------------------------------------------------


def _db_error() -> OperationalError:
    return OperationalError("SELECT ...", {}, Exception("no such table: llm_routes"))


def test_rows_cached_until_invalidated(whitelist):
    """設定讀一次後快取；invalidate_cache 後重新讀取。"""
    fetch = mock.AsyncMock(return_value={"jp_verb_pair": RouteChoice("yinli", "m1", None)})
    with mock.patch.object(routing, "_fetch_choices", fetch):
        _resolve("jp_verb_pair")
        _resolve("expression_correction")
        assert fetch.await_count == 1

        fetch.return_value = {"jp_verb_pair": RouteChoice("yinli", "m2", None)}
        routing.invalidate_cache()
        assert _resolve("jp_verb_pair").model == "m2"
        assert fetch.await_count == 2


@pytest.mark.parametrize(
    "error",
    [PoolTimeoutError("QueuePool limit reached"), ConnectionRefusedError("connection refused")],
    ids=["pool-timeout", "os-error"],
)
def test_other_db_errors_also_fall_back(whitelist, error):
    """連線池逾時與網路錯誤同樣視為 DB 異常，退回 .env 並標記 db_unavailable。"""
    with mock.patch.object(routing, "_fetch_choices", mock.AsyncMock(side_effect=error)):
        resolved = _resolve("jp_verb_pair")

    assert (resolved.source, resolved.db_unavailable) == ("env", True)


def test_db_failure_falls_back_to_env_and_retries(whitelist, caplog):
    """DB 異常：退回 .env、只在故障開始時記 WARNING（附 traceback）；失敗不快取，下次重試。"""
    fetch = mock.AsyncMock(side_effect=_db_error())
    with mock.patch.object(routing, "_fetch_choices", fetch), \
         caplog.at_level(logging.WARNING, logger=routing.__name__):
        first = _resolve("jp_verb_pair")
        assert _resolve("jp_verb_pair").source == "env"

    assert (first.source, first.db_unavailable) == ("env", True)
    assert fetch.await_count == 2
    warnings = [record for record in caplog.records if "讀取 LLM 用途設定" in record.getMessage()]
    assert len(warnings) == 1
    assert warnings[0].exc_info is not None
    assert "llm_routes" in warnings[0].getMessage()


def test_db_recovery_logged_and_next_failure_warned_again(whitelist, caplog):
    """故障 → 恢復記 INFO；恢復後再故障會再記 WARNING。"""
    fetch = mock.AsyncMock(side_effect=[_db_error(), {}])
    with mock.patch.object(routing, "_fetch_choices", fetch), \
         caplog.at_level(logging.INFO, logger=routing.__name__):
        _resolve("jp_verb_pair")
        _resolve("jp_verb_pair")
        routing.invalidate_cache()
        fetch.side_effect = _db_error()
        _resolve("jp_verb_pair")

    messages = [record.getMessage() for record in caplog.records]
    assert sum("讀取 LLM 用途設定" in message for message in messages) == 2
    assert sum("已恢復讀取" in message for message in messages) == 1


@pytest.mark.parametrize(
    "error",
    [
        AttributeError("'LLMRouteSetting' object has no attribute 'effort'"),
        InvalidRequestError("mapper 設定錯誤"),
        ModuleNotFoundError("app.infrastructure.database.models"),
    ],
    ids=["attribute", "orm-config", "import"],
)
def test_programming_error_is_not_swallowed(whitelist, error):
    """程式錯誤（屬性、ORM 設定、import）不當成 DB 故障，照常往上拋。"""
    fetch = mock.AsyncMock(side_effect=error)
    with mock.patch.object(routing, "_fetch_choices", fetch), pytest.raises(type(error)):
        _resolve("jp_verb_pair")


# ----------------------------------------------------------------------
# 交給工廠（Handing off to the factory）
# ----------------------------------------------------------------------


def test_env_layer_calls_factory_without_arguments(whitelist):
    """.env 那一層以不帶參數的 create_llm_client() 建立——與導入路由前相同。"""
    route = llm_route("test.env", "測試")
    with _rows(), mock.patch.object(routing.factory, "create_llm_client") as create:
        client = asyncio.run(route.client())

    create.assert_called_once_with()
    assert client is create.return_value


@pytest.mark.parametrize(
    ("choice", "expected_effort"),
    [
        (RouteChoice("claude-code", "opus", "low"), "low"),
        (RouteChoice("claude-code", "haiku", None), OMIT_EFFORT),
        (RouteChoice("yinli", "m1", None), OMIT_EFFORT),
    ],
)
def test_db_layer_passes_endpoint_model_effort(whitelist, choice, expected_effort):
    """DB 層傳端點、模型與 effort；模型不使用 effort 時傳 OMIT_EFFORT。"""
    route = llm_route("test.db", "測試")
    with _rows(**{"test.db": choice}), \
         mock.patch.object(routing.factory, "create_llm_client") as create:
        asyncio.run(route.client())

    create.assert_called_once_with(
        endpoint=choice.endpoint, model=choice.model, effort=expected_effort
    )


def test_db_layer_construction_failure_raises_with_context(whitelist, caplog):
    """DB 層建立失敗（如 CLI 不在）不退回 .env：記 ERROR 補路由脈絡後原樣拋出。"""
    route = llm_route("test.fail", "測試")
    error = LLMServiceError("找不到 claude 執行檔")
    with _rows(**{"test.fail": RouteChoice("claude-code", "opus", "high")}), \
         mock.patch.object(routing.factory, "create_llm_client", side_effect=error), \
         caplog.at_level(logging.ERROR, logger=routing.__name__), \
         pytest.raises(LLMServiceError) as exc_info:
        asyncio.run(route.client())

    assert exc_info.value is error
    assert "LLM 用途 'test.fail' 依 route 層設定" in caplog.text
    # 脈絡也跟著例外走（handler / TG 回報時看得到是哪個設定造成的）
    assert any("test.fail" in note for note in getattr(exc_info.value, "__notes__", []))


@pytest.mark.parametrize(
    ("rows", "layer"),
    [({"test.pkg": RouteChoice("claude-code", "opus", "high")}, "route"), ({}, "env")],
    ids=["db-layer", "env-layer"],
)
def test_missing_third_party_package_becomes_llm_error_with_context(whitelist, caplog, rows, layer):
    """端點需要的第三方套件沒裝：不論哪一層都轉成 LLMServiceError，訊息帶用途、層與套件名，
    生成端看得出是哪個設定、能怎麼修（原本是裸的 ModuleNotFoundError，沒有路由脈絡）。"""
    route = llm_route("test.pkg", "測試")
    error = ModuleNotFoundError("No module named 'jsonschema'", name="jsonschema")
    with _rows(**rows), \
         mock.patch.object(routing.factory, "create_llm_client", side_effect=error), \
         caplog.at_level(logging.ERROR, logger=routing.__name__), \
         pytest.raises(LLMServiceError) as exc_info:
        asyncio.run(route.client())

    message = str(exc_info.value)
    assert f"LLM 用途 'test.pkg' 依 {layer} 層設定" in message and "套件未安裝（jsonschema）" in message
    assert exc_info.value.__cause__ is error
    assert message in caplog.text


def test_missing_internal_module_is_not_disguised(whitelist):
    """import 失敗的是專案自己的模組：程式錯誤，照常拋出原本的例外。"""
    route = llm_route("test.internal", "測試")
    error = ModuleNotFoundError("No module named 'app.infrastructure.llm.nope'", name="app.infrastructure.llm.nope")
    with mock.patch.object(routing.factory, "create_llm_client", side_effect=error), \
         pytest.raises(ModuleNotFoundError):
        asyncio.run(route.client())


# ----------------------------------------------------------------------
# 真實資料表（Real table, in-memory SQLite）
# ----------------------------------------------------------------------


def _run_against_sqlite(rows: list[LLMRouteSetting], key: str, *, create_table: bool = True):
    """建一個記憶體 SQLite（可選擇不建表），寫入設定列，讓路由模組以原函式讀它。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def scenario():
        if create_table:
            async with engine.begin() as conn:
                await conn.run_sync(
                    lambda sync_conn: SQLModel.metadata.create_all(
                        sync_conn, tables=[LLMRouteSetting.__table__]
                    )
                )
            async with session_factory() as session:
                session.add_all(rows)
                await session.commit()
        try:
            return await routing.resolve(key)
        finally:
            await engine.dispose()

    routing.invalidate_cache()
    with mock.patch.object(routing, "_fetch_choices", _REAL_FETCH_CHOICES), \
         mock.patch("app.infrastructure.database.database.async_session_factory", session_factory):
        return asyncio.run(scenario())


def test_reads_rows_from_real_table(whitelist):
    """從真實的 llm_routes 讀出設定並解析；effort 空字串視同 None。"""
    rows = [
        LLMRouteSetting(route_key="*", endpoint="yinli", model="m1", effort="", updated_by="api"),
        LLMRouteSetting(
            route_key="jp_verb_pair", endpoint="claude-code", model="opus", effort="high", updated_by="tg:1"
        ),
    ]
    resolved = _run_against_sqlite(rows, "jp_verb_pair")
    fallback = _run_against_sqlite(
        [LLMRouteSetting(route_key="*", endpoint="yinli", model="m1", effort="", updated_by="api")],
        "expression_correction",
    )

    assert (resolved.source, resolved.endpoint, resolved.model, resolved.effort) == (
        "route", "claude-code", "opus", "high",
    )
    assert (fallback.source, fallback.endpoint, fallback.effort) == ("global", "yinli", None)


def test_missing_table_falls_back_to_env(whitelist, caplog):
    """資料表不存在（例如尚未建表）：退回 .env，警告點出 llm_routes。"""
    with caplog.at_level(logging.WARNING, logger=routing.__name__):
        resolved = _run_against_sqlite([], "jp_verb_pair", create_table=False)

    assert resolved.source == "env"
    assert "no such table: llm_routes" in caplog.text


# ----------------------------------------------------------------------
# 端到端：DB 選擇 → 用途鍵 → 真實工廠與客戶端（不 mock 工廠）
# ----------------------------------------------------------------------


def test_db_choice_builds_real_relay_client(whitelist):
    """DB 選了中轉站：route.client() 建出用該端點網址與金鑰的 LLMClient，標籤前綴為端點 ID。"""
    route = llm_route("test.e2e.relay", "測試")
    with _rows(**{"test.e2e.relay": RouteChoice("yinli", "m1", None)}):
        client = asyncio.run(route.client())

    assert isinstance(client, LLMClient)
    assert str(client._client.base_url) == "https://yinli.example/v1/"
    assert client._client.api_key == "k"
    assert client.model_label == "(yinli)m1"


def test_db_choice_without_effort_omits_cli_flag(whitelist, tmp_path):
    """DB 選了不帶 effort 的 CLI 模型：真實客戶端的命令列沒有 --effort，標籤沒有 @。"""
    route = llm_route("test.e2e.cli", "測試")
    with _rows(**{"test.e2e.cli": RouteChoice("claude-code", "haiku", None)}), \
         mock.patch.object(settings, "LLM_CLAUDE_CODE_WORKDIR", str(tmp_path / "cc")), \
         mock.patch.object(settings, "LLM_CLAUDE_CODE_AUDIT_DIR", ""), \
         mock.patch.object(settings, "LLM_CLAUDE_CODE_OAUTH_TOKEN", ""), \
         mock.patch.object(ClaudeCodeLLMClient, "_resolve_cli_path", return_value="/fake/claude"):
        client = asyncio.run(route.client())
        command = client._build_command("SYS", "{}")

    assert "--effort" not in command
    assert client.model_label == "(claude-code)haiku"


def test_env_layer_construction_failure_is_raised_unchanged(whitelist):
    """.env 那一層建立失敗（金鑰沒設、找不到 CLI）：與導入路由前相同，原樣拋出同一個 LLMServiceError，
    不加路由脈絡（沒有 DB 設定可改），也不會因為沒有選擇而變成 AttributeError。"""
    route = llm_route("test.envfail", "測試")
    error = LLMServiceError("API_KEY 未設定")
    with _rows(), \
         mock.patch.object(routing.factory, "create_llm_client", side_effect=error), \
         pytest.raises(LLMServiceError) as exc_info:
        asyncio.run(route.client())

    assert exc_info.value is error
    assert not getattr(exc_info.value, "__notes__", [])


def test_cli_workdir_failure_becomes_llm_error_with_context(whitelist, caplog):
    """CLI 工作目錄建不起來（掛載或權限有誤，OSError）：轉成帶用途與層的 LLMServiceError，
    不是沒有脈絡的 500。"""
    route = llm_route("test.workdir", "測試")
    error = PermissionError(13, "Permission denied", "/app/.claude_code_workdir")
    with _rows(**{"test.workdir": RouteChoice("claude-code", "opus", "high")}), \
         mock.patch.object(routing.factory, "create_llm_client", side_effect=error), \
         caplog.at_level(logging.ERROR, logger=routing.__name__), \
         pytest.raises(LLMServiceError) as exc_info:
        asyncio.run(route.client())

    message = str(exc_info.value)
    assert "LLM 用途 'test.workdir' 依 route 層設定" in message and "無法建立 CLI 工作目錄" in message
    assert exc_info.value.__cause__ is error
