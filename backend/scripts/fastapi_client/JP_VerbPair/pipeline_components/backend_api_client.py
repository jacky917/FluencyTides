"""後端管線 API 呼叫器。

Backend pipeline API caller: sends HTTP requests to the FastAPI card
generation endpoint with tenacity-based backoff retry for timeouts and
gateway errors.

專責發送 HTTP 請求至 FastAPI 的卡片生成端點，
並使用 tenacity 實作針對 Timeout 與 HTTP 伺服器錯誤的退避重試保護。
"""

import logging
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

logger = logging.getLogger(__name__)

def _should_retry_error(exception: Exception) -> bool:
    """自訂判斷是否該重試的邏輯。

    Custom retry predicate: always retry timeouts; retry HTTP 502/503/504.

    - TimeoutException 絕對要重試
    - HTTPStatusError 如果是 502, 503, 504 則重試

    Args:
        exception: 待判斷的例外。The exception to evaluate.

    Returns:
        bool: 是否應重試。Whether the call should be retried.
    """
    if isinstance(exception, httpx.TimeoutException):
        return True
    if isinstance(exception, httpx.HTTPStatusError):
        if exception.response.status_code in (502, 503, 504):
            return True
    return False

class BackendAPIClient:
    """後端卡片生成 API 的薄封裝（含退避重試）。

    Thin wrapper around the backend card-generation API with backoff retry.
    """

    def __init__(self, api_url: str, headers: dict | None = None):
        """初始化呼叫器。

        Initialize the client.

        Args:
            api_url: 生成端點完整 URL。Full URL of the generation endpoint.
            headers: 附加 HTTP 標頭（如 CF Access）。Extra HTTP headers.
        """
        self.api_url = api_url
        self.headers = headers or {}

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=15),
        retry=retry_if_exception(_should_retry_error),
        before_sleep=lambda retry_state: logger.warning(
            f"⚠️ API 呼叫失敗，準備進行第 {retry_state.attempt_number} 次重試... "
            f"原因: {retry_state.outcome.exception()}"
        )
    )
    async def invoke_generation_pipeline(self, payload: dict) -> dict:
        """觸發後端的卡片生成管線。

        Invoke the backend card-generation pipeline.

        Args:
            payload: 送給 /api/v1/verb-pair/generate-child-cards 的請求主體。
                Request body for the generation endpoint.

        Returns:
            dict: API 的 json 回應。The API's JSON response.
        """
        logger.info(f"🚀 準備發送 Payload 至 FastAPI 後端管線 ({self.api_url})...")
        
        # 使用 timeout=None 因為 LLM 生成可能需要數分鐘
        async with httpx.AsyncClient(headers=self.headers, timeout=None) as client:
            response = await client.post(self.api_url, json=payload)
            
            # 如果發生 4xx 或 5xx 錯誤，這會拋出 httpx.HTTPStatusError
            response.raise_for_status()
            
            logger.info("✅ API 請求處理成功！")
            return response.json()
