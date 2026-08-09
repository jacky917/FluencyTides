"""
OpenAI 相容層語音評分器實作。

OpenAI-compatible speech evaluator implementation.

使用 AsyncOpenAI SDK 將音檔轉為 Base64 並透過
Chat Completions API 的 input_audio 功能傳送給 LLM 進行評分。

Uses the AsyncOpenAI SDK to base64-encode audio and send it to the LLM for
scoring via the Chat Completions input_audio feature.

適用場景：
- 使用 OpenAI GPT-4o-audio-preview 等支援音訊輸入的模型。
- 使用其他提供 OpenAI 相容端點的服務商。

Applicable scenarios:
- OpenAI models with audio input support, e.g. GPT-4o-audio-preview.
- Any provider exposing an OpenAI-compatible endpoint.

設計決策：
- 使用 response_format 強制 JSON Schema 輸出，
  與現有 LLMClient 的 generate_structured_data 邏輯一致。

Design decision:
- Enforces JSON Schema output via response_format, consistent with the
  existing LLMClient.generate_structured_data logic.
"""

import base64
import json
import logging

from openai import AsyncOpenAI
import openai
import httpx

from app.core.config import settings
from app.core.exceptions import LLMServiceError
from app.infrastructure.audio_evaluator.base import BaseAudioEvaluator
from app.schemas.llm.speaking import AudioEvaluationResult

logger = logging.getLogger(__name__)

# LLM 評分用的 JSON Schema，強制約束輸出格式
_EVALUATION_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "description": "總分 0-100"},
        "feedback": {"type": "string", "description": "AI 評語"},
        "transcript": {"type": "string", "description": "語音逐字稿"},
    },
    "required": ["score", "feedback", "transcript"],
    "additionalProperties": False,
}


from app.core.dependencies import get_template_engine


class OpenAIAudioEvaluator(BaseAudioEvaluator):
    """使用 OpenAI 相容 API 的語音評分器。

    Speech evaluator using an OpenAI-compatible API.

    將音檔編碼為 Base64 後，透過 Chat Completions 的
    多模態輸入功能發送給 LLM 進行語音分析與評分。

    Encodes the audio as base64 and sends it to the LLM via Chat Completions
    multimodal input for speech analysis and scoring.

    Attributes:
        _client: AsyncOpenAI 客戶端實例。The AsyncOpenAI client instance.
        _model_name: 使用的模型名稱。The model name in use.
    """

    def __init__(self) -> None:
        """初始化 OpenAI 相容層語音評分器。

        Initialize the OpenAI-compatible speech evaluator.

        使用與 LLMClient 相同的 API Key 和 Base URL。

        Uses the same API key and base URL as LLMClient.

        Raises:
            LLMServiceError: 當必要設定未提供時。Raised when required
                settings are missing.
        """
        if not settings.AUDIO_API_KEY:
            raise LLMServiceError(
                "AUDIO_API_KEY 未設定，無法初始化 OpenAI Audio Evaluator。"
            )

        self._client = AsyncOpenAI(
            api_key=settings.AUDIO_API_KEY,
            base_url=settings.AUDIO_BASE_URL,
        )
        logger.info(
            "OpenAI Audio Evaluator 初始化完成",
        )

    async def evaluate_audio(
        self,
        audio_data: bytes,
        audio_filename: str,
        prompt_text: str,
        context_text: str,
        reference_answers: list[str],
        target_language: str | None = None,
        template_name: str = "prompts/audio_evaluator.j2",
    ) -> AudioEvaluationResult:
        """評估使用者語音，產出結構化評分結果。

        Evaluate the user's speech and produce a structured scoring result.

        Args:
            audio_data: 音檔的原始二進位資料（.ogg 格式）。Raw audio bytes
                (.ogg format).
            audio_filename: 音檔檔名。Audio file name.
            prompt_text: 卡片 Prompt。The card prompt.
            context_text: 卡片正面的 Context（文脈）。The card-front context.
            reference_answers: 參考範本列表。Reference answer list.
            target_language: 目標語言。Target language.
            template_name: 評分提示詞 j2 樣板。Jinja2 template for the
                evaluation prompt.

        Returns:
            AudioEvaluationResult 結構化評分結果。The structured evaluation
            result.

        Raises:
            LLMServiceError: API 呼叫失敗或輸出格式不符時。Raised when the
                API call fails or the output format is invalid.
        """
        audio_b64 = base64.b64encode(audio_data).decode("utf-8")
        
        engine = get_template_engine()
        system_prompt = engine.render(
            template_name,
            prompt_text=prompt_text,
            context_text=context_text,
            reference_answers=reference_answers,
            target_language=target_language,
            disable_markdown=False,
        )

        # 組裝含音訊的多模態訊息
        user_content: list[dict[str, object]] = [
            {"type": "text", "text": "請評估以下語音回覆："},
            {
                "type": "input_audio",
                "input_audio": {
                    "data": audio_b64,
                    "format": "ogg" if audio_filename.endswith(".ogg") else "wav",
                },
            },
        ]

        structured_format: dict[str, object] = {
            "type": "json_schema",
            "json_schema": {
                "name": "audio_evaluation",
                "schema": _EVALUATION_SCHEMA,
                "strict": True,
            },
        }

        try:
            import asyncio
            response = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=settings.AUDIO_MODEL_NAME,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    response_format=structured_format,
                    temperature=0.3,
                ),
                timeout=90.0
            )
        except asyncio.TimeoutError as e:
            logger.error("OpenAI Audio Evaluator API 呼叫超時 (Timeout)")
            raise LLMServiceError("OpenAI Audio Evaluator API 超時失敗") from e
        except (openai.OpenAIError, httpx.RequestError) as e:
            logger.error("OpenAI Audio Evaluator API 呼叫失敗: %s", e)
            raise LLMServiceError(
                f"OpenAI Audio Evaluator API 失敗: {e}"
            ) from e

        message = response.choices[0].message if response.choices else None
        content = message.content if message else None
        if not content:
            raise LLMServiceError("OpenAI Audio Evaluator 回傳空內容。")

        # 清理可能的 Markdown 格式
        cleaned = content.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        try:
            # 使用 raw_decode 可以自動忽略結尾多餘的字元
            decoder = json.JSONDecoder()
            parsed, idx = decoder.raw_decode(cleaned.lstrip())
            result = AudioEvaluationResult(**parsed)
            result.evaluator_label = settings.AUDIO_MODEL_NAME
            return result
        except (json.JSONDecodeError, ValueError) as e:
            logger.error("OpenAI Audio Evaluator 回傳格式錯誤: %s", content)
            raise LLMServiceError(
                f"語音評分結果格式錯誤: {e}"
            ) from e
