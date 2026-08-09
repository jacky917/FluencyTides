"""
Anki 相關 Pydantic V2 Schema 定義模組。

Pydantic V2 schema definitions for Anki interactions.

本模組定義了與 AnkiConnect API v6 交互所需的所有資料結構，
涵蓋筆記 (Note)、卡片模板 (Card Template)、模型建立 (Model Creation)、
以及 AnkiConnect 的請求/回應封裝。

Defines all data structures required for interacting with the AnkiConnect
API v6, covering notes, card templates, model creation, and the
AnkiConnect request/response envelopes.

所有跨邊界交互的資料結構均透過 Pydantic V2 進行強型別驗證，
嚴禁使用裸字典 (raw dict) 或 typing.Any。

All cross-boundary data structures are strongly validated with Pydantic V2;
raw dicts and typing.Any are strictly forbidden.
"""

from pydantic import BaseModel, ConfigDict, Field


# ============================================================================
# AnkiConnect 通訊層 Schema (Infrastructure Layer)
# ============================================================================

class AnkiActionRequest(BaseModel):
    """封裝發送給 AnkiConnect 的基礎 JSON-RPC 請求結構。

    Base JSON-RPC request envelope sent to AnkiConnect.

    AnkiConnect v6 要求所有請求都包含 action 與 version 欄位，
    params 與 key 為可選欄位。

    AnkiConnect v6 requires action and version on every request; params and
    key are optional.

    Attributes:
        action: AnkiConnect API 動作名稱，例如 'deckNames'、'addNote'。
            AnkiConnect API action name, e.g. 'deckNames', 'addNote'.
        version: API 版本號，固定為 6 以獲得完整錯誤處理支援。API version,
            fixed at 6 for full error-handling support.
        params: 傳遞給 API 動作的參數字典。Parameter dict for the action.
        key: API 金鑰，僅在伺服器啟用認證時需要。API key, needed only when
            the server enables authentication.
    """
    action: str
    version: int = 6
    params: dict[str, object] | None = None
    key: str | None = None


class AnkiActionResponse(BaseModel):
    """封裝 AnkiConnect 回傳的標準 JSON-RPC 回應結構。

    Standard JSON-RPC response envelope returned by AnkiConnect.

    AnkiConnect v6 的回應格式固定為 {"result": ..., "error": ...}。
    error 為 null 代表成功，否則包含錯誤訊息字串。

    The AnkiConnect v6 response is always {"result": ..., "error": ...};
    a null error means success, otherwise it carries the error message.

    Attributes:
        result: API 回傳的結果值，型別取決於具體的 API 動作。Result value,
            whose type depends on the specific action.
        error: 錯誤訊息字串，成功時為 None。Error message string; None on
            success.
    """
    result: object = None
    error: str | None = None


# ============================================================================
# 筆記操作 Schema (Note Actions)
# ============================================================================

class AnkiNoteOptions(BaseModel):
    """定義 AnkiConnect addNote 的重複檢查行為設定。

    Duplicate-check behavior options for AnkiConnect addNote.

    此模型控制 Anki 在新增筆記時如何處理潛在的重複內容，
    是防止同一張卡片被重複建立的關鍵防線。

    Controls how Anki handles potential duplicates when adding notes — the
    key safeguard against creating the same card twice.

    Attributes:
        allowDuplicate: 是否允許重複筆記。Whether duplicate notes are
            allowed.
        duplicateScope: 重複檢查範圍，'deck' 僅檢查目標牌組。Duplicate
            check scope; 'deck' checks only the target deck.
        duplicateScopeOptions: 進階重複檢查設定。Advanced duplicate-check
            options.
    """
    model_config = ConfigDict(populate_by_name=True)

    allowDuplicate: bool = False
    duplicateScope: str = "deck"
    duplicateScopeOptions: dict[str, str | bool] = Field(default_factory=dict)


class AnkiMediaAttachment(BaseModel):
    """Anki 媒體附件描述結構。

    Anki media attachment descriptor.

    支援三種來源方式（互斥）：Base64 資料、本地路徑、遠端 URL。

    Supports three mutually exclusive sources: Base64 data, local path, or
    remote URL.

    Attributes:
        url: 遠端檔案的下載 URL。Download URL of the remote file.
        filename: 儲存在 Anki 媒體資料夾中的檔名。Filename stored in the
            Anki media folder.
        fields: 要插入此媒體引用的欄位名稱列表。Field names into which the
            media reference is inserted.
        data: Base64 編碼的檔案內容。Base64-encoded file content.
        path: 本地檔案絕對路徑。Absolute local file path.
    """
    url: str | None = None
    filename: str
    fields: list[str] = Field(default_factory=list)
    data: str | None = None
    path: str | None = None


class AnkiNote(BaseModel):
    """用於 AnkiConnect addNote / addNotes 的筆記結構。

    Note structure for AnkiConnect addNote / addNotes.

    此模型嚴格對應 AnkiConnect v6 的 addNote params.note 格式，
    所有欄位均經過 Pydantic 驗證，確保型別安全。

    Strictly mirrors the AnkiConnect v6 addNote params.note format; every
    field is Pydantic-validated for type safety.

    Attributes:
        deckName: 目標牌組名稱，支援 '::' 分隔的巢狀牌組。Target deck name,
            supporting '::'-nested decks.
        modelName: 筆記類型（模型）名稱，例如 'TOEIC_Coach_Dark'。Note type
            (model) name.
        fields: 筆記欄位字典，鍵為欄位名稱，值為欄位內容。Field dict keyed
            by field name.
        tags: 標籤列表。Tag list.
        options: 重複檢查行為設定。Duplicate-check options.
        audio: 音訊附件列表。Audio attachments.
        video: 影片附件列表。Video attachments.
        picture: 圖片附件列表。Picture attachments.
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
    """AnkiConnect notesInfo 回傳的筆記詳細資訊結構。

    Detailed note info returned by AnkiConnect notesInfo.

    Attributes:
        noteId: 筆記的唯一識別 ID。Unique note ID.
        modelName: 所屬模型名稱。Owning model name.
        tags: 標籤列表。Tag list.
        fields: 欄位內容字典，每個欄位包含 value 與 order。Field dict where
            each field carries value and order.
        cards: 關聯卡片 ID 列表。Associated card IDs.
    """
    noteId: int
    modelName: str
    tags: list[str]
    fields: dict[str, dict[str, str | int]]
    cards: list[int]


# ============================================================================
# 模型管理 Schema (Model Actions)
# ============================================================================

class AnkiCardTemplate(BaseModel):
    """Anki 模型中的卡片樣板定義。

    Card template definition within an Anki model.

    每個模型至少包含一個卡片樣板，定義正面與背面的 HTML 渲染模板。

    Every model has at least one card template defining the front and back
    HTML render templates.

    Attributes:
        Name: 卡片樣板的名稱，例如 'Card 1'。Template name, e.g. 'Card 1'.
        Front: 正面 HTML 模板字串。Front-side HTML template string.
        Back: 背面 HTML 模板字串。Back-side HTML template string.
    """
    model_config = ConfigDict(populate_by_name=True)

    Name: str
    Front: str
    Back: str


class AnkiModelPayload(BaseModel):
    """用於 AnkiConnect createModel action 的完整參數結構。

    Full parameter structure for the AnkiConnect createModel action.

    此模型封裝了建立新 Anki 筆記類型所需的所有資訊，
    包含欄位定義、CSS 樣式與卡片正背面 HTML 模板。

    Encapsulates everything needed to create a new Anki note type: field
    definitions, CSS, and front/back card HTML templates.

    Attributes:
        modelName: 新模型的唯一名稱。Unique name of the new model.
        inOrderFields: 欄位名稱陣列，按照順序排列。Ordered field names.
        css: 共用 CSS 樣式表字串。Shared CSS stylesheet string.
        isCloze: 是否為克漏字 (Cloze) 題型，預設 False。Whether this is a
            cloze model; defaults to False.
        cardTemplates: 卡片樣板列表，定義正面與背面 HTML。Card template
            list defining front/back HTML.
    """
    modelName: str
    inOrderFields: list[str]
    css: str
    isCloze: bool = Field(default=False)
    cardTemplates: list[AnkiCardTemplate]


class AnkiCreateModelRequest(BaseModel):
    """封裝發送給 AnkiConnect 的 createModel 完整請求結構。

    Full createModel request envelope sent to AnkiConnect.

    Attributes:
        action: 固定為 'createModel'。Fixed to 'createModel'.
        version: API 版本號，固定為 6。API version, fixed at 6.
        params: 模型建立的完整參數。Full model-creation parameters.
    """
    action: str = Field(default="createModel")
    version: int = Field(default=6)
    params: AnkiModelPayload


# ============================================================================
# 媒體操作 Schema (Media Actions)
# ============================================================================

class AnkiStoreMediaParams(BaseModel):
    """用於 AnkiConnect storeMediaFile 的參數結構。

    Parameter structure for AnkiConnect storeMediaFile.

    提供三種方式指定檔案內容（優先順序：data > path > url）。

    Offers three ways to supply the file content (priority:
    data > path > url).

    Attributes:
        filename: 檔案名稱（含副檔名）。Filename with extension.
        data: Base64 編碼的檔案內容。Base64-encoded file content.
        path: 本地檔案絕對路徑。Absolute local file path.
        url: 遠端檔案 URL。Remote file URL.
        deleteExisting: 是否刪除同名既有檔案，預設 True。Whether to delete
            an existing file with the same name; defaults to True.
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
    """Anki 模型摘要資訊，用於前端下拉選單或模型列表 API。

    Anki model summary for frontend dropdowns or the model-list API.

    Attributes:
        model_name: 模型名稱（即 Anki 筆記類型名稱）。Model name (Anki note
            type name).
        model_file_name: 對應的 JSON 定義檔名（含 .json 副檔名）。The
            corresponding JSON definition filename (with .json extension).
        fields: 欄位名稱列表，按照模型定義的順序排列。Field names in model
            definition order.
        has_llm_schema: 是否包含 llm_schema 定義（用於 LLM 結構化輸出）。
            Whether an llm_schema definition exists (for LLM structured
            output).
    """

    model_name: str
    model_file_name: str
    fields: list[str] = Field(default_factory=list)
    has_llm_schema: bool = False


class AnkiDeckInfo(BaseModel):
    """Anki 牌組摘要資訊，用於前端下拉選單或牌組列表 API。

    Anki deck summary for frontend dropdowns or the deck-list API.

    Attributes:
        deck_name: 牌組名稱，支援 '::' 分隔的巢狀結構。Deck name,
            supporting '::'-nested structure.
        deck_id: 牌組的唯一識別 ID。Unique deck ID.
    """

    deck_name: str
    deck_id: int
