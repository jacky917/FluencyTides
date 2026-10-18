"""LLM 用途設定寫入服務的測試（模型路由計畫 D6）。

Tests for the route write service against a real temporary SQLite table:
validation (declared key, whitelist, trial client build), upsert, restore
default, orphan keys, listing order, single-snapshot reads, the
"database unreadable" path, and label / effort previews.
"""

import asyncio
from unittest import mock

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.core.exceptions import (
    InvalidLLMRouteError,
    LLMRouteNotFoundError,
    LLMRouteStoreUnavailableError,
    LLMServiceError,
)
from app.infrastructure.database.models import LLMRouteSetting
from app.infrastructure.llm import factory, routing
from app.infrastructure.llm.claude_code_client import ClaudeCodeLLMClient
from app.infrastructure.llm.routing import GLOBAL_ROUTE_KEY
from app.services import llm_route_service

KEY = "test.service"


@pytest.fixture(autouse=True)
def _declared_key(declare_route_key):
    declare_route_key(KEY, "服務測試")


def _run(coro):
    return asyncio.run(coro)


def _rows(session_factory) -> list[LLMRouteSetting]:
    async def fetch():
        async with session_factory() as session:
            return list((await session.execute(select(LLMRouteSetting))).scalars())

    return _run(fetch())


def _insert(session_factory, **fields) -> None:
    async def insert():
        async with session_factory() as session:
            session.add(LLMRouteSetting(updated_by="test", **fields))
            await session.commit()

    _run(insert())


def _db_down():
    """讓路由層讀 llm_routes 時得到資料庫錯誤。Make reads of llm_routes fail."""
    error = OperationalError("SELECT ...", {}, Exception("database is locked"))
    return mock.patch.object(routing, "_fetch_choices", mock.AsyncMock(side_effect=error))


# ----------------------------------------------------------------------
# 寫入（Writes）
# ----------------------------------------------------------------------


def test_set_route_inserts_and_takes_effect(llm_route_db, llm_whitelist):
    """寫入後立即生效（路由快取已清），並真的存進資料表。"""
    assert _run(routing.resolve(KEY)).source == "env"  # 先讓快取載入「沒有設定」，再證明寫入後確實清掉

    view = _run(llm_route_service.set_route(KEY, "yinli", "m1", None, updated_by="tg:42"))

    assert (view.resolved.source, view.resolved.endpoint, view.resolved.model) == ("route", "yinli", "m1")
    assert view.label == "(yinli)m1"
    assert _run(routing.resolve(KEY)).source == "route"
    [row] = _rows(llm_route_db)
    assert (row.route_key, row.endpoint, row.model, row.effort, row.updated_by) == (KEY, "yinli", "m1", None, "tg:42")


def test_set_route_updates_existing_row_and_normalizes(llm_route_db, llm_whitelist):
    """同一用途鍵再寫一次是更新，不會多一列；端點與 effort 不分大小寫、去空白。"""
    _run(llm_route_service.set_route(KEY, "yinli", "m1", None, updated_by="api"))
    view = _run(llm_route_service.set_route(KEY, " Claude-Code ", "opus", " LOW ", updated_by="api"))

    assert view.label == "(claude-code)opus@low"
    [row] = _rows(llm_route_db)
    assert (row.endpoint, row.model, row.effort) == ("claude-code", "opus", "low")


def test_empty_effort_is_stored_as_null(llm_route_db, llm_whitelist):
    """effort 空字串存成 NULL（模型不使用 effort）。"""
    _run(llm_route_service.set_route(KEY, "claude-code", "haiku", "", updated_by="api"))
    [row] = _rows(llm_route_db)
    assert row.effort is None


def test_global_key_can_be_set(llm_route_db, llm_whitelist):
    """全域 * 可以設定，未設定的用途隨之改用它。"""
    view = _run(llm_route_service.set_route(GLOBAL_ROUTE_KEY, "yinli", "m2", None, updated_by="api"))
    assert view.resolved.source == "global"
    assert _run(routing.resolve(KEY)).source == "global"


def test_committed_write_is_reported_without_reading_back(llm_route_db, llm_whitelist):
    """寫入提交後不再讀 DB：就算讀取此刻失敗，回報的仍是剛寫入的值（不會被說成失敗）。"""
    with _db_down():
        view = _run(llm_route_service.set_route(KEY, "yinli", "m1", None, updated_by="api"))

    assert (view.resolved.source, view.label, view.stored.model) == ("route", "(yinli)m1", "m1")
    assert len(_rows(llm_route_db)) == 1


def test_undeclared_key_is_rejected(llm_route_db, llm_whitelist):
    """未宣告的用途鍵不能寫（打錯字不會多出一筆孤兒設定）。"""
    with pytest.raises(LLMRouteNotFoundError):
        _run(llm_route_service.set_route("typo.key", "yinli", "m1", None, updated_by="api"))
    assert _rows(llm_route_db) == []


@pytest.mark.parametrize(
    ("endpoint", "model", "effort", "fragment"),
    [
        ("gone", "m1", None, "端點 'gone' 不存在"),
        ("yinli", "m9", None, "模型 'm9' 不在端點 'yinli' 的白名單"),
        ("claude-code", "opus", None, "effort '（未設定）' 不在模型 'opus' 的白名單"),
        ("claude-code", "opus", "max", "effort 'max' 不在模型 'opus' 的白名單"),
        ("claude-code", "haiku", "high", "不使用 effort"),
    ],
)
def test_choice_outside_whitelist_is_rejected(llm_route_db, llm_whitelist, endpoint, model, effort, fragment):
    """不在白名單（含該帶 effort 沒帶、不該帶卻帶）：拒絕且不寫入。"""
    with pytest.raises(InvalidLLMRouteError) as exc_info:
        _run(llm_route_service.set_route(KEY, endpoint, model, effort, updated_by="api"))

    assert fragment in exc_info.value.message
    assert _rows(llm_route_db) == []


@pytest.mark.parametrize(
    ("error", "fragment"),
    [
        (LLMServiceError("找不到 claude 執行檔"), "找不到 claude 執行檔"),
        (ModuleNotFoundError("No module named 'google.genai'", name="google.genai"), "套件未安裝（google.genai）"),
        (
            ImportError("cannot import name 'Client' from 'google.genai'", name="google.genai"),
            "套件版本不符或無法載入",
        ),
        (PermissionError(13, "Permission denied", "/app/.claude_code_workdir"), "無法建立 CLI 工作目錄"),
    ],
    ids=["llm-service-error", "missing-package", "incompatible-package", "workdir-not-writable"],
)
def test_failed_trial_build_is_rejected(llm_route_db, llm_whitelist, caplog, error, fragment):
    """試建客戶端失敗（CLI 不在、套件沒裝或版本不符、工作目錄不可寫）：存檔當下擋下（422）、記 WARNING、不寫入。"""
    with mock.patch.object(factory, "create_llm_client", side_effect=error), \
         pytest.raises(InvalidLLMRouteError) as exc_info:
        _run(llm_route_service.set_route(KEY, "claude-code", "opus", "high", updated_by="api"))

    assert "試建客戶端失敗" in exc_info.value.message and fragment in exc_info.value.message
    assert "試建客戶端失敗" in caplog.text
    assert _rows(llm_route_db) == []


@pytest.mark.parametrize(
    "error",
    [
        # import 機制會把 from X import Y 失敗時的 X 填進 name
        ImportError("cannot import name 'Foo' from 'app.infrastructure.llm.client'", name="app.infrastructure.llm.client"),
        ModuleNotFoundError("No module named 'app.infrastructure.llm.nope'", name="app.infrastructure.llm.nope"),
    ],
    ids=["import-name-typo", "missing-internal-module"],
)
def test_programming_import_error_is_not_disguised(llm_route_db, llm_whitelist, error):
    """程式錯誤照常拋出、不說成套件未安裝（422）：provider 內 import 名稱打錯，或缺的是專案
    自己的 app.* 模組（註冊表模組名打錯、provider 改名）——裝套件解決不了。"""
    with mock.patch.object(factory, "create_llm_client", side_effect=error), pytest.raises(type(error)):
        _run(llm_route_service.set_route(KEY, "yinli", "m1", None, updated_by="api"))
    assert _rows(llm_route_db) == []


@pytest.mark.parametrize(
    ("name", "internal"),
    [("app", True), ("app.infrastructure.llm.nope", True), ("appdirs", False), ("google.genai", False), (None, False)],
    ids=["app", "app-submodule", "app-prefixed-third-party", "third-party", "hand-raised"],
)
def test_internal_module_detection(name, internal):
    """只有 app 本身或 app.* 算專案內部；appdirs 這類同前綴的第三方套件、手動拋出（name 為 None）
    的例外都當成部署問題。"""
    for error_type in (ModuleNotFoundError, ImportError):
        assert routing.is_internal_import(error_type("x", name=name)) is internal


def _commit_then_fail(session_factory):
    """提交成功後才拋出非資料庫例外（例如關閉 session 時被取消）的 session 工廠。"""
    def factory():
        session = session_factory()
        real_commit = session.commit

        async def commit():
            await real_commit()
            raise RuntimeError("cancelled after commit")

        session.commit = commit
        return session

    return factory


@pytest.mark.parametrize("operation", ["set", "delete"])
def test_cache_is_cleared_even_if_a_committed_write_then_fails(llm_route_db, llm_whitelist, operation):
    """提交後才出錯（不是資料庫例外）：DB 已改，快取仍要清掉，之後的生成立即用新設定，不會一直用舊值。"""
    if operation == "delete":
        _run(llm_route_service.set_route(KEY, "yinli", "m1", None, updated_by="api"))
    expected = "env" if operation == "delete" else "route"
    assert _run(routing.resolve(KEY)).source != expected  # 先讓快取載入寫入前的狀態

    with mock.patch.object(llm_route_service, "_session_factory", return_value=_commit_then_fail(llm_route_db)), \
         pytest.raises(RuntimeError, match="cancelled after commit"):
        if operation == "set":
            _run(llm_route_service.set_route(KEY, "yinli", "m1", None, updated_by="api"))
        else:
            _run(llm_route_service.delete_route(KEY, updated_by="api"))

    assert _run(routing.resolve(KEY)).source == expected


def test_result_is_built_before_committing(llm_route_db, llm_whitelist):
    """回傳的狀態在寫入前就組好：組不出來時什麼都沒寫，不會出現「已提交卻回報失敗」。"""
    with mock.patch.object(llm_route_service, "preview_label", side_effect=RuntimeError("boom")), \
         pytest.raises(RuntimeError):
        _run(llm_route_service.set_route(KEY, "yinli", "m1", None, updated_by="api"))

    assert _rows(llm_route_db) == []


def test_database_failure_while_writing_is_503(llm_route_db, llm_whitelist, caplog):
    """寫入時資料庫異常：轉成 503（結果未知），記下 traceback，不是未分類的 500。"""
    error = OperationalError("INSERT ...", {}, Exception("database is locked"))
    broken_db = mock.patch.object(llm_route_service, "_session_factory", side_effect=error)
    with broken_db, pytest.raises(LLMRouteStoreUnavailableError, match="結果未知"):
        _run(llm_route_service.set_route(KEY, "yinli", "m1", None, updated_by="api"))
    with broken_db, pytest.raises(LLMRouteStoreUnavailableError):
        _run(llm_route_service.delete_route(KEY, updated_by="api"))

    assert "寫入 llm_routes 失敗" in caplog.text and "刪除 llm_routes 失敗" in caplog.text


def test_unreadable_db_cause_is_logged_on_each_management_read(llm_route_db, llm_whitelist, caplog):
    """管理操作讀不到 DB 時，每次都記下底層原因（不只靠很久以前的那一筆 WARNING）。"""
    with _db_down():
        for _ in range(2):
            with pytest.raises(LLMRouteStoreUnavailableError):
                _run(llm_route_service.describe(KEY))

    records = [r for r in caplog.records if "管理操作讀取 llm_routes 失敗" in r.getMessage()]
    assert len(records) == 2 and all(r.exc_info for r in records)


def test_real_cli_missing_is_rejected(llm_route_db, llm_whitelist):
    """不 mock 工廠：真的找不到 CLI 時，試建同樣擋下。"""
    with mock.patch.object(
        ClaudeCodeLLMClient, "_resolve_cli_path", side_effect=LLMServiceError("找不到 claude 執行檔")
    ), pytest.raises(InvalidLLMRouteError, match="找不到 claude 執行檔"):
        _run(llm_route_service.set_route(KEY, "claude-code", "opus", "high", updated_by="api"))


# ----------------------------------------------------------------------
# 恢復預設（Delete）
# ----------------------------------------------------------------------


def test_delete_restores_next_layer(llm_route_db, llm_whitelist):
    """恢復預設：刪掉該列，退回全域（或 .env），且之後的解析立即改用下一層（快取已清）。"""
    _run(llm_route_service.set_route(GLOBAL_ROUTE_KEY, "yinli", "m2", None, updated_by="api"))
    _run(llm_route_service.set_route(KEY, "yinli", "m1", None, updated_by="api"))
    assert _run(routing.resolve(KEY)).source == "route"  # 先讓快取載入用途層，再證明刪除後確實清掉

    result = _run(llm_route_service.delete_route(KEY, updated_by="api"))

    assert _run(routing.resolve(KEY)).source == "global"

    assert result.deleted is True
    assert result.view is not None and (result.view.resolved.source, result.view.resolved.model) == ("global", "m2")
    assert [row.route_key for row in _rows(llm_route_db)] == [GLOBAL_ROUTE_KEY]


def test_delete_without_row_reports_nothing_deleted(llm_route_db, llm_whitelist):
    """本來就沒有設定的已宣告鍵：不報錯，回報沒有刪到。"""
    result = _run(llm_route_service.delete_route(KEY, updated_by="api"))
    assert result.deleted is False
    assert result.view is not None and result.view.resolved.source == "env"


def test_orphan_row_can_be_deleted(llm_route_db, llm_whitelist):
    """程式中已不存在的用途鍵（孤兒列）可以刪除；刪完就不存在了。"""
    _insert(llm_route_db, route_key="removed.feature", endpoint="yinli", model="m1")

    result = _run(llm_route_service.delete_route("removed.feature", updated_by="api"))

    assert (result.deleted, result.view, result.view_unavailable) == (True, None, False)
    assert _rows(llm_route_db) == []


def test_delete_unknown_key_is_not_found(llm_route_db, llm_whitelist):
    """既未宣告、DB 中也沒有的鍵：404。"""
    with pytest.raises(LLMRouteNotFoundError):
        _run(llm_route_service.delete_route("never.existed", updated_by="api"))


# ----------------------------------------------------------------------
# 讀不到 DB（Database unreadable）
# ----------------------------------------------------------------------


def test_db_down_is_not_reported_as_not_found(llm_route_db, llm_whitelist):
    """讀不到 DB 時，查詢或刪除孤兒鍵是 503「讀不到」，不是 404「不存在」，且不刪任何東西。"""
    _insert(llm_route_db, route_key="removed.feature", endpoint="yinli", model="m1")

    with _db_down():
        with pytest.raises(LLMRouteStoreUnavailableError):
            _run(llm_route_service.delete_route("removed.feature", updated_by="api"))
        with pytest.raises(LLMRouteStoreUnavailableError):
            _run(llm_route_service.describe(KEY))

    assert len(_rows(llm_route_db)) == 1


def test_db_down_listing_still_returns_with_flag(llm_route_db, llm_whitelist):
    """讀不到 DB 時清單照常回傳（.env 預設），整體與每列都標示 db_unavailable。"""
    with _db_down():
        views, read_ok = _run(llm_route_service.list_routes())

    assert read_ok is False
    assert all(view.resolved.source == "env" and view.resolved.db_unavailable for view in views)


def test_listing_reads_the_database_once(llm_route_db, llm_whitelist):
    """列出全部用途只讀一次 DB（故障時也不會每列重試），各列來自同一份快照。"""
    error = OperationalError("SELECT ...", {}, Exception("database is locked"))
    fetch = mock.AsyncMock(side_effect=error)
    with mock.patch.object(routing, "_fetch_choices", fetch):
        _run(llm_route_service.list_routes())

    assert fetch.await_count == 1


def test_delete_succeeds_even_if_reading_back_fails(llm_route_db, llm_whitelist):
    """刪除已提交後讀回失敗：仍回報刪除成功，狀態標上 db_unavailable。"""
    _run(llm_route_service.set_route(KEY, "yinli", "m1", None, updated_by="api"))

    # 已宣告的鍵刪除前不必讀 DB；寫入沒有被弄壞，只有刪除後的讀回會失敗
    with _db_down():
        result = _run(llm_route_service.delete_route(KEY, updated_by="api"))

    assert result.deleted is True
    assert result.view is not None and result.view.resolved.db_unavailable is True
    assert _rows(llm_route_db) == []


def test_delete_reports_success_even_if_building_state_fails(llm_route_db, llm_whitelist, caplog):
    """刪除已提交、但組出刪除後狀態時出錯：仍回報刪除成功，並以 view_unavailable 與「孤兒鍵
    刪除後沒有狀態」區分，記錄原因。"""
    _run(llm_route_service.set_route(KEY, "yinli", "m1", None, updated_by="api"))
    with mock.patch.object(llm_route_service, "_view", side_effect=RuntimeError("boom")):
        result = _run(llm_route_service.delete_route(KEY, updated_by="api"))

    assert (result.deleted, result.view, result.view_unavailable) == (True, None, True)
    assert "組出刪除後的狀態失敗" in caplog.text
    assert _rows(llm_route_db) == []


# ----------------------------------------------------------------------
# 列出與標籤（Listing and labels）
# ----------------------------------------------------------------------


def test_list_order_global_declared_then_orphans(llm_route_db, llm_whitelist):
    """顯示順序：全域、已宣告的鍵（排序）、孤兒鍵；孤兒鍵即使字母較前也排在最後。"""
    _insert(llm_route_db, route_key="aa.orphan", endpoint="yinli", model="m1")

    views, read_ok = _run(llm_route_service.list_routes())
    keys = [view.route_key for view in views]

    assert read_ok is True
    assert keys[0] == GLOBAL_ROUTE_KEY
    assert keys[-1] == "aa.orphan"
    declared = [view.route_key for view in views if view.declared and view.route_key != GLOBAL_ROUTE_KEY]
    assert declared == sorted(declared)
    assert KEY in declared
    orphan = views[-1]
    assert (orphan.declared, orphan.description) == (False, None)
    assert orphan.stored is not None and orphan.stored.model == "m1"


_MISSING = ModuleNotFoundError("No module named 'jsonschema'", name="jsonschema")
_INCOMPATIBLE = ImportError("cannot import name 'Draft7Validator' from 'jsonschema'", name="jsonschema")


def _broken_import_for(endpoint_type: str, error: ImportError = _MISSING):
    """讓某類端點的客戶端模組 import 失敗（預設為缺套件），其他端點照常。"""
    real = factory.resolve_client_class

    def resolve(provider=None):
        if provider == endpoint_type:
            raise error
        return real(provider)

    return mock.patch.object(factory, "resolve_client_class", side_effect=resolve)


@pytest.mark.parametrize(
    ("error", "fragment"),
    [(_MISSING, "套件未安裝（jsonschema）"), (_INCOMPATIBLE, "套件版本不符或無法載入")],
    ids=["missing-package", "incompatible-package"],
)
def test_listing_survives_a_broken_package(llm_route_db, llm_whitelist, caplog, error, fragment):
    """生效端點需要的套件沒裝或版本不符（例如重新部署裝錯了）：清單與查詢照常，只有該列標上
    原因——管理員正需要這個介面把設定改到能用的端點。"""
    from app.core.config import settings

    with mock.patch.object(settings, "LLM_CLAUDE_CODE_MODELS", ("claude-opus-5@low|high",)):
        _insert(llm_route_db, route_key=KEY, endpoint="claude-code", model="claude-opus-5", effort="low")
        with _broken_import_for("claude-code", error):
            views, read_ok = _run(llm_route_service.list_routes())
            described = _run(llm_route_service.describe(KEY))

    view = next(view for view in views if view.route_key == KEY)
    assert read_ok is True
    assert view.resolved.source == "route"
    assert view.problem is not None and fragment in view.problem
    # 退化：取不到客戶端類別就不去前綴（正常是 (claude-code)opus-5@low），但仍看得出是哪組設定
    assert view.label == "(claude-code)claude-opus-5@low"
    assert described.problem == view.problem
    assert all(other.problem is None for other in views if other.route_key != KEY)
    assert fragment in caplog.text


def test_env_layer_with_a_missing_package_still_lists(llm_route_db, llm_whitelist):
    """.env 那一層的端點缺套件、DB 也沒有任何設定（重新部署少裝套件的典型情況）：清單照常，
    每一列都標上原因；那一層的預設 effort 取不到，生效 effort 與標籤都不帶 effort。"""
    from app.core.config import settings

    with mock.patch.object(settings, "LLM_PROVIDER", "claude-code"), \
         mock.patch.object(settings, "LLM_MODEL_NAME", "claude-opus-5"), \
         _broken_import_for("claude-code"):
        views, _ = _run(llm_route_service.list_routes())

    assert views and all(view.resolved.source == "env" for view in views)
    assert all(view.problem is not None and "jsonschema" in view.problem for view in views)
    assert all(view.effective_effort is None for view in views)
    assert {view.label for view in views} == {"(claude-code)claude-opus-5"}


def test_listing_does_not_hide_a_missing_internal_module(llm_route_db, llm_whitelist):
    """缺的是專案自己的 app.* 模組：程式錯誤，照常拋出，不退化成「套件未安裝」。"""
    error = ModuleNotFoundError("No module named 'app.infrastructure.llm.nope'", name="app.infrastructure.llm.nope")
    with mock.patch.object(factory, "resolve_client_class", side_effect=error), pytest.raises(ModuleNotFoundError):
        _run(llm_route_service.list_routes())


def test_skipped_env_endpoint_is_flagged(llm_route_db, llm_whitelist):
    """.env 的 LLM_PROVIDER 指向寫錯而被跳過的端點：落到 .env 那一層的用途標上原因，
    不顯示成正常的預設（那一層的每次生成都會失敗）；有 DB 設定的用途不受影響。"""
    from app.core.config import LLMEndpointSettings, settings

    _run(llm_route_service.set_route(KEY, "yinli", "m1", None, updated_by="api"))
    endpoints = {
        **settings.LLM_ENDPOINTS,
        "relay": LLMEndpointSettings(base_url="https://relay.example/v1", api_key=None, models=("x",)),
    }
    with mock.patch.object(settings, "LLM_ENDPOINTS", endpoints), \
         mock.patch.object(settings, "LLM_PROVIDER", "relay"):
        views, _ = _run(llm_route_service.list_routes())

    global_view = views[0]
    assert global_view.resolved.source == "env"
    assert global_view.problem is not None
    assert "LLM_PROVIDER='relay'" in global_view.problem and "API_KEY 未設定" in global_view.problem
    assert next(view for view in views if view.route_key == KEY).problem is None


def test_stale_row_is_shown_with_reason(llm_route_db, llm_whitelist):
    """DB 中已不在白名單的設定：照實列出，並帶跳過原因。"""
    _insert(llm_route_db, route_key=KEY, endpoint="yinli", model="retired-model")

    view = _run(llm_route_service.describe(KEY))

    assert view.stored is not None and view.stored.model == "retired-model"
    assert view.resolved.source == "env"
    assert "retired-model" in view.resolved.rejected[0].reason


def test_env_layer_reports_the_effort_actually_used(llm_route_db, llm_whitelist):
    """.env 那一層的生效 effort 填實際預設值（與標籤一致），不是 None。"""
    from app.core.config import settings

    with mock.patch.object(settings, "LLM_PROVIDER", "claude-code"), \
         mock.patch.object(settings, "LLM_MODEL_NAME", "claude-opus-5"):
        view = _run(llm_route_service.describe(KEY))

    assert view.resolved.source == "env"
    assert (view.effective_effort, view.label) == ("high", "(claude-code)opus-5@high")


@pytest.mark.parametrize(
    ("endpoint", "model", "effort", "env_layer", "expected"),
    [
        ("google", "gemini-3.5-flash", None, True, "gemini-3.5-flash"),
        ("yinli", "m1", None, False, "(yinli)m1"),
        ("claude-code", "claude-opus-5", "low", False, "(claude-code)opus-5@low"),
        ("claude-code", "claude-opus-5", None, True, "(claude-code)opus-5@high"),
        ("claude-code", "haiku", None, False, "(claude-code)haiku"),
    ],
    ids=["legacy-no-prefix", "relay", "cli-explicit", "cli-env-default", "cli-db-no-effort"],
)
def test_preview_label_matches_client_rules(llm_whitelist, endpoint, model, effort, env_layer, expected):
    """預覽標籤與客戶端規則一致：去前綴、google 不加前綴；effort 為 None 時，
    .env 那一層取預設，DB 的選擇則是不帶 effort。"""
    assert llm_route_service.preview_label(endpoint, model, effort, env_layer=env_layer) == expected


@pytest.mark.parametrize(
    ("choice", "env_layer"),
    [
        (("claude-code", "claude-opus-5", "low"), False),
        (("claude-code", "haiku", None), False),
        (("claude-code", "claude-opus-5", None), True),
    ],
    ids=["explicit-effort", "db-no-effort", "env-default"],
)
def test_preview_label_equals_real_client_label(llm_whitelist, choice, env_layer):
    """預覽標籤與真實客戶端的 model_label 相同（不必建客戶端就能顯示）。"""
    from app.infrastructure.llm.base import OMIT_EFFORT

    endpoint, model, effort = choice
    if env_layer:
        client = factory.create_llm_client(endpoint=endpoint, model=model)
    else:
        client = factory.create_llm_client(endpoint=endpoint, model=model, effort=effort if effort else OMIT_EFFORT)
    assert llm_route_service.preview_label(endpoint, model, effort, env_layer=env_layer) == client.model_label


def test_saved_label_matches_the_client_the_route_builds(llm_route_db, llm_whitelist):
    """存了不帶 effort 的 CLI 模型後，顯示的標籤就是之後生成時實際寫進卡片的標籤。"""
    view = _run(llm_route_service.set_route(KEY, "claude-code", "haiku", None, updated_by="api"))
    client = _run(routing.client_for(KEY))

    assert view.label == client.model_label == "(claude-code)haiku"


def test_strict_read_caches_and_clears_failure_state(llm_route_db, llm_whitelist, caplog):
    """嚴格讀取成功時寫入快取，並結束「DB 故障」狀態（下次故障才會再記 WARNING）。"""
    import logging

    routing._db_failing = True
    with caplog.at_level(logging.INFO, logger=routing.__name__):
        _run(routing.stored_choices_strict())

    assert routing._db_failing is False
    assert "已恢復讀取" in caplog.text
    fetch = mock.AsyncMock(return_value={})
    with mock.patch.object(routing, "_fetch_choices", fetch):
        _run(routing.stored_choices_strict())
    fetch.assert_not_awaited()  # 第二次直接用快取


# ----------------------------------------------------------------------
# 並發（Concurrency）
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "read",
    [routing.resolve, lambda key: routing.stored_choices_strict()],
    ids=["generation-path", "strict-management-path"],
)
def test_read_in_flight_during_a_write_does_not_cache_stale_rows(llm_route_db, llm_whitelist, read):
    """讀取在寫入前開始、寫入後才讀完：那次讀取自己拿到舊值（合理），但不會把舊快照寫回
    快取——否則寫入後的生成會一直用舊模型，直到下次寫入或重啟。"""
    real_fetch = routing._fetch_choices

    async def scenario():
        fetched, release = asyncio.Event(), asyncio.Event()

        async def slow_fetch():
            snapshot = await real_fetch()  # 寫入前的狀態：沒有設定
            fetched.set()
            await release.wait()
            return snapshot

        with mock.patch.object(routing, "_fetch_choices", slow_fetch):
            reader = asyncio.create_task(read(KEY))
            await fetched.wait()
            await llm_route_service.set_route(KEY, "yinli", "m1", None, updated_by="api")
            release.set()
            await reader
        return await routing.resolve(KEY)

    assert _run(scenario()).source == "route"


def test_concurrent_first_write_updates_instead_of_failing(llm_route_db, llm_whitelist, caplog):
    """同一用途鍵的第一次寫入同時發生：查的時候還沒有、提交時另一筆剛插入（撞唯一鍵）。結果是確定的：
    改成更新那一列並回報成功，不說成「資料庫異常、結果未知」。"""
    calls = 0

    def racing_factory():
        nonlocal calls
        calls += 1
        session = llm_route_db()
        if calls == 1:
            real_commit = session.commit

            async def commit():
                # 本次的 SELECT 已查過（沒有這一列），另一個請求搶先插入後才提交
                async with llm_route_db() as other:
                    other.add(LLMRouteSetting(route_key=KEY, endpoint="yinli", model="m2", updated_by="other"))
                    await other.commit()
                await real_commit()

            session.commit = commit
        return session

    import logging

    with mock.patch.object(llm_route_service, "_session_factory", return_value=racing_factory), \
         caplog.at_level(logging.INFO, logger=llm_route_service.__name__):
        view = _run(llm_route_service.set_route(KEY, "yinli", "m1", None, updated_by="api"))

    assert view.stored.model == "m1"
    [row] = _rows(llm_route_db)
    assert (row.model, row.updated_by) == ("m1", "api")
    assert "改為更新該列" in caplog.text


def test_model_name_is_trimmed_but_case_sensitive(llm_route_db, llm_whitelist):
    """模型名去前後空白；但區分大小寫（白名單是 m1，M1 不是同一個模型）。"""
    _run(llm_route_service.set_route(KEY, "yinli", " m1 ", None, updated_by="api"))
    [row] = _rows(llm_route_db)
    assert row.model == "m1"

    with pytest.raises(InvalidLLMRouteError, match="模型 'M1' 不在端點 'yinli' 的白名單"):
        _run(llm_route_service.set_route(KEY, "yinli", "M1", None, updated_by="api"))


def test_non_duplicate_integrity_error_is_not_retried(llm_route_db, llm_whitelist):
    """撞到的不是唯一鍵（那一列並不存在：NOT NULL、schema 沒跑 migration…）：不重做、不說成「剛被另一筆
    寫入建立」，照一般資料庫錯誤回 503 並記 traceback。"""
    from sqlalchemy.exc import IntegrityError

    error = IntegrityError("INSERT ...", {}, Exception("NOT NULL constraint failed: llm_routes.model"))
    upsert = mock.AsyncMock(side_effect=error)
    with mock.patch.object(llm_route_service, "_upsert", upsert), \
         pytest.raises(LLMRouteStoreUnavailableError):
        _run(llm_route_service.set_route(KEY, "yinli", "m1", None, updated_by="api"))

    assert upsert.await_count == 1


def test_refresh_global_client_logs_unexpected_errors_as_errors(llm_route_db, llm_whitelist, caplog):
    """全域客戶端建立時的非預期例外（程式或環境錯誤）：不拋出，但記 ERROR 附 traceback，不與「金鑰沒設」這類
    設定問題（WARNING）混在同一個等級。"""
    import logging
    from types import SimpleNamespace

    state = SimpleNamespace(llm_client=object())
    with mock.patch.object(routing, "client_for", side_effect=RuntimeError("註冊表與類別宣告不一致")):
        _run(llm_route_service.refresh_global_client(state))

    assert state.llm_client is None
    [record] = [r for r in caplog.records if "全域 LLM 客戶端" in r.getMessage()]
    assert record.levelno == logging.ERROR and record.exc_info


def test_refresh_global_client_config_errors_are_warnings(llm_route_db, llm_whitelist, caplog):
    """設定問題（LLMServiceError）記 WARNING。"""
    import logging
    from types import SimpleNamespace

    state = SimpleNamespace(llm_client=object())
    with mock.patch.object(routing, "client_for", side_effect=LLMServiceError("API_KEY 未設定")):
        _run(llm_route_service.refresh_global_client(state))

    [record] = [r for r in caplog.records if "全域 LLM 客戶端" in r.getMessage()]
    assert record.levelno == logging.WARNING and "API_KEY 未設定" in record.getMessage()
