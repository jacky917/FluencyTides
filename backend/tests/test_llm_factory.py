"""工廠的覆寫參數傳遞與端點路徑測試（計畫 §1.3 第一項 / §4 FactoryTests 追加；模型路由計畫 D5）。

Tests that ``create_llm_client`` forwards the ``model`` / ``effort``
overrides to every provider's constructor, and that the endpoint path
(``endpoint=...``, or ``LLM_PROVIDER`` naming an ``LLM_ENDPOINTS`` entry)
builds the right client with the endpoint's own credentials. 層級化前 anthropic 分支曾
``return AnthropicLLMClient()`` 把兩個覆寫靜默丟棄——不報錯、只是沒生效，
這類回歸只有測試抓得到。

刻意不放進 ``test_claude_code_client.py``：該檔依計畫 §7 必須一行不改。
"""

import asyncio
import json
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

import httpx
import openai
import pytest
from pydantic import SecretStr

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.core.config import LLMEndpointSettings, settings
from app.core.exceptions import LLMServiceError
from app.infrastructure.llm import factory
from app.infrastructure.llm.antigravity_client import AntigravityLLMClient
from app.infrastructure.llm.base import OMIT_EFFORT
from app.infrastructure.llm.claude_code_client import ClaudeCodeLLMClient
from app.infrastructure.llm.client import LLMClient
from app.infrastructure.llm.endpoints import get_endpoint
from app.infrastructure.llm.factory import create_llm_client
from app.infrastructure.llm.gemini_native_client import GeminiNativeLLMClient

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


@pytest.mark.parametrize(
    "provider", factory.providers_supporting_effort(), ids=factory.providers_supporting_effort()
)
def test_effort_setting_requires_rebuild(provider):
    """支援 effort 的 CLI provider，其 effort 設定鍵必須列在 REQUIRES_REBUILD_KEYS。

    TG /setconfig 改了 effort 卻不重建 singleton，診斷端點回報的標籤就會停在舊值
    （計畫 §D12）。由註冊表推導，下一個 provider 忘了加會在這裡被擋下。
    """
    from app.services.runtime_config_service import REQUIRES_REBUILD_KEYS

    client_cls = factory.resolve_client_class(provider)
    prefix = getattr(client_cls, "SETTINGS_PREFIX", None)
    if prefix is None:  # 非 CLI 型（如 anthropic 實作後）的 effort 設定鍵另行約定
        pytest.skip(f"{provider} 沒有 SETTINGS_PREFIX")
    assert f"{prefix}_EFFORT" in REQUIRES_REBUILD_KEYS


# ----------------------------------------------------------------------
# 指定端點（模型路由計畫 D5）
# ----------------------------------------------------------------------


def _endpoint_env(tmp_path=None, **overrides):
    """端點測試的設定：舊式 google ＋中轉站 yinli / or2 ＋ gemini-native 端點 gem ＋
    claude-code 白名單（``opus@low|high``、``haiku``）。

    給 ``tmp_path`` 時另把兩個 CLI 的工作目錄指到暫存目錄、關閉審計與 HOME 隔離，
    讓 CLI 客戶端可在測試中建立。
    """
    values = {
        "LLM_PROVIDER": "google",
        "LLM_MODEL_NAME": "gemini-3.5-flash",
        "LLM_API_KEY": "legacy-key",
        "LLM_BASE_URL": "https://legacy.example/v1/",
        "LLM_ENDPOINTS": {
            "yinli": LLMEndpointSettings(
                base_url="https://yinli.example/v1/", api_key=SecretStr("yinli-key"), models=("m1",)
            ),
            "or2": LLMEndpointSettings(
                base_url="https://or2.example/v1/", api_key=SecretStr("or2-key"), models=("m2",)
            ),
            "gem": LLMEndpointSettings(
                type="gemini-native", api_key=SecretStr("gem-key"), models=("gemini-3.5-flash",)
            ),
        },
        "LLM_CLAUDE_CODE_MODELS": ("opus@low|high", "haiku"),
        "LLM_ANTIGRAVITY_MODELS": ("gemini-x@low|high",),
        "LLM_CLAUDE_CODE_EFFORT": "high",
        "LLM_ANTIGRAVITY_EFFORT": "high",
        "LLM_CLAUDE_CODE_OAUTH_TOKEN": "",
        **overrides,
    }
    if tmp_path is not None:
        values.setdefault("LLM_CLAUDE_CODE_WORKDIR", str(tmp_path / "cc"))
        values.setdefault("LLM_CLAUDE_CODE_AUDIT_DIR", "")
        values.setdefault("LLM_ANTIGRAVITY_WORKDIR", str(tmp_path / "agy"))
        values.setdefault("LLM_ANTIGRAVITY_AUDIT_DIR", "")
        values.setdefault("LLM_ANTIGRAVITY_ISOLATE_HOME", False)
    stack = ExitStack()
    for name, value in values.items():
        stack.enter_context(mock.patch.object(settings, name, value))
    return stack


def test_two_relays_use_their_own_url_and_key():
    """兩家 openai 類端點同時可用，各自用自己的網址、金鑰，標籤前綴為各自的 ID。"""
    with _endpoint_env():
        yinli = create_llm_client(endpoint="yinli", model="m1")
        or2 = create_llm_client(endpoint="or2", model="m2")

    assert isinstance(yinli, LLMClient) and isinstance(or2, LLMClient)
    assert str(yinli._client.base_url) == "https://yinli.example/v1/"
    assert yinli._client.api_key == "yinli-key"
    assert str(or2._client.base_url) == "https://or2.example/v1/"
    assert or2._client.api_key == "or2-key"
    assert yinli.model_label == "(yinli)m1"
    assert or2.model_label == "(or2)m2"


@pytest.mark.parametrize(
    ("safety", "expect_in_body"),
    [(None, True), (False, False), ("true", True)],
    ids=["default-relay", "override-off", "override-on"],
)
def test_safety_settings_reach_request_body(safety, expect_in_body):
    """端點的 safety_settings 設定實際反映在送出的請求 body（傳輸層驗證）。"""
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "created": 0, "model": "m1",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": '{"ok": true}'}}],
        })

    endpoints_config = {
        "relay": LLMEndpointSettings(
            base_url="https://relay.example/v1/", api_key=SecretStr("k"), models=("m1",), safety_settings=safety
        ),
    }
    with _endpoint_env(LLM_ENDPOINTS=endpoints_config):
        client = create_llm_client(endpoint="relay", model="m1")
    client._client = openai.AsyncOpenAI(
        api_key="k", base_url="https://relay.example/v1/",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    asyncio.run(client.generate_structured_data("SYS", "USER", {"type": "object", "properties": {}}))

    assert ("safety_settings" in captured[0]) is expect_in_body


def test_explicit_legacy_endpoint_matches_no_endpoint_path():
    """端點等於 LLM_PROVIDER 時，客戶端的網址、金鑰、標籤與 safety 判斷和不指定端點相同。"""
    with _endpoint_env():
        legacy = create_llm_client()
        explicit = create_llm_client(endpoint="google", model="gemini-3.5-flash")

    assert legacy.endpoint is None
    assert legacy.model_label == explicit.model_label == "gemini-3.5-flash"
    assert str(legacy._client.base_url) == str(explicit._client.base_url)
    assert legacy._client.api_key == explicit._client.api_key == "legacy-key"
    assert legacy._supports_safety_settings is explicit._supports_safety_settings is False


def test_provider_naming_custom_endpoint_uses_it_without_endpoint_arg():
    """LLM_PROVIDER 指向 LLM_ENDPOINTS 的端點：不指定端點也用它的網址與金鑰，不讀舊設定。"""
    with _endpoint_env(LLM_PROVIDER="yinli", LLM_MODEL_NAME="m1"):
        client = create_llm_client()

    assert client.endpoint is not None and client.endpoint.id == "yinli"
    assert client._client.api_key == "yinli-key"
    assert str(client._client.base_url) == "https://yinli.example/v1/"
    assert client.model_label == "(yinli)m1"


def test_provider_naming_custom_gemini_endpoint_picks_gemini_client():
    """LLM_PROVIDER 指向 gemini-native 類的自訂端點時，選 Gemini 客戶端而不是 LLMClient。"""
    with _endpoint_env(LLM_PROVIDER="gem"), \
         mock.patch("app.infrastructure.llm.gemini_native_client.genai.Client") as fake_sdk:
        client = create_llm_client()

    assert isinstance(client, GeminiNativeLLMClient)
    fake_sdk.assert_called_once_with(api_key="gem-key")


def test_provider_naming_broken_custom_endpoint_fails_loudly():
    """LLM_PROVIDER 指向的自訂端點寫錯：明確報端點不存在，不默默改用 LLM_API_KEY。"""
    broken = {"yinli": LLMEndpointSettings(type="opneai", api_key=SecretStr("k"), models=("m1",))}
    with _endpoint_env(LLM_PROVIDER="yinli", LLM_ENDPOINTS=broken), \
         pytest.raises(LLMServiceError) as exc_info:
        create_llm_client()

    # 原因直接放在錯誤訊息裡，不必翻 log（log 可能已輪替）
    assert "LLM_ENDPOINTS__YINLI 設定有誤已被跳過" in str(exc_info.value)
    assert "TYPE='opneai' 無效" in str(exc_info.value)


def test_gemini_native_endpoint_uses_its_own_key():
    """gemini-native 端點用自己的金鑰（不是 LLM_API_KEY）。"""
    with _endpoint_env(), \
         mock.patch("app.infrastructure.llm.gemini_native_client.genai.Client") as fake_sdk:
        client = create_llm_client(endpoint="gem", model="gemini-3.5-flash")

    fake_sdk.assert_called_once_with(api_key="gem-key")
    assert client.model_label == "(gem)gemini-3.5-flash"


def test_unknown_endpoint_raises():
    """端點未定義時拋 LLMServiceError，說明未在 .env 定義。"""
    with _endpoint_env(), pytest.raises(LLMServiceError, match="未在 .env 定義"):
        create_llm_client(endpoint="missing", model="x")


def test_endpoint_missing_key_error_names_the_setting():
    """舊式端點缺金鑰時，錯誤訊息照舊點名 LLM_API_KEY。"""
    with _endpoint_env(LLM_API_KEY=None), pytest.raises(LLMServiceError, match="LLM_API_KEY 未設定"):
        create_llm_client(endpoint="google", model="gemini-3.5-flash")


@pytest.mark.parametrize(("provider", "entry"), _REGISTERED, ids=[key for key, _ in _REGISTERED])
def test_registered_provider_receives_endpoint(provider, entry):
    """每個註冊 provider 的建構子都收得到端點（日後新增的 provider 忘了收會在此失敗）。"""
    target = f"app.infrastructure.llm.{entry.module}.{entry.class_name}"
    with _endpoint_env(LLM_PROVIDER=provider), mock.patch(target) as fake_cls:
        create_llm_client(endpoint=provider, model="x", effort=None)

    kwargs = fake_cls.call_args.kwargs
    assert (kwargs["model"], kwargs["effort"]) == ("x", None)
    assert kwargs["endpoint"].type == provider
    # 真實類別也要接受自己類型的端點（新增 provider 忘了設 ENDPOINT_TYPES 會在此失敗）
    assert provider in factory.resolve_client_class(provider).ENDPOINT_TYPES


def test_client_rejects_endpoint_of_another_type():
    """客戶端只接受自己類型的端點：Gemini 客戶端不會拿中轉站的金鑰去打 Google。"""
    with _endpoint_env():
        relay = get_endpoint("yinli")
        with pytest.raises(LLMServiceError, match="不能使用 openai 類端點 'yinli'"):
            GeminiNativeLLMClient(model="m1", endpoint=relay)
        with pytest.raises(LLMServiceError, match="不能使用 gemini-native 類端點 'gem'"):
            LLMClient(model="m1", endpoint=get_endpoint("gem"))


@pytest.mark.parametrize("provider", ["google", "claude-code"])
def test_empty_effort_string_still_rejected(tmp_path, provider):
    """空字串 effort 照舊被拒（與 main 相同）；「不傳」只能用 OMIT_EFFORT 表達。"""
    with _endpoint_env(tmp_path, LLM_PROVIDER=provider, LLM_MODEL_NAME="opus"), \
         mock.patch.object(ClaudeCodeLLMClient, "_resolve_cli_path", return_value="/fake/claude"), \
         pytest.raises(LLMServiceError):
        create_llm_client(effort="")


@pytest.mark.parametrize(
    ("endpoint_id", "cli_cls", "model"),
    [
        ("claude-code", ClaudeCodeLLMClient, "opus"),
        ("antigravity", AntigravityLLMClient, "gemini-x"),
    ],
)
@pytest.mark.parametrize(
    ("effort", "expected_effort", "label_suffix"),
    [("low", "low", "@low"), (OMIT_EFFORT, None, "")],
    ids=["with-effort", "omit-effort"],
)
def test_cli_endpoint_effort_or_none(tmp_path, endpoint_id, cli_cls, model, effort, expected_effort, label_suffix):
    """CLI 端點：給 effort 時帶 --effort；OMIT_EFFORT 時省略，標籤也不帶 @。"""
    with _endpoint_env(tmp_path), \
         mock.patch.object(cli_cls, "_resolve_cli_path", return_value="/fake/cli"):
        client = create_llm_client(endpoint=endpoint_id, model=model, effort=effort)
        command = client._build_command("SYS", "{}")

    if expected_effort is None:
        assert "--effort" not in command
    else:
        assert command[command.index("--effort") + 1] == expected_effort
    assert client.model_label == f"({endpoint_id}){model}{label_suffix}"


def test_omit_effort_accepted_by_providers_without_effort():
    """不支援 effort 的 provider 收到 OMIT_EFFORT 視同 None，不拋錯。"""
    with _endpoint_env():
        client = create_llm_client(endpoint="yinli", model="m1", effort=OMIT_EFFORT)

    assert client.effort is None
