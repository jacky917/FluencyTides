"""Google native SDK (google-genai) speech evaluator implementation.

Google 原生 SDK (google-genai) 語音評分器實作。

Uploads audio directly to Gemini for analysis via the official Google Generative AI SDK.
Compared with the OpenAI-compatible layer, the native SDK has the most complete support for
Gemini's multimodal features, including the native File Upload API and audio-format handling.

使用 Google 官方 Generative AI SDK 直接上傳音檔給 Gemini 進行分析。
相比 OpenAI 相容層，原生 SDK 對 Gemini 的多模態功能支援最完整，
包括原生的 File Upload API 和音訊格式處理。

Design decisions:
- Uses google-genai (the new SDK) instead of google-generativeai (the legacy one), because the new
  SDK offers better async support and type hints.
- Sends inline_data directly instead of uploading through the File API, avoiding the extra
  complexity of file management. Voice files are usually small (<5MB), so inline_data is enough.

設計決策：
- 使用 google-genai（新版 SDK）而非 google-generativeai（舊版），
  因為新版 SDK 提供更好的非同步支援和型別提示。
- 直接使用 inline_data 而非 File API 上傳，避免檔案管理的額外複雜度。
  語音檔案通常很小（<5MB），inline_data 完全足夠。
"""

import json
import logging

from google import genai
from google.genai import types, errors

from app.core.exceptions import LLMServiceError
from app.infrastructure.audio_evaluator.base import BaseAudioEvaluator
from app.schemas.llm.speaking import AudioEvaluationResult

logger = logging.getLogger(__name__)


from app.core.dependencies import get_template_engine


class GeminiNativeAudioEvaluator(BaseAudioEvaluator):
    """A speech evaluator using the Google native SDK.

    使用 Google 原生 SDK 的語音評分器。

    Sends audio directly to Gemini through the google-genai SDK, using its native multimodal
    inference for speech analysis and scoring.

    透過 google-genai SDK 直接傳送音檔給 Gemini，
    利用原生的多模態推論能力進行語音分析與評分。

    Attributes:
        _client: the google-genai Client instance
            google-genai Client 實例
        _model_name: the Gemini model name in use
            使用的 Gemini 模型名稱
    """

    def __init__(self, *, api_key: str, model: str, api_key_setting: str = "AUDIO_API_KEY") -> None:
        """Initialize the Gemini native speech evaluator.

        初始化 Gemini 原生語音評分器。

        Args:
            api_key: the Google API key
                Google API 金鑰
            model: the model that listens to the audio (the ``audio_llm`` capability)
                聽音檔的模型（能力路由的 ``audio_llm``）
            api_key_setting: the setting name of the key, used in error messages
                金鑰的設定名（錯誤訊息用）

        Raises:
            LLMServiceError: if the key or the model is not set
                若金鑰或模型未設定
        """
        if not api_key:
            raise LLMServiceError(
                f"{api_key_setting} 未設定，"
                "無法初始化 Gemini Native Audio Evaluator。"
            )
        if not model:
            raise LLMServiceError("聽音檔的模型未設定（AUDIO_MODEL_NAME），無法初始化 Gemini Native Audio Evaluator。")

        self._client = genai.Client(api_key=api_key)
        self._model_name = model
        logger.info(
            "Gemini Native Audio Evaluator 初始化完成",
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

        The MIME type comes from the file name: ``.wav`` and ``.mp3`` are recognized, anything else
        is sent as ``audio/ogg``. Transient API errors (503, 429, overload) and timeouts are retried
        up to 3 attempts in total with exponential backoff; each attempt times out after 90 seconds.

        MIME 類型依檔名決定：可辨識 ``.wav`` 與 ``.mp3``，其餘一律以 ``audio/ogg`` 送出。暫時性的 API
        錯誤（503、429、負載過高）與逾時會以指數退避重試，總共最多 3 次；每次 90 秒逾時。

        Args:
            audio_data: the raw audio bytes (.ogg format)
                音檔原始二進位資料（.ogg 格式）
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
        engine = get_template_engine()
        evaluation_prompt = engine.render(
            template_name,
            prompt_text=prompt_text,
            context_text=context_text,
            reference_answers=reference_answers,
            reference_familiarity=reference_familiarity or [],
            target_language=target_language,
            disable_markdown=True,
        )

        # 根據副檔名決定 MIME 類型
        mime_type = "audio/ogg"
        if audio_filename.endswith(".wav"):
            mime_type = "audio/wav"
        elif audio_filename.endswith(".mp3"):
            mime_type = "audio/mpeg"

        model_name = self._model_name

        # 組裝多模態內容：Prompt 文字 + 音檔 inline data
        contents: list[types.Part | str] = [
            evaluation_prompt,
            types.Part.from_bytes(
                data=audio_data,
                mime_type=mime_type,
            ),
        ]

        max_retries = 3
        base_delay = 1.0  # 初始等待 1 秒

        for attempt in range(1, max_retries + 1):
            try:
                import asyncio
                # google-genai 的 generate_content 是同步的，
                # 但 aio 子模組提供非同步版本。加入 asyncio.wait_for 以防底層 httpx 卡死
                response = await asyncio.wait_for(
                    self._client.aio.models.generate_content(
                        model=model_name,
                        contents=contents,
                        config=types.GenerateContentConfig(
                            temperature=0.3,
                            response_mime_type="application/json",
                            response_schema=AudioEvaluationResult,
                        ),
                    ),
                    timeout=90.0  # 90 秒絕對超時
                )
                break  # 成功則跳出迴圈
            except asyncio.TimeoutError as e:
                logger.error("Gemini API 回應超時 (Timeout)。")
                if attempt < max_retries:
                    delay = base_delay * (2 ** (attempt - 1))
                    await asyncio.sleep(delay)
                    continue
                raise LLMServiceError("Gemini Native Audio Evaluator API 超時失敗") from e
            except errors.APIError as e:
                # 檢查是否為 503 或 429 暫時性錯誤
                is_transient = "503" in str(e) or "429" in str(e) or "UNAVAILABLE" in str(e) or "high demand" in str(e)
                
                if is_transient and attempt < max_retries:
                    delay = base_delay * (2 ** (attempt - 1))  # 1s, 2s...
                    logger.warning(f"Gemini API 暫時性錯誤 ({e})，{delay} 秒後進行第 {attempt + 1} 次重試...")
                    import asyncio
                    await asyncio.sleep(delay)
                    continue
                
                logger.error("Gemini Native Audio Evaluator API 呼叫失敗: %s", e)
                raise LLMServiceError(
                    f"Gemini Native Audio Evaluator API 失敗: {e}"
                ) from e

        content = response.text
        if not content:
            raise LLMServiceError(
                "Gemini Native Audio Evaluator 回傳空內容。"
            )

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
            result = AudioEvaluationResult(**parsed)
            result.evaluator_label = model_name
            return result
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(
                "Gemini Native Audio Evaluator 回傳格式錯誤: %s", content
            )
            raise LLMServiceError(
                f"語音評分結果格式錯誤: {e}"
            ) from e
