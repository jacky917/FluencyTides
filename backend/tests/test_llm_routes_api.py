"""LLM 用途設定 API 的測試（模型路由計畫 D6）。

HTTP-level tests for ``/api/v1/llm-routes`` against a real temporary SQLite
table: listing with options (no secrets), writes with 404 / 422 / 503
mapping, restoring defaults, orphan rows, API-key enforcement on the real
application, and that a write takes effect for the resolver.
"""

import asyncio
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.api.llm_routes import router
from app.core.config import settings
from app.core.exceptions import FluencyTidesError
from app.infrastructure.database.models import LLMRouteSetting
from app.infrastructure.llm import factory, routing

KEY = "test.api"


@pytest.fixture(autouse=True)
def _declared_key(declare_route_key):
    declare_route_key(KEY, "API 測試")


@pytest.fixture
def client(llm_route_db, llm_whitelist):
    """只掛本 router 與全域錯誤 handler 的小 app。A small app with this router."""
    from app.main import fluencytides_error_handler

    app = FastAPI()
    app.add_exception_handler(FluencyTidesError, fluencytides_error_handler)
    app.include_router(router, prefix="/api/v1")
    with TestClient(app) as test_client:
        yield test_client


def _route(body: dict, key: str) -> dict:
    return next(route for route in body["routes"] if route["route_key"] == key)


def _insert(session_factory, **fields) -> None:
    async def insert():
        async with session_factory() as session:
            session.add(LLMRouteSetting(updated_by="test", **fields))
            await session.commit()

    asyncio.run(insert())


def test_get_lists_routes_and_options_without_secrets(client):
    """GET 列出全部用途（含全域）與可選的端點白名單，不含金鑰。"""
    response = client.get("/api/v1/llm-routes")

    assert response.status_code == 200
    body = response.json()
    assert body["db_unavailable"] is False
    assert body["routes"][0]["route_key"] == "*"
    route = _route(body, KEY)
    assert (route["source"], route["label"], route["stored"]) == ("env", "gemini-3.5-flash", None)
    endpoints = {endpoint["id"]: endpoint for endpoint in body["endpoints"]}
    assert endpoints["claude-code"]["models"] == [
        {"name": "opus", "efforts": ["low", "high"]},
        {"name": "sonnet", "efforts": ["high"]},
        {"name": "haiku", "efforts": []},
    ]
    assert "sk-yinli-secret" not in response.text
    assert "legacy-key" not in response.text
    assert "api_key" not in response.text


def test_put_stores_choice_and_takes_effect(client):
    """PUT 寫入後回傳新狀態，路由立即改用它。"""
    response = client.put(
        f"/api/v1/llm-routes/{KEY}", json={"endpoint": "claude-code", "model": "opus", "effort": "low"}
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["source"], body["label"]) == ("route", "(claude-code)opus@low")
    assert body["stored"] == {"endpoint": "claude-code", "model": "opus", "effort": "low"}
    assert asyncio.run(routing.resolve(KEY)).source == "route"


def test_put_global_key(client):
    """全域鍵 * 可直接放在路徑中。"""
    response = client.put("/api/v1/llm-routes/*", json={"endpoint": "yinli", "model": "m2"})
    assert response.status_code == 200
    assert _route(client.get("/api/v1/llm-routes").json(), KEY)["source"] == "global"


@pytest.mark.parametrize(
    ("key", "payload", "status", "error_code", "fragment"),
    [
        ("typo.key", {"endpoint": "yinli", "model": "m1"}, 404, "LLM_ROUTE_NOT_FOUND", "未在程式中宣告"),
        (KEY, {"endpoint": "yinli", "model": "m9"}, 422, "INVALID_LLM_ROUTE", "不在端點 'yinli' 的白名單"),
        (KEY, {"endpoint": "claude-code", "model": "opus"}, 422, "INVALID_LLM_ROUTE", "effort"),
    ],
    ids=["unknown-key", "model-not-whitelisted", "effort-required"],
)
def test_put_rejections(client, key, payload, status, error_code, fragment):
    """未宣告的鍵 404；不在白名單 422；都以 error_code / message 回報，且不寫入。"""
    response = client.put(f"/api/v1/llm-routes/{key}", json=payload)

    assert response.status_code == status
    assert response.json()["error_code"] == error_code
    assert fragment in response.json()["message"]
    assert _route(client.get("/api/v1/llm-routes").json(), KEY)["stored"] is None


def test_put_malformed_body_is_framework_422(client):
    """欄位格式不符（空端點）是 FastAPI 自己的 422（detail 為欄位錯誤清單），與白名單拒絕區分。"""
    response = client.put(f"/api/v1/llm-routes/{KEY}", json={"endpoint": "", "model": "m1"})

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


@pytest.mark.parametrize(
    "error",
    [ModuleNotFoundError("No module named 'google.genai'", name="google.genai"), PermissionError("denied")],
    ids=["missing-package", "workdir-not-writable"],
)
def test_put_trial_build_failure_is_422(client, error):
    """試建時缺套件或工作目錄不可寫：422（不是 500），且不寫入。"""
    with mock.patch.object(factory, "create_llm_client", side_effect=error):
        response = client.put(f"/api/v1/llm-routes/{KEY}", json={"endpoint": "yinli", "model": "m1"})

    assert response.status_code == 422
    assert "試建客戶端失敗" in response.json()["message"]
    assert _route(client.get("/api/v1/llm-routes").json(), KEY)["stored"] is None


def test_delete_restores_default(client):
    """DELETE 恢復預設並回傳退回後的狀態；沒有設定時 deleted=false。"""
    client.put(f"/api/v1/llm-routes/{KEY}", json={"endpoint": "yinli", "model": "m1"})

    first = client.delete(f"/api/v1/llm-routes/{KEY}")
    second = client.delete(f"/api/v1/llm-routes/{KEY}")

    assert first.status_code == 200
    assert first.json()["deleted"] is True
    assert first.json()["route"]["source"] == "env"
    assert second.json()["deleted"] is False


def test_orphan_rows_are_listed_and_deletable(client, llm_route_db):
    """孤兒列：GET 列出 declared=false、description=null；DELETE 後 route=null。"""
    _insert(llm_route_db, route_key="removed.feature", endpoint="yinli", model="m1")

    orphan = _route(client.get("/api/v1/llm-routes").json(), "removed.feature")
    response = client.delete("/api/v1/llm-routes/removed.feature")

    assert (orphan["declared"], orphan["description"], orphan["stored"]["model"]) == (False, None, "m1")
    assert response.json() == {"deleted": True, "route": None, "route_unavailable": False}


def test_delete_distinguishes_unavailable_state_from_orphan(client):
    """刪除已提交、但組不出刪除後狀態：route=null 且 route_unavailable=true，客戶端知道要重新 GET，
    不會誤以為這個鍵已不存在。"""
    from app.services import llm_route_service

    client.put(f"/api/v1/llm-routes/{KEY}", json={"endpoint": "yinli", "model": "m1"})
    with mock.patch.object(llm_route_service, "_view", side_effect=RuntimeError("boom")):
        response = client.delete(f"/api/v1/llm-routes/{KEY}")

    assert response.status_code == 200
    assert response.json() == {"deleted": True, "route": None, "route_unavailable": True}


def test_get_reports_problem_when_a_package_is_missing(client, llm_route_db):
    """生效端點需要的套件沒裝：GET 仍回 200，該列帶 problem（不是整份清單 500）。"""
    _insert(llm_route_db, route_key=KEY, endpoint="claude-code", model="opus", effort="low")
    real = factory.resolve_client_class

    def resolve(provider=None):
        if provider == "claude-code":
            raise ModuleNotFoundError("No module named 'jsonschema'", name="jsonschema")
        return real(provider)

    with mock.patch.object(factory, "resolve_client_class", side_effect=resolve):
        response = client.get("/api/v1/llm-routes")

    assert response.status_code == 200
    assert "套件未安裝（jsonschema）" in _route(response.json(), KEY)["problem"]
    assert response.json()["routes"][0]["problem"] is None


def test_delete_unknown_key_is_404(client):
    """既未宣告、DB 中也沒有的鍵：404。"""
    response = client.delete("/api/v1/llm-routes/never.existed")
    assert (response.status_code, response.json()["error_code"]) == (404, "LLM_ROUTE_NOT_FOUND")


def test_db_down_listing_is_flagged_and_lookups_are_503(client):
    """讀不到 DB：GET 照常回 200 並標 db_unavailable；刪除孤兒鍵回 503（不是 404）。"""
    error = OperationalError("SELECT ...", {}, Exception("database is locked"))
    with mock.patch.object(routing, "_fetch_choices", mock.AsyncMock(side_effect=error)):
        listing = client.get("/api/v1/llm-routes")
        deletion = client.delete("/api/v1/llm-routes/removed.feature")

    assert listing.status_code == 200
    assert listing.json()["db_unavailable"] is True
    assert all(route["db_unavailable"] for route in listing.json()["routes"])
    assert (deletion.status_code, deletion.json()["error_code"]) == (503, "LLM_ROUTE_STORE_UNAVAILABLE")


def test_database_failure_while_writing_is_503(client):
    """PUT 時資料庫寫入失敗：503 並帶 error_code（不是未分類的 500）。"""
    from app.services import llm_route_service

    error = OperationalError("INSERT ...", {}, Exception("database is locked"))
    with mock.patch.object(llm_route_service, "_session_factory", side_effect=error):
        response = client.put(f"/api/v1/llm-routes/{KEY}", json={"endpoint": "yinli", "model": "m1"})

    assert (response.status_code, response.json()["error_code"]) == (503, "LLM_ROUTE_STORE_UNAVAILABLE")


# ----------------------------------------------------------------------
# 真實 app：路由有掛上、且受 API 金鑰保護（Real application wiring and auth）
# ----------------------------------------------------------------------


@pytest.fixture
def real_app_client(llm_route_db, llm_whitelist):
    """以真實的 app.main.app 建 TestClient（不進入 lifespan，不連 Anki / TG）。

    刻意不在 import 失敗時略過：這是安全相關的測試，載入不了就該失敗。
    """
    from app.main import app

    return TestClient(app)


def test_real_app_requires_api_key_when_configured(real_app_client):
    """設定了 API_SECRET_KEY 時：沒帶或帶錯金鑰的寫入被拒（401）；正確金鑰才寫入。
    同時證明路由確實掛在真實 app 上。"""
    url = f"/api/v1/llm-routes/{KEY}"
    payload = {"endpoint": "yinli", "model": "m1"}
    with mock.patch.object(settings, "API_SECRET_KEY", "s3cret"):
        missing = real_app_client.put(url, json=payload)
        wrong = real_app_client.put(url, json=payload, headers={"X-API-Key": "nope"})
        listing = real_app_client.get("/api/v1/llm-routes", headers={"X-API-Key": "s3cret"})
        ok = real_app_client.put(url, json=payload, headers={"X-API-Key": "s3cret"})

    assert (missing.status_code, wrong.status_code) == (401, 401)
    assert _route(listing.json(), KEY)["stored"] is None  # 被拒的兩次都沒有寫入
    assert ok.status_code == 200
    assert ok.json()["stored"]["model"] == "m1"


def test_get_shows_skipped_layer_and_effective_effort(client, llm_route_db):
    """GET 照實呈現：已不在白名單的設定列在 stored、原因列在 rejected，生效的是 .env 那一層，
    且 .env 那一層的 effective.effort 填實際的預設值（與 label 一致）。"""
    _insert(llm_route_db, route_key=KEY, endpoint="yinli", model="retired-model")
    provider = mock.patch.object(settings, "LLM_PROVIDER", "claude-code")
    model = mock.patch.object(settings, "LLM_MODEL_NAME", "claude-opus-5")
    with provider, model:
        route = _route(client.get("/api/v1/llm-routes").json(), KEY)

    assert route["stored"]["model"] == "retired-model"
    assert route["source"] == "env"
    assert "retired-model" in route["rejected"][0]["reason"]
    assert route["effective"] == {"endpoint": "claude-code", "model": "claude-opus-5", "effort": "high"}
    assert route["label"] == "(claude-code)opus-5@high"


def test_writing_the_global_key_rebuilds_the_global_client(client):
    """PUT / DELETE 全域 * 之後重建 app.state.llm_client（診斷端點的標籤跟著全域設定）；改其他用途不重建。"""
    state = client.app.state
    state.llm_client = None

    client.put(f"/api/v1/llm-routes/{KEY}", json={"endpoint": "yinli", "model": "m1"})
    assert state.llm_client is None

    client.put("/api/v1/llm-routes/*", json={"endpoint": "yinli", "model": "m2"})
    assert state.llm_client.model_label == "(yinli)m2"

    client.delete("/api/v1/llm-routes/*")
    assert state.llm_client.model_label == "gemini-3.5-flash"


def test_unauthenticated_requests_are_rejected_on_every_method(real_app_client):
    """設定了 API_SECRET_KEY 時，GET / PUT / DELETE 沒帶金鑰都是 401，且不會改到設定。"""
    with mock.patch.object(settings, "API_SECRET_KEY", "s3cret"):
        real_app_client.put(
            "/api/v1/llm-routes/*", json={"endpoint": "yinli", "model": "m1"}, headers={"X-API-Key": "s3cret"}
        )
        responses = [
            real_app_client.get("/api/v1/llm-routes"),
            real_app_client.put("/api/v1/llm-routes/*", json={"endpoint": "yinli", "model": "m2"}),
            real_app_client.delete("/api/v1/llm-routes/*"),
        ]
        listing = real_app_client.get("/api/v1/llm-routes", headers={"X-API-Key": "s3cret"})

    assert [response.status_code for response in responses] == [401, 401, 401]
    assert _route(listing.json(), "*")["stored"]["model"] == "m1"


def test_delete_global_key_falls_back_to_env(client):
    """DELETE 全域 *：刪掉後其他用途退回 .env。"""
    client.put("/api/v1/llm-routes/*", json={"endpoint": "yinli", "model": "m2"})
    assert _route(client.get("/api/v1/llm-routes").json(), KEY)["source"] == "global"

    response = client.delete("/api/v1/llm-routes/*")

    assert (response.json()["deleted"], response.json()["route"]["source"]) == (True, "env")
    assert asyncio.run(routing.resolve(KEY)).source == "env"


def test_failed_global_write_still_rebuilds_the_global_client(client):
    """寫入 * 回 503（結果未知，DB 可能已改）時也重建全域客戶端，不留著可能已過時的那一個。"""
    from app.services import llm_route_service

    state = client.app.state
    state.llm_client = None
    error = OperationalError("INSERT ...", {}, Exception("database is locked"))
    with mock.patch.object(llm_route_service, "_session_factory", side_effect=error):
        response = client.put("/api/v1/llm-routes/*", json={"endpoint": "yinli", "model": "m2"})

    assert response.status_code == 503
    assert state.llm_client is not None  # 已依目前讀得到的設定（此時是 .env）重建
