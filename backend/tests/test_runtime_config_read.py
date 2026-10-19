"""執行期設定服務與 config API(唯讀)的單元測試。

Unit tests for the runtime config service and API (read-only).

對應計畫 docs/archive/runtime_config_service_FEAT_2026-08-29.md §3.5。``MODIFY_*`` 白名單設定
已由能力路由取代,這裡只剩 runtime 對帳區塊。
"""

import asyncio
import json
from types import SimpleNamespace
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import scripts.common.env  # noqa: F401  # 載入 .env,供 app.core.config 使用

from app.api.config import router as config_router
from app.core.config import settings
from app.services import runtime_config_service as rcs_mod
from app.services.runtime_config_service import RuntimeConfigService


def _make_app(llm_client=None) -> FastAPI:
    """建立只掛 config router 的最小 app(繞過 lifespan/DB)。

    Build a minimal app with only the config router, bypassing lifespan.
    """
    app = FastAPI()
    app.include_router(config_router, prefix="/api/v1")
    app.state.llm_client = llm_client
    return app


class TestServiceRead:
    def test_runtime_info_reads_live_client_label(self):
        fake_client = SimpleNamespace(_formatted_model_name="(claude-code)opus-5@medium")
        state = SimpleNamespace(llm_client=fake_client)
        info = asyncio.run(RuntimeConfigService().get_runtime_info(state))
        assert info["llm_label"] == "(claude-code)opus-5@medium"
        assert info["anki_connect_url"] == settings.ANKI_CONNECT_URL

    def test_runtime_info_none_client(self):
        info = asyncio.run(
            RuntimeConfigService().get_runtime_info(SimpleNamespace(llm_client=None))
        )
        assert info["llm_label"] is None

    def test_claude_code_probe_reports_version(self):
        """provider=claude-code 且 CLI 可執行 → 回報實際版本字串。"""
        fake_client = SimpleNamespace(
            _formatted_model_name="(claude-code)opus-5@medium",
            _cli_path="/fake/claude",
            _effort="medium",
        )
        fake_completed = SimpleNamespace(
            returncode=0, stdout=b"2.1.211 (Claude Code)\n", stderr=b""
        )
        with mock.patch.object(settings, "LLM_PROVIDER", "claude-code"), \
             mock.patch.object(settings, "LLM_CLAUDE_CODE_OAUTH_TOKEN", "sk-x"), \
             mock.patch.object(rcs_mod.subprocess, "run", return_value=fake_completed):
            info = asyncio.run(
                RuntimeConfigService().get_runtime_info(
                    SimpleNamespace(llm_client=fake_client)
                )
            )
        cc = info["claude_code"]
        assert cc["client_initialized"] is True
        assert cc["cli_version"] == "2.1.211 (Claude Code)"
        assert cc["oauth_token_configured"] is True
        assert cc["effort"] == "medium"

    def test_account_probe_reports_subscription_plan(self):
        """auth status --json 正常 → 回報方案,且不外洩 email / org 識別。

        Reports the plan and never leaks the identifying fields the CLI
        also returns.
        """
        fake_client = SimpleNamespace(
            _formatted_model_name="(claude-code)opus-5@medium",
            _cli_path="/fake/claude",
            _effort="medium",
        )
        auth_json = json.dumps({
            "loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty",
            "email": "someone@example.com", "orgId": "org-123",
            "orgName": "someone's Organization", "subscriptionType": "max",
        }).encode()
        calls = [
            SimpleNamespace(returncode=0, stdout=b"2.1.211 (Claude Code)", stderr=b""),
            SimpleNamespace(returncode=0, stdout=auth_json, stderr=b""),
        ]
        with mock.patch.object(settings, "LLM_PROVIDER", "claude-code"), \
             mock.patch.object(rcs_mod.subprocess, "run", side_effect=calls):
            info = asyncio.run(
                RuntimeConfigService().get_runtime_info(
                    SimpleNamespace(llm_client=fake_client)
                )
            )
        account = info["claude_code"]["account"]
        assert account["status"] == "ok"
        assert account["subscription_type"] == "max"
        assert account["logged_in"] is True
        assert account["auth_method"] == "claude.ai"
        # 識別性欄位不得出現在對外區塊
        assert "email" not in account
        assert not any("example.com" in str(v) for v in account.values())
        assert not any("org-123" in str(v) for v in account.values())

    def test_account_probe_survives_non_json_output(self):
        """CLI 版本太舊、輸出不是 JSON → 回 unknown 而不是炸掉。

        Older CLIs without ``auth status --json`` degrade to unknown.
        """
        fake_client = SimpleNamespace(_cli_path="/fake/claude", _effort="medium")
        calls = [
            SimpleNamespace(returncode=0, stdout=b"2.1.211 (Claude Code)", stderr=b""),
            SimpleNamespace(returncode=0, stdout=b"error: unknown command", stderr=b""),
        ]
        with mock.patch.object(settings, "LLM_PROVIDER", "claude-code"), \
             mock.patch.object(rcs_mod.subprocess, "run", side_effect=calls):
            info = asyncio.run(
                RuntimeConfigService().get_runtime_info(
                    SimpleNamespace(llm_client=fake_client)
                )
            )
        assert info["claude_code"]["account"]["status"] == "unknown"

    def test_account_probe_reports_failed_exit(self):
        """auth status 非零退出(例:容器內未登入)→ unknown 帶 stderr 摘要。"""
        fake_client = SimpleNamespace(_cli_path="/fake/claude", _effort="medium")
        calls = [
            SimpleNamespace(returncode=0, stdout=b"2.1.211 (Claude Code)", stderr=b""),
            SimpleNamespace(returncode=1, stdout=b"", stderr=b"not logged in"),
        ]
        with mock.patch.object(settings, "LLM_PROVIDER", "claude-code"), \
             mock.patch.object(rcs_mod.subprocess, "run", side_effect=calls):
            info = asyncio.run(
                RuntimeConfigService().get_runtime_info(
                    SimpleNamespace(llm_client=fake_client)
                )
            )
        account = info["claude_code"]["account"]
        assert account["status"] == "unknown"
        assert "not logged in" in account["detail"]

    def test_claude_code_probe_reports_error_without_client(self):
        """client 未初始化 → 不炸,回報無 CLI 路徑可探測。"""
        with mock.patch.object(settings, "LLM_PROVIDER", "claude-code"):
            info = asyncio.run(
                RuntimeConfigService().get_runtime_info(
                    SimpleNamespace(llm_client=None)
                )
            )
        cc = info["claude_code"]
        assert cc["client_initialized"] is False
        assert cc["cli_version"] is None
        assert "cli_version_error" in cc

    def test_token_with_space_flagged_as_format_error(self):
        """token 內含空格 → 格式檢查恆開,直接點名複製斷行事故。"""
        with mock.patch.object(settings, "LLM_PROVIDER", "claude-code"), \
             mock.patch.object(settings, "LLM_CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-ab cd"):
            info = asyncio.run(
                RuntimeConfigService().get_runtime_info(
                    SimpleNamespace(llm_client=None)
                )
            )
        cc = info["claude_code"]
        assert cc["oauth_token_format_ok"] is False
        assert "空白" in cc["oauth_token_format_error"]

    def test_auth_check_ok_via_minimal_request(self):
        """check_auth=True 且最小請求成功 → auth_check ok。"""
        fake_client = SimpleNamespace(
            _formatted_model_name="(claude-code)opus-5@medium",
            _cli_path="/fake/claude",
            _effort="medium",
            _build_env=lambda: {"PATH": "/x"},
        )
        version_ok = SimpleNamespace(returncode=0, stdout=b"2.1.251\n", stderr=b"")
        with mock.patch.object(settings, "LLM_PROVIDER", "claude-code"), \
             mock.patch.object(settings, "LLM_CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-good"), \
             mock.patch.object(rcs_mod.subprocess, "run", return_value=version_ok):
            info = asyncio.run(
                RuntimeConfigService().get_runtime_info(
                    SimpleNamespace(llm_client=fake_client), check_auth=True
                )
            )
        assert info["claude_code"]["auth_check"]["status"] == "ok"

    def test_auth_check_failed_reports_stderr(self):
        """check_auth=True 且 CLI 回非零 → auth_check failed 並帶原因。"""
        fake_client = SimpleNamespace(
            _formatted_model_name="x", _cli_path="/fake/claude", _effort="medium",
            _build_env=lambda: {},
        )
        fail = SimpleNamespace(returncode=1, stdout=b"", stderr=b"authentication_error")
        with mock.patch.object(settings, "LLM_PROVIDER", "claude-code"), \
             mock.patch.object(rcs_mod.subprocess, "run", return_value=fail):
            info = asyncio.run(
                RuntimeConfigService().get_runtime_info(
                    SimpleNamespace(llm_client=fake_client), check_auth=True
                )
            )
        auth = info["claude_code"]["auth_check"]
        assert auth["status"] == "failed"
        assert "authentication_error" in auth["detail"]

    def test_non_claude_code_provider_has_no_probe_block(self):
        with mock.patch.object(settings, "LLM_PROVIDER", "google"):
            info = asyncio.run(
                RuntimeConfigService().get_runtime_info(
                    SimpleNamespace(llm_client=None)
                )
            )
        assert info["claude_code"] is None


class TestConfigApi:
    def test_endpoint_shape(self):
        """只回 runtime 區塊;舊的 configs(MODIFY_*)與 /config/{key} 已移除。"""
        fake_client = SimpleNamespace(_formatted_model_name="(claude-code)opus-5@medium")
        client = TestClient(_make_app(llm_client=fake_client))
        resp = client.get("/api/v1/config")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {"runtime"}
        assert body["runtime"]["llm_label"] == "(claude-code)opus-5@medium"
        assert client.get("/api/v1/config/AUDIO_MODEL_NAME").status_code == 404

# ----------------------------------------------------------------------
# 模型路由的診斷（模型路由計畫 D9）
# ----------------------------------------------------------------------


def test_runtime_info_lists_routes_and_endpoints_without_secrets(llm_route_db, llm_whitelist, declare_route_key):
    """runtime 列出各用途生效的設定與來源、端點與白名單（不含金鑰）；全域端點取自全域 * 的設定。"""
    from app.services import capability_route_service

    declare_route_key("test.diag", "診斷測試")
    asyncio.run(capability_route_service.set_route("*", "yinli", "m1", None, updated_by="api"))

    info = asyncio.run(RuntimeConfigService().get_runtime_info(SimpleNamespace(llm_client=None)))

    routes = {route["route_key"]: route["capabilities"]["llm"] for route in info["capability_routes"]["routes"]}
    assert info["capability_routes"]["db_unavailable"] is False
    assert (routes["*"]["source"], routes["*"]["label"]) == ("global", "(yinli)m1")
    assert (routes["test.diag"]["source"], routes["test.diag"]["label"]) == ("global", "(yinli)m1")
    assert info["llm_provider"] == "yinli"
    assert info["claude_code"] is None  # 全域端點不是 CLI 型：不探測 claude-code
    endpoints = {endpoint["id"]: endpoint for endpoint in info["llm_endpoints"]["endpoints"]}
    assert endpoints["yinli"]["base_url"] == "https://yinli.example/v1"
    assert endpoints["yinli"]["models"] == [{"name": "m1", "efforts": []}, {"name": "m2", "efforts": []}]
    text = json.dumps(info, ensure_ascii=False)
    assert "sk-yinli-secret" not in text and "legacy-key" not in text


def test_runtime_info_probes_the_global_cli_endpoint(llm_route_db, llm_whitelist):
    """全域 * 設成 CLI 型端點時，claude-code 診斷跟著全域設定（不是 .env 的 LLM_PROVIDER）。"""
    from app.services import capability_route_service

    asyncio.run(capability_route_service.set_route("*", "claude-code", "haiku", None, updated_by="api"))
    probe = mock.AsyncMock(return_value={"client_initialized": True})
    with mock.patch.object(RuntimeConfigService, "_probe_claude_code", probe):
        info = asyncio.run(RuntimeConfigService().get_runtime_info(SimpleNamespace(llm_client=None)))

    assert info["llm_provider"] == "claude-code"
    assert info["claude_code"] == {"client_initialized": True}
    assert probe.await_args.args[2] == "claude-code"


def test_runtime_info_still_reports_when_routes_fail(llm_route_db, llm_whitelist, caplog):
    """組各用途清單時發生非預期錯誤：該區塊帶錯誤說明並記 traceback，其餘照常回報（帶病回報）。"""
    from app.services import capability_route_service

    with mock.patch.object(capability_route_service, "list_routes", side_effect=RuntimeError("boom")):
        info = asyncio.run(RuntimeConfigService().get_runtime_info(SimpleNamespace(llm_client=None)))

    assert info["capability_routes"] == {"error": "RuntimeError: boom"}
    assert info["anki_connect_url"] == settings.ANKI_CONNECT_URL
    assert info["llm_endpoints"]["endpoints"]
    assert "診斷端點組 capability_routes 失敗" in caplog.text


def test_runtime_info_lists_skipped_endpoints(llm_route_db, llm_whitelist):
    """設定有誤被跳過的端點列在 llm_endpoints.skipped，附原因。"""
    from app.core.config import LLMEndpointSettings

    endpoints = {**settings.LLM_ENDPOINTS, "bad": LLMEndpointSettings(base_url="https://bad.example/v1", models=("x",))}
    with mock.patch.object(settings, "LLM_ENDPOINTS", endpoints):
        info = asyncio.run(RuntimeConfigService().get_runtime_info(SimpleNamespace(llm_client=None)))

    assert info["llm_endpoints"]["skipped"] == {"bad": "API_KEY 未設定"}


def test_refresh_global_client_follows_the_global_route(llm_route_db, llm_whitelist):
    """全域客戶端依全域 * 建立：設了 * 就用它，沒設就用 .env。"""
    from app.services import capability_route_service

    state = SimpleNamespace()
    asyncio.run(capability_route_service.refresh_global_client(state))
    assert state.llm_client.model_label == "gemini-3.5-flash"

    asyncio.run(capability_route_service.set_route("*", "yinli", "m2", None, updated_by="api"))
    asyncio.run(capability_route_service.refresh_global_client(state))
    assert state.llm_client.model_label == "(yinli)m2"


def test_refresh_global_client_failure_leaves_none_and_logs(llm_route_db, llm_whitelist, caplog):
    """全域客戶端建不起來（例如金鑰沒設）：不拋出（不影響啟動與寫入），設為 None 並記 WARNING。"""
    from app.core.exceptions import LLMServiceError
    from app.infrastructure.llm import routing
    from app.services import capability_route_service

    state = SimpleNamespace(llm_client=object())
    with mock.patch.object(routing, "client_for", side_effect=LLMServiceError("API_KEY 未設定")):
        asyncio.run(capability_route_service.refresh_global_client(state))

    assert state.llm_client is None
    assert "全域 LLM 客戶端建立失敗" in caplog.text and "API_KEY 未設定" in caplog.text


def test_runtime_info_flags_a_global_client_out_of_sync(llm_route_db, llm_whitelist, caplog):
    """全域客戶端與全域 * 目前的設定不一致（例如啟動時讀不到 DB 而用了 .env、之後 DB 恢復）：
    llm_client_matches_global 為 False 並記 WARNING，不讓標籤與端點互相矛盾而沒有提示。"""
    from app.services import capability_route_service

    state = SimpleNamespace(llm_client=None)
    asyncio.run(capability_route_service.refresh_global_client(state))  # 依 .env 建立
    info = asyncio.run(RuntimeConfigService().get_runtime_info(state))
    assert info["llm_client_matches_global"] is True

    asyncio.run(capability_route_service.set_route("*", "yinli", "m1", None, updated_by="api"))  # 沒有重建
    info = asyncio.run(RuntimeConfigService().get_runtime_info(state))

    assert info["llm_client_matches_global"] is False
    assert (info["llm_label"], info["llm_provider"]) == ("gemini-3.5-flash", "yinli")
    assert "不一致" in caplog.text


def test_runtime_info_cannot_compare_when_routes_fail(llm_route_db, llm_whitelist):
    """組不出清單時無法比對：llm_client_matches_global 為 None，llm_provider 退回 .env 的 LLM_PROVIDER。"""
    from app.services import capability_route_service

    with mock.patch.object(capability_route_service, "list_routes", side_effect=RuntimeError("boom")):
        info = asyncio.run(RuntimeConfigService().get_runtime_info(SimpleNamespace(llm_client=None)))

    assert info["llm_client_matches_global"] is None
    assert info["llm_provider"] == "google"
