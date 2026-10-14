"""
基礎設施層 (Infrastructure Layer) 的異常定義。

Exception definitions for the infrastructure layer.
"""

from .base import FluencyTidesError


class InfrastructureBaseError(FluencyTidesError):
    """基礎設施層的基礎錯誤。

    Base error for the infrastructure layer.
    """
    error_code = "INFRASTRUCTURE_ERROR"
    status_code = 502


class LLMServiceError(InfrastructureBaseError):
    """LLM 服務異常。

    LLM service error.

    當 LLM API 請求失敗、回傳空內容、或結構化輸出解析失敗時拋出。

    Raised when an LLM API request fails, returns empty content, or
    structured-output parsing fails.
    """
    error_code = "LLM_SERVICE_ERROR"


class LLMRetryExhaustedError(LLMServiceError):
    """LLM 重試耗盡仍無合規輸出（空內容 / 壞 JSON / schema 不符）。

    Raised when every retry still yields no usable structured output.

    訊息須含 ``"LLM API 在所有重試後仍回傳空內容"``：生成腳本的錯誤分級表
    以此子字串走「記錄失敗並跳過此句」路徑；``error_code`` 是同一語義的
    機器可讀版本，供腳本日後改讀 ``ErrorResponse.error_code``。
    """
    error_code = "LLM_RETRY_EXHAUSTED"


class LLMQuotaError(LLMServiceError):
    """LLM 額度耗盡或速率限制（429 / usage limit）。

    Raised on quota exhaustion or rate limiting.

    訊息須含 ``"Quota"``：生成腳本據此走「暫停 60 秒」路徑。
    """
    error_code = "LLM_QUOTA"


class LLMRefusalError(LLMServiceError):
    """LLM 因安全政策拒答（Gemini SAFETY / PROHIBITED_CONTENT、Anthropic refusal）。

    Raised when the model refuses on safety grounds.

    訊息須同時含 ``"PROHIBITED_CONTENT"`` 與
    ``"LLM API 在所有重試後仍回傳空內容"``，讓既有分級表視同跳句。
    """
    error_code = "LLM_REFUSAL"


class AnkiServiceError(InfrastructureBaseError):
    """Anki 服務異常。

    Anki service error.

    當 AnkiConnect API 請求失敗（非重複卡片、非牌組不存在）時拋出。

    Raised when an AnkiConnect API request fails for reasons other than
    duplicate cards or a missing deck.
    """
    error_code = "ANKI_SERVICE_ERROR"


class AnkiFieldCorruptedError(InfrastructureBaseError):
    """Anki JSON 欄位內容損毀異常。

    Anki JSON field corruption error.

    當卡片欄位中儲存的 JSON 陣列無法解析（或非陣列型別）時拋出，
    以阻止後續寫入操作靜默清空既有資料。

    Raised when the JSON array stored in a note field cannot be parsed
    (or is not a list), preventing subsequent write operations from
    silently wiping existing data.
    """
    error_code = "ANKI_FIELD_CORRUPTED"


class STTServiceError(InfrastructureBaseError):
    """自架 STT (Whisper/Speaches) 服務異常。

    Self-hosted STT (Whisper/Speaches) service error.

    當 STT 服務連線失敗、逾時、模型未安裝或轉錄請求失敗時拋出。

    Raised when the STT service is unreachable, times out, the model is
    not installed, or the transcription request fails.
    """
    error_code = "STT_SERVICE_ERROR"


class StorageServiceError(InfrastructureBaseError):
    """物件存儲服務異常。

    Object storage service error.

    當 MinIO 操作失敗（上傳、下載、刪除等）時拋出。

    Raised when a MinIO operation (upload, download, delete, etc.) fails.
    """
    error_code = "STORAGE_SERVICE_ERROR"
