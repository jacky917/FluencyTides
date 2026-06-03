"""
FluencyTides 統一異常類別階層模組。

本模組定義了專案的異常繼承體系，供全域 Exception Handler 捕獲並
回傳統一的 ErrorResponse JSON。遵循 03_Acceptance_Criteria.md §2：
不允許將原始 traceback 直接暴露給前端。

設計原則：
    - 所有業務相關異常均繼承自 FluencyTidesError 基類。
    - 每個子類別定義唯一的 error_code（機器可讀）和預設 status_code。
    - Controller 層不需要 try/except 這些異常，全域 Handler 統一處理。
    - Infrastructure 層的原生異常（如 AnkiConnectError、S3Error）
      應在 Service 層被包裝為此處定義的語意化異常。
"""


class FluencyTidesError(Exception):
    """FluencyTides 專案頂層異常基類。

    所有業務相關異常均繼承此類別，確保全域 Exception Handler
    能統一捕獲並格式化回應。

    Attributes:
        error_code: 機器可讀的錯誤代碼字串。
        status_code: 對應的 HTTP 狀態碼。
        message: 人類可讀的錯誤訊息。
    """

    error_code: str = "INTERNAL_ERROR"
    status_code: int = 500

    def __init__(self, message: str) -> None:
        """初始化 FluencyTidesError。

        Args:
            message: 人類可讀的錯誤訊息。
        """
        super().__init__(message)
        self.message = message


class DuplicateCardError(FluencyTidesError):
    """卡片重複錯誤。

    當嘗試新增的卡片在目標牌組中已存在時拋出。
    """

    error_code = "DUPLICATE_CARD"
    status_code = 409


class DeckNotFoundError(FluencyTidesError):
    """牌組不存在錯誤。

    當指定的 Anki 牌組在本地 Anki 中找不到時拋出。
    """

    error_code = "DECK_NOT_FOUND"
    status_code = 404


class ModelFileNotFoundError(FluencyTidesError):
    """模型定義檔不存在錯誤。

    當 anki_models/ 目錄下找不到對應的 JSON 定義檔時拋出。
    """

    error_code = "MODEL_FILE_NOT_FOUND"
    status_code = 404


class PromptTemplateNotFoundError(FluencyTidesError):
    """Prompt 模板不存在錯誤。

    當 Jinja2 模板目錄下找不到對應的 .j2 模板檔時拋出。
    """

    error_code = "PROMPT_TEMPLATE_NOT_FOUND"
    status_code = 404


class LLMServiceError(FluencyTidesError):
    """LLM 服務異常。

    當 LLM API 請求失敗、回傳空內容、或結構化輸出解析失敗時拋出。
    """

    error_code = "LLM_SERVICE_ERROR"
    status_code = 502


class AnkiServiceError(FluencyTidesError):
    """Anki 服務異常。

    當 AnkiConnect API 請求失敗（非重複卡片、非牌組不存在）時拋出。
    """

    error_code = "ANKI_SERVICE_ERROR"
    status_code = 502


class StorageServiceError(FluencyTidesError):
    """物件存儲服務異常。

    當 MinIO 操作失敗（上傳、下載、刪除等）時拋出。
    """

    error_code = "STORAGE_SERVICE_ERROR"
    status_code = 502


class AuthenticationError(FluencyTidesError):
    """認證失敗錯誤。

    當 API 請求未攜帶有效的 API Key 時拋出。
    """

    error_code = "AUTHENTICATION_FAILED"
    status_code = 401
