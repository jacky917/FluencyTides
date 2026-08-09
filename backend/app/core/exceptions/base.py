"""
FluencyTides 統一異常類別基底模組。

Base module for the unified FluencyTides exception hierarchy.
"""

class FluencyTidesError(Exception):
    """FluencyTides 專案頂層異常基類。

    Top-level base exception for the FluencyTides project.

    所有業務相關異常均繼承此類別，確保全域 Exception Handler
    能統一捕獲並格式化回應。

    All business-related exceptions inherit from this class so the global
    exception handler can catch them uniformly and format the response.

    Attributes:
        error_code: 機器可讀的錯誤代碼字串。Machine-readable error code string.
        status_code: 對應的 HTTP 狀態碼。Corresponding HTTP status code.
        message: 人類可讀的錯誤訊息。Human-readable error message.
    """

    error_code: str = "INTERNAL_ERROR"
    status_code: int = 500

    def __init__(self, message: str) -> None:
        """初始化 FluencyTidesError。

        Initialize a FluencyTidesError.

        Args:
            message: 人類可讀的錯誤訊息。Human-readable error message.
        """
        super().__init__(message)
        self.message = message
