"""GeminiNativeLLMClient 的單元測試（計畫 D7 / §4 測試）。

Unit tests for the native google-genai text client.

兩層攔截：
- 多數測試以 ``AsyncMock`` 替換 ``client.aio.models.generate_content``，回應用
  SDK 的真實型別組出，測分流與例外映射
- ``TestHttpLayer`` 改在 SDK 的 HTTP 請求點攔截：請求走 SDK 真實的 wire 轉換、
  回應走 SDK 真實的解析，證明 ``safetySettings`` / ``systemInstruction`` /
  ``responseJsonSchema`` 確實出現在送出的 body 裡
都不打網路。
"""

import asyncio
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import aiohttp
import httpx
import pytest
from google.genai import errors, types

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.core.config import settings
from app.core.exceptions import (
    LLMQuotaError,
    LLMRefusalError,
    LLMRetryExhaustedError,
    LLMServiceError,
)
from app.infrastructure.llm import factory
from app.infrastructure.llm.gemini_native_client import GeminiNativeLLMClient
from app.services.runtime_config_service import RuntimeConfigService

_SKIP_IDENTIFIER = "LLM API 在所有重試後仍回傳空內容"
_VALID_JSON = '{"item": {"name": "x"}}'

_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"item": {"$ref": "#/$defs/Item"}},
    "required": ["item"],
    "$defs": {
        "Item": {"type": "object", "properties": {"name": {"type": "string"}}},
    },
}

_FLAT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"item": {"type": "object", "properties": {"name": {"type": "string"}}}},
    "required": ["item"],
}


def _response(
    text: str | None = _VALID_JSON,
    finish_reason: str | None = "STOP",
    *,
    block_reason: str | None = None,
    with_candidate: bool = True,
) -> types.GenerateContentResponse:
    """以 SDK 真實型別組出回應。Build a response from real SDK types.

    Args:
        text: 候選的文字；None 表示沒有文字 part。
        finish_reason: 候選的 finish_reason。
        block_reason: 給值時附上 ``prompt_feedback.block_reason``。
        with_candidate: False 時不帶任何候選（prompt 被擋或空回應）。
    """
    feedback = (
        types.GenerateContentResponsePromptFeedback(block_reason=types.BlockedReason(block_reason))
        if block_reason is not None
        else None
    )
    candidates = []
    if with_candidate:
        parts = [types.Part(text=text)] if text is not None else []
        candidates.append(
            types.Candidate(
                content=types.Content(role="model", parts=parts),
                finish_reason=types.FinishReason(finish_reason) if finish_reason else None,
            )
        )
    return types.GenerateContentResponse(candidates=candidates, prompt_feedback=feedback)


def _api_error(
    cls: type[errors.APIError], code: int, status: str, message: str | None = None, **extra: object
) -> errors.APIError:
    body: dict[str, object] = {"code": code, "message": message or f"{status} test", "status": status}
    body.update(extra)
    return cls(code, {"error": body})


@pytest.fixture
def base_env():
    """最小可用設定。Minimal working settings."""
    with mock.patch.object(settings, "LLM_PROVIDER", "gemini-native"), \
         mock.patch.object(settings, "LLM_API_KEY", "test-key"), \
         mock.patch.object(settings, "LLM_BASE_URL", "https://ignored.example/v1"), \
         mock.patch.object(settings, "LLM_MODEL_NAME", "gemini-test"):
        yield


@pytest.fixture
def env(base_env):
    """另替換 genai.Client：真實建構每次約 1 秒，且各測試會再換掉 aio 呼叫點。"""
    with mock.patch("app.infrastructure.llm.gemini_native_client.genai.Client") as fake_sdk:
        yield fake_sdk


@pytest.fixture
def no_sleep():
    """替換 asyncio 模組本身的 sleep（全域），短路內容層與網路層兩處的重試等待。

    ``asyncio.timeout`` 不經 sleep，逾時測試不受影響。
    """
    with mock.patch("asyncio.sleep", new=mock.AsyncMock()) as fake_sleep:
        yield fake_sleep


def _client_with(*side_effect: object, model: str | None = None) -> tuple[GeminiNativeLLMClient, mock.AsyncMock]:
    client = GeminiNativeLLMClient(model=model)
    fake = mock.AsyncMock(side_effect=list(side_effect))
    client._client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=fake)))
    return client, fake


def _run(client: GeminiNativeLLMClient):
    return asyncio.run(client.generate_structured_data("SYS", "USER", _SCHEMA))


# ----------------------------------------------------------------------
# 成功路徑與請求內容
# ----------------------------------------------------------------------


def test_happy_path_returns_parsed_data_and_label(env, no_sleep):
    client, fake = _client_with(_response())
    result = _run(client)

    assert result.parsed_data == {"item": {"name": "x"}}
    assert result.raw_content == _VALID_JSON
    assert result.attempt_count == 1
    assert result.model_name == "(gemini-native)gemini-test"
    fake.assert_awaited_once()


def test_request_carries_safety_settings_system_instruction_and_flat_schema(env, no_sleep):
    client, fake = _client_with(_response())
    _run(client)

    kwargs = fake.await_args.kwargs
    assert kwargs["model"] == "gemini-test"
    assert kwargs["contents"] == "USER"
    config: types.GenerateContentConfig = kwargs["config"]
    # 等於原始 system_prompt：沒有像 LLMClient 那樣把 schema 抄進 prompt
    assert config.system_instruction == "SYS"
    assert config.temperature == 0.0
    assert config.response_mime_type == "application/json"
    # 刻意用 response_json_schema 而非 response_schema（2026-10-14 實測）
    assert config.response_schema is None
    assert config.response_json_schema == _FLAT_SCHEMA
    assert {(s.category, s.threshold) for s in config.safety_settings} == {
        (types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, types.HarmBlockThreshold.BLOCK_NONE),
        (types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, types.HarmBlockThreshold.BLOCK_NONE),
        (types.HarmCategory.HARM_CATEGORY_HARASSMENT, types.HarmBlockThreshold.BLOCK_NONE),
        (types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, types.HarmBlockThreshold.BLOCK_NONE),
    }


def test_model_override_is_sent_in_request_and_label(env, no_sleep):
    """覆寫值必須真的送出；只改標籤會讓卡片 tag 與實際計費模型不符。"""
    client, fake = _client_with(_response(), model="gemini-other")
    result = _run(client)

    assert fake.await_args.kwargs["model"] == "gemini-other"
    assert result.model_name == "(gemini-native)gemini-other"


def test_fenced_json_is_cleaned(env, no_sleep):
    client, _ = _client_with(_response(f"```json\n{_VALID_JSON}\n```"))
    assert _run(client).parsed_data == {"item": {"name": "x"}}


def test_empty_then_success_counts_attempts(env, no_sleep):
    client, fake = _client_with(_response(None), _response())
    result = _run(client)
    assert result.attempt_count == 2
    assert fake.await_count == 2
    # 內容層重試間隔固定 2 秒（與 LLMClient 相同，拒答率比對的前提）
    no_sleep.assert_awaited_once_with(2)


def test_unblocked_prompt_feedback_does_not_mask_success(env, no_sleep):
    """真實 API 常回不帶 block_reason 的 promptFeedback；不能因此判失敗。"""
    response = _response()
    response.prompt_feedback = types.GenerateContentResponsePromptFeedback()
    client, _ = _client_with(response)
    assert _run(client).attempt_count == 1


# ----------------------------------------------------------------------
# 內容層失敗分流（重試 3 次後以最後一次的型態決定子類）
# ----------------------------------------------------------------------


@pytest.mark.parametrize("reason", ["SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"])
def test_refusal_finish_reasons_raise_refusal_after_retries(env, no_sleep, reason):
    client, fake = _client_with(*[_response(None, reason)] * 3)
    with pytest.raises(LLMRefusalError) as exc_info:
        _run(client)

    message = str(exc_info.value)
    assert exc_info.value.error_code == "LLM_REFUSAL"
    assert _SKIP_IDENTIFIER in message and "PROHIBITED_CONTENT" in message
    assert f"finish_reason={reason}" in message
    assert fake.await_count == 3


def test_refusal_with_complete_valid_json_is_not_success(env, no_sleep):
    """被安全中斷的候選即使帶完整合法 JSON 也是拒答，絕不能當成功寫進卡片。"""
    client, _ = _client_with(*[_response(_VALID_JSON, "SAFETY")] * 3)
    with pytest.raises(LLMRefusalError):
        _run(client)


@pytest.mark.parametrize("block_reason", ["PROHIBITED_CONTENT", "JAILBREAK"])
def test_prompt_block_reason_raises_refusal(env, no_sleep, block_reason):
    client, fake = _client_with(*[_response(block_reason=block_reason, with_candidate=False)] * 3)
    with pytest.raises(LLMRefusalError) as exc_info:
        _run(client)
    assert f"finish_reason={block_reason}" in str(exc_info.value)
    assert fake.await_count == 3


def test_other_block_reason_is_not_refusal(env, no_sleep):
    """OTHER 等未列入的封鎖刻意不算拒答（改動會直接改變拒答率統計）。"""
    client, _ = _client_with(*[_response(block_reason="OTHER", with_candidate=False)] * 3)
    with pytest.raises(LLMRetryExhaustedError) as exc_info:
        _run(client)
    assert not isinstance(exc_info.value, LLMRefusalError)
    assert "finish_reason=OTHER" in str(exc_info.value)


def test_unspecified_block_reason_falls_through_to_candidate(env, no_sleep):
    """BLOCKED_REASON_UNSPECIFIED 不算被擋，要看候選自己的 finish_reason。"""
    client, _ = _client_with(*[_response(None, "SAFETY", block_reason="BLOCKED_REASON_UNSPECIFIED")] * 3)
    with pytest.raises(LLMRefusalError) as exc_info:
        _run(client)
    assert "finish_reason=SAFETY" in str(exc_info.value)


def test_refusal_then_success_recovers(env, no_sleep):
    client, _ = _client_with(_response(None, "SAFETY"), _response())
    assert _run(client).attempt_count == 2


def test_last_attempt_decides_subclass_retry_exhausted(env, no_sleep):
    """前兩次拒答、最後一次截斷 → 以最後一次為準，不是拒答。"""
    client, _ = _client_with(
        _response(None, "SAFETY"), _response(None, "SAFETY"), _response('{"item": {"na', "MAX_TOKENS"),
    )
    with pytest.raises(LLMRetryExhaustedError) as exc_info:
        _run(client)
    assert not isinstance(exc_info.value, LLMRefusalError)


def test_last_attempt_decides_subclass_refusal(env, no_sleep):
    """前兩次截斷 / 壞 JSON、最後一次拒答 → 拒答。"""
    client, _ = _client_with(
        _response('{"item": {"na', "MAX_TOKENS"), _response("not json"), _response(None, "SAFETY"),
    )
    with pytest.raises(LLMRefusalError):
        _run(client)


def test_recitation_is_retry_exhausted_with_reason(env, no_sleep):
    client, _ = _client_with(*[_response(None, "RECITATION")] * 3)
    with pytest.raises(LLMRetryExhaustedError) as exc_info:
        _run(client)
    assert not isinstance(exc_info.value, LLMRefusalError)
    assert str(exc_info.value) == "LLM API 在所有重試後仍回傳空內容（finish_reason=RECITATION）。"


def test_no_candidates_is_empty_with_unknown_reason(env, no_sleep):
    client, _ = _client_with(*[_response(with_candidate=False)] * 3)
    with pytest.raises(LLMRetryExhaustedError) as exc_info:
        _run(client)
    assert str(exc_info.value) == "LLM API 在所有重試後仍回傳空內容（finish_reason=UNKNOWN）。"


def test_max_tokens_truncation_reads_as_invalid_json_not_empty(env, no_sleep):
    """截斷是有內容的；訊息與 LLMClient 對同一情況的「非有效 JSON」一致，不借用空內容字串。"""
    client, _ = _client_with(*[_response('{"item": {"na', "MAX_TOKENS")] * 3)
    with pytest.raises(LLMRetryExhaustedError) as exc_info:
        _run(client)
    message = str(exc_info.value)
    assert exc_info.value.error_code == "LLM_RETRY_EXHAUSTED"
    assert message.startswith("LLM 輸出非有效 JSON 格式")
    assert "finish_reason=MAX_TOKENS" in message
    assert _SKIP_IDENTIFIER not in message


def test_max_tokens_with_no_text_is_empty(env, no_sleep):
    """thinking 吃光額度時可能一個字都沒有：那就是空內容。"""
    client, _ = _client_with(*[_response(None, "MAX_TOKENS")] * 3)
    with pytest.raises(LLMRetryExhaustedError) as exc_info:
        _run(client)
    assert str(exc_info.value) == "LLM API 在所有重試後仍回傳空內容（finish_reason=MAX_TOKENS）。"


def test_invalid_json_is_retry_exhausted(env, no_sleep):
    client, fake = _client_with(*[_response("not json")] * 3)
    with pytest.raises(LLMRetryExhaustedError) as exc_info:
        _run(client)
    assert str(exc_info.value).startswith("LLM 輸出非有效 JSON 格式")
    assert fake.await_count == 3


def test_non_object_json_is_retry_exhausted(env, no_sleep):
    client, _ = _client_with(*[_response("[1, 2]")] * 3)
    with pytest.raises(LLMRetryExhaustedError) as exc_info:
        _run(client)
    assert "頂層為 list" in str(exc_info.value)


# ----------------------------------------------------------------------
# 網路層 / API 錯誤映射
# ----------------------------------------------------------------------


def test_quota_429_raises_immediately(env, no_sleep):
    client, fake = _client_with(_api_error(errors.ClientError, 429, "RESOURCE_EXHAUSTED"))
    with pytest.raises(LLMQuotaError) as exc_info:
        _run(client)

    message = str(exc_info.value)
    assert exc_info.value.error_code == "LLM_QUOTA"
    assert "429" in message and "Quota" in message
    fake.assert_awaited_once()


def test_quota_after_content_failure_still_quota(env, no_sleep):
    """內容層失敗之後遇到 429，不能被吞成「再試一次」或改判拒答。"""
    client, fake = _client_with(
        _response(None, "SAFETY"), _api_error(errors.ClientError, 429, "RESOURCE_EXHAUSTED"),
    )
    with pytest.raises(LLMQuotaError):
        _run(client)
    assert fake.await_count == 2


@pytest.mark.parametrize(
    "transient",
    [
        _api_error(errors.ServerError, 503, "UNAVAILABLE"),
        # 正式環境 SDK 走 aiohttp：連線類錯誤是這一族，不是 httpx 的
        aiohttp.ServerDisconnectedError(),
        aiohttp.ClientOSError(),
        aiohttp.ServerTimeoutError(),
        aiohttp.ClientPayloadError("body cut"),
        httpx.ConnectError("boom"),
        httpx.ReadTimeout("slow"),
        httpx.RemoteProtocolError("peer closed"),
        TimeoutError(),
    ],
    ids=[
        "503", "aiohttp-disconnected", "aiohttp-oserror", "aiohttp-timeout", "aiohttp-payload",
        "httpx-connect", "httpx-read-timeout", "httpx-remote-protocol", "TimeoutError",
    ],
)
def test_transient_errors_are_retried_then_succeed(env, no_sleep, transient):
    client, fake = _client_with(transient, _response())
    result = _run(client)
    assert result.attempt_count == 1  # 網路層重試不計入內容層嘗試
    assert fake.await_count == 2


def test_transient_backoff_is_exponential(env, no_sleep):
    client, _ = _client_with(
        _api_error(errors.ServerError, 503, "UNAVAILABLE"),
        _api_error(errors.ServerError, 503, "UNAVAILABLE"),
        _response(),
    )
    _run(client)
    assert [c.args[0] for c in no_sleep.await_args_list] == [1.0, 2.0]


_CONNECTION_KEY = SimpleNamespace(host="generativelanguage.googleapis.com", port=443, ssl=True)


@pytest.mark.parametrize(
    "error",
    [
        httpx.UnsupportedProtocol("ftp://"),
        # 同屬 aiohttp.ClientConnectionError，但是代理 / 憑證這類確定性錯誤
        aiohttp.ClientProxyConnectionError(_CONNECTION_KEY, OSError("proxy refused")),
        aiohttp.ClientSSLError(_CONNECTION_KEY, OSError("certificate verify failed")),
        aiohttp.ServerFingerprintMismatch(b"expected", b"got", "generativelanguage.googleapis.com", 443),
    ],
    ids=["httpx-unsupported-protocol", "aiohttp-proxy", "aiohttp-ssl", "aiohttp-fingerprint"],
)
def test_deterministic_transport_error_is_not_retried(env, no_sleep, error):
    """代理 / 憑證 / 協定這類設定錯誤重試也沒用，且不該被說成「瞬時錯誤」。"""
    client, fake = _client_with(error)
    with pytest.raises(LLMServiceError) as exc_info:
        _run(client)
    assert "瞬時" not in str(exc_info.value)
    fake.assert_awaited_once()


def test_server_error_exhausted_is_plain_service_error(env, no_sleep):
    client, fake = _client_with(*[_api_error(errors.ServerError, 500, "INTERNAL")] * 3)
    with pytest.raises(LLMServiceError) as exc_info:
        _run(client)
    assert type(exc_info.value) is LLMServiceError
    assert exc_info.value.error_code == "LLM_SERVICE_ERROR"
    assert fake.await_count == 3


@pytest.mark.parametrize(
    ("error", "type_name"),
    [(TimeoutError(), "TimeoutError"), (aiohttp.ServerDisconnectedError(), "ServerDisconnectedError")],
)
def test_transient_exhaustion_message_names_the_error_type(env, no_sleep, error, type_name):
    """耗盡訊息由基底 _retry_transient 產生；str 為空的例外也要能辨識。"""
    client, fake = _client_with(*[error] * 3)
    with pytest.raises(LLMServiceError) as exc_info:
        _run(client)
    assert type_name in str(exc_info.value)
    assert fake.await_count == 3


def test_request_timeout_is_enforced_per_attempt(env, no_sleep):
    """掛住的請求要在單次上限內被切斷，否則會卡住批次腳本或 bot handler。"""

    async def hang(**_kwargs):
        # 單次上限沒生效時 5 秒後明確失敗，而不是讓整套測試無限等待
        try:
            await asyncio.wait_for(asyncio.Event().wait(), 5)
        except TimeoutError:
            pytest.fail("REQUEST_TIMEOUT_SECONDS 沒有套用到請求上")

    client, fake = _client_with()
    fake.side_effect = hang
    with mock.patch.object(GeminiNativeLLMClient, "REQUEST_TIMEOUT_SECONDS", 0.01):
        with pytest.raises(LLMServiceError) as exc_info:
            _run(client)
    assert "逾時" in str(exc_info.value)
    assert fake.await_count == 3


@pytest.mark.parametrize(
    ("code", "status", "hint"),
    [
        (400, "INVALID_ARGUMENT", "schema"),
        (401, "UNAUTHENTICATED", "LLM_API_KEY"),
        (403, "PERMISSION_DENIED", "LLM_API_KEY"),
        (404, "NOT_FOUND", "LLM_MODEL_NAME"),
    ],
)
def test_client_errors_are_not_retried_and_carry_config_hint(env, no_sleep, code, status, hint):
    client, fake = _client_with(_api_error(errors.ClientError, code, status))
    with pytest.raises(LLMServiceError) as exc_info:
        _run(client)
    message = str(exc_info.value)
    assert type(exc_info.value) is LLMServiceError
    assert f"{code} {status}" in message and hint in message
    fake.assert_awaited_once()


@pytest.mark.parametrize("reason", ["API_KEY_INVALID", "API_KEY_EXPIRED"])
def test_invalid_api_key_reported_as_400_points_to_key(env, no_sleep, reason):
    """Gemini 對無效 / 過期金鑰回 400 而非 401；提示要指向金鑰，不是 schema。"""
    error = _api_error(
        errors.ClientError, 400, "INVALID_ARGUMENT", message="API key not valid.",
        details=[{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": reason}],
    )
    client, _ = _client_with(error)
    with pytest.raises(LLMServiceError) as exc_info:
        _run(client)
    message = str(exc_info.value)
    assert "LLM_API_KEY 無效或已過期" in message
    assert "schema" not in message


_POISONED_DETAILS = [{"reason": "PROHIBITED_CONTENT", "quota": "429 Quota"}]


@pytest.mark.parametrize(
    "errors_to_raise",
    [
        [_api_error(errors.ClientError, 400, "INVALID_ARGUMENT", message="bad request", details=_POISONED_DETAILS)],
        # 5xx 耗盡走基底 _retry_transient 的訊息，也不能帶 details
        [_api_error(errors.ServerError, 503, "UNAVAILABLE", message="bad request", details=_POISONED_DETAILS)] * 3,
    ],
    ids=["400", "503-exhausted"],
)
def test_api_error_message_excludes_raw_details(env, no_sleep, errors_to_raise):
    """回應 details 裡的字不能進訊息：腳本以子字串分級，會被誤判成拒答或額度。"""
    client, _ = _client_with(*errors_to_raise)
    with pytest.raises(LLMServiceError) as exc_info:
        _run(client)
    message = str(exc_info.value)
    assert "bad request" in message
    assert "PROHIBITED_CONTENT" not in message and "429" not in message and "Quota" not in message


def test_api_error_message_is_truncated_and_handles_missing_fields(env, no_sleep):
    """代理回 HTML 錯誤頁時 message 是整頁；缺欄位時不能印出 None。"""
    client, _ = _client_with(errors.ClientError(400, {"error": {"code": 400, "message": "x" * 5000}}))
    with pytest.raises(LLMServiceError) as exc_info:
        _run(client)
    message = str(exc_info.value)
    assert "x" * 500 in message and "x" * 501 not in message
    assert "None" not in message


@pytest.mark.parametrize(
    ("error", "type_name"),
    [(KeyError("candidates"), "KeyError"), (ValueError(), "ValueError")],
)
def test_unexpected_exception_is_wrapped_with_type_and_traceback(env, no_sleep, caplog, error, type_name):
    """非預期例外：訊息帶型別名（str 為空時仍可辨識），log 帶 traceback。"""
    client, _ = _client_with(error)
    with caplog.at_level(logging.ERROR, logger="app.infrastructure.llm.gemini_native_client"):
        with pytest.raises(LLMServiceError) as exc_info:
            _run(client)
    assert type_name in str(exc_info.value)
    assert any(
        record.exc_info
        for record in caplog.records
        if record.name == "app.infrastructure.llm.gemini_native_client" and record.levelno == logging.ERROR
    )


# ----------------------------------------------------------------------
# 建構子與工廠
# ----------------------------------------------------------------------


def test_sdk_client_is_built_with_llm_api_key(env):
    GeminiNativeLLMClient()
    env.assert_called_once_with(api_key="test-key")


def test_sdk_client_construction_failure_is_service_error(env):
    env.side_effect = ValueError("bad key format")
    with pytest.raises(LLMServiceError, match="無法初始化 Gemini 原生客戶端: ValueError"):
        GeminiNativeLLMClient()


def test_missing_api_key_raises(env):
    with mock.patch.object(settings, "LLM_API_KEY", None):
        with pytest.raises(LLMServiceError, match="LLM_API_KEY 未設定"):
            GeminiNativeLLMClient()


def test_effort_is_rejected_with_legacy_message(env):
    with pytest.raises(LLMServiceError) as exc_info:
        GeminiNativeLLMClient(effort="high")
    assert "effort 覆寫僅" in str(exc_info.value)
    assert "LLM_PROVIDER='gemini-native'" in str(exc_info.value)


def test_base_url_is_not_required(env):
    with mock.patch.object(settings, "LLM_BASE_URL", None):
        assert GeminiNativeLLMClient().model_label == "(gemini-native)gemini-test"


def test_factory_routes_to_native_client(env):
    """真實類別經註冊表解析（同時核對靜態旗標）並實例化。"""
    assert factory.resolve_client_class("gemini-native") is GeminiNativeLLMClient
    assert factory.is_cli_provider("gemini-native") is False
    assert "gemini-native" not in factory.providers_supporting_effort()
    assert isinstance(factory.create_llm_client(), GeminiNativeLLMClient)


def test_runtime_info_has_no_cli_probe_block(env):
    info = asyncio.run(
        RuntimeConfigService().get_runtime_info(SimpleNamespace(llm_client=None))
    )
    assert info["claude_code"] is None


# ----------------------------------------------------------------------
# HTTP 層：SDK 真實的 wire 轉換與回應解析
# ----------------------------------------------------------------------


class TestHttpLayer:
    """在 SDK 的 ``async_request`` 攔截：驗送出的 body 與從原始 JSON 解析的分流。"""

    @staticmethod
    def _run_with_wire_response(payload: dict[str, object]) -> tuple[object, list[dict[str, object]]]:
        client = GeminiNativeLLMClient()  # 真實 genai.Client（base_env，不替換 SDK）
        sent: list[dict[str, object]] = []

        async def fake_request(http_method, path, request_dict, http_options=None):
            sent.append({"path": path, "body": request_dict})
            return types.HttpResponse(headers={}, body=json.dumps(payload))

        with mock.patch.object(client._client._api_client, "async_request", side_effect=fake_request):
            try:
                outcome: object = _run(client)
            except LLMServiceError as error:
                outcome = error
        return outcome, sent

    def test_wire_body_carries_safety_settings_and_system_instruction(self, base_env, no_sleep):
        result, sent = self._run_with_wire_response({
            "candidates": [{
                "content": {"role": "model", "parts": [{"text": _VALID_JSON}]},
                "finishReason": "STOP",
            }],
        })

        assert not isinstance(result, Exception), result
        assert result.parsed_data == {"item": {"name": "x"}}
        assert sent[0]["path"] == "models/gemini-test:generateContent"
        body = sent[0]["body"]
        assert body["systemInstruction"]["parts"] == [{"text": "SYS"}]
        assert body["contents"] == [{"role": "user", "parts": [{"text": "USER"}]}]
        assert body["safetySettings"] == [
            {"category": category, "threshold": "BLOCK_NONE"}
            for category in (
                "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                "HARM_CATEGORY_HATE_SPEECH",
                "HARM_CATEGORY_HARASSMENT",
                "HARM_CATEGORY_DANGEROUS_CONTENT",
            )
        ]
        generation = body["generationConfig"]
        assert generation["responseJsonSchema"] == _FLAT_SCHEMA
        assert generation["responseMimeType"] == "application/json"
        assert generation["temperature"] == 0.0

    def test_wire_prompt_block_is_refusal(self, base_env, no_sleep):
        outcome, sent = self._run_with_wire_response(
            {"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}}
        )
        assert isinstance(outcome, LLMRefusalError)
        assert len(sent) == 3
