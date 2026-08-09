"""
自架 Whisper (Speaches / faster-whisper) 轉錄客戶端。

Self-hosted Whisper (Speaches / faster-whisper) transcription client.

透過 OpenAI 相容的 /v1/audio/transcriptions 端點呼叫區網內的
Speaches 服務，將 Telegram 語音 (.ogg) 轉為純文字逐字稿。

Calls the LAN-hosted Speaches service through the OpenAI-compatible
/v1/audio/transcriptions endpoint to turn Telegram voice messages
(.ogg) into plain-text transcripts.

設計決策：
- 語言參數必須明確傳入：實測顯示自動偵測會誤判（日文被判為韓文、
  信心度 0.29），故由本類負責 locale → ISO 639-1 映射（§2.4）。
- 模型名於「每次呼叫時」讀取 settings，以支援 /setconfig 熱切換
  （比照 gemini_client 先例，§2.3 解法 2）。
- 不做模型自動下載：生產路徑上 3-5 分鐘的下載會撞爆 TG handler
  超時；模型缺失由伺服器回錯、包成 STTServiceError 提示手動安裝。

Design decisions:
- The language parameter must be passed explicitly: testing showed that
  auto-detection misidentifies languages (Japanese detected as Korean at
  0.29 confidence), so this class owns the locale → ISO 639-1 mapping.
- The model name is read from settings on every call so /setconfig hot
  swaps take effect (mirroring the gemini_client precedent).
- No automatic model download: a 3-5 minute download on the production
  path would blow the Telegram handler timeout; a missing model surfaces
  as a server error wrapped in STTServiceError suggesting manual install.
"""

import asyncio
import logging

import httpx
import openai
from openai import AsyncOpenAI

from app.core.config import settings
from app.core.exceptions import STTServiceError

logger = logging.getLogger(__name__)

# STT 呼叫的絕對超時秒數（區網服務較快，取 60s；各 LLM evaluator 慣例為 90s）
# Absolute timeout for STT calls (LAN service is fast; evaluators use 90s).
_STT_TIMEOUT_SECONDS = 60.0


class WhisperClient:
    """自架 Speaches (faster-whisper) 的非同步轉錄客戶端。

    Async transcription client for the self-hosted Speaches
    (faster-whisper) service.

    Attributes:
        _client: OpenAI 相容的非同步客戶端實例。The OpenAI-compatible
            async client instance.
    """

    def __init__(self) -> None:
        """初始化 WhisperClient。

        Initialize the WhisperClient.

        Raises:
            STTServiceError: STT_SERVER_URL 未設定時拋出。Raised when
                STT_SERVER_URL is not configured.
        """
        if not settings.STT_SERVER_URL:
            raise STTServiceError(
                "STT_SERVER_URL 未設定，無法初始化 WhisperClient。"
            )
        self._client = AsyncOpenAI(
            api_key=settings.STT_API_KEY,
            base_url=settings.STT_SERVER_URL,
        )
        logger.info("WhisperClient 初始化完成: %s", settings.STT_SERVER_URL)

    @staticmethod
    def to_whisper_language(target_language: str | None) -> str | None:
        """將 BCP-47 locale 映射為 Whisper 的 ISO 639-1 語言代碼。

        Map a BCP-47 locale to Whisper's ISO 639-1 language code.

        Args:
            target_language: 卡片的目標語言 locale（如 'ja-JP'）；
                'other'、空字串或 None 代表不指定。The card's target
                language locale (e.g. 'ja-JP'); 'other', empty string,
                or None means unspecified.

        Returns:
            ISO 639-1 代碼（如 'ja'）；無法判定時回傳 None（Whisper
            將自動偵測）。The ISO 639-1 code (e.g. 'ja'), or None to
            let Whisper auto-detect.
        """
        if not target_language:
            return None
        lang = target_language.strip().lower()
        if not lang or lang == "other":
            return None
        return lang.split("-")[0]

    async def transcribe(
        self,
        audio_data: bytes,
        audio_filename: str,
        target_language: str | None = None,
    ) -> str:
        """將音檔轉錄為純文字逐字稿。

        Transcribe an audio file into a plain-text transcript.

        Args:
            audio_data: 音檔原始二進位資料（.ogg 等）。Raw audio bytes
                (.ogg etc.).
            audio_filename: 音檔檔名（供服務端判斷格式）。Audio filename
                (lets the server infer the format).
            target_language: 目標語言 locale，將映射為 ISO 639-1 傳入；
                None/'other' 時回退自動偵測。Target-language locale,
                mapped to ISO 639-1; falls back to auto-detect for
                None/'other'.

        Returns:
            轉錄出的逐字稿文字。The transcribed text.

        Raises:
            STTServiceError: 連線失敗、逾時或服務回傳錯誤時拋出。
                Raised on connection failure, timeout, or server error.
        """
        language = self.to_whisper_language(target_language)
        # 模型名於呼叫時讀取，支援 /setconfig 熱切換
        # Read the model name per call to honor /setconfig hot swaps.
        model_name = settings.STT_MODEL_NAME

        kwargs: dict[str, object] = {
            "model": model_name,
            "file": (audio_filename, audio_data),
            "response_format": "json",
        }
        if language:
            kwargs["language"] = language

        try:
            transcript = await asyncio.wait_for(
                self._client.audio.transcriptions.create(**kwargs),  # type: ignore[arg-type]
                timeout=_STT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as e:
            logger.error("STT 轉錄超時 (>%ss)", _STT_TIMEOUT_SECONDS)
            raise STTServiceError(
                f"STT 服務逾時（>{_STT_TIMEOUT_SECONDS:.0f} 秒），"
                "請確認 Speaches 服務狀態。"
            ) from e
        except (openai.OpenAIError, httpx.RequestError) as e:
            logger.error("STT 轉錄失敗: %s", e)
            raise STTServiceError(
                f"STT 服務呼叫失敗: {e}。"
                f"請確認服務已啟動且模型 '{model_name}' 已安裝"
                f"（POST {settings.STT_SERVER_URL}/models/{model_name}）。"
            ) from e

        text = getattr(transcript, "text", "") or ""
        logger.info(
            "STT 轉錄完成 (lang=%s, model=%s, %d chars)",
            language or "auto", model_name, len(text),
        )
        return text
