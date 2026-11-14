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


class DuplicateFoundError(ServiceBaseError):
    """生成草稿前發現 Anki 已有同一張卡，要使用者決定覆蓋、允許重複或取消（server_drafts 計畫書 D10.2）。

    An existing card was found before generating a draft; the user decides.

    Attributes:
        duplicate: 舊卡的摘要（``note_id`` 與給網頁顯示的內容）；全域 handler 放進回應的 ``duplicate``。
            The old card, put into the response's ``duplicate``.
    """
    error_code = "DUPLICATE_FOUND"
    status_code = 409

    def __init__(self, message: str, duplicate: dict[str, object]) -> None:
        """初始化錯誤。Initialize the error.

        Args:
            message: 人類可讀的錯誤訊息。The message.
            duplicate: 舊卡的摘要。The old card.
        """
        super().__init__(message)
        self.duplicate = duplicate


class DeckNotFoundError(ServiceBaseError):
    """牌組不存在錯誤。

    Deck-not-found error.

    當指定的 Anki 牌組在本地 Anki 中找不到時拋出。

    Raised when the specified Anki deck cannot be found in the local Anki.
    """
    error_code = "DECK_NOT_FOUND"
    status_code = 404


class DeckNameInvalidError(ServiceBaseError):
    """牌組名稱不合格（deck_create 計畫書 §4）。

    Invalid deck name.

    新增牌組時名稱是空的、``::`` 分出的某一層是空的，或全名太長時拋出。

    Raised when a new deck's name is empty, has an empty ``::`` level, or is
    too long.
    """
    error_code = "DECK_NAME_INVALID"
    status_code = 400


class DeckExistsError(ServiceBaseError):
    """牌組已存在（deck_create 計畫書 §3）。

    Deck already exists.

    新增牌組時 Anki 已有同名的牌組：不當成成功，讓使用者知道名稱打成既有的牌組了。

    Raised when a deck with the same name already exists; not treated as a
    success so that a mistyped name does not go unnoticed.
    """
    error_code = "DECK_EXISTS"
    status_code = 409


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


class InvalidRelationTypeError(ServiceBaseError):
    """使用者給的關係類型名稱不合法（空白、用了系統命名空間、或含不允許的字元）。

    The user-supplied relation type name is invalid: blank, uses a system
    namespace (``core.``), or contains characters outside the allowed set.
    """
    error_code = "INVALID_RELATION_TYPE"
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


# ============================================================================
# Handler 能力與筆記操作（handlers 計畫書 D6）
# Handler capabilities and note operations (handlers plan D6)
# ============================================================================


class HandlerOperationUnsupportedError(ServiceBaseError):
    """這個 handler 沒有開放這項操作或動作；`message` 為能力宣告的原因，前端直接顯示。

    The handler does not offer this operation or action; the message is the
    reason from its capability declaration.
    """
    error_code = "HANDLER_OPERATION_UNSUPPORTED"
    status_code = 400


class NoteNotFoundError(ServiceBaseError):
    """Anki 中找不到這張筆記（可能已刪除或尚未同步）。

    The note does not exist in Anki.
    """
    error_code = "NOTE_NOT_FOUND"
    status_code = 404


class NoteTypeNotCreatableError(ServiceBaseError):
    """建立時指定的筆記類型不是這個 handler 能建立的類型。

    The requested note type is not the one this handler creates.
    """
    error_code = "NOTE_TYPE_NOT_CREATABLE"
    status_code = 422


class NoteNotOwnedByHandlerError(ServiceBaseError):
    """筆記的類型不歸網址上的這個 handler 負責。

    The note's type is not handled by the handler in the URL.
    """
    error_code = "NOTE_NOT_OWNED_BY_HANDLER"
    status_code = 400


class FieldNotEditableError(ServiceBaseError):
    """這個欄位被 handler 宣告為 JSON 或媒體欄位，不能直接以文字修改或在建卡時填寫。

    The field is declared as a JSON or media field and cannot be written as
    plain text.
    """
    error_code = "FIELD_NOT_EDITABLE"
    status_code = 400


class JsonItemChangedError(ServiceBaseError):
    """要動的 JSON 清單項目已經變了（別處新增、刪除過，或卡片畫面還沒同步），找不到同一筆。

    The JSON list item to change is no longer there (changed elsewhere or the
    card view is stale).
    """
    error_code = "JSON_ITEM_CHANGED"
    status_code = 409


class JsonFieldConflictError(ServiceBaseError):
    """網頁編輯 JSON 欄位期間，別處（TG、腳本、另一個分頁）改過這一欄，不寫入。

    The JSON field changed elsewhere while it was being edited.
    """
    error_code = "JSON_FIELD_CONFLICT"
    status_code = 409


class InvalidNoteQueryError(ServiceBaseError):
    """依 handler 查筆記（含圖譜）的參數不適用：筆記類型不歸它、名稱欄位不存在、關聯圖譜指定了名稱欄位。

    A query parameter does not apply to this handler's notes (graph included).
    """
    error_code = "INVALID_NOTE_QUERY"
    status_code = 422


class InvalidNoteFieldsError(ServiceBaseError):
    """要寫入的欄位不是 ``{欄位名: 值}`` 的形式。

    The fields to write are not a mapping of field names to values.
    """
    error_code = "INVALID_NOTE_FIELDS"
    status_code = 422


class TagInvalidError(ServiceBaseError):
    """建卡時加的標籤不合格：含空白、是系統標籤、太長或太多。

    A tag given at creation is invalid: whitespace, a system tag, too long or too many.
    """
    error_code = "TAG_INVALID"
    status_code = 400


class DraftStateError(ServiceBaseError):
    """建卡草稿的狀態不允許這個操作；回應附上目前的草稿（server_drafts 計畫書 D6）。只作為基底，實際拋出子類別。

    The create session's state does not allow this; the response carries the current draft.

    Attributes:
        draft: 目前的草稿（``Draft.to_dict()``），沒有草稿時為 None；全域 handler 放進回應的 ``draft``。
            The current draft, put into the response's ``draft``.
    """
    error_code = "DRAFT_STATE_ERROR"
    status_code = 409

    def __init__(self, message: str, draft: dict[str, object] | None = None) -> None:
        """初始化錯誤。Initialize the error.

        Args:
            message: 人類可讀的錯誤訊息。The message.
            draft: 目前的草稿。The current draft.
        """
        super().__init__(message)
        self.draft = draft


class CreateSessionBusyError(DraftStateError):
    """已經有一份建卡工作進行中（全站同一時間只有一份），或草稿是另一種卡片的，不接受新的生成或建卡（D2）。

    Another create session is in progress; nothing new is accepted.
    """
    error_code = "CREATE_SESSION_BUSY"


class DraftBusyError(DraftStateError):
    """草稿不是 ``ready``（生成中、建立中或生成失敗），不能修改或建立；生成中、建立中也不能廢棄（D3、D8）。

    The draft is not ready for this operation.
    """
    error_code = "DRAFT_BUSY"


class DraftVersionConflictError(DraftStateError):
    """草稿在其他地方改過了：請求帶的版本不是最新的（D5）。

    The draft changed elsewhere; the given version is stale.
    """
    error_code = "DRAFT_VERSION_CONFLICT"


class DraftNotFailedError(DraftStateError):
    """只有生成失敗的草稿能重試（D3）。

    Only a failed draft can be retried.
    """
    error_code = "DRAFT_NOT_FAILED"


class DraftNotFoundError(ServiceBaseError):
    """草稿已不在：可能已建立、在其他地方廢棄，或後端重啟過（D7、R1）。

    The draft is gone: created, discarded elsewhere, or the backend restarted.
    """
    error_code = "DRAFT_NOT_FOUND"
    status_code = 404


class DraftStateTooLargeError(ServiceBaseError):
    """網頁的編輯狀態超過上限（D5）。

    The web editing state is over the size limit.
    """
    error_code = "DRAFT_STATE_TOO_LARGE"
    status_code = 413
