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

Dependencies:
    - openai: AsyncOpenAI 客戶端
    - pydantic: 資料驗證
"""

import asyncio
import json
import logging

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion

from app.core.config import settings
from app.core.exceptions import LLMServiceError
from app.schemas.llm import LLMGenerateResult

logger = logging.getLogger(__name__)


class LLMClient:
    """封裝與 LLM（相容 OpenAI 格式）相關操作的非同步客戶端。

    使用 OpenAI API 的 response_format 功能強制保證結構化 JSON 輸出，
    配合 Anki 模型的 JSON Schema 定義，確保 LLM 回傳的資料能直接
    映射到 Anki 筆記的欄位中。

    Attributes:
        _client: 非同步的 OpenAI API 客戶端實例。
        _model_name: LLM 的模型名稱（例如 'gemini-2.0-flash'）。
    """

    # 最大重試次數，用於處理暫時性 API 錯誤
    MAX_RETRIES = 3

    # 重試間隔秒數
    RETRY_DELAY_SECONDS = 2

    def __init__(self) -> None:
        """根據 Settings 的設定初始化 AsyncOpenAI 客戶端。

        Raises:
            LLMServiceError: 當 LLM_API_KEY 或 LLM_BASE_URL 未設定時。
        """
        if not settings.LLM_API_KEY:
            raise LLMServiceError("LLM_API_KEY 未設定，無法初始化 LLM 客戶端。")
        if not settings.LLM_BASE_URL:
            raise LLMServiceError("LLM_BASE_URL 未設定，無法初始化 LLM 客戶端。")

        self._client = AsyncOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
        )
        self._model_name = settings.LLM_MODEL_NAME
        logger.info("LLMClient 初始化完成，目標模型: %s", self._model_name)

    async def generate_structured_data(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, object],
    ) -> LLMGenerateResult:
        """呼叫 LLM 並取得嚴格符合 response_schema 的 JSON 資料。

        利用 OpenAI API 的 response_format 功能保證輸出為合規的 JSON，
        配合重試機制與 Markdown 格式清理，確保可靠的結構化輸出。

        Phase 2 改進：回傳 LLMGenerateResult Pydantic 模型（含原始文字、
        解析資料、模型名稱、重試次數），取代舊版的裸 dict。

        Args:
            system_prompt: 指定給 LLM 的系統提示，規範其扮演角色與注意事項。
            user_prompt: 使用者的輸入內容，例如要被製成卡片的原文。
            response_schema: JSON Schema 定義字典，用於約束 LLM 的回傳格式。

        Returns:
            LLMGenerateResult Pydantic 模型實例。

        Raises:
            LLMServiceError: 多次重試後 LLM 仍回傳空內容、非有效 JSON、
                             或 API 請求失敗時。
        """
        # 記錄完整的請求日誌，便於除錯與監控（符合 llm-structured-output skill §9）
        logger.info(
            "LLM 結構化生成請求 -> model: %s, user_prompt 長度: %d 字元",
            self._model_name,
            len(user_prompt),
        )
        logger.debug(
            "LLM 請求詳情 -> system_prompt: %s, schema_keys: %s",
            system_prompt[:200],
            list(response_schema.get("properties", {}).keys())
            if isinstance(response_schema.get("properties"), dict)
            else "N/A",
        )

        # 建構 OpenAI Structured Outputs 所需的 response_format 格式
        structured_format: dict[str, object] = {
            "type": "json_schema",
            "json_schema": {
                "name": "structured_output",
                "schema": response_schema,
                "strict": True,
            },
        }

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                response: ChatCompletion = (
                    await self._client.chat.completions.create(
                        model=self._model_name,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        response_format=structured_format,
                        # temperature=0.0 以追求最大的格式穩定性與減少幻覺，
                        # 因為此 LLM 的職責是「精確填充 JSON 欄位」而非「創意寫作」。
                        temperature=0.0,
                    )
                )
            except Exception as e:
                logger.error(
                    "LLM API 請求失敗 (第 %d 次): %s", attempt, str(e)
                )
                if attempt == self.MAX_RETRIES:
                    raise LLMServiceError(
                        f"LLM API 請求在 {self.MAX_RETRIES} 次重試後仍失敗: {e}"
                    ) from e
                await asyncio.sleep(self.RETRY_DELAY_SECONDS)
                continue

            response_content = response.choices[0].message.content

            if not response_content:
                logger.error(
                    "LLM API 回傳內容為空 (第 %d 次)。", attempt
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
                    model_name=self._model_name,
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

        部分 LLM 即使在 response_format 模式下，仍可能在回傳內容前後
        加上 ```json ... ``` 標記，需要清除後才能正確解析 JSON。

        Args:
            content: LLM 的原始回傳字串。

        Returns:
            清理後的純 JSON 字串。
        """
        cleaned = content.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]

        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]

        return cleaned.strip()
