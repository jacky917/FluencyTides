"""
基礎任務處理器 (Base Task Handler) 模組。

``BaseHandler`` 提供單一筆記卡片的標準 CRUD 與標準圖譜；各 handler 以類別屬性宣告能力與欄位，
需要不同行為時才覆寫（handlers 計畫書 D10、D12）。

Base task handler module.

``BaseHandler`` provides the standard CRUD and the standard graph for
single-note cards; handlers declare capabilities and fields as class
attributes and override only where they behave differently.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Literal

from app.core.exceptions import (
    FieldNotEditableError,
    HandlerOperationUnsupportedError,
    InvalidNoteFieldsError,
    InvalidNoteQueryError,
)
from app.infrastructure.anki.search import note_types_query
from app.services.media_names import MediaKind, media_field_text
from app.services.task_handlers.capabilities import GraphSpec, HandlerCapabilities, JsonFieldSpec, SearchField

#: 生成前發現重複時的決定：``overwrite`` 新卡建好後刪掉舊卡、``allow`` 允許重複。
#: The decision on a duplicate found before generating.
DuplicateDecision = Literal["overwrite", "allow"]

# 草稿輸入裡由系統放進去的鍵（不是使用者的輸入，``NoteService.resolve_draft_duplicate`` 寫入、
# ``execute_draft`` 讀）。草稿只在記憶體，存在輸入裡就跟著草稿走，重試時也還在。
#: 重複的決定（值見 ``DuplicateDecision``）。The key of the duplicate decision in a draft input.
DUPLICATE_DECISION_KEY = "duplicate_decision"
#: 這個決定針對的舊卡 ID。The key of the old card the decision is about.
DUPLICATE_NOTE_KEY = "duplicate_note_id"
#: 查重複失敗、略過了檢查時的原因（網頁的預覽顯示提醒）。The key of why the check was skipped.
DUPLICATE_CHECK_SKIPPED_KEY = "duplicate_check_skipped"
#: 以上三個系統鍵。All system keys in a draft input.
DRAFT_SYSTEM_KEYS = (DUPLICATE_DECISION_KEY, DUPLICATE_NOTE_KEY, DUPLICATE_CHECK_SKIPPED_KEY)


@dataclass(frozen=True)
class DuplicateResolution:
    """使用者對某一張舊卡做的重複決定。決定綁著舊卡：查到的換了一張，就不能沿用。

    The user's decision about one specific existing card.

    Attributes:
        decision: 覆蓋或允許重複。The decision.
        note_id: 決定針對的舊卡。The old card it is about.
    """

    decision: DuplicateDecision
    note_id: int

    @classmethod
    def from_input(cls, draft_input: Mapping[str, object] | None) -> DuplicateResolution | None:
        """草稿輸入裡存的決定；沒有或不完整時為 None。The decision stored in a draft input.

        Args:
            draft_input: 草稿的輸入。The draft input.

        Returns:
            決定，或 None。The decision, or None.
        """
        values = draft_input or {}
        decision = values.get(DUPLICATE_DECISION_KEY)
        note_id = values.get(DUPLICATE_NOTE_KEY)
        if decision in ("overwrite", "allow") and isinstance(note_id, int):
            return cls(decision, note_id)  # type: ignore[arg-type]
        return None

    def into_input(self, draft_input: Mapping[str, object]) -> dict[str, object]:
        """寫進草稿輸入。Stores the decision in a draft input.

        Args:
            draft_input: 不含系統鍵的輸入。The input without system keys.

        Returns:
            新的輸入。The new input.
        """
        return {**draft_input, DUPLICATE_DECISION_KEY: self.decision, DUPLICATE_NOTE_KEY: self.note_id}

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from pydantic import BaseModel

    from app.services.card_service import CardService
    from app.services.relation_service import RelationService


class BaseHandler(ABC):
    """卡片任務處理器的基底類別。

    Base class for card task handlers.

    每個 Handler 負責一種卡片（成組的卡片由同一個 handler 負責多個筆記類型）。標準實作寫好不代表開放：
    開不開放由 ``CAPABILITIES`` 決定，註冊表在註冊時檢查宣告是否有實作支撐。

    Each handler owns one kind of card. A standard implementation being
    present does not mean it is offered: ``CAPABILITIES`` decides, and the
    registry checks the declaration against the implementation.

    Attributes:
        CAPABILITIES: 服務層能對這個 handler 做什麼（D2）。預設只開放讀取。What the
            service layer may do; reading only by default.
        GRAPH: 圖譜節點取哪些欄位（D12）。Which fields graph nodes use.
        RELATION_LABEL_FIELD: 關聯表用來辨認節點的文字欄位；有宣告時，標準修改會在它改變時同步
            關聯表兩端的標籤（D12）。The field relations use as node text.
        JSON_FIELDS: JSON 欄位名稱；建卡與改欄位都不能直接寫（handlers 計畫書 D3）。JSON field names.
        JSON_FIELD_SPECS: 能在網頁以表單編輯的 JSON 欄位與格式（json_field_editor 計畫 D1）；
            要搭配 ``CAPABILITIES.update_json``。Editable JSON fields and their formats.
        MEDIA_FIELDS: 媒體欄位 → 種類（``audio``、``image``；media_upload 計畫書 D3）。經 :meth:`field_texts`
            的寫入（標準建立、``update_fields``）只能填 ``POST /media`` 命名格式的檔名、原值或空白，寫入時包成
            ``[sound:檔名]`` 或 ``<img src="檔名">``；網頁的欄位彈窗顯示上傳元件，標準建立的表單也會列出並
            標 ``x-ui``。不套用的地方：覆寫 ``CREATE_PARAMS`` 的建卡（要自己在參數標 ``x-ui``、組欄位，
            見計畫書 §6.4），以及 handler 自己的具名動作（例如人脈卡 TG 用的 ``set_photo``，另有檢查）。
            Media fields and their kinds.
        CREATE_TAGS: 標準建立加上的標籤。Tags the standard create adds.
        CREATE_PARAMS: 覆寫建立的 handler 的建立參數模型（D3）；服務層以它驗證參數、產生網頁表單。
            標準建立不用它（參數就是筆記欄位）。The create parameter model of a
            handler overriding ``execute_create``.
        SEARCH_FIELDS: 網頁欄位搜尋的欄位按鈕（要搭配 ``CAPABILITIES.search``）；不宣告時由
            :meth:`search_fields` 從 ``CREATE_PARAMS`` 與 ``JSON_FIELD_SPECS`` 自動產生。
            The web field-search buttons; derived when not declared.
        DRAFT_PARAMS: 網頁預覽流程（``create.flow="preview"``）向 LLM 要草稿的輸入模型；服務層以它驗證
            輸入、產生網頁表單，再交給 :meth:`execute_draft`。The draft input model of the
            web preview flow.
    """

    CAPABILITIES: ClassVar[HandlerCapabilities] = HandlerCapabilities()
    GRAPH: ClassVar[GraphSpec] = GraphSpec()
    RELATION_LABEL_FIELD: ClassVar[str | None] = None
    JSON_FIELDS: ClassVar[tuple[str, ...]] = ()
    JSON_FIELD_SPECS: ClassVar[Mapping[str, JsonFieldSpec]] = {}
    MEDIA_FIELDS: ClassVar[Mapping[str, MediaKind]] = {}
    CREATE_TAGS: ClassVar[tuple[str, ...]] = ("HandlerGenerated",)
    CREATE_PARAMS: ClassVar[type[BaseModel] | None] = None
    SEARCH_FIELDS: ClassVar[tuple[SearchField, ...]] = ()
    DRAFT_PARAMS: ClassVar[type[BaseModel] | None] = None

    def search_fields(self) -> tuple[SearchField, ...]:
        """網頁欄位搜尋的欄位按鈕：有宣告 ``SEARCH_FIELDS`` 就用它；否則依建立參數（媒體欄位除外）
        與能以表單編輯的 JSON 欄位自動產生，一個欄位一顆按鈕。

        The field-search buttons: ``SEARCH_FIELDS`` when declared, otherwise one
        per create parameter (media excluded) and editable JSON field.

        Returns:
            欄位按鈕；沒有可用的欄位時為空。The buttons, possibly empty.
        """
        if self.SEARCH_FIELDS:
            return self.SEARCH_FIELDS
        fields: list[SearchField] = []
        if self.CREATE_PARAMS is not None:
            for name, info in self.CREATE_PARAMS.model_fields.items():
                if name not in self.MEDIA_FIELDS:
                    fields.append(SearchField(name, info.title or name, (name,)))
        fields.extend(SearchField(name, spec.title, (name,)) for name, spec in self.JSON_FIELD_SPECS.items())
        return tuple(fields)

    @property
    @abstractmethod
    def handler_name(self) -> str:
        """處理器名稱，出現在網址裡 (例如: 'speaking_coach')。

        Handler name used in URLs (e.g. 'speaking_coach').
        """

    @property
    @abstractmethod
    def title(self) -> str:
        """中文名稱，前端直接顯示 (例如: '對話卡')。

        Display title (e.g. '對話卡').
        """

    @property
    @abstractmethod
    def supported_models(self) -> list[str]:
        """這個處理器負責的 Anki 筆記類型。

        The Anki note types this handler owns.
        """

    # =========================================================================
    # 標準 CRUD（單一筆記）
    # Standard CRUD (single note)
    # =========================================================================

    async def execute_create(
        self,
        card_service: CardService,
        relation_service: RelationService,
        deck_name: str,
        model_name: str,
        parameters: dict[str, object],
    ) -> list[int]:
        """標準建立：手動填表，``parameters`` 就是 ``{欄位名: 值}``（D3）。

        Standard create: ``parameters`` maps field names to values.

        欄位是否存在、首欄位是否重複由 ``CardService.create_note`` 檢查；沒填的欄位留空。
        媒體欄位填 ``POST /media`` 回傳的檔名，寫入時包成 Anki 的寫法（見 :meth:`field_texts`）。

        Args:
            card_service: 卡片服務。Card service.
            relation_service: 關聯服務（標準建立不用）。Relation service (unused).
            deck_name: 目標牌組，不存在時自動建立。Target deck, created if missing.
            model_name: 筆記類型（服務層已確認是 ``create.note_type``）。The note type.
            parameters: 欄位值。Field values.

        Returns:
            建立的 Note ID（一律是陣列，成組卡片一次會建多張）。The created note IDs.

        Raises:
            FieldNotEditableError: 填了 JSON 欄位。When a JSON field is given.
            MediaFieldInvalidError: 媒體欄位填的不是上傳取得的檔名。A media field without an uploaded name.
            FieldMismatchError: 欄位不屬於這個筆記類型。On unknown fields.
            DuplicateCardError: 首欄位重複。On a duplicate first field.
        """
        self.reject_locked_fields(parameters)
        fields = self.field_texts(parameters)
        note_id = await card_service.create_note(
            deck_name=deck_name,
            model_name=model_name,
            fields=fields,
            tags=list(self.CREATE_TAGS),
        )
        return [note_id]

    async def execute_draft(self, parameters: dict[str, object], existing_tags: list[str]) -> dict[str, object]:
        """網頁預覽流程的草稿：依使用者的輸入（已經 ``DRAFT_PARAMS`` 驗證）請 LLM 生成，不寫入 Anki。

        Draft for the web preview flow, generated from the validated input; nothing is written.

        回傳值是預先填好的建立參數（``CREATE_PARAMS`` 的形狀）：網頁讓使用者勾選、修改後，原樣送回
        建卡端點。另可帶 ``tags`` 鍵（建議的標籤），服務層取出來整理成 ``suggested_tags``。

        Args:
            parameters: ``DRAFT_PARAMS`` 驗證後的輸入。The validated input.
            existing_tags: 這種卡片用過的標籤（依次數排序、不含系統標籤），給 LLM 優先沿用。
                Tags already used by this kind of card, most used first.

        Returns:
            建立參數的草稿（可帶 ``tags``）。The draft create parameters, optionally with ``tags``.
        """
        raise NotImplementedError(f"{self.handler_name} 沒有草稿流程")

    async def find_draft_duplicate(
        self, card_service: CardService, parameters: Mapping[str, object]
    ) -> dict[str, object] | None:
        """生成草稿之前查 Anki 有沒有建過同一張卡（呼叫 LLM 之前，省下不必要的生成）。預設不查。

        Look for an existing card before generating a draft; none by default.

        Args:
            card_service: 卡片服務。Card service.
            parameters: ``DRAFT_PARAMS`` 驗證後的輸入。The validated draft input.

        Returns:
            舊卡的摘要（至少有 ``note_id``，其餘給網頁顯示），沒有重複時為 None。The old card, or None.

        Raises:
            AnkiConnectError: 查詢失敗（呼叫端決定要不要略過）。When the lookup fails.
        """
        return None

    async def after_create(
        self, card_service: CardService, parameters: Mapping[str, object], note_ids: list[int]
    ) -> str | None:
        """建卡成功之後、同步之前的後續處理（例如覆蓋時刪掉舊卡），刪除也會在同一次同步推上 AnkiWeb。預設不做事。

        Follow-up after a successful create, before the sync; nothing by default.

        新卡已經建立：這裡失敗時不能拋錯（使用者會以為沒建成而重建），改回傳提醒，接在建卡回應的
        ``sync_warning``。

        Args:
            card_service: 卡片服務。Card service.
            parameters: 建立參數（``CREATE_PARAMS`` 驗證後）。The create parameters.
            note_ids: 剛建立的筆記。The created notes.

        Returns:
            給使用者的提醒，沒有時為 None。A warning, or None.
        """
        return None

    async def execute_read(
        self,
        card_service: CardService,
        relation_service: RelationService,
        note_id: int,
    ) -> dict[str, object]:
        """標準讀取：單一筆記（形狀同通用讀取，§4.3）。成組卡片之後會覆寫成附上整組。

        Standard read of a single note, shaped like the generic read.

        Args:
            card_service: 卡片服務。Card service.
            relation_service: 關聯服務（標準讀取不用）。Relation service (unused).
            note_id: 筆記 ID。The note ID.

        Returns:
            筆記內容，見 ``note_detail``。The note, see ``note_detail``.

        Raises:
            NoteNotFoundError: 筆記不存在。When the note does not exist.
        """
        return note_detail(await card_service.get_note(note_id))

    async def execute_update(
        self,
        card_service: CardService,
        relation_service: RelationService,
        note_id: int,
        parameters: dict[str, object],
    ) -> None:
        """標準修改：只支援 ``action="update_fields"``，只寫有變的欄位。

        Standard update: only ``update_fields``, writing changed fields only.

        ``RELATION_LABEL_FIELD`` 有變時，同步更新關聯表裡這張筆記兩端的標籤（D12）。
        其他動作由覆寫的 handler 實作（例如口說卡的 ``add_audio``）。

        Args:
            card_service: 卡片服務。Card service.
            relation_service: 關聯服務。Relation service.
            note_id: 筆記 ID。The note ID.
            parameters: ``{"action": "update_fields", "fields": {欄位名: 值}}``。

        Raises:
            HandlerOperationUnsupportedError: 不是 ``update_fields``。For other actions.
            InvalidNoteFieldsError: ``fields`` 不是 ``{欄位名: 值}``。When fields is not a mapping.
            FieldNotEditableError: 改了 JSON 欄位。When a JSON field is given.
            MediaFieldInvalidError: 媒體欄位填的不是上傳取得的檔名（也不是原本的值）。A media field
                without an uploaded name.
            FieldMismatchError: 欄位不屬於這個筆記類型。On unknown fields.
            NoteNotFoundError: 筆記不存在。When the note does not exist.
        """
        action = parameters.get("action")
        if action != "update_fields":
            raise HandlerOperationUnsupportedError(f"{self.title}不支援動作：{action}")
        fields = parameters.get("fields")
        if not isinstance(fields, dict):
            raise InvalidNoteFieldsError("update_fields 的 fields 必須是 {欄位名: 值}")
        self.reject_locked_fields(fields)

        note = await card_service.get_note(note_id)
        current = note_field_values(note)
        changed = {
            name: text for name, text in self.field_texts(fields, current).items() if text != current.get(name)
        }
        if not changed:
            return
        await card_service.update_note_fields(note_id, changed, model_name=str(note["modelName"]))

        label_field = self.RELATION_LABEL_FIELD
        if label_field and label_field in changed:
            await relation_service.update_labels_for_note(note_id, changed[label_field].strip())

    async def execute_delete(
        self,
        card_service: CardService,
        relation_service: RelationService,
        note_id: int,
    ) -> None:
        """標準刪除：先刪筆記、再刪關聯。

        Standard delete: the note first, then its relations.

        反過來的話，筆記刪除失敗時關聯已經沒了、無法復原；先刪筆記的話，萬一關聯刪除失敗，
        留下的孤兒關聯會被 ``RelationService.sync_with_anki`` 清掉。

        Args:
            card_service: 卡片服務。Card service.
            relation_service: 關聯服務。Relation service.
            note_id: 筆記 ID。The note ID.
        """
        await card_service.delete_note(note_id)
        await relation_service.delete_relations_for_note(note_id)

    # =========================================================================
    # 查詢與標準圖譜
    # Query and the standard graph
    # =========================================================================

    async def query_notes(
        self,
        card_service: CardService,
        deck_name: str | None = None,
        note_type: str | None = None,
    ) -> list[int]:
        """handler 的查詢：它負責的筆記類型、在指定牌組裡的筆記。成組卡片之後可覆寫成只取母卡。

        The handler's query: notes of its note types in the given deck.

        Args:
            card_service: 卡片服務。Card service.
            deck_name: 牌組（含子牌組）；None 表示全部。The deck, or None for all.
            note_type: 只看其中一種筆記類型；None 表示全部。One of its note types, or None.

        Returns:
            筆記 ID。The note IDs.

        Raises:
            InvalidNoteQueryError: ``note_type`` 不歸這個 handler。When the note type
                is not this handler's.
        """
        return await card_service.find_notes(note_types_query(self.note_types_for(note_type), deck_name))

    async def execute_read_graph(
        self,
        card_service: CardService,
        relation_service: RelationService,
        deck_name: str | None = None,
        *,
        note_type: str | None = None,
        label_field: str | None = None,
    ) -> dict[str, object]:
        """標準圖譜：每張筆記一個節點，沒有連線（D12）。

        Standard graph: one node per note, no links.

        節點名稱依序取 ``label_field``、``GRAPH.default_label``；沒指定或該筆記的欄位為空時顯示 nid。
        有宣告 ``GRAPH.subtitle`` 時節點另帶 ``translation``（副標）。

        Args:
            card_service: 卡片服務。Card service.
            relation_service: 關聯服務（標準圖譜不用）。Relation service (unused).
            deck_name: 牌組；None 表示全部。The deck, or None for all.
            note_type: 只看其中一種筆記類型。One of the handler's note types.
            label_field: 節點名稱用的欄位。The field to label nodes with.

        Returns:
            ``{"nodes", "links", "label_fields", "label_field"}``；``label_fields`` 是可選的
            名稱欄位，``label_field`` 是實際採用的（None 表示 nid）。The graph, the label
            fields to choose from and the one used.

        Raises:
            InvalidNoteQueryError: ``note_type`` 不歸這個 handler，或 ``label_field``
                不是這些筆記類型的欄位。On a note type or label field that does not apply.
        """
        note_types = self.note_types_for(note_type)
        label_fields = await _field_names(card_service, note_types)
        if label_field is not None and label_field not in label_fields:
            raise InvalidNoteQueryError(f"{label_field} 不是 {'、'.join(note_types)} 的欄位")
        used = label_field or (
            self.GRAPH.default_label if self.GRAPH.default_label in label_fields else None
        )

        note_ids = await self.query_notes(card_service, deck_name, note_type)
        notes = await card_service.anki_client.get_notes_info(note_ids) if note_ids else []
        nodes: list[dict[str, object]] = []
        for note in notes:
            nid = str(note.noteId)
            label = clean_html(field_value(note.fields, used)).strip() if used else ""
            node: dict[str, object] = {
                "id": nid,
                "note_id": note.noteId,
                "note_type": note.modelName,
                "label": label or nid,
            }
            if self.GRAPH.subtitle:
                node["translation"] = clean_html(field_value(note.fields, self.GRAPH.subtitle))
            nodes.append(node)
        return {"nodes": nodes, "links": [], "label_fields": label_fields, "label_field": used}

    # =========================================================================
    # 共用檢查
    # Shared checks
    # =========================================================================

    def note_types_for(self, note_type: str | None) -> list[str]:
        """查詢範圍內的筆記類型：指定的那一種（必須歸這個 handler），或全部。

        The note types in scope: the given one, which must be this handler's,
        or all of them.

        Args:
            note_type: 只看其中一種；None 表示全部。One note type, or None for all.

        Returns:
            筆記類型。The note types.

        Raises:
            InvalidNoteQueryError: 不歸這個 handler。When it is not this handler's.
        """
        if note_type is None:
            return list(self.supported_models)
        if note_type not in self.supported_models:
            raise InvalidNoteQueryError(f"{note_type} 不歸「{self.title}」負責")
        return [note_type]

    async def after_json_update(
        self, card_service: CardService, note_id: int, field_name: str, items: list[object]
    ) -> None:
        """網頁整欄寫入 JSON 欄位之後（同步之前）呼叫；預設不做事。

        Called after the web writes a whole JSON field, before the sync.
        覆寫的 handler 可以維護相關欄位（例如人脈卡的最後聯絡日）。

        Args:
            card_service: 卡片服務。Card service.
            note_id: 筆記 ID。The note.
            field_name: 寫入的 JSON 欄位。The field written.
            items: 寫入的清單。The items written.
        """

    def reject_locked_fields(self, field_names: Iterable[str]) -> None:
        """宣告為 JSON 的欄位不能直接以文字寫入（handlers 計畫書 D3）。媒體欄位不在這裡擋：它能填上傳取得的
        檔名，由 :meth:`field_texts` 檢查。

        JSON fields cannot be written as plain text. Media fields are checked by
        :meth:`field_texts` instead.

        Raises:
            FieldNotEditableError: 含有 JSON 欄位。When any is present.
        """
        locked = [name for name in field_names if name in self.JSON_FIELDS]
        if locked:
            raise FieldNotEditableError(f"這些欄位不能直接修改：{'、'.join(locked)}")

    def field_texts(self, values: Mapping[str, object], current: Mapping[str, str] | None = None) -> dict[str, str]:
        """把送來的欄位值轉成要寫進 Anki 的字串；媒體欄位另外檢查並包成 Anki 的寫法（media_upload 計畫書 D3）。

        The texts to write for the given field values; media fields are checked and wrapped.

        Args:
            values: ``{欄位名: 值}``。Field name to submitted value.
            current: 修改時欄位原本的值（媒體欄位送回原值代表不變）；建立時省略。The current values.

        Returns:
            ``{欄位名: 要寫入的字串}``。Field name to the text to write.

        Raises:
            MediaFieldInvalidError: 媒體欄位填的不是 ``POST /media`` 產生的檔名，或種類不符。
                A media field without an uploaded name of its kind.
        """
        existing = current or {}
        return {
            name: (
                media_field_text(name, self.MEDIA_FIELDS[name], value, existing.get(name, ""))
                if name in self.MEDIA_FIELDS
                else field_text(value)
            )
            for name, value in values.items()
        }


# =============================================================================
# 共用工具
# Helpers
# =============================================================================


def field_text(value: object) -> str:
    """欄位值轉成 Anki 的字串；None 視為空字串。A field value as Anki text."""
    return "" if value is None else str(value)


def clean_html(html_str: object) -> str:
    """移除 Anki 可能加入的 HTML 標籤與跳脫字元。

    Strip HTML tags and escape entities that Anki may have added.

    Args:
        html_str: 可能包含 HTML 的原始字串。Raw string that may contain HTML.

    Returns:
        清洗後的純文字。Cleaned plain text.
    """
    text = re.sub(r"<[^>]+>", "", str(html_str))
    return text.replace("&quot;", '"').replace("&nbsp;", " ")


def note_field_values(note: Mapping[str, object]) -> dict[str, str]:
    """``CardService.get_note`` 結果的欄位值。The field values of a note dict.

    Args:
        note: ``CardService.get_note`` 的回傳。A note from ``CardService.get_note``.

    Returns:
        ``{欄位名: 值}``。Field name to value.
    """
    fields = note.get("fields")
    if not isinstance(fields, dict):
        return {}
    return {name: field_text(field.get("value")) for name, field in fields.items()}


def note_detail(note: Mapping[str, object]) -> dict[str, object]:
    """把 AnkiConnect 的筆記轉成 API 的形狀（§4.3），欄位依筆記類型的順序排成陣列。

    Converts an AnkiConnect note into the API shape, fields in model order.

    Args:
        note: ``CardService.get_note`` 的回傳（camelCase）。A note from ``CardService.get_note``.

    Returns:
        ``{note_id, note_type, tags, cards, fields: [{name, value}]}``。The note.
    """
    fields = note.get("fields")
    ordered = sorted(fields.items(), key=lambda item: item[1].get("order", 0)) if isinstance(fields, dict) else []
    return {
        "note_id": note["noteId"],
        "note_type": note["modelName"],
        "tags": list(note.get("tags") or []),
        "cards": list(note.get("cards") or []),
        "fields": [{"name": name, "value": field_text(field.get("value"))} for name, field in ordered],
    }


def field_value(fields: Mapping[str, Mapping[str, object]], name: str | None) -> object:
    """``notesInfo`` 欄位的值；沒指定或欄位不存在時為空字串。A notesInfo field value, or empty."""
    if not name or name not in fields:
        return ""
    return fields[name].get("value", "")


async def _field_names(card_service: CardService, note_types: list[str]) -> list[str]:
    """這些筆記類型的欄位名（向 Anki 查，依順序、不重複）。The note types' field names."""
    names: list[str] = []
    for note_type in note_types:
        for name in await card_service.anki_client.get_model_field_names(note_type):
            if name not in names:
                names.append(name)
    return names
