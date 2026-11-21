"""Self-hosted Whisper (Speaches / faster-whisper) transcription client.

自架 Whisper (Speaches / faster-whisper) 轉錄客戶端。

Calls the LAN-hosted Speaches service through the OpenAI-compatible
/v1/audio/transcriptions endpoint to turn Telegram voice messages
(.ogg) into plain-text transcripts.

透過 OpenAI 相容的 /v1/audio/transcriptions 端點呼叫區網內的
Speaches 服務，將 Telegram 語音 (.ogg) 轉為純文字逐字稿。

Design decisions:
- The language parameter must be passed explicitly: testing showed that
  auto-detection misidentifies languages (Japanese detected as Korean at
  0.29 confidence), so this class owns the locale → ISO 639-1 mapping
  (§2.4).
- Server, key and model are passed in by the caller (the ``stt``
  capability route, see ``audio_evaluator/factory.py``); when a setting
  changes, the factory builds a new client.
- No automatic model download: a 3-5 minute download on the production
  path would blow the Telegram handler timeout; a missing model surfaces
  as a server error, wrapped in STTServiceError that suggests a manual
  install.

設計決策：
- 語言參數必須明確傳入：實測顯示自動偵測會誤判（日文被判為韓文、
  信心度 0.29），故由本類負責 locale → ISO 639-1 映射（§2.4）。
- 伺服器、金鑰與模型由呼叫端傳入（能力路由的 ``stt``，見
  ``audio_evaluator/factory.py``）；設定變更時由工廠建立新的客戶端。
- 不做模型自動下載：生產路徑上 3-5 分鐘的下載會撞爆 TG handler
  超時；模型缺失由伺服器回錯、包成 STTServiceError 提示手動安裝。
"""

import asyncio
import logging

import httpx
import openai
from openai import AsyncOpenAI

from app.core.exceptions import STTServiceError

logger = logging.getLogger(__name__)

# STT 呼叫的絕對超時秒數（區網服務較快，取 60s；各 LLM evaluator 慣例為 90s）
# Absolute timeout for STT calls (LAN service is fast; evaluators use 90s).
_STT_TIMEOUT_SECONDS = 60.0


class WhisperClient:
    """An async transcription client for the self-hosted Speaches (faster-whisper) service.

    自架 Speaches (faster-whisper) 的非同步轉錄客戶端。

    Attributes:
        _client: the OpenAI-compatible async client instance
            OpenAI 相容的非同步客戶端實例
        _server_url: the Speaches server URL
            Speaches 服務網址
        _model_name: the Whisper model name
            Whisper 模型名
    """

    def __init__(self, *, server_url: str | None, model: str, api_key: str = "no-key") -> None:
        """Initialize the WhisperClient.

        初始化 WhisperClient。

        Args:
            server_url: the Speaches server URL (``STT_SERVER_URL``)
                Speaches 服務網址（``STT_SERVER_URL``）
            model: the Whisper model name (the ``stt`` capability)
                Whisper 模型名（能力路由的 ``stt``）
            api_key: the service key (``STT_API_KEY``; self-hosted services usually do not check it);
                an empty value is replaced with ``"no-key"``
                服務的金鑰（``STT_API_KEY``，自架服務通常不檢查）；空值會換成 ``"no-key"``

        Raises:
            STTServiceError: if the server URL or the model is not set
                若服務網址或模型未設定
        """
        if not server_url:
            raise STTServiceError(
                "STT_SERVER_URL 未設定，無法初始化 WhisperClient。"
            )
        if not model:
            raise STTServiceError("Whisper 模型未設定（STT_MODEL_NAME），無法初始化 WhisperClient。")
        self._client = AsyncOpenAI(api_key=api_key or "no-key", base_url=server_url)
        self._server_url = server_url
        self._model_name = model
        logger.info("WhisperClient 初始化完成: %s（%s）", server_url, model)

    @property
    def model_name(self) -> str:
        """The Whisper model name.

        Whisper 模型名。
        """
        return self._model_name

    @staticmethod
    def to_whisper_language(target_language: str | None) -> str | None:
        """Map a BCP-47 locale to Whisper's ISO 639-1 language code.

        將 BCP-47 locale 映射為 Whisper 的 ISO 639-1 語言代碼。

        The locale is lowercased and the part before the first ``-`` is used.

        locale 轉成小寫後取第一個 ``-`` 之前的部分。

        Args:
            target_language: the card's target-language locale (for example ``'ja-JP'``);
                ``'other'``, an empty string or ``None`` means unspecified
                卡片的目標語言 locale（如 ``'ja-JP'``）；``'other'``、空字串或 ``None`` 代表不指定

        Returns:
            the ISO 639-1 code (for example ``'ja'``); ``None`` when it cannot be decided (Whisper
            then auto-detects)
            ISO 639-1 代碼（如 ``'ja'``）；無法判定時回傳 ``None``（Whisper 將自動偵測）
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
        """Transcribe an audio file into a plain-text transcript.

        將音檔轉錄為純文字逐字稿。

        The call times out after 60 seconds.

        呼叫 60 秒逾時。

        Args:
            audio_data: the raw audio bytes (.ogg and so on)
                音檔原始二進位資料（.ogg 等）
            audio_filename: the audio file name (lets the server infer the format)
                音檔檔名（供服務端判斷格式）
            target_language: the target-language locale, mapped to ISO 639-1 before sending; falls
                back to auto-detection for ``None``/``'other'``
                目標語言 locale，將映射為 ISO 639-1 傳入；``None``/``'other'`` 時回退自動偵測

        Returns:
            the transcribed text; an empty string when the service returns no text
            轉錄出的逐字稿文字；服務沒有回傳文字時為空字串

        Raises:
            STTServiceError: if the connection fails, the call times out, or the service returns an
                error
                若連線失敗、逾時或服務回傳錯誤
        """
        language = self.to_whisper_language(target_language)
        model_name = self._model_name

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
                f"（POST {self._server_url}/models/{model_name}）。"
            ) from e

        text = getattr(transcript, "text", "") or ""
        logger.info(
            "STT 轉錄完成 (lang=%s, model=%s, %d chars)",
            language or "auto", model_name, len(text),
        )
        return text
