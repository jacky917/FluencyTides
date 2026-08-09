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


class AnkiServiceError(InfrastructureBaseError):
    """Anki 服務異常。

    Anki service error.

    當 AnkiConnect API 請求失敗（非重複卡片、非牌組不存在）時拋出。

    Raised when an AnkiConnect API request fails for reasons other than
    duplicate cards or a missing deck.
    """
    error_code = "ANKI_SERVICE_ERROR"


class StorageServiceError(InfrastructureBaseError):
    """物件存儲服務異常。

    Object storage service error.

    當 MinIO 操作失敗（上傳、下載、刪除等）時拋出。

    Raised when a MinIO operation (upload, download, delete, etc.) fails.
    """
    error_code = "STORAGE_SERVICE_ERROR"
