"""Pydantic V2 schema definitions for Anki interactions.

Anki 相關 Pydantic V2 Schema 定義模組。

Defines all data structures required for interacting with the AnkiConnect
API v6, covering notes, card templates, model creation, and the
AnkiConnect request/response envelopes.

本模組定義了與 AnkiConnect API v6 交互所需的所有資料結構，
涵蓋筆記 (Note)、卡片模板 (Card Template)、模型建立 (Model Creation)、
以及 AnkiConnect 的請求/回應封裝。

All cross-boundary data structures are strongly validated with Pydantic V2;
raw dicts and ``typing.Any`` are strictly forbidden.

所有跨邊界交互的資料結構均透過 Pydantic V2 進行強型別驗證，
嚴禁使用裸字典 (raw dict) 或 ``typing.Any``。
"""

from pydantic import BaseModel, ConfigDict, Field


# ============================================================================
# AnkiConnect 通訊層 Schema (Infrastructure Layer)
# ============================================================================

class AnkiActionRequest(BaseModel):
    """The base JSON-RPC request envelope sent to AnkiConnect.

    封裝發送給 AnkiConnect 的基礎 JSON-RPC 請求結構。

    AnkiConnect v6 requires ``action`` and ``version`` on every request;
    ``params`` and ``key`` are optional.

    AnkiConnect v6 要求所有請求都包含 ``action`` 與 ``version`` 欄位，
    ``params`` 與 ``key`` 為可選欄位。

    Attributes:
        action: the AnkiConnect API action name, for example ``'deckNames'`` or ``'addNote'``
            AnkiConnect API 動作名稱，例如 ``'deckNames'``、``'addNote'``
        version: the API version, fixed at 6 for full error-handling support
            API 版本號，固定為 6 以獲得完整錯誤處理支援
        params: the parameter dict passed to the action
            傳遞給 API 動作的參數字典
        key: the API key, needed only when the server enables authentication
            API 金鑰，僅在伺服器啟用認證時需要
    """
    action: str
    version: int = 6
    params: dict[str, object] | None = None
    key: str | None = None


class AnkiActionResponse(BaseModel):
    """The standard JSON-RPC response envelope returned by AnkiConnect.

    封裝 AnkiConnect 回傳的標準 JSON-RPC 回應結構。

    The AnkiConnect v6 response is always ``{"result": ..., "error": ...}``.
    A null ``error`` means success; otherwise it carries the error message string.

    AnkiConnect v6 的回應格式固定為 ``{"result": ..., "error": ...}``。
    ``error`` 為 null 代表成功，否則包含錯誤訊息字串。

    Attributes:
        result: the value returned by the API, whose type depends on the specific action
            API 回傳的結果值，型別取決於具體的 API 動作
        error: the error message string; ``None`` on success
            錯誤訊息字串，成功時為 ``None``
    """
    result: object = None
    error: str | None = None


# ============================================================================
# 筆記操作 Schema (Note Actions)
# ============================================================================

class AnkiNoteOptions(BaseModel):
    """Duplicate-check behavior options for AnkiConnect ``addNote``.

    定義 AnkiConnect ``addNote`` 的重複檢查行為設定。

    Controls how Anki handles potential duplicates when adding notes; it is the
    key safeguard against creating the same card twice.

    此模型控制 Anki 在新增筆記時如何處理潛在的重複內容，
    是防止同一張卡片被重複建立的關鍵防線。

    Attributes:
        allowDuplicate: whether duplicate notes are allowed
            是否允許重複筆記
        duplicateScope: the duplicate-check scope; ``'deck'`` checks only the target deck
            重複檢查範圍，``'deck'`` 僅檢查目標牌組
        duplicateScopeOptions: advanced duplicate-check options
            進階重複檢查設定
    """
    model_config = ConfigDict(populate_by_name=True)

    allowDuplicate: bool = False
    duplicateScope: str = "deck"
    duplicateScopeOptions: dict[str, str | bool] = Field(default_factory=dict)


class AnkiMediaAttachment(BaseModel):
    """An Anki media attachment descriptor.

    Anki 媒體附件描述結構。

    Supports three mutually exclusive sources: Base64 data, a local path, or a
    remote URL.

    支援三種來源方式（互斥）：Base64 資料、本地路徑、遠端 URL。

    Attributes:
        url: the download URL of the remote file
            遠端檔案的下載 URL
        filename: the filename stored in the Anki media folder
            儲存在 Anki 媒體資料夾中的檔名
        fields: the names of the fields into which the media reference is inserted
            要插入此媒體引用的欄位名稱列表
        data: the Base64-encoded file content
            Base64 編碼的檔案內容
        path: the absolute local file path
            本地檔案絕對路徑
    """
    url: str | None = None
    filename: str
    fields: list[str] = Field(default_factory=list)
    data: str | None = None
    path: str | None = None


class AnkiNote(BaseModel):
    """The note structure for AnkiConnect ``addNote`` / ``addNotes``.

    用於 AnkiConnect ``addNote`` / ``addNotes`` 的筆記結構。

    Strictly mirrors the AnkiConnect v6 ``addNote`` ``params.note`` format;
    every field is validated by Pydantic for type safety.

    此模型嚴格對應 AnkiConnect v6 的 ``addNote`` ``params.note`` 格式，
    所有欄位均經過 Pydantic 驗證，確保型別安全。

    Attributes:
        deckName: the target deck name; supports ``'::'``-separated nested decks
            目標牌組名稱，支援 ``'::'`` 分隔的巢狀牌組
        modelName: the note type (model) name, for example ``'TOEIC_Coach_Dark'``
            筆記類型（模型）名稱，例如 ``'TOEIC_Coach_Dark'``
        fields: the note fields, keyed by field name, with the field content as values
            筆記欄位字典，鍵為欄位名稱，值為欄位內容
        tags: the tag list
            標籤列表
        options: the duplicate-check options
            重複檢查行為設定
        audio: the audio attachments
            音訊附件列表
        video: the video attachments
            影片附件列表
        picture: the picture attachments
            圖片附件列表
    """
    model_config = ConfigDict(populate_by_name=True)

    deckName: str
    modelName: str
    fields: dict[str, str]
    tags: list[str] = Field(default_factory=list)
    options: AnkiNoteOptions | None = None
    audio: list[AnkiMediaAttachment] | None = None
    video: list[AnkiMediaAttachment] | None = None
    picture: list[AnkiMediaAttachment] | None = None


class AnkiNoteInfo(BaseModel):
    """Detailed note information returned by AnkiConnect ``notesInfo``.

    AnkiConnect ``notesInfo`` 回傳的筆記詳細資訊結構。

    Attributes:
        noteId: the unique note ID
            筆記的唯一識別 ID
        modelName: the name of the owning model
            所屬模型名稱
        tags: the tag list
            標籤列表
        fields: the field contents; each field carries ``value`` and ``order``
            欄位內容字典，每個欄位包含 ``value`` 與 ``order``
        cards: the IDs of the associated cards
            關聯卡片 ID 列表
    """
    noteId: int
    modelName: str
    tags: list[str]
    fields: dict[str, dict[str, str | int]]
    cards: list[int]


class AnkiCardField(BaseModel):
    """The value and position of one field in ``cardsInfo``.

    ``cardsInfo`` 中一個欄位的值與順序。

    Attributes:
        value: the raw field value
            欄位原始值
        order: the position in the note type, counting from 0
            在筆記類型中的位置（0 起算）
    """
    value: str
    order: int


class AnkiCardInfo(BaseModel):
    """Card information returned by AnkiConnect ``cardsInfo``.

    AnkiConnect ``cardsInfo`` 回傳的卡片資訊。

    Only the keys in use are kept; the rest are ignored.

    只取用到的欄位，其餘忽略。

    Attributes:
        cardId: the card ID
            卡片 ID
        note: the ID of the owning note
            所屬筆記 ID
        deckName: the full name of the deck the card is in
            所在牌組的完整名稱
        modelName: the note type
            筆記類型
        ord: the card template ordinal, counting from 0
            卡片模板序號（0 起算）
        fields: field name mapped to its value and position
            欄位名 → 值與順序
        question: the front as rendered by Anki (``[sound:]`` already replaced by ``[anki:play:q:N]``)
            Anki 渲染好的正面（``[sound:]`` 已換成 ``[anki:play:q:N]``）
        answer: the back as rendered by Anki (``[sound:]`` already replaced by ``[anki:play:a:N]``)
            Anki 渲染好的背面（``[sound:]`` 已換成 ``[anki:play:a:N]``）
    """
    cardId: int
    note: int
    deckName: str
    modelName: str
    ord: int
    fields: dict[str, AnkiCardField]
    question: str
    answer: str


# ============================================================================
# 模型管理 Schema (Model Actions)
# ============================================================================

class AnkiCardTemplate(BaseModel):
    """A card template definition within an Anki model.

    Anki 模型中的卡片樣板定義。

    Every model has at least one card template, which defines the HTML render
    templates for the front and back.

    每個模型至少包含一個卡片樣板，定義正面與背面的 HTML 渲染模板。

    Attributes:
        Name: the card template name, for example ``'Card 1'``
            卡片樣板的名稱，例如 ``'Card 1'``
        Front: the front-side HTML template string
            正面 HTML 模板字串
        Back: the back-side HTML template string
            背面 HTML 模板字串
    """
    model_config = ConfigDict(populate_by_name=True)

    Name: str
    Front: str
    Back: str


class AnkiModelPayload(BaseModel):
    """The full parameter structure for the AnkiConnect ``createModel`` action.

    用於 AnkiConnect ``createModel`` action 的完整參數結構。

    Encapsulates everything needed to create a new Anki note type: field
    definitions, CSS styles, and the front/back card HTML templates.

    此模型封裝了建立新 Anki 筆記類型所需的所有資訊，
    包含欄位定義、CSS 樣式與卡片正背面 HTML 模板。

    Attributes:
        modelName: the unique name of the new model
            新模型的唯一名稱
        inOrderFields: the field names, in order
            欄位名稱陣列，按照順序排列
        css: the shared CSS stylesheet string
            共用 CSS 樣式表字串
        isCloze: whether this is a cloze model; defaults to ``False``
            是否為克漏字 (Cloze) 題型，預設 ``False``
        cardTemplates: the card templates defining the front and back HTML
            卡片樣板列表，定義正面與背面 HTML
    """
    modelName: str
    inOrderFields: list[str]
    css: str
    isCloze: bool = Field(default=False)
    cardTemplates: list[AnkiCardTemplate]


class AnkiCreateModelRequest(BaseModel):
    """The full ``createModel`` request envelope sent to AnkiConnect.

    封裝發送給 AnkiConnect 的 ``createModel`` 完整請求結構。

    Attributes:
        action: fixed to ``'createModel'``
            固定為 ``'createModel'``
        version: the API version, fixed at 6
            API 版本號，固定為 6
        params: the full model-creation parameters
            模型建立的完整參數
    """
    action: str = Field(default="createModel")
    version: int = Field(default=6)
    params: AnkiModelPayload


# ============================================================================
# 媒體操作 Schema (Media Actions)
# ============================================================================

class AnkiStoreMediaParams(BaseModel):
    """The parameter structure for AnkiConnect ``storeMediaFile``.

    用於 AnkiConnect ``storeMediaFile`` 的參數結構。

    Offers three ways to supply the file content, in priority order:
    ``data`` > ``path`` > ``url``.

    提供三種方式指定檔案內容（優先順序：``data`` > ``path`` > ``url``）。

    Attributes:
        filename: the filename, including the extension
            檔案名稱（含副檔名）
        data: the Base64-encoded file content
            Base64 編碼的檔案內容
        path: the absolute local file path
            本地檔案絕對路徑
        url: the remote file URL
            遠端檔案 URL
        deleteExisting: whether to delete an existing file with the same name; defaults to ``True``
            是否刪除同名既有檔案，預設 ``True``
    """
    filename: str
    data: str | None = None
    path: str | None = None
    url: str | None = None
    deleteExisting: bool = True


# ============================================================================
# 模型/牌組查詢 Schema (API Response)
# ============================================================================

class AnkiModelInfo(BaseModel):
    """An Anki model summary for frontend dropdowns or the model-list API.

    Anki 模型摘要資訊，用於前端下拉選單或模型列表 API。

    Attributes:
        model_name: the model name, that is, the Anki note type name
            模型名稱（即 Anki 筆記類型名稱）
        model_file_name: the corresponding JSON definition filename, including the ``.json`` extension
            對應的 JSON 定義檔名（含 ``.json`` 副檔名）
        fields: the field names, in model definition order
            欄位名稱列表，按照模型定義的順序排列
        has_llm_schema: whether an ``llm_schema`` definition exists (used for LLM structured output)
            是否包含 ``llm_schema`` 定義（用於 LLM 結構化輸出）
    """

    model_name: str
    model_file_name: str
    fields: list[str] = Field(default_factory=list)
    has_llm_schema: bool = False


class AnkiDeckInfo(BaseModel):
    """An Anki deck summary for frontend dropdowns or the deck-list API.

    Anki 牌組摘要資訊，用於前端下拉選單或牌組列表 API。

    Attributes:
        deck_name: the deck name; supports a ``'::'``-separated nested structure
            牌組名稱，支援 ``'::'`` 分隔的巢狀結構
        deck_id: the unique deck ID
            牌組的唯一識別 ID
    """

    deck_name: str
    deck_id: int
