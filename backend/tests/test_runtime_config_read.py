"""執行期設定服務與 config API(唯讀切片)的單元測試。

Unit tests for the runtime config service and API (read-only slice).

對應計畫 docs/wip/runtime_config_service_FEAT_2026-08-29.md §3.5。
以 mock 白名單與最小 FastAPI app 測試,不觸碰真實 .env 白名單內容。
"""

from types import SimpleNamespace
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import scripts.common.env  # noqa: F401  # 載入 .env,供 app.core.config 使用

from app.api.config import router as config_router
from app.core.config import settings
from app.services import runtime_config_service as rcs_mod
from app.services.runtime_config_service import RuntimeConfigService


FAKE_WHITELIST = {
    "LLM_MODEL_NAME": ["model-a", "model-b"],
    "AUDIO_EVALUATOR_PROVIDER": None,  # 不限選項
}


def _make_app(llm_client=None) -> FastAPI:
    """建立只掛 config router 的最小 app(繞過 lifespan/DB)。

    Build a minimal app with only the config router, bypassing lifespan.
    """
    app = FastAPI()
    app.include_router(config_router, prefix="/api/v1")
    app.state.llm_client = llm_client
    return app


class TestServiceRead:
    def test_list_configs_returns_whitelisted_with_current_values(self):
        with mock.patch.object(rcs_mod, "get_modifiable_configs", return_value=FAKE_WHITELIST), \
             mock.patch.object(settings, "LLM_MODEL_NAME", "model-a"):
            entries = {e.key: e for e in RuntimeConfigService().list_configs()}
        assert entries["LLM_MODEL_NAME"].current_value == "model-a"
        assert entries["LLM_MODEL_NAME"].options == ["model-a", "model-b"]
        assert entries["LLM_MODEL_NAME"].requires_rebuild is True
        assert entries["AUDIO_EVALUATOR_PROVIDER"].options is None

    def test_get_config_outside_whitelist_returns_none(self):
        """白名單外(含真實存在的敏感鍵)一律 None——防鍵名探測。"""
        with mock.patch.object(rcs_mod, "get_modifiable_configs", return_value=FAKE_WHITELIST):
            svc = RuntimeConfigService()
            assert svc.get_config("LLM_API_KEY") is None
            assert svc.get_config("NOT_A_REAL_KEY") is None

    def test_runtime_info_reads_live_client_label(self):
        fake_client = SimpleNamespace(_formatted_model_name="(claude-code)opus-5@medium")
        state = SimpleNamespace(llm_client=fake_client)
        info = RuntimeConfigService().get_runtime_info(state)
        assert info["llm_label"] == "(claude-code)opus-5@medium"
        assert info["anki_connect_url"] == settings.ANKI_CONNECT_URL

    def test_runtime_info_none_client(self):
        info = RuntimeConfigService().get_runtime_info(SimpleNamespace(llm_client=None))
        assert info["llm_label"] is None


class TestConfigApi:
    def test_list_endpoint_shape(self):
        fake_client = SimpleNamespace(_formatted_model_name="(claude-code)opus-5@medium")
        with mock.patch.object(rcs_mod, "get_modifiable_configs", return_value=FAKE_WHITELIST), \
             mock.patch.object(settings, "LLM_MODEL_NAME", "model-a"):
            client = TestClient(_make_app(llm_client=fake_client))
            resp = client.get("/api/v1/config")
        assert resp.status_code == 200
        body = resp.json()
        assert body["runtime"]["llm_label"] == "(claude-code)opus-5@medium"
        keys = {c["key"] for c in body["configs"]}
        assert keys == {"LLM_MODEL_NAME", "AUDIO_EVALUATOR_PROVIDER"}

    def test_get_single_key(self):
        with mock.patch.object(rcs_mod, "get_modifiable_configs", return_value=FAKE_WHITELIST), \
             mock.patch.object(settings, "LLM_MODEL_NAME", "model-b"):
            client = TestClient(_make_app())
            resp = client.get("/api/v1/config/LLM_MODEL_NAME")
        assert resp.status_code == 200
        assert resp.json()["current_value"] == "model-b"

    def test_get_outside_whitelist_is_404(self):
        with mock.patch.object(rcs_mod, "get_modifiable_configs", return_value=FAKE_WHITELIST):
            client = TestClient(_make_app())
            assert client.get("/api/v1/config/LLM_API_KEY").status_code == 404
            assert client.get("/api/v1/config/NOPE").status_code == 404
