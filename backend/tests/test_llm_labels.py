"""模型標籤規則的參數化測試（計畫 D6 / D10）。

Parametrized tests for the model label rule. 每一例都對應 D10 的相容表：
既有 provider 的標籤字串必須逐位元不變。
"""

import sys
from pathlib import Path
from unittest import mock

import pytest

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.core.config import settings
from app.infrastructure.llm.labels import build_model_label
from scripts.common import llm_label
from scripts.common.llm_label import local_llm_model_label


@pytest.mark.parametrize(
    ("provider", "model", "effort", "strip", "expected"),
    [
        ("google", "gemini-3.1-pro-preview", None, "", "gemini-3.1-pro-preview"),
        ("openai", "gpt-5", None, "", "gpt-5"),
        ("", "gemini-3.5-flash", None, "", "gemini-3.5-flash"),
        ("yinli", "deepseek-chat", None, "", "(yinli)deepseek-chat"),
        ("claude-code", "claude-opus-5", "medium", "claude-", "(claude-code)opus-5@medium"),
        ("claude-code", "opus", "high", "claude-", "(claude-code)opus@high"),
        ("gemini-native", "gemini-3.5-pro", None, "", "(gemini-native)gemini-3.5-pro"),
        ("antigravity", "gemini-3.5-pro", "high", "", "(antigravity)gemini-3.5-pro@high"),
        ("  Claude-Code  ", "claude-opus-5", "low", "claude-", "(claude-code)opus-5@low"),
        # effort 大小寫 / 空白不敏感——與 CLI 客戶端驗證後的值一致，腳本側才不會分岔
        ("claude-code", "claude-opus-5", " High ", "claude-", "(claude-code)opus-5@high"),
        ("claude-code", "claude-opus-5", "", "claude-", "(claude-code)opus-5"),
    ],
)
def test_build_model_label(provider, model, effort, strip, expected):
    assert build_model_label(provider, model, effort, strip_prefix=strip) == expected


def test_script_wrapper_matches_backend_rule_for_claude_code():
    """腳本側的本機推算（查不到後端時的退回）與後端 client 的標籤逐字一致（2026-08-28 事故的防線）。"""
    with mock.patch.multiple(
        settings,
        LLM_PROVIDER="claude-code",
        LLM_MODEL_NAME="claude-opus-5",
        LLM_CLAUDE_CODE_EFFORT="medium",
    ):
        assert local_llm_model_label() == "(claude-code)opus-5@medium"


def test_script_wrapper_normalizes_effort_like_backend():
    """設定寫 'High'：後端客戶端驗證後標 @high，腳本側必須一樣。"""
    with mock.patch.multiple(
        settings,
        LLM_PROVIDER="claude-code",
        LLM_MODEL_NAME="claude-opus-5",
        LLM_CLAUDE_CODE_EFFORT="High",
    ):
        assert local_llm_model_label() == "(claude-code)opus-5@high"


def test_script_wrapper_bare_for_google():
    with mock.patch.multiple(settings, LLM_PROVIDER="google", LLM_MODEL_NAME="gemini-3.1-pro-preview"):
        assert local_llm_model_label() == "gemini-3.1-pro-preview"


# ----------------------------------------------------------------------
# 腳本向後端查詢用途的標籤（模型路由計畫 D9）
# ----------------------------------------------------------------------


def _backend_routes(routes: list[dict] | None = None, status: int = 200):
    """讓 httpx.get 回一份假的 GET /api/v1/capability-routes 回應，並記下呼叫。

    ``routes`` 每項寫成 ``{route_key, label, problem}``，這裡包成 API 的「用途鍵 × 能力」形狀。
    """
    response = mock.MagicMock(status_code=status)
    response.json.return_value = {
        "routes": [
            {"route_key": route["route_key"], "capabilities": {"llm": {"label": route["label"], "problem": route["problem"]}}}
            for route in routes or []
        ]
    }
    if status >= 400:
        import httpx

        response.raise_for_status.side_effect = httpx.HTTPStatusError("boom", request=mock.MagicMock(), response=response)
    return mock.patch.object(llm_label.httpx, "get", return_value=response)


@pytest.fixture(autouse=True)
def _fresh_label_cache():
    llm_label.build_llm_model_label.cache_clear()
    yield
    llm_label.build_llm_model_label.cache_clear()


def test_script_label_comes_from_the_backend_route():
    """失敗紀錄的標籤取後端該用途生效的標籤（/models 改過的值），不是本機 .env 推算；同一用途只查一次。"""
    routes = [
        {"route_key": "jp_core_verb", "label": "(yinli)m1", "problem": None},
        {"route_key": "jp_verb_pair", "label": "(claude-code)opus-5@low", "problem": None},
    ]
    with _backend_routes(routes) as get, \
         mock.patch.multiple(settings, LLM_PROVIDER="google", LLM_MODEL_NAME="gemini-3.5-flash"):
        first = llm_label.build_llm_model_label("jp_verb_pair")
        second = llm_label.build_llm_model_label("jp_verb_pair")

    assert first == second == "(claude-code)opus-5@low"
    assert get.call_count == 1
    assert get.call_args.args[0].endswith("/api/v1/capability-routes")


def test_script_label_sends_api_key_when_configured():
    """後端設了 API 金鑰時，查詢要帶上 X-API-Key（/api/v1/capability-routes 受 verify_api_key 保護）。"""
    with _backend_routes([{"route_key": "jp_verb_pair", "label": "m", "problem": None}]) as get, \
         mock.patch.object(settings, "API_SECRET_KEY", "s3cret"):
        llm_label.build_llm_model_label("jp_verb_pair")

    assert get.call_args.kwargs["headers"]["X-API-Key"] == "s3cret"


@pytest.mark.parametrize(
    "backend",
    [
        lambda: _backend_routes(status=401),
        lambda: _backend_routes([{"route_key": "other", "label": "x", "problem": None}]),
        lambda: mock.patch.object(llm_label.httpx, "get", side_effect=__import__("httpx").ConnectError("refused")),
    ],
    ids=["http-error", "unknown-route", "backend-down"],
)
def test_script_label_falls_back_to_local_with_warning(caplog, backend):
    """後端查不到（認證失敗、版本不含這個用途、沒啟動）：退回本機推算，並警告標籤可能不一致。"""
    with backend(), mock.patch.multiple(settings, LLM_PROVIDER="google", LLM_MODEL_NAME="gemini-3.5-flash"):
        label = llm_label.build_llm_model_label("jp_verb_pair")

    assert label == "gemini-3.5-flash"
    assert "無法向後端查詢用途 'jp_verb_pair'" in caplog.text
