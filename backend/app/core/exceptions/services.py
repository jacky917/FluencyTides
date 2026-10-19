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


class RouteError(ServiceBaseError):
    """能力路由設定（TG /setconfig、/api/v1/capability-routes）的錯誤基類。

    Base error for capability route settings management.
    """
    error_code = "ROUTE_ERROR"
    status_code = 400


class RouteNotFoundError(RouteError):
    """用途鍵不存在或不可設定。

    查詢與刪除時指「既未在程式中宣告、DB 中也沒有」；寫入時指「未在程式中宣告」——
    即使 DB 有該鍵的孤兒列也一樣（孤兒鍵只能刪除）。

    The route key does not exist (lookup / delete: neither declared nor
    stored) or cannot be set (write: not declared, even if an orphan row
    exists).
    """
    error_code = "ROUTE_NOT_FOUND"
    status_code = 404


class InvalidRouteError(RouteError):
    """要寫入的設定不合格：用途鍵沒有宣告這個能力、不在 .env 白名單，或試建客戶端失敗（呼叫端可修正）。

    The capability is not declared for the key, the choice is outside the
    whitelist, or the trial client build failed.
    """
    error_code = "INVALID_ROUTE"
    status_code = 422


class RouteStoreUnavailableError(RouteError):
    """capability_routes 資料表讀寫時資料庫異常。

    兩種情況，訊息不同：讀不到（無法判斷設定是否存在，與「不存在」區分），或寫入／刪除時
    出錯（結果未知，應先重新查看）。DB 恢復後重試即可——寫入是 upsert、刪除依鍵，重做
    一次結果相同。

    The capability_routes table could not be read (existence cannot be decided) or
    a write / delete failed with an unknown outcome. Retrying after the
    database recovers is safe: both operations are idempotent.
    """
    error_code = "ROUTE_STORE_UNAVAILABLE"
    status_code = 503


class CardNotFoundError(ServiceBaseError):
    """Anki 中找不到指定的卡片（可能已刪除或尚未同步）。

    The requested card does not exist in Anki.
    """
    error_code = "CARD_NOT_FOUND"
    status_code = 404


class InvalidMediaFilenameError(ServiceBaseError):
    """媒體檔名不合格：只接受 Anki 媒體資料夾中的單一檔名，不接受路徑。

    The media filename is not a bare file name (paths are rejected).
    """
    error_code = "INVALID_MEDIA_FILENAME"
    status_code = 400


class MediaFileNotFoundError(ServiceBaseError):
    """Anki 媒體資料夾中沒有這個檔案。

    The file does not exist in Anki's media folder.
    """
    error_code = "MEDIA_FILE_NOT_FOUND"
    status_code = 404


class InvalidAnkiSearchError(ServiceBaseError):
    """Anki 拒絕使用者輸入的搜尋語句（多半是語法錯誤）。

    Anki rejected the user's search query, usually a syntax error.
    """
    error_code = "INVALID_ANKI_SEARCH"
    status_code = 400


class RelationSyncRefusedError(ServiceBaseError):
    """同步清理孤兒關聯時，Anki 的回應看起來不完整，為避免誤刪而拒絕清理。

    Raised when syncing relations with Anki is refused to avoid mass deletion:
    Anki returned no notes, or the sweep would delete too large a share of the
    relations without an explicit ``force``.
    """
    error_code = "RELATION_SYNC_REFUSED"
    status_code = 409


class RelationSyncEmptyAnkiError(RelationSyncRefusedError):
    """Anki 回傳 0 張筆記：一律拒絕，``force`` 也無效。

    Anki returned no notes; refused even with ``force``.
    """
    error_code = "RELATION_SYNC_EMPTY_ANKI"


class RelationSyncNeedsForceError(RelationSyncRefusedError):
    """要刪的關聯超過一半：清單可能不完整，確認後帶 ``force`` 才執行。

    More than half of the relations would be deleted; retry with ``force``
    after confirming Anki is fully loaded.

    Attributes:
        to_delete: 將刪除的關聯數。Relations that would be deleted.
        considered: 考慮範圍內的關聯數。Relations considered.
    """
    error_code = "RELATION_SYNC_NEEDS_FORCE"

    def __init__(self, message: str, *, to_delete: int, considered: int) -> None:
        super().__init__(message)
        self.to_delete = to_delete
        self.considered = considered
