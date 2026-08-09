"""
服務層 (Service Layer) 與領域層 (Domain Layer) 的異常定義。

Exception definitions for the service and domain layers.
"""

from .base import FluencyTidesError


class ServiceBaseError(FluencyTidesError):
    """服務層的基礎錯誤。

    Base error for the service layer.
    """
    error_code = "SERVICE_ERROR"
    status_code = 400


class DuplicateCardError(ServiceBaseError):
    """卡片重複錯誤。

    Duplicate card error.

    當嘗試新增的卡片在目標牌組中已存在時拋出。

    Raised when the card being added already exists in the target deck.
    """
    error_code = "DUPLICATE_CARD"
    status_code = 409


class DeckNotFoundError(ServiceBaseError):
    """牌組不存在錯誤。

    Deck-not-found error.

    當指定的 Anki 牌組在本地 Anki 中找不到時拋出。

    Raised when the specified Anki deck cannot be found in the local Anki.
    """
    error_code = "DECK_NOT_FOUND"
    status_code = 404


class ModelFileNotFoundError(ServiceBaseError):
    """模型定義檔不存在錯誤。

    Model definition file not found.

    當 anki_models/ 目錄下找不到對應的 JSON 定義檔時拋出。

    Raised when the corresponding JSON definition file cannot be found under
    the anki_models/ directory.
    """
    error_code = "MODEL_FILE_NOT_FOUND"
    status_code = 404


class PromptTemplateNotFoundError(ServiceBaseError):
    """Prompt 模板不存在錯誤。

    Prompt template not found.

    當 Jinja2 模板目錄下找不到對應的 .j2 模板檔時拋出。

    Raised when the corresponding .j2 template file cannot be found in the
    Jinja2 template directory.
    """
    error_code = "PROMPT_TEMPLATE_NOT_FOUND"
    status_code = 404


class AuthenticationError(ServiceBaseError):
    """認證失敗錯誤。

    Authentication failure error.

    當 API 請求未攜帶有效的 API Key 時拋出。

    Raised when an API request does not carry a valid API key.
    """
    error_code = "AUTHENTICATION_FAILED"
    status_code = 401


class LLMGenerationError(ServiceBaseError):
    """LLM 生成內容業務邏輯錯誤。

    Business-logic error in LLM-generated content.

    當 LLM 順利回傳了結果，但內容不符合業務邏輯（例如幻覺、邏輯矛盾等）時拋出。
    這有別於基礎設施層的 LLMServiceError (502 網路或解析錯誤)。

    Raised when the LLM returned successfully but the content violates
    business logic (e.g. hallucination, contradictions). Distinct from the
    infrastructure-level LLMServiceError (502 network/parsing errors).
    """
    error_code = "LLM_GENERATION_FAILED"
    status_code = 422


class ClozePositioningError(LLMGenerationError):
    """挖空定位失敗錯誤。

    Cloze-blank positioning failure.

    專門處理 LLM 回傳的挖空字串無法在原文中精確定位的情況。
    這通常是因為 LLM 擅自更改了漢字寫法，或是挖空範圍完全錯亂。

    Handles the case where cloze substrings returned by the LLM cannot be
    located exactly in the original sentence, usually because the LLM altered
    kanji spellings or produced completely misplaced blanks.
    """
    error_code = "CLOZE_POSITIONING_FAILED"
    status_code = 422
