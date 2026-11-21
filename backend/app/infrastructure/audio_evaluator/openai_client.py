"""OpenAI-compatible speech evaluator implementation.

OpenAI 相容層語音評分器實作。

Uses the AsyncOpenAI SDK to base64-encode the audio and send it to the LLM for scoring through
the input_audio feature of the Chat Completions API.

使用 AsyncOpenAI SDK 將音檔轉為 Base64 並透過
Chat Completions API 的 input_audio 功能傳送給 LLM 進行評分。

Applicable scenarios:
- OpenAI models that accept audio input, such as GPT-4o-audio-preview.
- Other providers that offer an OpenAI-compatible endpoint.

適用場景：
- 使用 OpenAI GPT-4o-audio-preview 等支援音訊輸入的模型。
- 使用其他提供 OpenAI 相容端點的服務商。

Design decisions:
- Enforces JSON Schema output through response_format, consistent with the
  generate_structured_data logic of the existing LLMClient.

設計決策：
- 使用 response_format 強制 JSON Schema 輸出，
  與現有 LLMClient 的 generate_structured_data 邏輯一致。
"""

import base64
import json
import logging

from openai import AsyncOpenAI
import openai
import httpx

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
    """A speech evaluator using an OpenAI-compatible API.

    使用 OpenAI 相容 API 的語音評分器。

    Encodes the audio as base64 and sends it to the LLM through the multimodal input of Chat
    Completions for speech analysis and scoring.

    將音檔編碼為 Base64 後，透過 Chat Completions 的
    多模態輸入功能發送給 LLM 進行語音分析與評分。

    Attributes:
        _client: the AsyncOpenAI client instance
            AsyncOpenAI 客戶端實例
        _model_name: the model name in use
            使用的模型名稱
    """

    def __init__(
        self, *, api_key: str, model: str, base_url: str | None = None, api_key_setting: str = "AUDIO_API_KEY"
    ) -> None:
        """Initialize the OpenAI-compatible speech evaluator.

        初始化 OpenAI 相容層語音評分器。

        Args:
            api_key: the API key
                API 金鑰
            model: the model that listens to the audio (the ``audio_llm`` capability)
                聽音檔的模型（能力路由的 ``audio_llm``）
            base_url: the OpenAI-compatible endpoint URL; ``None`` means the official OpenAI API
                OpenAI 相容端點網址；``None`` 為 OpenAI 官方
            api_key_setting: the setting name of the key, used in error messages
                金鑰的設定名（錯誤訊息用）

        Raises:
            LLMServiceError: if the key or the model is not set
                若金鑰或模型未設定
        """
        if not api_key:
            raise LLMServiceError(
                f"{api_key_setting} 未設定，無法初始化 OpenAI Audio Evaluator。"
            )
        if not model:
            raise LLMServiceError("聽音檔的模型未設定（AUDIO_MODEL_NAME），無法初始化 OpenAI Audio Evaluator。")

        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model_name = model
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
        reference_familiarity: list[int | None] | None = None,
    ) -> AudioEvaluationResult:
        """Evaluate the user's speech and produce a structured scoring result.

        評估使用者語音，產出結構化評分結果。

        The audio format is sent as ``ogg`` for ``.ogg`` file names and as ``wav`` otherwise. The
        API call times out after 90 seconds and is not retried.

        檔名為 ``.ogg`` 時音訊格式送 ``ogg``，其餘送 ``wav``。API 呼叫 90 秒逾時，不重試。

        Args:
            audio_data: the raw audio bytes (.ogg format)
                音檔的原始二進位資料（.ogg 格式）
            audio_filename: the audio file name
                音檔檔名
            prompt_text: the card Prompt
                卡片 Prompt
            context_text: the card-front Context
                卡片正面的 Context（文脈）
            reference_answers: the reference answers
                參考範本列表
            target_language: the target language
                目標語言
            template_name: the Jinja2 template for the evaluation prompt
                評分提示詞 j2 樣板
            reference_familiarity: the familiarity of each reference answer (as in the base class)
                每句參考答案的熟識度（同 base）

        Returns:
            the structured evaluation result, with ``evaluator_label`` set to the model name
            結構化評分結果，``evaluator_label`` 設為模型名稱

        Raises:
            LLMServiceError: if the API call fails or the output format is invalid
                若 API 呼叫失敗或輸出格式不符
        """
        audio_b64 = base64.b64encode(audio_data).decode("utf-8")
        
        engine = get_template_engine()
        system_prompt = engine.render(
            template_name,
            prompt_text=prompt_text,
            context_text=context_text,
            reference_answers=reference_answers,
            reference_familiarity=reference_familiarity or [],
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
                    model=self._model_name,
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
            result.evaluator_label = self._model_name
            return result
        except (json.JSONDecodeError, ValueError) as e:
            logger.error("OpenAI Audio Evaluator 回傳格式錯誤: %s", content)
            raise LLMServiceError(
                f"語音評分結果格式錯誤: {e}"
            ) from e
