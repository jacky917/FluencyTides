"""Structured-output LLM client module (OpenAI-compatible HTTP).

LLM 結構化輸出客戶端模組（OpenAI 相容 HTTP）。

This module wraps the interaction with LLMs that follow the OpenAI API format (for example Gemini's
OpenAI-compatible endpoint, official OpenAI, and third-party relays), using Response Format (JSON Schema) to
guarantee the output matches the given format.

Refactored from old/Anki/utils/llm_client.py, keeping these key design points:

- uses the AsyncOpenAI client for asynchronous I/O
- enforces the JSON Schema constraint through the Structured Outputs feature
- built-in retries (at most 3 attempts) and Markdown cleanup
- temperature=0.0 for maximum format stability and fewer hallucinations

Phase 2 improvements:

- the return value became the LLMGenerateResult Pydantic model (with the raw text and retry statistics)
- complete input/output logging was added (per llm-structured-output skill §9)
- exceptions are wrapped as LLMServiceError, keeping the Infrastructure layer's error boundary uniform

2026-09-06 hierarchy: attached to :class:`~app.infrastructure.llm.base.BaseLLMClient`; the schema tools moved to
``schema_utils`` (this class keeps a delegating method of the same name for existing callers); retry exhaustion /
quota / refusal now raise the matching ``LLMServiceError`` subclasses, with unchanged message text.

Design decisions:

- temperature is 0.0 because this LLM's job is "filling JSON fields precisely", not "creative writing", so format
  consistency must be maximized and hallucinations reduced.
- the retry interval is a fixed 2 seconds rather than exponential backoff, because LLM API errors are usually
  transient (such as rate limits) and recover after a short wait.

Dependencies:
    - openai: the AsyncOpenAI client
    - pydantic: data validation

本模組封裝與 LLM（相容 OpenAI API 格式，例如 Gemini 的 OpenAI 相容端點、
OpenAI 官方、各第三方中轉站）的交互，透過 Response Format (JSON Schema)
強制保證輸出符合指定格式。

重構自 old/Anki/utils/llm_client.py，保留了以下關鍵設計：
- 使用 AsyncOpenAI 客戶端實現非同步 I/O。
- 透過 Structured Outputs 功能強制 JSON Schema 約束。
- 內建重試機制（最多 3 次）與 Markdown 格式清理。
- temperature=0.0 以追求最大的格式穩定性與減少幻覺。

Phase 2 改進：
- 回傳值改為 LLMGenerateResult Pydantic 模型（含原始文字與重試統計）。
- 新增完整的 input/output 日誌記錄（符合 llm-structured-output skill §9）。
- 異常包裝為 LLMServiceError，保持 Infrastructure 層的錯誤邊界統一。

2026-09-06 層級化：掛上 ``BaseLLMClient``；
schema 工具搬到 ``schema_utils``（本類保留同名轉呼方法供既有呼叫端）；
重試耗盡 / 額度 / 拒答改拋 ``LLMServiceError`` 的對應子類，訊息文字不變。

設計決策：
- temperature 設為 0.0 是因為此 LLM 的職責是「精確填充 JSON 欄位」
  而非「創意寫作」，需要最大化格式一致性與減少幻覺。
- 重試間隔使用固定 2 秒而非指數退避，是因為 LLM API 的錯誤
  通常為瞬時性問題（如速率限制），短暫等待即可恢復。

依賴：
    - openai：AsyncOpenAI 客戶端
    - pydantic：資料驗證
"""

import asyncio
import json
import logging
from typing import ClassVar

from openai import AsyncOpenAI
import openai
import httpx
from openai.types.chat import ChatCompletion, ChatCompletionMessageFunctionToolCall
from openai.types.shared_params import ResponseFormatJSONSchema

from app.core.config import settings
from app.core.exceptions import (
    LLMQuotaError,
    LLMRefusalError,
    LLMRetryExhaustedError,
    LLMServiceError,
)
from app.infrastructure.llm.base import BaseLLMClient, EffortArg
from app.infrastructure.llm.endpoints import NO_SAFETY_SETTINGS_IDS, TYPE_OPENAI, Endpoint
from app.infrastructure.llm.schema_utils import resolve_json_schema, strip_markdown_fences
from app.schemas.llm.base import LLMGenerateResult

logger = logging.getLogger(__name__)

# 視為安全拒答的 finish_reason（大小寫不敏感比對）。刻意不含 RECITATION：
# 那是版權內容重複輸出而停止，不是安全拒答，歸入重試耗盡，免得灌水拒答率。
# finish_reason values treated as safety refusals (case-insensitive).
# RECITATION is deliberately excluded: it is a copyright stop, not a refusal.
_REFUSAL_FINISH_REASONS: frozenset[str] = frozenset(
    {"CONTENT_FILTER", "SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST"}
)

# 不送 safety_settings 的 provider（定義與理由見 endpoints.NO_SAFETY_SETTINGS_IDS）；
# 指定端點時改看端點的 send_safety_settings
_NO_SAFETY_SETTINGS_PROVIDERS: frozenset[str] = NO_SAFETY_SETTINGS_IDS


class LLMClient(BaseLLMClient):
    """Asynchronous client wrapping operations against an LLM compatible with the OpenAI format.

    封裝與 LLM（相容 OpenAI 格式）相關操作的非同步客戶端。

    Uses the OpenAI API's response_format feature to guarantee structured JSON output; together with the JSON
    Schema of the Anki models, this makes sure the data the LLM returns maps directly onto Anki note fields.

    使用 OpenAI API 的 response_format 功能強制保證結構化 JSON 輸出，
    配合 Anki 模型的 JSON Schema 定義，確保 LLM 回傳的資料能直接
    映射到 Anki 筆記的欄位中。

    Attributes:
        MAX_RETRIES: the maximum number of attempts, for handling temporary API errors
            最大重試次數，用於處理暫時性 API 錯誤
        RETRY_DELAY_SECONDS: the retry interval in seconds
            重試間隔秒數
        _client: the asynchronous OpenAI API client instance
            非同步的 OpenAI API 客戶端實例
        _supports_safety_settings: whether safety_settings is attached for this endpoint
            是否對此端點附帶 safety_settings
    """

    # 此類別服務多個 provider 值（google / openai / 各中轉站），工廠不以此路由；
    # 標籤前綴由 labels.build_model_label 依實際 provider 決定。
    PROVIDER_NAME: ClassVar[str] = "openai-compatible"
    ENDPOINT_TYPES: ClassVar[frozenset[str]] = frozenset({TYPE_OPENAI})

    # 最大重試次數，用於處理暫時性 API 錯誤
    MAX_RETRIES = 3

    # 重試間隔秒數
    RETRY_DELAY_SECONDS = 2

    def __init__(
        self,
        *,
        model: str | None = None,
        effort: EffortArg = None,
        endpoint: Endpoint | None = None,
    ) -> None:
        """Initialize the AsyncOpenAI client from the endpoint (or the legacy single-set settings).

        依端點（或舊式單組設定）初始化 AsyncOpenAI 客戶端。

        Args:
            model: the model-name override for this instance; ``None`` keeps ``LLM_MODEL_NAME``
                覆寫模型名；None 沿用 ``LLM_MODEL_NAME``
            effort: not supported by this provider; any value is rejected by the base class
                本 provider 不支援；給值即由基底拋錯
            endpoint: the endpoint; ``None`` reads ``LLM_API_KEY`` / ``LLM_BASE_URL``
                端點；None 讀 ``LLM_API_KEY`` / ``LLM_BASE_URL``

        Raises:
            LLMServiceError: if the endpoint type mismatches, effort is given, the key or base URL is not set, or
                the model name is empty
                若端點類型不符、effort 給值、金鑰或端點網址未設定，或模型名為空
        """
        self._check_endpoint_type(endpoint)
        if endpoint is not None:
            api_key = endpoint.api_key.get_secret_value() if endpoint.api_key else None
            base_url = endpoint.base_url
            key_setting, url_setting = endpoint.api_key_setting, endpoint.base_url_setting
        else:
            api_key, base_url = settings.LLM_API_KEY, settings.LLM_BASE_URL
            key_setting, url_setting = "LLM_API_KEY", "LLM_BASE_URL"

        # 檢查順序與層級化前一致：effort（原在工廠）→ API 金鑰 → 端點 → 模型名
        self._reject_unsupported_effort(effort)
        if not api_key:
            raise LLMServiceError(f"{key_setting} 未設定，無法初始化 LLM 客戶端。")
        if not base_url:
            raise LLMServiceError(f"{url_setting} 未設定，無法初始化 LLM 客戶端。")
        super().__init__(model=model, effort=effort, endpoint=endpoint)

        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=120.0,  # 加入 120 秒的 Timeout，防止中轉站無回應時無限卡死
        )
        self._supports_safety_settings = (
            endpoint.send_safety_settings
            if endpoint is not None
            else self._provider not in _NO_SAFETY_SETTINGS_PROVIDERS
        )

        logger.info("LLMClient 初始化完成，目標模型: %s", self.model_label)

    async def generate_structured_data(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, object],
    ) -> LLMGenerateResult:
        """Call the LLM and obtain JSON data that strictly conforms to ``response_schema``.

        呼叫 LLM 並取得嚴格符合 response_schema 的 JSON 資料。

        Uses the OpenAI API's response_format feature to guarantee compliant JSON output, together with retries and
        Markdown cleanup, for reliable structured output. The schema is also appended to the system prompt as a
        second safeguard for relays without strict json_schema support.

        利用 OpenAI API 的 response_format 功能保證輸出為合規的 JSON，
        配合重試機制與 Markdown 格式清理，確保可靠的結構化輸出。schema 也會附加在系統提示中，
        作為不支援 strict json_schema 的中轉站的雙重保險。

        Args:
            system_prompt: the system prompt given to the LLM
                指定給 LLM 的系統提示
            user_prompt: the user's input
                使用者的輸入內容
            response_schema: the JSON Schema definition dict
                JSON Schema 定義字典

        Returns:
            an ``LLMGenerateResult`` Pydantic model instance
            ``LLMGenerateResult`` Pydantic 模型實例

        Raises:
            LLMQuotaError: if rate limiting still fails after every retry
                若速率限制在所有重試後仍失敗
            LLMRefusalError: if the content is still empty after every retry and the last ``finish_reason`` is a
                safety refusal
                若所有重試後仍為空內容，且最後一次的 finish_reason 為安全拒答
            LLMRetryExhaustedError: if empty content or invalid JSON is still returned after every retry
                若多次重試後仍回傳空內容或非有效 JSON
            LLMServiceError: if any other API request fails, or the API keeps returning an invalid format
                若其他 API 請求失敗，或 API 持續回傳無效格式
        """
        logger.info(
            "LLM 結構化生成請求 -> model: %s, user_prompt 長度: %d 字元",
            self.model_label,
            len(user_prompt),
        )
        properties = response_schema.get("properties")
        logger.debug(
            "LLM 請求詳情 -> system_prompt: %s, schema_keys: %s",
            system_prompt[:200],
            list(properties.keys()) if isinstance(properties, dict) else "N/A",
        )

        # 某些模型（如 Gemini）不支援 JSON Schema 中的 $defs 與 $ref，必須展開
        resolved_schema = resolve_json_schema(response_schema)

        # 針對第三方中轉站（如 Yinli / DeepSeek），因為他們可能不支援 strict json_schema，
        # 我們必須將 Schema 結構明確寫在 system_prompt 中，作為雙重保險。
        schema_instruction = (
            "\n\n【重要：請嚴格遵守以下 JSON Schema 輸出結構，不要遺漏或隨意改變任何必填欄位】\n"
            "```json\n"
            f"{json.dumps(resolved_schema, ensure_ascii=False, indent=2)}\n"
            "```\n"
        )
        system_prompt_with_schema = system_prompt + schema_instruction

        # 建構 OpenAI Structured Outputs 所需的 response_format 格式
        structured_format: ResponseFormatJSONSchema = {
            "type": "json_schema",
            "json_schema": {
                "name": "structured_output",
                "schema": resolved_schema,
                "strict": True,
            },
        }

        # 只有支援 safety_settings 的中轉站才會附帶此參數（None 即不送，與 SDK 預設相同）
        extra_body: dict[str, object] | None = {
            "safety_settings": [
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
            ]
        } if self._supports_safety_settings else None

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                response: ChatCompletion = (
                    await self._client.chat.completions.create(
                        model=self._model_name,
                        messages=[
                            {"role": "system", "content": system_prompt_with_schema},
                            {"role": "user", "content": user_prompt},
                        ],
                        response_format=structured_format,
                        # temperature=0.0 以追求最大的格式穩定性與減少幻覺
                        temperature=0.0,
                        extra_body=extra_body,
                    )
                )
            except (openai.OpenAIError, httpx.RequestError) as e:
                logger.error(
                    "LLM API 請求失敗 (第 %d 次): %s", attempt, str(e)
                )
                if attempt == self.MAX_RETRIES:
                    # 速率限制以子類標記，訊息不變（本身已含 429 字樣供腳本分級）
                    error_cls = LLMQuotaError if isinstance(e, openai.RateLimitError) else LLMServiceError
                    raise error_cls(
                        f"LLM API 請求在 {self.MAX_RETRIES} 次重試後仍失敗: {e}"
                    ) from e
                await asyncio.sleep(self.RETRY_DELAY_SECONDS)
                continue

            if isinstance(response, str):
                logger.error("LLM API 回傳了字串而非 ChatCompletion 物件: %s", response)
                if attempt == self.MAX_RETRIES:
                    raise LLMServiceError(f"LLM API 回傳無效格式: {response}")
                await asyncio.sleep(self.RETRY_DELAY_SECONDS)
                continue

            message = response.choices[0].message if hasattr(response, 'choices') and response.choices else None
            response_content = message.content if message else None

            # 處理部分中轉站 (如 New API) 將 json_schema 映射為 tool_calls 的情況
            # SDK 型別另含 custom 工具呼叫（無 .function，內容在 .custom.input）；
            # 中轉站映射出的必為 function 型，其他型別視同空內容、走下方重試
            if not response_content and message and message.tool_calls:
                first_call = message.tool_calls[0]
                if isinstance(first_call, ChatCompletionMessageFunctionToolCall):
                    response_content = first_call.function.arguments

            if not response_content:
                finish_reason = response.choices[0].finish_reason if response.choices else "unknown"
                logger.error(
                    "LLM API 回傳內容為空 (第 %d 次)。原因 (finish_reason): %s", attempt, finish_reason
                )
                if attempt == self.MAX_RETRIES:
                    if str(finish_reason or "").upper() in _REFUSAL_FINISH_REASONS:
                        # 訊息同時帶兩個分級表識別字串：既有腳本視同跳句
                        raise LLMRefusalError(
                            "LLM API 在所有重試後仍回傳空內容"
                            f"（安全拒答 PROHIBITED_CONTENT，finish_reason={finish_reason}）。"
                        )
                    raise LLMRetryExhaustedError(
                        "LLM API 在所有重試後仍回傳空內容。"
                    )
                await asyncio.sleep(self.RETRY_DELAY_SECONDS)
                continue

            # 清理可能的 Markdown 程式碼區塊標記
            cleaned_content = strip_markdown_fences(response_content)

            try:
                parsed_data: dict[str, object] = json.loads(cleaned_content)

                logger.info(
                    "LLM 結構化輸出成功 -> 第 %d 次嘗試, 回傳 %d 個欄位",
                    attempt,
                    len(parsed_data),
                )
                logger.debug(
                    "LLM 回傳原始文字 (前 500 字元): %s",
                    response_content[:500],
                )

                return LLMGenerateResult(
                    raw_content=response_content,
                    parsed_data=parsed_data,
                    model_name=self.model_label,
                    attempt_count=attempt,
                )

            except json.JSONDecodeError as decode_error:
                logger.error(
                    "無法將 LLM API 回傳結果解析為 JSON (第 %d 次)。原始文字: %s",
                    attempt,
                    response_content,
                )
                if attempt == self.MAX_RETRIES:
                    raise LLMRetryExhaustedError(
                        f"LLM 輸出非有效 JSON 格式: {decode_error}"
                    ) from decode_error
                logger.info("準備重啟第 %d 次請求...", attempt + 1)
                await asyncio.sleep(self.RETRY_DELAY_SECONDS)

        # 理論上不會執行到這裡，但為了型別安全加上保底
        raise LLMRetryExhaustedError("LLM 結構化輸出在所有重試後仍然失敗。")

    # ------------------------------------------------------------------
    # 既有呼叫端相容的轉呼（Compatibility shim；計畫 §8 移除）
    # ------------------------------------------------------------------

    @classmethod
    def _resolve_json_schema(
        cls, schema: dict[str, object], defs: dict[str, object] | None = None
    ) -> dict[str, object]:
        """Delegate to :func:`schema_utils.resolve_json_schema` (kept for compatibility).

        轉呼 ``schema_utils.resolve_json_schema``（相容保留）。

        Kept for existing callers that reuse the flattener without instantiating a client.

        保留給不建立客戶端、直接重用攤平功能的既有呼叫端。

        Args:
            schema: the Pydantic-generated JSON Schema
                Pydantic 產生的 JSON Schema
            defs: the top-level ``$defs`` dict; ``None`` reads it from ``schema``
                最上層的 ``$defs`` 字典；None 從 ``schema`` 讀取

        Returns:
            the schema with every ``$ref`` inlined and ``$defs`` removed
            展開所有 ``$ref`` 並移除 ``$defs`` 後的 schema
        """
        return resolve_json_schema(schema, defs)
