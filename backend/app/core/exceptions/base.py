"""Base module for the unified FluencyTides exception hierarchy.

FluencyTides 統一異常類別基底模組。
"""

class FluencyTidesError(Exception):
    """The top-level base exception for the FluencyTides project.

    FluencyTides 專案頂層異常基類。

    All business-related exceptions inherit from this class so the global
    exception handler can catch them uniformly and format the response.

    所有業務相關異常均繼承此類別，確保全域 Exception Handler
    能統一捕獲並格式化回應。

    Attributes:
        error_code: the machine-readable error code string
            機器可讀的錯誤代碼字串
        status_code: the corresponding HTTP status code
            對應的 HTTP 狀態碼
        message: the human-readable error message
            人類可讀的錯誤訊息
    """

    error_code: str = "INTERNAL_ERROR"
    status_code: int = 500

    def __init__(self, message: str) -> None:
        """Create the error with a human-readable message.

        以人類可讀的錯誤訊息建立此錯誤。

        Args:
            message: the human-readable error message
                人類可讀的錯誤訊息
        """
        super().__init__(message)
        self.message = message
