"""
Anki 相關 Pydantic V2 Schema 定義模組。

本模組定義了與 AnkiConnect API v6 交互所需的所有資料結構，
涵蓋筆記 (Note)、卡片模板 (Card Template)、模型建立 (Model Creation)、
以及 AnkiConnect 的請求/回應封裝。

所有跨邊界交互的資料結構均透過 Pydantic V2 進行強型別驗證，
嚴禁使用裸字典 (raw dict) 或 typing.Any。
"""

from pydantic import BaseModel, ConfigDict, Field


# ============================================================================
# AnkiConnect 通訊層 Schema (Infrastructure Layer)
# ============================================================================

class AnkiActionRequest(BaseModel):
    """封裝發送給 AnkiConnect 的基礎 JSON-RPC 請求結構。

    AnkiConnect v6 要求所有請求都包含 action 與 version 欄位，
    params 與 key 為可選欄位。

    Attributes:
        action: AnkiConnect API 動作名稱，例如 'deckNames'、'addNote'。
        version: API 版本號，固定為 6 以獲得完整錯誤處理支援。
        params: 傳遞給 API 動作的參數字典。
        key: API 金鑰，僅在伺服器啟用認證時需要。
    """
    action: str
    version: int = 6
    params: dict[str, object] | None = None
    key: str | None = None


class AnkiActionResponse(BaseModel):
    """封裝 AnkiConnect 回傳的標準 JSON-RPC 回應結構。

    AnkiConnect v6 的回應格式固定為 {"result": ..., "error": ...}。
    error 為 null 代表成功，否則包含錯誤訊息字串。

    Attributes:
        result: API 回傳的結果值，型別取決於具體的 API 動作。
        error: 錯誤訊息字串，成功時為 None。
    """
    result: object = None
    error: str | None = None


# ============================================================================
# 筆記操作 Schema (Note Actions)
# ============================================================================

class AnkiNoteOptions(BaseModel):
    """定義 AnkiConnect addNote 的重複檢查行為設定。

    此模型控制 Anki 在新增筆記時如何處理潛在的重複內容，
    是防止同一張卡片被重複建立的關鍵防線。

    Attributes:
        allowDuplicate: 是否允許重複筆記。
        duplicateScope: 重複檢查範圍，'deck' 僅檢查目標牌組。
        duplicateScopeOptions: 進階重複檢查設定。
    """
    model_config = ConfigDict(populate_by_name=True)

    allowDuplicate: bool = False
    duplicateScope: str = "deck"
    duplicateScopeOptions: dict[str, str | bool] = Field(default_factory=dict)


class AnkiMediaAttachment(BaseModel):
    """Anki 媒體附件描述結構。

    支援三種來源方式（互斥）：Base64 資料、本地路徑、遠端 URL。

    Attributes:
        url: 遠端檔案的下載 URL。
        filename: 儲存在 Anki 媒體資料夾中的檔名。
        fields: 要插入此媒體引用的欄位名稱列表。
        data: Base64 編碼的檔案內容。
        path: 本地檔案絕對路徑。
    """
    url: str | None = None
    filename: str
    fields: list[str] = Field(default_factory=list)
    data: str | None = None
    path: str | None = None


class AnkiNote(BaseModel):
    """用於 AnkiConnect addNote / addNotes 的筆記結構。

    此模型嚴格對應 AnkiConnect v6 的 addNote params.note 格式，
    所有欄位均經過 Pydantic 驗證，確保型別安全。

    Attributes:
        deckName: 目標牌組名稱，支援 '::' 分隔的巢狀牌組。
        modelName: 筆記類型（模型）名稱，例如 'TOEIC_Coach_Dark'。
        fields: 筆記欄位字典，鍵為欄位名稱，值為欄位內容。
        tags: 標籤列表。
        options: 重複檢查行為設定。
        audio: 音訊附件列表。
        video: 影片附件列表。
        picture: 圖片附件列表。
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

    Attributes:
        noteId: 筆記的唯一識別 ID。
        modelName: 所屬模型名稱。
        tags: 標籤列表。
        fields: 欄位內容字典，每個欄位包含 value 與 order。
        cards: 關聯卡片 ID 列表。
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

    每個模型至少包含一個卡片樣板，定義正面與背面的 HTML 渲染模板。

    Attributes:
        Name: 卡片樣板的名稱，例如 'Card 1'。
        Front: 正面 HTML 模板字串。
        Back: 背面 HTML 模板字串。
    """
    model_config = ConfigDict(populate_by_name=True)

    Name: str
    Front: str
    Back: str


class AnkiModelPayload(BaseModel):
    """用於 AnkiConnect createModel action 的完整參數結構。

    此模型封裝了建立新 Anki 筆記類型所需的所有資訊，
    包含欄位定義、CSS 樣式與卡片正背面 HTML 模板。

    Attributes:
        modelName: 新模型的唯一名稱。
        inOrderFields: 欄位名稱陣列，按照順序排列。
        css: 共用 CSS 樣式表字串。
        isCloze: 是否為克漏字 (Cloze) 題型，預設 False。
        cardTemplates: 卡片樣板列表，定義正面與背面 HTML。
    """
    modelName: str
    inOrderFields: list[str]
    css: str
    isCloze: bool = Field(default=False)
    cardTemplates: list[AnkiCardTemplate]


class AnkiCreateModelRequest(BaseModel):
    """封裝發送給 AnkiConnect 的 createModel 完整請求結構。

    Attributes:
        action: 固定為 'createModel'。
        version: API 版本號，固定為 6。
        params: 模型建立的完整參數。
    """
    action: str = Field(default="createModel")
    version: int = Field(default=6)
    params: AnkiModelPayload


# ============================================================================
# 媒體操作 Schema (Media Actions)
# ============================================================================

class AnkiStoreMediaParams(BaseModel):
    """用於 AnkiConnect storeMediaFile 的參數結構。

    提供三種方式指定檔案內容（優先順序：data > path > url）。

    Attributes:
        filename: 檔案名稱（含副檔名）。
        data: Base64 編碼的檔案內容。
        path: 本地檔案絕對路徑。
        url: 遠端檔案 URL。
        deleteExisting: 是否刪除同名既有檔案，預設 True。
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

    Attributes:
        model_name: 模型名稱（即 Anki 筆記類型名稱）。
        model_file_name: 對應的 JSON 定義檔名（含 .json 副檔名）。
        fields: 欄位名稱列表，按照模型定義的順序排列。
        has_llm_schema: 是否包含 llm_schema 定義（用於 LLM 結構化輸出）。
    """

    model_name: str
    model_file_name: str
    fields: list[str] = Field(default_factory=list)
    has_llm_schema: bool = False


class AnkiDeckInfo(BaseModel):
    """Anki 牌組摘要資訊，用於前端下拉選單或牌組列表 API。

    Attributes:
        deck_name: 牌組名稱，支援 '::' 分隔的巢狀結構。
        deck_id: 牌組的唯一識別 ID。
    """

    deck_name: str
    deck_id: int
