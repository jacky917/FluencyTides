"""Speech evaluator implementation for third-party proxy APIs (for example Yinli API).

第三方中轉 API (如引力 API) 的語音評分器實作。

Provides compatibility fallbacks for proxy gateways that do not fully support OpenAI
`input_audio` or the strict `json_schema` mode.

針對不完全支援 OpenAI `input_audio` 與 `json_schema` 嚴格模式的中轉站，
提供相容性降級處理。

Design decisions:
- Uses response_format={"type": "json_object"} instead of the strict JSON Schema, to avoid proxy
  parsing errors.
- Carries the base64 audio inside an `image_url` data URI to get around the proxies' limits on
  `input_audio`; this is currently the most common multimodal hack for open-source gateways such
  as OneAPI/NewAPI. ``.ogg`` recordings are first converted to MP3 with FFmpeg and sent as
  `audio/mp3`; other files are sent as `audio/wav`.

設計決策：
- 使用 response_format={"type": "json_object"} 替代嚴格的 JSON Schema，避免中轉解析錯誤。
- 使用 `image_url` 夾帶音訊 Base64 的方式，繞過中轉站對 `input_audio` 的限制，
  此為目前 OneAPI/NewAPI 等開源架構最通用的多模態 Hack 做法。``.ogg`` 錄音先以 FFmpeg 轉成 MP3、
  以 `audio/mp3` 送出；其他檔案以 `audio/wav` 送出。
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
from app.core.dependencies import get_template_engine

logger = logging.getLogger(__name__)


class ProxyAudioEvaluator(BaseAudioEvaluator):
    """A speech evaluator tailored to third-party proxy gateways.

    針對第三方中轉站特製的語音評分器。

    Uses the OpenAI-compatible AsyncOpenAI SDK, but downgrades the audio by disguising it as an
    `image_url`, and uses `json_object` to force the proxy to return JSON.

    使用 OpenAI 相容的 AsyncOpenAI SDK，但將音檔降級偽裝為 `image_url`，
    並使用 `json_object` 強制要求中轉站回傳 JSON。

    Attributes:
        _client: the AsyncOpenAI client instance
            AsyncOpenAI 客戶端實例
    """

    def __init__(
        self, *, api_key: str, model: str, base_url: str | None = None, api_key_setting: str = "AUDIO_API_KEY"
    ) -> None:
        """Initialize the proxy audio evaluator.

        初始化 Proxy Audio Evaluator。

        Args:
            api_key: the API key
                API 金鑰
            model: the model that listens to the audio (the ``audio_llm`` capability)
                聽音檔的模型（能力路由的 ``audio_llm``）
            base_url: the proxy URL; when ``None`` a warning is logged and the default endpoint is used
                中轉站網址；為 ``None`` 時記錄警告並使用預設端點
            api_key_setting: the setting name of the key, used in error messages
                金鑰的設定名（錯誤訊息用）

        Raises:
            LLMServiceError: if the key or the model is not set
                若金鑰或模型未設定
        """
        if not api_key:
            raise LLMServiceError(
                f"{api_key_setting} 未設定，無法初始化 Proxy Audio Evaluator。"
            )
        if not model:
            raise LLMServiceError("聽音檔的模型未設定（AUDIO_MODEL_NAME），無法初始化 Proxy Audio Evaluator。")
        if not base_url:
            logger.warning(
                "使用 ProxyAudioEvaluator 但未設定中轉站網址，將使用預設端點。"
            )

        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model_name = model
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
        reference_familiarity: list[int | None] | None = None,
    ) -> AudioEvaluationResult:
        """Evaluate the user's speech and produce a structured scoring result.

        評估使用者語音，產出結構化評分結果。

        ``.ogg`` audio is converted to MP3 with FFmpeg before sending. The API call times out after
        90 seconds and is not retried.

        ``.ogg`` 音檔送出前先以 FFmpeg 轉成 MP3。API 呼叫 90 秒逾時，不重試。

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
            LLMServiceError: if the audio conversion fails, the API call fails, or the output format
                is invalid (including a missing required field)
                若音訊轉檔失敗、API 呼叫失敗或輸出格式不符（含缺少必填欄位）
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
            reference_familiarity=reference_familiarity or [],
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
                    model=self._model_name,
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
            result.evaluator_label = self._model_name
            return result
        except (json.JSONDecodeError, ValueError) as e:
            logger.error("Proxy Audio Evaluator 回傳格式錯誤: %s", content)
            raise LLMServiceError(
                f"語音評分結果格式錯誤: {e}"
            ) from e
