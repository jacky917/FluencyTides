"""Exception definitions for the service and domain layers.

服務層 (Service Layer) 與領域層 (Domain Layer) 的異常定義。
"""

from .base import FluencyTidesError


class ServiceBaseError(FluencyTidesError):
    """The base error for the service layer.

    服務層的基礎錯誤。
    """
    error_code = "SERVICE_ERROR"
    status_code = 400


class DuplicateCardError(ServiceBaseError):
    """A duplicate card error.

    卡片重複錯誤。

    Raised when the card being added already exists in the target deck.

    當嘗試新增的卡片在目標牌組中已存在時拋出。
    """
    error_code = "DUPLICATE_CARD"
    status_code = 409


class DuplicateFoundError(ServiceBaseError):
    """The error raised when Anki already has the same card before a draft is generated.

    生成草稿前發現 Anki 已有同一張卡。

    The user decides whether to overwrite, allow the duplicate, or cancel (server_drafts plan D10.2).

    要使用者決定覆蓋、允許重複或取消（server_drafts 計畫書 D10.2）。

    Attributes:
        duplicate: a summary of the old card (``note_id`` and the content shown on the web page); the
            global handler puts it into the response's ``duplicate``
            舊卡的摘要（``note_id`` 與給網頁顯示的內容）；全域 handler 放進回應的 ``duplicate``
    """
    error_code = "DUPLICATE_FOUND"
    status_code = 409

    def __init__(self, message: str, duplicate: dict[str, object]) -> None:
        """Create the error with the old card's summary.

        以舊卡的摘要建立此錯誤。

        Args:
            message: the human-readable error message
                人類可讀的錯誤訊息
            duplicate: a summary of the old card
                舊卡的摘要
        """
        super().__init__(message)
        self.duplicate = duplicate


class DeckNotFoundError(ServiceBaseError):
    """A deck-not-found error.

    牌組不存在錯誤。

    Raised when the specified Anki deck cannot be found in the local Anki.

    當指定的 Anki 牌組在本地 Anki 中找不到時拋出。
    """
    error_code = "DECK_NOT_FOUND"
    status_code = 404


class DeckNameInvalidError(ServiceBaseError):
    """An invalid deck name (deck_create plan §4).

    牌組名稱不合格（deck_create 計畫書 §4）。

    Raised when a new deck's name is empty, has an empty ``::`` level, or is
    too long.

    新增牌組時名稱是空的、``::`` 分出的某一層是空的，或全名太長時拋出。
    """
    error_code = "DECK_NAME_INVALID"
    status_code = 400


class DeckExistsError(ServiceBaseError):
    """The deck already exists (deck_create plan §3).

    牌組已存在（deck_create 計畫書 §3）。

    Raised when Anki already has a deck with the same name; not treated as a
    success, so the user learns the name matched an existing deck.

    新增牌組時 Anki 已有同名的牌組：不當成成功，讓使用者知道名稱打成既有的牌組了。
    """
    error_code = "DECK_EXISTS"
    status_code = 409


class ModelFileNotFoundError(ServiceBaseError):
    """A model definition file not found error.

    模型定義檔不存在錯誤。

    Raised when the corresponding JSON definition file cannot be found under
    the anki_models/ directory.

    當 anki_models/ 目錄下找不到對應的 JSON 定義檔時拋出。
    """
    error_code = "MODEL_FILE_NOT_FOUND"
    status_code = 404


class PromptTemplateNotFoundError(ServiceBaseError):
    """A prompt template not found error.

    Prompt 模板不存在錯誤。

    Raised when the corresponding .j2 template file cannot be found in the
    Jinja2 template directory.

    當 Jinja2 模板目錄下找不到對應的 .j2 模板檔時拋出。
    """
    error_code = "PROMPT_TEMPLATE_NOT_FOUND"
    status_code = 404


class AuthenticationError(ServiceBaseError):
    """An authentication failure error.

    認證失敗錯誤。

    Raised when an API request does not carry a valid API key.

    當 API 請求未攜帶有效的 API Key 時拋出。
    """
    error_code = "AUTHENTICATION_FAILED"
    status_code = 401


class LLMGenerationError(ServiceBaseError):
    """A business-logic error in LLM-generated content.

    LLM 生成內容業務邏輯錯誤。

    Raised when the LLM returned successfully but the content violates
    business logic (for example hallucination or contradictions). Distinct
    from the infrastructure-level LLMServiceError (502 network or parsing
    errors).

    當 LLM 順利回傳了結果，但內容不符合業務邏輯（例如幻覺、邏輯矛盾等）時拋出。
    這有別於基礎設施層的 LLMServiceError (502 網路或解析錯誤)。
    """
    error_code = "LLM_GENERATION_FAILED"
    status_code = 422


class ClozePositioningError(LLMGenerationError):
    """A cloze positioning failure.

    挖空定位失敗錯誤。

    Handles the case where cloze substrings returned by the LLM cannot be
    located exactly in the original sentence, usually because the LLM altered
    kanji spellings or produced completely misplaced blanks.

    專門處理 LLM 回傳的挖空字串無法在原文中精確定位的情況。
    這通常是因為 LLM 擅自更改了漢字寫法，或是挖空範圍完全錯亂。
    """
    error_code = "CLOZE_POSITIONING_FAILED"
    status_code = 422


class RouteError(ServiceBaseError):
    """The base error for capability route settings (Telegram /setconfig, /api/v1/capability-routes).

    能力路由設定（TG /setconfig、/api/v1/capability-routes）的錯誤基類。
    """
    error_code = "ROUTE_ERROR"
    status_code = 400


class RouteNotFoundError(RouteError):
    """The route key does not exist or cannot be set.

    用途鍵不存在或不可設定。

    For lookup and delete it means "neither declared in code nor in the DB"; for writes it means
    "not declared in code", even if the DB has an orphan row for the key (orphan keys can only be
    deleted).

    查詢與刪除時指「既未在程式中宣告、DB 中也沒有」；寫入時指「未在程式中宣告」——
    即使 DB 有該鍵的孤兒列也一樣（孤兒鍵只能刪除）。
    """
    error_code = "ROUTE_NOT_FOUND"
    status_code = 404


class InvalidRouteError(RouteError):
    """The setting to write is invalid; the caller can fix it.

    要寫入的設定不合格（呼叫端可修正）。

    The route key does not declare this capability, the choice is outside the ``.env`` whitelist, or
    the trial client construction failed.

    用途鍵沒有宣告這個能力、不在 .env 白名單，或試建客戶端失敗。
    """
    error_code = "INVALID_ROUTE"
    status_code = 422


class RouteStoreUnavailableError(RouteError):
    """A database error while reading or writing the cfg_capability_routes table.

    cfg_capability_routes 資料表讀寫時資料庫異常。

    Two cases, with different messages: the table cannot be read (whether a setting exists cannot be
    decided, which is distinguished from "does not exist"), or a write / delete failed (the outcome
    is unknown; look again first). Retrying after the DB recovers is enough: writes are upserts and
    deletes go by key, so doing it again gives the same result.

    兩種情況，訊息不同：讀不到（無法判斷設定是否存在，與「不存在」區分），或寫入／刪除時
    出錯（結果未知，應先重新查看）。DB 恢復後重試即可——寫入是 upsert、刪除依鍵，重做
    一次結果相同。
    """
    error_code = "ROUTE_STORE_UNAVAILABLE"
    status_code = 503


class CardNotFoundError(ServiceBaseError):
    """The requested card does not exist in Anki (possibly deleted or not yet synced).

    Anki 中找不到指定的卡片（可能已刪除或尚未同步）。
    """
    error_code = "CARD_NOT_FOUND"
    status_code = 404


class InvalidMediaFilenameError(ServiceBaseError):
    """An invalid media file name: only a bare file name in Anki's media folder is accepted, not a path.

    媒體檔名不合格：只接受 Anki 媒體資料夾中的單一檔名，不接受路徑。
    """
    error_code = "INVALID_MEDIA_FILENAME"
    status_code = 400


class MediaFileNotFoundError(ServiceBaseError):
    """The media file does not exist in Anki's media folder or in the upload staging area.

    Anki 媒體資料夾或上傳暫存區中沒有這個檔案。
    """
    error_code = "MEDIA_FILE_NOT_FOUND"
    status_code = 404


class InvalidAnkiSearchError(ServiceBaseError):
    """Anki rejected the user's search query, usually because of a syntax error.

    Anki 拒絕使用者輸入的搜尋語句（多半是語法錯誤）。
    """
    error_code = "INVALID_ANKI_SEARCH"
    status_code = 400


class InvalidRelationTypeError(ServiceBaseError):
    """The user-supplied relation type name is invalid.

    使用者給的關係類型名稱不合法。

    It is blank, uses a system namespace (``core.``), or contains characters outside the allowed set.

    空白、用了系統命名空間（``core.``）、或含不允許的字元。
    """
    error_code = "INVALID_RELATION_TYPE"
    status_code = 400


class RelationSyncRefusedError(ServiceBaseError):
    """The error raised when pruning orphan relations is refused to avoid deleting by mistake.

    同步清理孤兒關聯時，為避免誤刪而拒絕清理。

    Anki's answer looks incomplete: it returned no notes, or the sweep would delete too large a
    share of the relations without an explicit ``force``.

    Anki 的回應看起來不完整：回傳 0 張筆記，或未明確帶 ``force`` 卻要刪掉太大比例的關聯。
    """
    error_code = "RELATION_SYNC_REFUSED"
    status_code = 409


class RelationSyncEmptyAnkiError(RelationSyncRefusedError):
    """Anki returned 0 notes; always refused, even with ``force``.

    Anki 回傳 0 張筆記：一律拒絕，``force`` 也無效。
    """
    error_code = "RELATION_SYNC_EMPTY_ANKI"


class RelationSyncNeedsForceError(RelationSyncRefusedError):
    """More than half of the relations would be deleted; the list may be incomplete.

    要刪的關聯超過一半：清單可能不完整。

    Retry with ``force`` after confirming.

    確認後帶 ``force`` 才執行。

    Attributes:
        to_delete: the number of relations that would be deleted
            將刪除的關聯數
        considered: the number of relations considered
            考慮範圍內的關聯數
    """
    error_code = "RELATION_SYNC_NEEDS_FORCE"

    def __init__(self, message: str, *, to_delete: int, considered: int) -> None:
        """Create the error with the deletion counts.

        以刪除數量建立此錯誤。

        Args:
            message: the human-readable error message
                人類可讀的錯誤訊息
            to_delete: the number of relations that would be deleted
                將刪除的關聯數
            considered: the number of relations considered
                考慮範圍內的關聯數
        """
        super().__init__(message)
        self.to_delete = to_delete
        self.considered = considered


# ============================================================================
# Handler 能力與筆記操作（handlers 計畫書 D6）
# Handler capabilities and note operations (handlers plan D6)
# ============================================================================


class HandlerOperationUnsupportedError(ServiceBaseError):
    """This handler does not offer the operation or action.

    這個 handler 沒有開放這項操作或動作。

    ``message`` is the reason from the capability declaration, shown directly by the front end.

    ``message`` 為能力宣告的原因，前端直接顯示。
    """
    error_code = "HANDLER_OPERATION_UNSUPPORTED"
    status_code = 400


class NoteNotFoundError(ServiceBaseError):
    """The note does not exist in Anki (possibly deleted or not yet synced).

    Anki 中找不到這張筆記（可能已刪除或尚未同步）。
    """
    error_code = "NOTE_NOT_FOUND"
    status_code = 404


class NoteTypeNotCreatableError(ServiceBaseError):
    """The note type given at creation is not one this handler can create.

    建立時指定的筆記類型不是這個 handler 能建立的類型。
    """
    error_code = "NOTE_TYPE_NOT_CREATABLE"
    status_code = 422


class NoteNotOwnedByHandlerError(ServiceBaseError):
    """The note's type is not handled by the handler in the URL.

    筆記的類型不歸網址上的這個 handler 負責。
    """
    error_code = "NOTE_NOT_OWNED_BY_HANDLER"
    status_code = 400


class FieldNotEditableError(ServiceBaseError):
    """The field cannot be edited directly as text or filled in at creation.

    這個欄位不能直接以文字修改或在建卡時填寫。

    The handler declares it a JSON field, or the system maintains it. Media fields are handled
    separately: they only accept file names in the ``POST /media`` naming format
    (``MediaFieldInvalidError``).

    handler 宣告為 JSON 欄位，或由系統維護。媒體欄位另外處理：只能填 ``POST /media`` 命名格式的檔名
    （``MediaFieldInvalidError``）。
    """
    error_code = "FIELD_NOT_EDITABLE"
    status_code = 400


class JsonItemChangedError(ServiceBaseError):
    """The JSON list item to change has changed and the same item cannot be found.

    要動的 JSON 清單項目已經變了，找不到同一筆。

    It was added or deleted elsewhere, or the card view has not been synced yet.

    別處新增、刪除過，或卡片畫面還沒同步。
    """
    error_code = "JSON_ITEM_CHANGED"
    status_code = 409


class JsonFieldConflictError(ServiceBaseError):
    """The JSON field changed elsewhere while it was being edited on the web page; nothing is written.

    網頁編輯 JSON 欄位期間，別處改過這一欄，不寫入。

    "Elsewhere" means Telegram, a script, or another tab.

    「別處」指 TG、腳本、另一個分頁。
    """
    error_code = "JSON_FIELD_CONFLICT"
    status_code = 409


class MediaTypeUnsupportedError(ServiceBaseError):
    """The uploaded file is not an accepted format for its kind, or its content cannot be recognized.

    上傳的檔案不是這個種類接受的格式，或內容無法辨識。

    media_upload plan D2. Judged by the file content, not the extension: a text file renamed to
    ``.mp3`` is still rejected.

    media_upload 計畫書 D2。依檔案內容判斷，不看副檔名：文字檔改名成 ``.mp3`` 一樣會被擋下。
    """
    error_code = "MEDIA_TYPE_UNSUPPORTED"
    status_code = 415


class MediaTooLargeError(ServiceBaseError):
    """The uploaded file is over the size limit (media_upload plan D2).

    上傳的檔案超過大小上限（media_upload 計畫書 D2）。
    """
    error_code = "MEDIA_TOO_LARGE"
    status_code = 413


class MediaConvertFailedError(ServiceBaseError):
    """Converting the audio to mp3 failed.

    音檔轉成 mp3 失敗。

    The file is damaged, has no sound, or the conversion timed out (media_upload plan D2). A backend
    without ffmpeg is a different error (``MediaConverterUnavailableError``).

    檔案損壞、裡面沒有聲音，或轉檔逾時（media_upload 計畫書 D2）。後端沒有 ffmpeg
    是另一個錯誤（``MediaConverterUnavailableError``）。
    """
    error_code = "MEDIA_CONVERT_FAILED"
    status_code = 422


class MediaConverterUnavailableError(ServiceBaseError):
    """A conversion is needed, but the backend cannot find or start ffmpeg.

    需要轉檔，但後端找不到或無法啟動 ffmpeg。

    media_upload plan D2. This is a server problem; trying another file does not help. Formats that
    need no conversion (mp3, ogg, wav) are not affected.

    media_upload 計畫書 D2。這是伺服器的問題，換一個檔案沒有用；不需轉檔的格式（mp3、ogg、wav）不受影響。
    """
    error_code = "MEDIA_CONVERTER_UNAVAILABLE"
    status_code = 503


class MediaUploadExpiredError(ServiceBaseError):
    """An uploaded file used by a create or update is no longer in the staging area.

    建卡或修改用到的上傳檔已經不在暫存區。

    It is older than a day, the backend was redeployed, or it was already used on another card
    (media_upload plan D1). The check runs before anything is written, so Anki is not touched.

    超過一天、後端重新部署過，或已經用在別張卡上（media_upload 計畫書 D1）。檢查在寫入任何東西之前，
    Anki 不會被動到。
    """
    error_code = "MEDIA_UPLOAD_EXPIRED"
    status_code = 410


class MediaFieldInvalidError(ServiceBaseError):
    """A media field does not match the ``POST /media`` naming format, or the kind does not match.

    媒體欄位填的不符合 ``POST /media`` 的命名格式，或種類不符。

    media_upload plan D3. Only the format is checked; it does not confirm that the file is actually
    in Anki.

    media_upload 計畫書 D3。只檢查格式，不確認檔案真的在 Anki 裡。
    """
    error_code = "MEDIA_FIELD_INVALID"
    status_code = 422


class InvalidNoteQueryError(ServiceBaseError):
    """A parameter of a per-handler note query (graph included) does not apply.

    依 handler 查筆記（含圖譜）的參數不適用。

    The note type does not belong to the handler, the name field does not exist, or a name field was
    given for the relation graph.

    筆記類型不歸它、名稱欄位不存在、關聯圖譜指定了名稱欄位。
    """
    error_code = "INVALID_NOTE_QUERY"
    status_code = 422


class InvalidNoteFieldsError(ServiceBaseError):
    """The fields to write are not in the form ``{field_name: value}``.

    要寫入的欄位不是 ``{欄位名: 值}`` 的形式。
    """
    error_code = "INVALID_NOTE_FIELDS"
    status_code = 422


class ActionContractError(InvalidNoteFieldsError):
    """A named action's parameters do not match the contract with the backend.

    具名動作的參數和後端的約定不一致。

    Wrong keys or a wrong list name: a programming error in the caller, not something the user can
    fix.

    鍵不對、清單名不對：呼叫端的程式錯誤，不是使用者能修正的內容。
    """
    error_code = "ACTION_CONTRACT"


class ActionContractError(InvalidNoteFieldsError):
    """A named action's parameters do not match the contract with the backend.

    具名動作的參數和後端的約定不一致。

    Wrong keys or a wrong list name: a programming error in the caller, not something the user can
    fix.

    鍵不對、清單名不對：呼叫端的程式錯誤，不是使用者能修正的內容。
    """
    error_code = "ACTION_CONTRACT"


class TagInvalidError(ServiceBaseError):
    """A tag added at creation is invalid: it contains whitespace, is a system tag, or is too long or too many.

    建卡時加的標籤不合格：含空白、是系統標籤、太長或太多。
    """
    error_code = "TAG_INVALID"
    status_code = 400


class DraftStateError(ServiceBaseError):
    """The card creation draft's state does not allow this operation.

    建卡草稿的狀態不允許這個操作。

    The response carries the current draft (server_drafts plan D6). Only a base class; subclasses
    are what is actually raised.

    回應附上目前的草稿（server_drafts 計畫書 D6）。只作為基底，實際拋出子類別。

    Attributes:
        draft: the current draft (``Draft.to_dict()``), or ``None`` if there is none; the global
            handler puts it into the response's ``draft``
            目前的草稿（``Draft.to_dict()``），沒有草稿時為 ``None``；全域 handler 放進回應的 ``draft``
    """
    error_code = "DRAFT_STATE_ERROR"
    status_code = 409

    def __init__(self, message: str, draft: dict[str, object] | None = None) -> None:
        """Create the error with the current draft.

        以目前的草稿建立此錯誤。

        Args:
            message: the human-readable error message
                人類可讀的錯誤訊息
            draft: the current draft, or ``None`` if there is none
                目前的草稿；沒有草稿時為 ``None``
        """
        super().__init__(message)
        self.draft = draft


class CreateSessionBusyError(DraftStateError):
    """New generation or creation is not accepted because the create slot is taken (D2).

    已經有建卡工作進行中，不接受新的生成或建卡（D2）。

    A create job is already in progress (only one site-wide at a time), or the draft is for another
    kind of card.

    已經有一份建卡工作進行中（全站同一時間只有一份），或草稿是另一種卡片的。
    """
    error_code = "CREATE_SESSION_BUSY"


class DraftBusyError(DraftStateError):
    """The draft is not ``ready`` for this operation (D3, D8).

    草稿不是 ``ready``，不能進行這個操作（D3、D8）。

    While it is generating, being created, or failed, it cannot be updated or created; while it is
    generating or being created, it cannot be discarded either.

    生成中、建立中或生成失敗時不能修改或建立；生成中、建立中也不能廢棄。
    """
    error_code = "DRAFT_BUSY"


class DraftVersionConflictError(DraftStateError):
    """The draft changed elsewhere: the request's version is not the latest (D5).

    草稿在其他地方改過了：請求帶的版本不是最新的（D5）。
    """
    error_code = "DRAFT_VERSION_CONFLICT"


class DraftNotFailedError(DraftStateError):
    """Only a draft whose generation failed can be retried (D3).

    只有生成失敗的草稿能重試（D3）。
    """
    error_code = "DRAFT_NOT_FAILED"


class DraftNotFoundError(ServiceBaseError):
    """The draft is gone: it may have been created, discarded elsewhere, or the backend restarted (D7, R1).

    草稿已不在：可能已建立、在其他地方廢棄，或後端重啟過（D7、R1）。
    """
    error_code = "DRAFT_NOT_FOUND"
    status_code = 404


class DraftStateTooLargeError(ServiceBaseError):
    """The web page's editing state is over the size limit (D5).

    網頁的編輯狀態超過上限（D5）。
    """
    error_code = "DRAFT_STATE_TOO_LARGE"
    status_code = 413
