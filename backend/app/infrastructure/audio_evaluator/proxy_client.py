"""
第三方中轉 API (如引力 API) 的語音評分器實作。

Speech evaluator implementation for third-party proxy APIs (e.g. Yinli API).

針對不完全支援 OpenAI `input_audio` 與 `json_schema` 嚴格模式的中轉站，
提供相容性降級處理。

Provides compatibility fallbacks for proxy gateways that do not fully
support OpenAI `input_audio` or strict `json_schema` mode.

設計決策：
- 使用 response_format={"type": "json_object"} 替代嚴格的 JSON Schema，避免中轉解析錯誤。
- 使用 `image_url` 夾帶 `audio/ogg` Base64 的方式，繞過中轉站對 `input_audio` 的限制，
  此為目前 OneAPI/NewAPI 等開源架構最通用的多模態 Hack 做法。

Design decisions:
- Uses response_format={"type": "json_object"} instead of strict JSON Schema
  to avoid proxy parsing errors.
- Smuggles base64 audio inside an `image_url` data URI to bypass proxies'
  `input_audio` limitations — the most common multimodal hack for
  OneAPI/NewAPI-style gateways.
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
from app.core.dependencies import get_template_engine

logger = logging.getLogger(__name__)


class ProxyAudioEvaluator(BaseAudioEvaluator):
    """針對第三方中轉站特製的語音評分器。

    Speech evaluator tailored to third-party proxy gateways.

    使用 OpenAI 相容的 AsyncOpenAI SDK，但將音檔降級偽裝為 `image_url`，
    並使用 `json_object` 強制要求中轉站回傳 JSON。

    Uses the OpenAI-compatible AsyncOpenAI SDK, but disguises the audio as an
    `image_url` and forces JSON output via `json_object` mode.

    Attributes:
        _client: AsyncOpenAI 客戶端實例。The AsyncOpenAI client instance.
    """

    def __init__(self) -> None:
        """初始化 Proxy Audio Evaluator。

        Initialize the proxy audio evaluator.

        使用設定的 AUDIO_API_KEY 與 AUDIO_BASE_URL。

        Uses the configured AUDIO_API_KEY and AUDIO_BASE_URL.

        Raises:
            LLMServiceError: 當必要設定未提供時。Raised when required
                settings are missing.
        """
        if not settings.AUDIO_API_KEY:
            raise LLMServiceError(
                "AUDIO_API_KEY 未設定，無法初始化 Proxy Audio Evaluator。"
            )
        if not settings.AUDIO_BASE_URL:
            logger.warning(
                "使用 ProxyAudioEvaluator 但未設定 AUDIO_BASE_URL，將使用預設端點。"
            )

        self._client = AsyncOpenAI(
            api_key=settings.AUDIO_API_KEY,
            base_url=settings.AUDIO_BASE_URL,
        )
        logger.info(
            "Proxy Audio Evaluator 初始化完成 (支援第三方中轉)",
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
        from app.infrastructure.ffmpeg.client import FfmpegClient, FfmpegError
        
        try:
            if audio_filename.endswith(".ogg"):
                ffmpeg_client = FfmpegClient()
                audio_data = await ffmpeg_client.convert_to_mp3(audio_data)
                mime_type = "audio/mp3"
            else:
                mime_type = "audio/wav"
        except FfmpegError as e:
            logger.error("Proxy Audio Evaluator 轉檔失敗: %s", e)
            raise LLMServiceError(f"音訊格式轉換失敗，無法發送給中轉站。") from e

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

        # 針對 json_object 模式，必須在 System Prompt 明確要求 JSON，
        # 並給定結構提示，防止模型亂答。
        json_structure_instruction = (
            "\n\n請務必以 JSON 格式輸出，且必須包含以下三個欄位：\n"
            "{\n"
            '  "score": <整數型別 0-100>,\n'
            '  "feedback": "<字串，評語>+",\n'
            '  "transcript": "<字串，逐字稿>"\n'
            "}"
        )
        system_prompt += json_structure_instruction

        # 組裝 Hack 版的多模態訊息：將音檔偽裝在 image_url 的 Data URI 裡
        user_content: list[dict[str, object]] = [
            {"type": "text", "text": "請評估附帶的語音回覆："},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime_type};base64,{audio_b64}"
                },
            },
        ]

        try:
            import asyncio
            response = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=settings.AUDIO_MODEL_NAME,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.3,
                ),
                timeout=90.0
            )
        except asyncio.TimeoutError as e:
            logger.error("Proxy Audio Evaluator API 呼叫超時 (Timeout)")
            raise LLMServiceError("Proxy Audio Evaluator API 超時失敗") from e
        except (openai.OpenAIError, httpx.RequestError) as e:
            logger.error("Proxy Audio Evaluator API 呼叫失敗: %s", e)
            raise LLMServiceError(
                f"Proxy Audio Evaluator API 失敗: {e}"
            ) from e

        message = response.choices[0].message if response.choices else None
        content = message.content if message else None
        if not content:
            raise LLMServiceError("Proxy Audio Evaluator 回傳空內容。")

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
            # 使用 raw_decode 可以自動忽略結尾多餘的字元（例如 LLM 偶爾多吐出的 '}'）
            decoder = json.JSONDecoder()
            parsed, idx = decoder.raw_decode(cleaned.lstrip())
            
            # 手動驗證必填欄位
            required_keys = ["score", "feedback", "transcript"]
            for key in required_keys:
                if key not in parsed:
                    raise ValueError(f"JSON 缺少必填欄位: {key}")

            result = AudioEvaluationResult(**parsed)
            result.evaluator_label = settings.AUDIO_MODEL_NAME
            return result
        except (json.JSONDecodeError, ValueError) as e:
            logger.error("Proxy Audio Evaluator 回傳格式錯誤: %s", content)
            raise LLMServiceError(
                f"語音評分結果格式錯誤: {e}"
            ) from e
