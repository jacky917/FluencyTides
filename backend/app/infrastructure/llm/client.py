"""
LLM 結構化輸出客戶端模組。

本模組封裝與 LLM（相容 OpenAI API 格式，例如 Gemini）的交互，
透過 Response Format (JSON Schema) 強制保證輸出 100% 符合指定格式。

重構自 old/Anki/utils/llm_client.py，保留了以下關鍵設計：
- 使用 AsyncOpenAI 客戶端實現非同步 I/O。
- 透過 Structured Outputs 功能強制 JSON Schema 約束。
- 內建重試機制（最多 3 次）與 Markdown 格式清理。
- temperature=0.0 以追求最大的格式穩定性與減少幻覺。

Phase 2 改進：
- 回傳值改為 LLMGenerateResult Pydantic 模型（含原始文字與重試統計）。
- 新增完整的 input/output 日誌記錄（符合 llm-structured-output skill §9）。
- 異常包裝為 LLMServiceError，保持 Infrastructure 層的錯誤邊界統一。

設計決策：
- temperature 設為 0.0 是因為此 LLM 的職責是「精確填充 JSON 欄位」
  而非「創意寫作」，需要最大化格式一致性與減少幻覺。
- 重試間隔使用固定 2 秒而非指數退避，是因為 LLM API 的錯誤
  通常為瞬時性問題（如速率限制），短暫等待即可恢復。

English summary:
    Structured-output LLM client module. Wraps interactions with an
    OpenAI-compatible LLM (e.g. Gemini), enforcing 100% schema-conformant
    output via response_format (JSON Schema). Key traits: AsyncOpenAI for
    async I/O, structured outputs with schema constraints, built-in retries
    (up to 3) with Markdown-fence cleanup, temperature=0.0 for maximum
    format stability. Phase 2: returns the LLMGenerateResult Pydantic model
    (raw text + retry stats), full input/output logging, and errors wrapped
    as LLMServiceError to keep the infrastructure error boundary uniform.
    Fixed 2-second retry intervals are used instead of exponential backoff
    because LLM API errors are usually transient (e.g. rate limits).

Dependencies:
    - openai: AsyncOpenAI 客戶端。AsyncOpenAI client.
    - pydantic: 資料驗證。Data validation.
"""

import asyncio
import json
import logging

from openai import AsyncOpenAI
import openai
import httpx
from openai.types.chat import ChatCompletion

from app.core.config import settings
from app.core.exceptions import LLMServiceError
from app.schemas.llm.base import LLMGenerateResult

logger = logging.getLogger(__name__)


class LLMClient:
    """封裝與 LLM（相容 OpenAI 格式）相關操作的非同步客戶端。

    Async client wrapping operations against an OpenAI-compatible LLM.

    使用 OpenAI API 的 response_format 功能強制保證結構化 JSON 輸出，
    配合 Anki 模型的 JSON Schema 定義，確保 LLM 回傳的資料能直接
    映射到 Anki 筆記的欄位中。

    Uses the OpenAI response_format feature to guarantee structured JSON
    output, so LLM responses map directly onto Anki note fields via the Anki
    model's JSON Schema.

    Attributes:
        _client: 非同步的 OpenAI API 客戶端實例。The AsyncOpenAI client
            instance.
        _model_name: LLM 的模型名稱（例如 'gemini-2.0-flash'）。The LLM model
            name, e.g. 'gemini-2.0-flash'.
    """

    # 最大重試次數，用於處理暫時性 API 錯誤
    MAX_RETRIES = 3

    # 重試間隔秒數
    RETRY_DELAY_SECONDS = 2

    def __init__(self, *, model: str | None = None) -> None:
        """根據 Settings 的設定初始化 AsyncOpenAI 客戶端。

        Initialize the AsyncOpenAI client from Settings.

        Args:
            model: 覆寫模型名；None 沿用 ``LLM_MODEL_NAME``（僅作用於此
                實例）。Instance-scoped model override.

        Raises:
            LLMServiceError: 當 LLM_API_KEY 或 LLM_BASE_URL 未設定時。Raised
                when LLM_API_KEY or LLM_BASE_URL is not configured.
        """
        if not settings.LLM_API_KEY:
            raise LLMServiceError("LLM_API_KEY 未設定，無法初始化 LLM 客戶端。")
        if not settings.LLM_BASE_URL:
            raise LLMServiceError("LLM_BASE_URL 未設定，無法初始化 LLM 客戶端。")

        self._client = AsyncOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            timeout=120.0,  # 加入 120 秒的 Timeout，防止中轉站無回應時無限卡死
        )
        self._model_name = (model or settings.LLM_MODEL_NAME or "").strip()
        if not self._model_name:
            raise LLMServiceError("模型名為空：LLM_MODEL_NAME 未設定且未提供覆寫。")

        provider = settings.LLM_PROVIDER.lower().strip() if settings.LLM_PROVIDER else ""
        provider_prefix = f"({provider})" if provider and provider not in ("google", "openai") else ""
        self._formatted_model_name = f"{provider_prefix}{self._model_name}"
        
        # Google 原生 OpenAI 相容端點嚴格遵守 OpenAI Schema，
        # 不認識 safety_settings 欄位，會直接 400 拒絕。
        # 只有第三方中轉站（如 yinli 等）才支援透過 extra_body 傳遞 safety_settings。
        self._supports_safety_settings = provider not in ("google", "openai", "")
        
        logger.info("LLMClient 初始化完成，目標模型: %s", self._formatted_model_name)

    async def generate_structured_data(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, object],
    ) -> LLMGenerateResult:
        """呼叫 LLM 並取得嚴格符合 response_schema 的 JSON 資料。

        Call the LLM and obtain JSON data strictly conforming to
        response_schema.

        利用 OpenAI API 的 response_format 功能保證輸出為合規的 JSON，
        配合重試機制與 Markdown 格式清理，確保可靠的結構化輸出。

        Uses the OpenAI response_format feature to guarantee valid JSON,
        combined with retries and Markdown-fence cleanup for reliable
        structured output.

        Phase 2 改進：回傳 LLMGenerateResult Pydantic 模型（含原始文字、
        解析資料、模型名稱、重試次數），取代舊版的裸 dict。

        Phase 2: returns the LLMGenerateResult Pydantic model (raw text,
        parsed data, model name, attempt count) instead of a bare dict.

        Args:
            system_prompt: 指定給 LLM 的系統提示，規範其扮演角色與注意事項。
                System prompt defining the LLM's role and constraints.
            user_prompt: 使用者的輸入內容，例如要被製成卡片的原文。User
                input, e.g. the source text to turn into a card.
            response_schema: JSON Schema 定義字典，用於約束 LLM 的回傳格式。
                JSON Schema dict constraining the LLM's output format.

        Returns:
            LLMGenerateResult Pydantic 模型實例。An LLMGenerateResult model
            instance.

        Raises:
            LLMServiceError: 多次重試後 LLM 仍回傳空內容、非有效 JSON、
                或 API 請求失敗時。Raised when, after all retries, the LLM
                still returns empty content, invalid JSON, or the API call
                fails.
        """
        # 記錄完整的請求日誌，便於除錯與監控（符合 llm-structured-output skill §9）
        logger.info(
            "LLM 結構化生成請求 -> model: %s, user_prompt 長度: %d 字元",
            self._formatted_model_name,
            len(user_prompt),
        )
        logger.debug(
            "LLM 請求詳情 -> system_prompt: %s, schema_keys: %s",
            system_prompt[:200],
            list(response_schema.get("properties", {}).keys())
            if isinstance(response_schema.get("properties"), dict)
            else "N/A",
        )

        # 某些模型（如 Gemini）不支援 JSON Schema 中的 $defs 與 $ref，必須展開
        resolved_schema = self._resolve_json_schema(response_schema)

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
        structured_format: dict[str, object] = {
            "type": "json_schema",
            "json_schema": {
                "name": "structured_output",
                "schema": resolved_schema,
                "strict": True,
            },
        }

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
                        # temperature=0.0 以追求最大的格式穩定性與減少幻覺，
                        # 因為此 LLM 的職責是「精確填充 JSON 欄位」而非「創意寫作」。
                        temperature=0.0,
                        # 只有支援 safety_settings 的中轉站才會附帶此參數，
                        # Google 原生 OpenAI 相容端點不支援，會直接 400 拒絕。
                        **({"extra_body": {
                            "safety_settings": [
                                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
                            ]
                        }} if self._supports_safety_settings else {}),
                    )
                )
            except (openai.OpenAIError, httpx.RequestError) as e:
                logger.error(
                    "LLM API 請求失敗 (第 %d 次): %s", attempt, str(e)
                )
                if attempt == self.MAX_RETRIES:
                    raise LLMServiceError(
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
            if not response_content and message and getattr(message, "tool_calls", None):
                response_content = message.tool_calls[0].function.arguments

            if not response_content:
                finish_reason = response.choices[0].finish_reason if response.choices else "unknown"
                logger.error(
                    "LLM API 回傳內容為空 (第 %d 次)。原因 (finish_reason): %s", attempt, finish_reason
                )
                if attempt == self.MAX_RETRIES:
                    raise LLMServiceError(
                        "LLM API 在所有重試後仍回傳空內容。"
                    )
                await asyncio.sleep(self.RETRY_DELAY_SECONDS)
                continue

            # 清理可能的 Markdown 程式碼區塊標記（部分 LLM 會在
            # response_format 模式下仍包裹 ```json ... ``` 標記）
            cleaned_content = self._strip_markdown_fences(response_content)

            try:
                parsed_data: dict[str, object] = json.loads(cleaned_content)

                # 記錄成功的結果日誌
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
                    model_name=self._formatted_model_name,
                    attempt_count=attempt,
                )

            except json.JSONDecodeError as decode_error:
                logger.error(
                    "無法將 LLM API 回傳結果解析為 JSON (第 %d 次)。原始文字: %s",
                    attempt,
                    response_content,
                )
                if attempt == self.MAX_RETRIES:
                    raise LLMServiceError(
                        f"LLM 輸出非有效 JSON 格式: {decode_error}"
                    ) from decode_error
                logger.info("準備重啟第 %d 次請求...", attempt + 1)
                await asyncio.sleep(self.RETRY_DELAY_SECONDS)

        # 理論上不會執行到這裡，但為了型別安全加上保底
        raise LLMServiceError("LLM 結構化輸出在所有重試後仍然失敗。")

    @staticmethod
    def _strip_markdown_fences(content: str) -> str:
        """清理 LLM 回傳中可能包含的 Markdown 程式碼區塊標記。

        Strip Markdown code-fence markers that the LLM response may contain.

        部分 LLM 即使在 response_format 模式下，仍可能在回傳內容前後
        加上 ```json ... ``` 標記，需要清除後才能正確解析 JSON。

        Some LLMs still wrap output in ```json ... ``` fences even in
        response_format mode; these must be removed before JSON parsing.

        Args:
            content: LLM 的原始回傳字串。The raw LLM response string.

        Returns:
            清理後的純 JSON 字串。The cleaned pure-JSON string.
        """
        cleaned = content.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]

        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]

        return cleaned.strip()

    @classmethod
    def _resolve_json_schema(cls, schema: dict[str, object], defs: dict[str, object] = None) -> dict[str, object]:
        """遞迴展開 JSON Schema 中的 $ref，並移除 $defs。

        Recursively inline $ref entries in a JSON Schema and drop $defs.

        因部分 LLM（如 Gemini 的 Structured Outputs 實作）不支援 $defs 與 $ref，
        我們必須在發送前將其攤平（Inline）。

        Some LLMs (e.g. Gemini's Structured Outputs implementation) do not
        support $defs/$ref, so the schema must be flattened before sending.

        Args:
            schema: Pydantic 產生的 JSON Schema。The Pydantic-generated JSON
                Schema.
            defs: 最上層提取出來的 $defs 字典。The top-level $defs dict.

        Returns:
            展開所有 $ref 並移除 $defs 後的 JSON Schema。The schema with all
            $ref inlined and $defs removed.
        """
        if defs is None:
            defs = schema.get("$defs", {})

        if isinstance(schema, dict):
            if "$ref" in schema:
                ref_path = str(schema["$ref"])
                ref_name = ref_path.split("/")[-1]
                resolved = cls._resolve_json_schema(defs.get(ref_name, {}), defs)
                
                # 保留其他非 $ref 屬性並合併展開後的內容
                new_schema = {k: v for k, v in schema.items() if k != "$ref"}
                new_schema.update(resolved)
                return new_schema
            else:
                return {k: cls._resolve_json_schema(v, defs) for k, v in schema.items() if k != "$defs"}
        elif isinstance(schema, list):
            return [cls._resolve_json_schema(item, defs) for item in schema]
        else:
            return schema
