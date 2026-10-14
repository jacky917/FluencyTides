"""``LLMServiceError`` 三個子類的測試（計畫 D5）。

Tests for the three ``LLMServiceError`` subclasses: distinct ``error_code``,
subclass relationship preserved, and the codes surfacing through the
project's global exception handler into ``ErrorResponse``.
"""

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.core.exceptions import (
    FluencyTidesError,
    LLMQuotaError,
    LLMRefusalError,
    LLMRetryExhaustedError,
    LLMServiceError,
    LLMServiceUnavailableError,
)


@pytest.mark.parametrize(
    ("exc_cls", "code"),
    [
        (LLMRetryExhaustedError, "LLM_RETRY_EXHAUSTED"),
        (LLMQuotaError, "LLM_QUOTA"),
        (LLMRefusalError, "LLM_REFUSAL"),
        (LLMServiceUnavailableError, "LLM_SERVICE_UNAVAILABLE"),
    ],
)
def test_subclass_has_own_code_and_stays_a_service_error(exc_cls, code):
    exc = exc_cls("msg")
    assert exc.error_code == code
    assert isinstance(exc, LLMServiceError)  # 既有 except LLMServiceError 一律仍能捕獲
    assert exc.status_code == LLMServiceError.status_code
    assert exc.message == "msg"


def test_base_code_unchanged():
    assert LLMServiceError("x").error_code == "LLM_SERVICE_ERROR"


def test_codes_reach_error_response_through_global_handler():
    """經 main.py 的全域 handler，error_code 原樣進入 ErrorResponse。"""
    try:
        from app.main import fluencytides_error_handler
    except Exception as import_error:  # noqa: BLE001 - 環境缺依賴時跳過而非誤判
        pytest.skip(f"app.main 無法載入: {import_error}")

    app = FastAPI()
    app.add_exception_handler(FluencyTidesError, fluencytides_error_handler)

    @app.get("/boom")
    async def boom():
        raise LLMQuotaError("LLM API 回報額度 (Quota)")

    resp = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert resp.status_code == LLMQuotaError.status_code
    body = resp.json()
    assert body["error_code"] == "LLM_QUOTA"
    assert "Quota" in body["message"]


# ----------------------------------------------------------------------
# LLMClient 的空內容分流：finish_reason → 例外子類（以 MockTransport 攔截 HTTP）
# ----------------------------------------------------------------------


def _empty_completion(finish_reason: str) -> dict:
    return {
        "id": "x", "object": "chat.completion", "created": 0, "model": "m",
        "choices": [{"index": 0, "finish_reason": finish_reason,
                     "message": {"role": "assistant", "content": None}}],
    }


def _run_empty_content(finish_reason: str) -> LLMServiceError:
    """讓 LLMClient 每次都收到空內容，回傳最後拋出的例外。"""
    import asyncio
    from unittest import mock

    import httpx
    from openai import AsyncOpenAI

    from app.core.config import settings
    from app.infrastructure.llm.client import LLMClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_empty_completion(finish_reason))

    with mock.patch.multiple(
        settings, LLM_PROVIDER="google", LLM_API_KEY="test-key",
        LLM_BASE_URL="http://llm.test/v1", LLM_MODEL_NAME="m",
    ):
        client = LLMClient()
    client._client = AsyncOpenAI(
        api_key="k", base_url="http://llm.test/v1", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    client.RETRY_DELAY_SECONDS = 0

    with pytest.raises(LLMServiceError) as exc_info:
        asyncio.run(client.generate_structured_data("s", "u", {"type": "object"}))
    return exc_info.value


@pytest.mark.parametrize("finish_reason", ["PROHIBITED_CONTENT", "SAFETY", "content_filter"])
def test_safety_finish_reason_raises_refusal_with_both_markers(finish_reason):
    """安全拒答 → LLM_REFUSAL，訊息同時含兩個分級表識別字串。"""
    error = _run_empty_content(finish_reason)
    assert isinstance(error, LLMRefusalError)
    assert "PROHIBITED_CONTENT" in str(error)
    assert "LLM API 在所有重試後仍回傳空內容" in str(error)


@pytest.mark.parametrize("finish_reason", ["RECITATION", "stop", "length"])
def test_non_safety_empty_content_raises_retry_exhausted(finish_reason):
    """RECITATION（版權重複）與一般空內容 → LLM_RETRY_EXHAUSTED，不算拒答。"""
    error = _run_empty_content(finish_reason)
    assert type(error) is LLMRetryExhaustedError
    assert error.error_code == "LLM_RETRY_EXHAUSTED"
    assert "PROHIBITED_CONTENT" not in str(error)
