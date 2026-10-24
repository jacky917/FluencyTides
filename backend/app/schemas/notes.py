"""
Handler 與筆記 API 的請求、回應模型（handlers 計畫書 §4）。

Request and response models of the handler and note API.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# GET /handlers
# =============================================================================


class OperationOut(BaseModel):
    """一項操作是否開放；不開放時 ``reason`` 必有值。Whether an operation is offered."""

    supported: bool
    reason: str | None = None


class CreateCapabilityOut(OperationOut):
    """建卡能力（D2）。The create capability."""

    channels: list[Literal["web", "tg", "script"]] = Field(
        description="實際能從哪些管道建立：含 web 可選；非空但不含 web 反灰並顯示 reason；空陣列不顯示"
    )
    flow: Literal["form", "preview"] | None = None
    deck: Literal["selected", "fixed"] | None = None
    fixed_deck: str | None = None
    note_type: str | None = Field(default=None, description="建卡建出的筆記類型")


class GraphCapabilityOut(OperationOut):
    """圖譜能力；``links`` 表示有關聯連線。The graph capability."""

    links: bool = False


class CapabilitiesOut(BaseModel):
    """一個 handler 的全部能力。All capabilities of a handler."""

    create: CreateCapabilityOut
    read: OperationOut
    update_fields: OperationOut
    update_json: OperationOut = Field(description="網頁以表單編輯 JSON 欄位（格式見 json_field_specs）")
    delete: OperationOut
    actions: list[str]
    graph: GraphCapabilityOut


class HandlerInfo(BaseModel):
    """``GET /handlers`` 的一項（§4.1）。One handler."""

    name: str
    title: str
    note_types: list[str] = Field(description="這個 handler 負責的筆記類型")
    capabilities: CapabilitiesOut
    create_schema: dict[str, Any] | None = Field(
        description="網頁建卡表單的 JSON Schema；不開放、不是表單流程或 Anki 連不上時為 null"
    )
    json_fields: list[str] = Field(description="JSON 欄位：建卡表單不列、不能直接修改、網頁唯讀顯示")
    json_field_specs: dict[str, "JsonFieldSpecOut"] = Field(
        default_factory=dict, description="能以表單編輯的 JSON 欄位：標題與每一筆的 JSON Schema"
    )
    media_fields: list[str] = Field(description="媒體欄位：同上")


class JsonFieldSpecOut(BaseModel):
    """一個能以表單編輯的 JSON 欄位（json_field_editor 計畫 D2）。An editable JSON field."""

    title: str
    item_schema: dict[str, Any] = Field(description="每一筆的 JSON Schema，前端依它產生表單")


# =============================================================================
# 筆記
# =============================================================================


class NoteField(BaseModel):
    """一個欄位（依筆記類型的順序）。A field, in note-type order."""

    name: str
    value: str


class NoteDetail(BaseModel):
    """單一筆記（§4.3）。成組卡片的 handler 讀取之後會多附相關資料，所以允許額外的鍵。

    A single note; handler reads of grouped cards may add more keys.
    """

    model_config = ConfigDict(extra="allow")

    note_id: int
    note_type: str
    tags: list[str]
    cards: list[int]
    fields: list[NoteField]


class Permission(BaseModel):
    """對這張筆記能不能做某件事；不允許時 ``reason`` 必有值。Whether an action is allowed."""

    allowed: bool
    reason: str | None = None


class NotePermissions(BaseModel):
    """通用讀取附上的權限（D4）。Permissions in the generic read."""

    update_fields: Permission
    update_json: Permission
    delete: Permission


class JsonFieldState(BaseModel):
    """一個 JSON 欄位目前的內容（json_field_editor 計畫 D3）。A JSON field's current content."""

    items: list[Any] = Field(description="解析好的清單；欄位壞掉時為空")
    version: str = Field(description="欄位原始字串的雜湊；寫入時送回，用來偵測別處的修改")
    error: str | None = Field(description="欄位壞掉時的原因（這時不能編輯）")


class GenericNoteDetail(NoteDetail):
    """``GET /notes/{note_id}``：附上負責的 handler 與權限。The generic read."""

    handler: str | None = Field(description="負責的 handler；沒有 handler 負責這種筆記類型時為 null")
    permissions: NotePermissions
    json_fields: dict[str, JsonFieldState] = Field(
        default_factory=dict, description="能以表單編輯的 JSON 欄位（handler 的 JSON_FIELD_SPECS）目前的內容"
    )


class CreateNoteRequest(BaseModel):
    """``POST /handlers/{name}/notes``（§4.2）。"""

    deck: str = Field(min_length=1, description="牌組；handler 的建卡牌組為 fixed 時忽略，不存在時自動建立")
    note_type: str = Field(description="必須是該 handler 的 create.note_type")
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="建立參數：標準建立是 {欄位名: 值}；覆寫建立的 handler 依 create_schema",
    )


class CreateNoteResponse(BaseModel):
    """建卡結果。The created notes."""

    note_ids: list[int] = Field(description="一律是陣列（外語糾錯一次建母卡與多張子卡）")
    deck: str = Field(description="實際建立的牌組")
    sync_warning: str | None = Field(description="寫入後同步 AnkiWeb 失敗時的提醒；卡片仍已建立")


class UpdateNoteRequest(BaseModel):
    """``PATCH /handlers/{name}/notes/{note_id}``（§4.4）：只送有變的欄位。"""

    fields: dict[str, str]


class UpdateNoteResponse(NoteDetail):
    """修改後的完整筆記與同步提醒。The updated note and the sync warning."""

    sync_warning: str | None


class DeleteNoteResponse(BaseModel):
    """刪除結果（§4.5）。"""

    sync_warning: str | None


class GraphResponse(BaseModel):
    """``GET /handlers/{name}/graph``（§4.6）。節點與連線的形狀依 handler 而定（標準單點或關聯圖譜）。

    The graph; node and link shapes depend on the handler.
    """

    nodes: list[dict[str, Any]]
    links: list[dict[str, Any]]
    label_fields: list[str] = Field(description="可選的名稱欄位；關聯圖譜為空陣列")
    label_field: str | None = Field(description="實際採用的名稱欄位；null 表示顯示 nid")


class DeckInfo(BaseModel):
    """``GET /decks`` 的一項（與原本的 ``/handlers/decks`` 相同）。"""

    deck_name: str
    deck_id: int


class UpdateJsonRequest(BaseModel):
    """``PUT /handlers/{name}/notes/{note_id}/json/{field}``：整欄送出（json_field_editor 計畫 D4）。"""

    items: list[dict[str, Any]] = Field(description="整個清單，依顯示順序")
    version: str = Field(description="讀取時拿到的版本")


class UpdateJsonResponse(JsonFieldState):
    """寫入後的這一欄與同步提醒。The field after writing and the sync warning."""

    sync_warning: str | None = Field(description="寫入後同步 AnkiWeb 失敗時的提醒；資料仍已寫入")


# json_field_specs 用到後面才定義的 JsonFieldSpecOut
HandlerInfo.model_rebuild()
