"""Exception definitions for the infrastructure layer.

基礎設施層 (Infrastructure Layer) 的異常定義。
"""

from .base import FluencyTidesError


class InfrastructureBaseError(FluencyTidesError):
    """The base error for the infrastructure layer.

    基礎設施層的基礎錯誤。
    """
    error_code = "INFRASTRUCTURE_ERROR"
    status_code = 502


class LLMServiceError(InfrastructureBaseError):
    """An LLM service error.

    LLM 服務異常。

    Raised when an LLM API request fails, returns empty content, or
    structured-output parsing fails.

    當 LLM API 請求失敗、回傳空內容、或結構化輸出解析失敗時拋出。
    """
    error_code = "LLM_SERVICE_ERROR"


class LLMRetryExhaustedError(LLMServiceError):
    """The error raised when every LLM retry still yields no valid output (empty, bad JSON, schema mismatch).

    LLM 重試耗盡仍無合規輸出（空內容 / 壞 JSON / schema 不符）。

    The message must contain ``"LLM API 在所有重試後仍回傳空內容"``: the generation scripts' error
    classification table uses this substring to take the "record the failure and skip this sentence"
    path. ``error_code`` is the machine-readable version of the same meaning, so the scripts can
    later read ``ErrorResponse.error_code`` instead.

    訊息須含 ``"LLM API 在所有重試後仍回傳空內容"``：生成腳本的錯誤分級表
    以此子字串走「記錄失敗並跳過此句」路徑；``error_code`` 是同一語義的
    機器可讀版本，供腳本日後改讀 ``ErrorResponse.error_code``。
    """
    error_code = "LLM_RETRY_EXHAUSTED"


class LLMQuotaError(LLMServiceError):
    """The error raised on LLM quota exhaustion or rate limiting (429 / usage limit).

    LLM 額度耗盡或速率限制（429 / usage limit）。

    The message must contain ``"Quota"``: the generation scripts use it to take the "pause for 60
    seconds" path.

    訊息須含 ``"Quota"``：生成腳本據此走「暫停 60 秒」路徑。
    """
    error_code = "LLM_QUOTA"


class LLMRefusalError(LLMServiceError):
    """The error raised when the LLM refuses on safety grounds.

    LLM 因安全政策拒答。

    Covers Gemini SAFETY / PROHIBITED_CONTENT and Anthropic refusals. The message must contain both
    ``"PROHIBITED_CONTENT"`` and ``"LLM API 在所有重試後仍回傳空內容"``, so the existing
    classification table treats it as a skipped sentence.

    涵蓋 Gemini SAFETY / PROHIBITED_CONTENT、Anthropic refusal。訊息須同時含 ``"PROHIBITED_CONTENT"`` 與
    ``"LLM API 在所有重試後仍回傳空內容"``，讓既有分級表視同跳句。
    """
    error_code = "LLM_REFUSAL"


class LLMServiceUnavailableError(LLMServiceError):
    """The error raised when transient failures persist through every attempt.

    暫時性失敗在所有嘗試後仍未恢復。

    For example the service is unavailable or the CLI exits midway. Kept separate from content
    problems (``LLMRetryExhaustedError``): the message **deliberately does not contain** the
    skip-sentence marker string, because an infrastructure failure should not be recorded by the
    generation scripts as "blocked by safety review". The scripts currently still record it as a
    generic 5xx failure; once they read ``error_code`` they can pause instead (plan §8).

    例如服務端不可用、CLI 中途結束。與內容問題（``LLMRetryExhaustedError``）分開：訊息**刻意不含**
    跳句識別字串，基礎設施故障不該被生成腳本記成「安全審查攔截」。腳本目前仍會把它當一般
    5xx 記失敗；改讀 ``error_code`` 後可改為暫停（計畫 §8）。
    """
    error_code = "LLM_SERVICE_UNAVAILABLE"


class AnkiServiceError(InfrastructureBaseError):
    """An Anki service error.

    Anki 服務異常。

    Raised when an AnkiConnect API request fails for reasons other than
    duplicate cards or a missing deck.

    當 AnkiConnect API 請求失敗（非重複卡片、非牌組不存在）時拋出。
    """
    error_code = "ANKI_SERVICE_ERROR"


class AnkiFieldCorruptedError(InfrastructureBaseError):
    """An Anki JSON field corruption error.

    Anki JSON 欄位內容損毀異常。

    Raised when the JSON array stored in a note field cannot be parsed
    (or is not a list), preventing subsequent write operations from
    silently wiping existing data.

    當卡片欄位中儲存的 JSON 陣列無法解析（或非陣列型別）時拋出，
    以阻止後續寫入操作靜默清空既有資料。
    """
    error_code = "ANKI_FIELD_CORRUPTED"


class STTServiceError(InfrastructureBaseError):
    """A self-hosted STT (Whisper/Speaches) service error.

    自架 STT (Whisper/Speaches) 服務異常。

    Raised when the STT service is unreachable, times out, the model is
    not installed, or the transcription request fails.

    當 STT 服務連線失敗、逾時、模型未安裝或轉錄請求失敗時拋出。
    """
    error_code = "STT_SERVICE_ERROR"


class AudioEvaluatorUnavailableError(InfrastructureBaseError):
    """The error raised when no audio evaluator can be built from the current capability route settings.

    依目前的能力路由設定建不出錄音評分器。

    For example a missing key or server, a missing package, or an invalid strategy. Kept separate
    from failures during evaluation (``LLMServiceError`` / ``STTServiceError``): this is a settings
    problem, fixed by changing the settings (Telegram ``/setconfig`` or the web page's tool settings).

    例如金鑰或伺服器未設定、套件未安裝、策略無效。與評分途中的失敗（``LLMServiceError`` /
    ``STTServiceError``）分開：這是設定問題，改設定（TG ``/setconfig`` 或網頁的工具設定）就能修。
    """
    error_code = "AUDIO_EVALUATOR_UNAVAILABLE"
    status_code = 503


class StorageServiceError(InfrastructureBaseError):
    """An object storage service error.

    物件存儲服務異常。

    Raised when a MinIO operation (upload, download, delete, and so on)
    fails.

    當 MinIO 操作失敗（上傳、下載、刪除等）時拋出。
    """
    error_code = "STORAGE_SERVICE_ERROR"
