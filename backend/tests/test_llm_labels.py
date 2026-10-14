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
from scripts.common.llm_label import build_llm_model_label


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
    """腳本側包裝與後端 client 的標籤逐字一致（2026-08-28 事故的防線）。"""
    with mock.patch.multiple(
        settings,
        LLM_PROVIDER="claude-code",
        LLM_MODEL_NAME="claude-opus-5",
        LLM_CLAUDE_CODE_EFFORT="medium",
    ):
        assert build_llm_model_label() == "(claude-code)opus-5@medium"


def test_script_wrapper_normalizes_effort_like_backend():
    """設定寫 'High'：後端客戶端驗證後標 @high，腳本側必須一樣。"""
    with mock.patch.multiple(
        settings,
        LLM_PROVIDER="claude-code",
        LLM_MODEL_NAME="claude-opus-5",
        LLM_CLAUDE_CODE_EFFORT="High",
    ):
        assert build_llm_model_label() == "(claude-code)opus-5@high"


def test_script_wrapper_bare_for_google():
    with mock.patch.multiple(settings, LLM_PROVIDER="google", LLM_MODEL_NAME="gemini-3.1-pro-preview"):
        assert build_llm_model_label() == "gemini-3.1-pro-preview"
