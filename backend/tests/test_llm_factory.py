"""工廠的覆寫參數傳遞測試（計畫 §1.3 第一項 / §4 FactoryTests 追加）。

Tests that ``create_llm_client`` forwards the ``model`` / ``effort``
overrides to every provider's constructor. 層級化前 anthropic 分支曾
``return AnthropicLLMClient()`` 把兩個覆寫靜默丟棄——不報錯、只是沒生效，
這類回歸只有測試抓得到。

刻意不放進 ``test_claude_code_client.py``：該檔依計畫 §7 必須一行不改。
"""

import sys
from pathlib import Path
from unittest import mock

import pytest

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.core.config import settings
from app.core.exceptions import LLMServiceError
from app.infrastructure.llm import factory
from app.infrastructure.llm.factory import create_llm_client

# 註冊表逐列展開：日後新增 provider 自動納入，不必回來改這裡
_REGISTERED = sorted(factory._REGISTRY.items())


@pytest.mark.parametrize(
    ("provider", "entry"), _REGISTERED, ids=[key for key, _ in _REGISTERED]
)
@pytest.mark.parametrize(
    ("model", "effort"),
    [("override-model", "high"), (None, None)],
    ids=["with-overrides", "no-overrides"],
)
def test_registered_provider_receives_overrides(provider, entry, model, effort):
    """每個註冊 provider 的建構子都收到原樣的 model / effort。"""
    target = f"app.infrastructure.llm.{entry.module}.{entry.class_name}"
    with mock.patch.object(settings, "LLM_PROVIDER", provider), \
         mock.patch(target) as fake_cls:
        client = create_llm_client(model=model, effort=effort)

    assert client is fake_cls.return_value
    fake_cls.assert_called_once_with(model=model, effort=effort)


@pytest.mark.parametrize("provider", ["google", "yinli", ""])
def test_unregistered_provider_falls_back_to_llm_client_with_overrides(provider):
    """未註冊 provider 走 OpenAI 相容 LLMClient，覆寫同樣原樣傳入。"""
    with mock.patch.object(settings, "LLM_PROVIDER", provider), \
         mock.patch.object(factory, "LLMClient") as fake_cls:
        client = create_llm_client(model="override-model", effort=None)

    assert client is fake_cls.return_value
    fake_cls.assert_called_once_with(model="override-model", effort=None)


def test_llm_client_actually_uses_model_override():
    """不 mock 類別：真實 LLMClient 以覆寫值為模型名，標籤隨之改變。"""
    with mock.patch.object(settings, "LLM_PROVIDER", "google"), \
         mock.patch.object(settings, "LLM_API_KEY", "test-key"), \
         mock.patch.object(settings, "LLM_BASE_URL", "http://llm.test/v1"), \
         mock.patch.object(settings, "LLM_MODEL_NAME", "settings-model"):
        client = create_llm_client(model="override-model")

    assert client.model_label == "override-model"


def test_anthropic_stub_echoes_both_overrides():
    """不 mock 類別：anthropic stub 的錯誤訊息回顯兩個覆寫值（§7 驗收項）。"""
    with mock.patch.object(settings, "LLM_PROVIDER", "anthropic"):
        with pytest.raises(LLMServiceError) as exc_info:
            create_llm_client(model="x", effort="high")

    message = str(exc_info.value)
    assert "尚未實作" in message
    assert "model='x'" in message
    assert "effort='high'" in message


def test_anthropic_stub_without_overrides_has_no_echo_suffix():
    """沒有覆寫時不附「收到覆寫」尾巴，訊息維持原樣。"""
    with mock.patch.object(settings, "LLM_PROVIDER", "anthropic"):
        with pytest.raises(LLMServiceError) as exc_info:
            create_llm_client()

    assert "收到覆寫" not in str(exc_info.value)
