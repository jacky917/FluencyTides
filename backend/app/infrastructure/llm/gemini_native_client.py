"""
Gemini 原生 SDK（google-genai）文字結構化輸出客戶端（計畫 D7）。

``LLM_PROVIDER=google`` 走的是 Google 的 OpenAI 相容端點，該端點不認識
``safety_settings``（傳了直接 400），安全閾值永遠是 Google 預設——而 galgame
台詞正是最容易觸發拒答的內容。原生 SDK 的 ``GenerateContentConfig`` 直接收
``safety_settings``，本客戶端四類一律 ``BLOCK_NONE``。

與 OpenAI 相容 :class:`~app.infrastructure.llm.client.LLMClient` 的差異：

- system prompt 走 ``system_instruction``，不併入 contents，也不把 schema 抄進
  prompt（原生 structured output 由服務端強制，不需要中轉站那層雙重保險）
- schema 以 ``response_json_schema`` 傳送；先經 ``resolve_json_schema`` 展平。
  2026-10-14 實測（google-genai 2.10.0 / gemini-3.1-pro-preview）四種組合——
  ``response_json_schema`` / ``response_schema`` × 原始 ``$defs`` / 展平——
  全數被接受且輸出通過 Pydantic 驗證；展平是為了與其他 provider 送出同一份
  schema，排除 ``$ref`` 解析差異這個變因
- 網路層瞬時錯誤（5xx、連線中斷、傳輸層逾時）走 ``BaseLLMClient._retry_transient``
  指數退避（1 秒、2 秒）。SDK 的 tenacity 重試預設關閉，但環境裝了 aiohttp 時
  SDK 的非同步請求一律走 aiohttp，而 aiohttp 路徑對連線錯誤另有一次內建重試
  （先等隨機 1–10 秒，無法關閉），本客戶端的退避疊在它之上
- 429 / RESOURCE_EXHAUSTED **不**在此重試，直接拋 ``LLMQuotaError``：額度或
  每分鐘速率耗盡時秒級退避撐不過配額窗口，生成腳本的分級表會暫停 60 秒

內容層失敗（空內容、截斷、壞 JSON、拒答）的重試次數與間隔與 ``LLMClient`` 相同
（3 次、固定 2 秒），最後一次的失敗型態決定例外子類——與 google 路徑比對拒答率
時，重試次數須一致才可比。拒答的認定兩邊並不完全相同（見 ``_REFUSAL_REASONS``
與主迴圈的註解），比對時要把這點算進去。

English summary:
    Structured-output client over the native google-genai SDK. Unlike the
    OpenAI-compatible path it can send ``safety_settings`` (all four
    categories at ``BLOCK_NONE``), uses ``system_instruction`` and
    ``response_json_schema``. Transport-level transient errors use the base
    exponential-backoff helper; quota errors surface immediately as
    ``LLMQuotaError``; content-level failures retry like ``LLMClient`` and
    the last attempt's failure picks the exception subclass.

Dependencies:
    - google-genai: 原生 Gemini SDK（本模組只在被工廠選中時才載入）。
    - aiohttp / httpx: SDK 的兩種傳輸層，用於辨識連線類瞬時錯誤。
"""

import asyncio
import json
import logging
from typing import ClassVar

import aiohttp
import httpx
from google import genai
from google.genai import errors, types

from app.core.config import settings
from app.core.exceptions import (
    LLMQuotaError,
    LLMRefusalError,
    LLMRetryExhaustedError,
    LLMServiceError,
)
from app.infrastructure.llm.base import BaseLLMClient
from app.infrastructure.llm.schema_utils import resolve_json_schema, strip_markdown_fences
from app.schemas.llm.base import LLMGenerateResult

logger = logging.getLogger(__name__)

# 四類可調閾值一律關到 BLOCK_NONE；PROHIBITED_CONTENT 等不可調的封鎖仍會發生
# All four adjustable categories at BLOCK_NONE; non-adjustable blocks remain.
_SAFETY_SETTINGS: list[types.SafetySetting] = [
    types.SafetySetting(category=category, threshold=types.HarmBlockThreshold.BLOCK_NONE)
    for category in (
        types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        types.HarmCategory.HARM_CATEGORY_HARASSMENT,
        types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
    )
]

# 視為安全拒答的 finish_reason（候選被中斷）/ block_reason（prompt 被擋）。
# - 與 LLMClient 一樣不含 RECITATION：那是版權重複輸出而停止，不是安全拒答
# - 比 LLMClient 多 SPII（敏感個資）與 JAILBREAK（prompt 被判為越獄）：兩者都是
#   安全類封鎖，只在原生回應出現；LLMClient 的 CONTENT_FILTER 是 OpenAI 端點的值，
#   原生 SDK 不會出現
# - 其餘值（OTHER / LANGUAGE / MODEL_ARMOR …）刻意不算拒答，歸入重試耗盡
# finish/block reasons treated as safety refusals; everything else counts as
# retry exhaustion.
_REFUSAL_REASONS: frozenset[str] = frozenset(
    {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "JAILBREAK"}
)

# 網路層可重試的傳輸例外。SDK 有 aiohttp 時走 aiohttp（正式環境即此），否則走
# httpx，兩者都要列。只收連線 / 讀寫 / 逾時類；代理設定錯、協定不支援等確定性
# 錯誤不重試。內建 TimeoutError 涵蓋 SDK 或 socket 自己拋的逾時（aiohttp 的
# ServerTimeoutError 同時是它與 ClientConnectionError 的子類）。
# Retryable transport exceptions for both of the SDK's HTTP backends.
_TRANSIENT_TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (
    aiohttp.ClientConnectionError,
    aiohttp.ClientPayloadError,
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
    ConnectionError,
    TimeoutError,
)

# 上一組的例外：同屬 aiohttp.ClientConnectionError，但是確定性錯誤，重試沒用。
# 代理設定錯、SSL / 憑證驗證失敗（例如公司 MITM 代理）、憑證指紋不符（安全訊號，
# 更不該被重試掉）。
# Deterministic subclasses of the transient families above; never retried.
_NON_TRANSIENT_TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (
    aiohttp.ClientProxyConnectionError,
    aiohttp.ClientSSLError,
    aiohttp.ServerFingerprintMismatch,
)

# 確定性設定錯誤的提示：附在訊息裡，讓使用者知道該檢查哪個設定
# Hints for deterministic configuration errors, appended to the message.
_CLIENT_ERROR_HINTS: dict[int, str] = {
    400: "請求被拒，多半是 schema 或參數不被接受",
    401: "請檢查 LLM_API_KEY 是否有效",
    403: "請檢查 LLM_API_KEY 的權限，或專案是否已啟用 Generative Language API",
    404: "模型不存在，請檢查 LLM_MODEL_NAME",
}

# Gemini API 對無效 / 過期金鑰回 400 而非 401，要從 details 的 ErrorInfo.reason 認
# Gemini reports bad keys as 400; recognize them by ErrorInfo.reason.
_API_KEY_ERROR_REASONS: frozenset[str] = frozenset({"API_KEY_INVALID", "API_KEY_EXPIRED"})

# 不可用回應寫進 log 的原始文字上限（截斷時可能上萬字）
_LOG_TEXT_LIMIT = 2000

# 例外訊息裡 API 錯誤說明的上限：代理回 HTML 錯誤頁時 message 是整頁內容
_ERROR_MESSAGE_LIMIT = 500


def _is_transient(error: BaseException) -> bool:
    """網路層瞬時錯誤：5xx 與連線 / 逾時類傳輸例外。429 刻意不在此列（見模組說明）。

    Transport-level transient errors: 5xx and connection/timeout transport
    exceptions. 429 is deliberately excluded (see module docstring).
    """
    if isinstance(error, _NON_TRANSIENT_TRANSPORT_ERRORS):
        return False
    return isinstance(error, (errors.ServerError, *_TRANSIENT_TRANSPORT_ERRORS))


def _reason_name(reason: object) -> str:
    """把 SDK 的 enum 轉成大寫名稱；None 回空字串。純字串也能正規化（防禦性）。

    Normalize an SDK enum to its upper-case name; also accepts plain strings.
    """
    if reason is None:
        return ""
    return str(getattr(reason, "name", reason)).upper()


def _api_error_summary(error: errors.APIError) -> str:
    """APIError 的精簡描述：狀態碼、狀態名、人看的訊息。

    Compact ``code status: message`` description of an APIError.

    刻意不用 ``str(error)``：它會嵌入整包回應 JSON，而生成腳本以子字串比對
    分級（``"429"`` / ``"PROHIBITED_CONTENT"``），details 裡碰巧出現這些字就會
    誤判。完整內容另寫進 log。
    """
    message = (error.message or "（無訊息）")[:_ERROR_MESSAGE_LIMIT]
    return f"{error.code} {error.status or 'UNKNOWN'}: {message}"


def _describe_error(error: BaseException) -> str:
    """網路層重試耗盡時寫進訊息的錯誤描述（交給 ``_retry_transient``）。

    Error description for the transient-retry exhaustion message.
    """
    if isinstance(error, errors.APIError):
        return _api_error_summary(error)
    return f"{type(error).__name__}: {error!r}"


def _client_error_hint(error: errors.APIError) -> str | None:
    """依 details 的 ErrorInfo.reason（優先）或狀態碼給出設定提示。

    Configuration hint from ErrorInfo.reason (preferred) or the status code.
    """
    body = error.details if isinstance(error.details, dict) else {}
    error_body = body.get("error")
    details = error_body.get("details") if isinstance(error_body, dict) else None
    reasons = {
        item.get("reason") for item in (details if isinstance(details, list) else []) if isinstance(item, dict)
    }
    if reasons & _API_KEY_ERROR_REASONS:
        return "LLM_API_KEY 無效或已過期"
    return _CLIENT_ERROR_HINTS.get(error.code)


class GeminiNativeLLMClient(BaseLLMClient):
    """使用 google-genai 原生 SDK 的結構化輸出客戶端。

    Structured-output client using the native google-genai SDK.

    Attributes:
        _client: google-genai Client 實例。The google-genai client.
    """

    PROVIDER_NAME: ClassVar[str] = "gemini-native"

    # 內容層重試（空內容 / 截斷 / 壞 JSON / 拒答）：與 LLMClient 相同
    MAX_RETRIES = 3
    RETRY_DELAY_SECONDS = 2

    # 網路層瞬時錯誤重試（每次內容層嘗試內部）：指數退避 1s → 2s
    TRANSIENT_MAX_RETRIES = 3
    TRANSIENT_BASE_DELAY_SECONDS = 1.0

    # 單次請求（含 SDK 內部行為）的絕對上限。數字取自 LLMClient 的 AsyncOpenAI
    # timeout，但語義不同：那邊是 httpx 的分階段超時（connect / read 各自計時），
    # 這裡以 asyncio.timeout 包住整次呼叫
    REQUEST_TIMEOUT_SECONDS = 120.0

    def __init__(self, *, model: str | None = None, effort: str | None = None) -> None:
        """以 ``LLM_API_KEY`` 建立 google-genai Client。

        Create the google-genai client from ``LLM_API_KEY``.

        金鑰沿用 ``LLM_API_KEY``：Google 原生與 OpenAI 相容端點共用同一把 key。
        ``LLM_BASE_URL`` 對此 provider 無意義，忽略。

        Args:
            model: 覆寫模型名；None 沿用 ``LLM_MODEL_NAME``。Model override.
            effort: 本 provider 不支援；給值即拋錯。Unsupported; rejected.

        Raises:
            LLMServiceError: effort 給值、``LLM_API_KEY`` 未設定、模型名為空，
                或 SDK Client 建立失敗時。
        """
        # 檢查順序與 LLMClient 一致：effort → API 金鑰 → 模型名
        self._reject_unsupported_effort(effort)
        if not settings.LLM_API_KEY:
            raise LLMServiceError("LLM_API_KEY 未設定，無法初始化 Gemini 原生客戶端。")
        super().__init__(model=model, effort=effort)

        if settings.LLM_BASE_URL:
            logger.debug("gemini-native 不使用 LLM_BASE_URL（%s），已忽略。", settings.LLM_BASE_URL)

        try:
            self._client = genai.Client(api_key=settings.LLM_API_KEY)
        except Exception as error:  # noqa: BLE001 - 契約：建構失敗一律以 LLMServiceError 拋出
            logger.exception("google-genai Client 建立失敗")
            raise LLMServiceError(
                f"無法初始化 Gemini 原生客戶端: {type(error).__name__}: {error!r}"
            ) from error
        logger.info("GeminiNativeLLMClient 初始化完成，目標模型: %s", self.model_label)

    async def generate_structured_data(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, object],
    ) -> LLMGenerateResult:
        """呼叫 Gemini 並取得嚴格符合 ``response_schema`` 的 JSON 資料。

        Call Gemini and obtain JSON strictly conforming to the schema.

        Args:
            system_prompt: 系統提示，走 ``system_instruction``。System prompt.
            user_prompt: 使用者輸入。User input.
            response_schema: JSON Schema 定義字典。JSON Schema dict.

        Returns:
            LLMGenerateResult 實例。An LLMGenerateResult.

        Raises:
            LLMRefusalError: 最後一次嘗試仍為安全拒答。
            LLMRetryExhaustedError: 多次重試後仍為空內容、截斷或非有效 JSON。
            LLMQuotaError: 429 / 額度耗盡。
            LLMServiceError: 其他 API 失敗（含瞬時錯誤重試耗盡、逾時、設定錯誤）。
        """
        logger.info(
            "LLM 結構化生成請求 -> model: %s, user_prompt 長度: %d 字元",
            self.model_label,
            len(user_prompt),
        )

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            # temperature=0.0 以追求最大的格式穩定性與減少幻覺（與 LLMClient 一致）
            temperature=0.0,
            response_mime_type="application/json",
            response_json_schema=resolve_json_schema(response_schema),
            safety_settings=_SAFETY_SETTINGS,
        )

        for attempt in range(1, self.MAX_RETRIES + 1):
            response = await self._request(user_prompt, config)
            reason = self._refusal_or_finish_reason(response)
            text = response.text or ""

            # 失敗型態："refusal" / "empty" / 壞 JSON 的說明文字；None 為成功。
            # 被安全中斷的候選可能帶半截、甚至完整合法的 JSON——一律不送解析，
            # 直接按拒答處理（LLMClient 只在空內容時才看 finish_reason，這裡更嚴）。
            failure: str | None = None
            parsed_data: dict[str, object] | None = None
            if reason in _REFUSAL_REASONS:
                failure = "refusal"
            elif not text:
                failure = "empty"
            else:
                try:
                    decoded = json.loads(strip_markdown_fences(text))
                except json.JSONDecodeError as decode_error:
                    failure = f"非有效 JSON 格式: {decode_error}"
                else:
                    if isinstance(decoded, dict):
                        parsed_data = decoded
                    else:
                        failure = f"非有效 JSON 格式: 頂層為 {type(decoded).__name__} 而非 object"

            if parsed_data is not None:
                logger.info(
                    "LLM 結構化輸出成功 -> 第 %d 次嘗試, 回傳 %d 個欄位",
                    attempt,
                    len(parsed_data),
                )
                logger.debug("LLM 回傳原始文字 (前 500 字元): %s", text[:500])
                return LLMGenerateResult(
                    raw_content=text,
                    parsed_data=parsed_data,
                    model_name=self.model_label,
                    attempt_count=attempt,
                )

            is_last = attempt == self.MAX_RETRIES
            logger.log(
                logging.ERROR if is_last else logging.WARNING,
                "Gemini 回傳不可用 (第 %d/%d 次, response_id=%s, model_version=%s)。"
                "reason=%s (%s), 失敗型態=%s；原始文字(前 %d 字): %s",
                attempt,
                self.MAX_RETRIES,
                response.response_id,
                response.model_version,
                reason or "UNKNOWN",
                self._reason_message(response) or "Google 未附說明",
                failure,
                _LOG_TEXT_LIMIT,
                text[:_LOG_TEXT_LIMIT] or "（無）",
            )
            if is_last:
                self._raise_final(reason, failure or "empty")
            await asyncio.sleep(self.RETRY_DELAY_SECONDS)

        # 理論上不會執行到這裡，但為了型別安全加上保底
        raise LLMRetryExhaustedError("LLM 結構化輸出在所有重試後仍然失敗。")

    # ------------------------------------------------------------------
    # 內部 helper（Internal helpers）
    # ------------------------------------------------------------------

    async def _request(
        self, user_prompt: str, config: types.GenerateContentConfig
    ) -> types.GenerateContentResponse:
        """送出一次請求（含網路層瞬時重試），把 SDK 例外映射成 LLMServiceError 族。

        Send one request (with transport-level transient retries) and map SDK
        exceptions onto the ``LLMServiceError`` family.

        Raises:
            LLMQuotaError: 429 / RESOURCE_EXHAUSTED（不重試）。
            LLMServiceError: 瞬時錯誤重試耗盡、逾時、其他 4xx（附設定提示），
                或任何非預期例外（訊息帶型別名，log 帶 traceback）。
        """
        try:
            return await self._retry_transient(
                lambda: self._client.aio.models.generate_content(
                    model=self._model_name,
                    contents=user_prompt,
                    config=config,
                ),
                is_transient=_is_transient,
                max_retries=self.TRANSIENT_MAX_RETRIES,
                base_delay=self.TRANSIENT_BASE_DELAY_SECONDS,
                timeout=self.REQUEST_TIMEOUT_SECONDS,
                describe_error=_describe_error,
            )
        except LLMServiceError:
            # _retry_transient 的重試耗盡 / 逾時已包成 LLMServiceError（訊息經
            # _describe_error，不嵌回應 JSON），原樣上拋，不讓下方的 Exception 分支再包一層
            raise
        except errors.APIError as error:
            summary = _api_error_summary(error)
            if error.code == 429:
                # 429 是預期內的狀況（腳本會暫停後續跑），記 WARNING 免得淹沒真正的錯誤
                logger.warning("Gemini API 額度或速率限制 (model=%s): %s", self._model_name, error)
                # 訊息同時含 429 與 Quota：生成腳本分級表以任一子字串判斷暫停
                raise LLMQuotaError(f"Gemini API 額度或速率限制（Quota）: {summary}") from error
            logger.error("Gemini API 請求失敗 (model=%s): %s", self._model_name, error)
            hint = _client_error_hint(error)
            raise LLMServiceError(
                f"Gemini API 請求失敗: {summary}" + (f"（{hint}）" if hint else "")
            ) from error
        except Exception as error:  # noqa: BLE001 - 契約：任何失敗一律以 LLMServiceError 族拋出
            # 型別名與 repr 必帶：TimeoutError() 之類的例外 str 是空字串
            logger.exception("Gemini API 呼叫發生非預期錯誤 (model=%s)", self._model_name)
            raise LLMServiceError(
                f"Gemini API 呼叫發生非預期錯誤: {type(error).__name__}: {error!r}"
            ) from error

    @staticmethod
    def _refusal_or_finish_reason(response: types.GenerateContentResponse) -> str:
        """取出停止原因：prompt 被擋時回 block_reason，否則回首個候選的 finish_reason。

        Return the prompt's block_reason when the prompt itself was blocked,
        otherwise the first candidate's finish_reason (upper-case name).
        """
        feedback = response.prompt_feedback
        block_reason = _reason_name(feedback.block_reason if feedback else None)
        if block_reason and block_reason != "BLOCKED_REASON_UNSPECIFIED":
            return block_reason
        candidates = response.candidates or []
        return _reason_name(candidates[0].finish_reason) if candidates else ""

    @staticmethod
    def _reason_message(response: types.GenerateContentResponse) -> str:
        """Google 附的人讀原因（block_reason_message / finish_message），供 log 用。

        Google's human-readable block/finish message, if any.

        JAILBREAK / OTHER / LANGUAGE 等封鎖只有 reason 代碼時無從判斷原因，
        這兩個欄位是 Google 給的唯一解釋。
        """
        feedback = response.prompt_feedback
        if feedback and feedback.block_reason_message:
            return feedback.block_reason_message
        candidates = response.candidates or []
        return (candidates[0].finish_message or "") if candidates else ""

    @staticmethod
    def _raise_final(reason: str, failure: str) -> None:
        """依最後一次嘗試的失敗型態拋出對應子類，訊息一律帶 finish_reason。

        Raise the subclass matching the last attempt's failure; every message
        carries the finish/block reason.

        拒答與空內容的訊息含生成腳本分級表的跳句識別字串；壞 JSON（含截斷造成的
        半截 JSON）沿用 LLMClient 的原句，不含識別字串，走腳本的一般失敗路徑。

        Raises:
            LLMRefusalError: 安全拒答。
            LLMRetryExhaustedError: 空內容、截斷或壞 JSON。
        """
        reason_text = reason or "UNKNOWN"
        if failure == "refusal":
            # 與 LLMClient 同一句型：兩個分級表識別字串皆含，既有腳本視同跳句
            raise LLMRefusalError(
                "LLM API 在所有重試後仍回傳空內容"
                f"（安全拒答 PROHIBITED_CONTENT，finish_reason={reason_text}）。"
            )
        if failure == "empty":
            raise LLMRetryExhaustedError(
                f"LLM API 在所有重試後仍回傳空內容（finish_reason={reason_text}）。"
            )
        raise LLMRetryExhaustedError(f"LLM 輸出{failure}（finish_reason={reason_text}）")
