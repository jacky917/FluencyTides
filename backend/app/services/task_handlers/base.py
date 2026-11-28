"""Base task handler module.

基礎任務處理器（Base Task Handler）模組。

``BaseHandler`` provides the standard CRUD and the standard graph for single-note cards; handlers declare
capabilities and fields as class attributes and override only where they need different behavior (handlers
plan D10, D12).

``BaseHandler`` 提供單一筆記卡片的標準 CRUD 與標準圖譜；各 handler 以類別屬性宣告能力與欄位，
需要不同行為時才覆寫（handlers 計畫書 D10、D12）。
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

#: The decision on a duplicate found before generating: ``overwrite`` deletes the old card once the new one is
#: created, ``allow`` allows the duplicate.
#: 生成前發現重複時的決定：``overwrite`` 新卡建好後刪掉舊卡、``allow`` 允許重複。
DuplicateDecision = Literal["overwrite", "allow"]

# 草稿輸入裡由系統放進去的鍵（不是使用者的輸入，``NoteService.resolve_draft_duplicate`` 寫入、
# ``execute_draft`` 讀）。草稿只在記憶體，存在輸入裡就跟著草稿走，重試時也還在。
#: The key of the duplicate decision in a draft input (values: see ``DuplicateDecision``).
#: 重複的決定（值見 ``DuplicateDecision``）。
DUPLICATE_DECISION_KEY = "duplicate_decision"
#: The key of the ID of the old card the decision is about.
#: 這個決定針對的舊卡 ID。
DUPLICATE_NOTE_KEY = "duplicate_note_id"
#: The key of why the duplicate check was skipped after the lookup failed (the web preview shows a warning).
#: 查重複失敗、略過了檢查時的原因（網頁的預覽顯示提醒）。
DUPLICATE_CHECK_SKIPPED_KEY = "duplicate_check_skipped"
#: The three system keys above.
#: 以上三個系統鍵。
DRAFT_SYSTEM_KEYS = (DUPLICATE_DECISION_KEY, DUPLICATE_NOTE_KEY, DUPLICATE_CHECK_SKIPPED_KEY)


@dataclass(frozen=True)
class DuplicateResolution:
    """The user's duplicate decision about one specific existing card.

    使用者對某一張舊卡做的重複決定。

    The decision is bound to that old card: if the lookup finds a different card, it cannot be reused.

    決定綁著舊卡：查到的換了一張，就不能沿用。

    Attributes:
        decision: overwrite or allow the duplicate
            覆蓋或允許重複
        note_id: the old card the decision is about
            決定針對的舊卡
    """

    decision: DuplicateDecision
    note_id: int

    @classmethod
    def from_input(cls, draft_input: Mapping[str, object] | None) -> DuplicateResolution | None:
        """Return the decision stored in a draft input.

        回傳草稿輸入裡存的決定。

        Args:
            draft_input: the draft input; may be ``None``
                草稿的輸入，可為 ``None``

        Returns:
            the decision, or ``None`` when it is missing or incomplete
            決定；沒有或不完整時為 ``None``
        """
        values = draft_input or {}
        decision = values.get(DUPLICATE_DECISION_KEY)
        note_id = values.get(DUPLICATE_NOTE_KEY)
        if decision in ("overwrite", "allow") and isinstance(note_id, int):
            return cls(decision, note_id)  # type: ignore[arg-type]
        return None

    def into_input(self, draft_input: Mapping[str, object]) -> dict[str, object]:
        """Return a copy of a draft input with this decision stored in it.

        回傳寫進此決定的草稿輸入副本。

        Args:
            draft_input: the input without system keys
                不含系統鍵的輸入

        Returns:
            the new input
            新的輸入
        """
        return {**draft_input, DUPLICATE_DECISION_KEY: self.decision, DUPLICATE_NOTE_KEY: self.note_id}

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from pydantic import BaseModel

    from app.services.card_service import CardService
    from app.services.relation_service import RelationService
    from app.services.task_handlers.shared.recording_spec import RecordingEvaluation


class BaseHandler(ABC):
    """Base class for card task handlers.

    卡片任務處理器的基底類別。

    Each handler owns one kind of card (a card group is owned by one handler covering several note types). A
    standard implementation being present does not mean it is offered: ``CAPABILITIES`` decides, and the
    registry checks at registration time that the declaration is backed by an implementation.

    每個 Handler 負責一種卡片（成組的卡片由同一個 handler 負責多個筆記類型）。標準實作寫好不代表開放：
    開不開放由 ``CAPABILITIES`` 決定，註冊表在註冊時檢查宣告是否有實作支撐。

    Attributes:
        CAPABILITIES: what the service layer may do with this handler (D2); reading only by default
            服務層能對這個 handler 做什麼（D2）；預設只開放讀取
        GRAPH: which fields graph nodes use (D12)
            圖譜節點取哪些欄位（D12）
        RELATION_LABEL_FIELD: the text field relations use to identify a node. When declared, the standard
            update syncs the labels at both ends of the relations when it changes (D12).
            關聯表用來辨認節點的文字欄位；有宣告時，標準修改會在它改變時同步關聯表兩端的標籤（D12）。
        JSON_FIELDS: the JSON field names; neither create nor field updates may write them directly (handlers
            plan D3)
            JSON 欄位名稱；建卡與改欄位都不能直接寫（handlers 計畫書 D3）
        JSON_FIELD_SPECS: the JSON fields editable through a web form and their formats (json_field_editor plan
            D1); must go with ``CAPABILITIES.update_json``
            能在網頁以表單編輯的 JSON 欄位與格式（json_field_editor 計畫 D1）；要搭配 ``CAPABILITIES.update_json``
        MEDIA_FIELDS: media field to kind (``audio``, ``image``; media_upload plan D3). Writes through
            :meth:`field_texts` (standard create, ``update_fields``) accept only a file name in the
            ``POST /media`` naming format, the original value, or blank, and wrap it as ``[sound:name]`` or
            ``<img src="name">``; the web field dialog shows an upload widget, and the standard create form also
            lists the field marked with ``x-ui``. It does not apply to a create that overrides
            ``CREATE_PARAMS`` (the parameters must carry ``x-ui`` and build the fields themselves, see plan
            §6.4), nor to a handler's own named actions (for example the person card's TG ``set_photo``, which
            has its own checks).
            媒體欄位 → 種類（``audio``、``image``；media_upload 計畫書 D3）。經 ``field_texts`` 的寫入（標準建立、
            ``update_fields``）只能填 ``POST /media`` 命名格式的檔名、原值或空白，寫入時包成 ``[sound:檔名]`` 或
            ``<img src="檔名">``；網頁的欄位彈窗顯示上傳元件，標準建立的表單也會列出並標 ``x-ui``。不套用的
            地方：覆寫 ``CREATE_PARAMS`` 的建卡（要自己在參數標 ``x-ui``、組欄位，見計畫書 §6.4），以及
            handler 自己的具名動作（例如人脈卡 TG 用的 ``set_photo``，另有檢查）。
        CREATE_TAGS: the tags the standard create adds
            標準建立加上的標籤
        CREATE_PARAMS: the create parameter model of a handler that overrides the create (D3); the service layer
            validates parameters and builds the web form with it. The standard create does not use it (its
            parameters are the note fields).
            覆寫建立的 handler 的建立參數模型（D3）；服務層以它驗證參數、產生網頁表單。標準建立不用它
            （參數就是筆記欄位）。
        SEARCH_FIELDS: the field buttons of the web field search (must go with ``CAPABILITIES.search``); when
            not declared, :meth:`search_fields` derives them from ``CREATE_PARAMS`` and ``JSON_FIELD_SPECS``
            網頁欄位搜尋的欄位按鈕（要搭配 ``CAPABILITIES.search``）；不宣告時由 ``search_fields`` 從
            ``CREATE_PARAMS`` 與 ``JSON_FIELD_SPECS`` 自動產生
        DRAFT_PARAMS: the input model the web preview flow (``create.flow="preview"``) uses to ask the LLM for a
            draft; the service layer validates the input and builds the web form with it, then passes it to
            :meth:`execute_draft`
            網頁預覽流程（``create.flow="preview"``）向 LLM 要草稿的輸入模型；服務層以它驗證輸入、產生網頁
            表單，再交給 ``execute_draft``
        RECORDING_EVAL: how this handler's cards are recorded and scored (the route key, and per field the
            prompt, reference answers and target language); ``None`` means the cards cannot be recorded
            這個 handler 的卡片怎麼錄音評分（用途鍵，以及每個欄位的提示詞、參考答案與目標語言）；
            ``None`` 表示卡片不能錄音評分
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
    RECORDING_EVAL: ClassVar[RecordingEvaluation | None] = None

    def search_fields(self) -> tuple[SearchField, ...]:
        """Return the field buttons of the web field search.

        回傳網頁欄位搜尋的欄位按鈕。

        ``SEARCH_FIELDS`` is used when declared; otherwise the buttons are derived from the create parameters
        (media fields excluded) and the JSON fields editable through a form, one button per field.

        有宣告 ``SEARCH_FIELDS`` 就用它；否則依建立參數（媒體欄位除外）與能以表單編輯的 JSON 欄位自動產生，
        一個欄位一顆按鈕。

        Returns:
            the field buttons; empty when no field is available
            欄位按鈕；沒有可用的欄位時為空
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
        """The handler name used in URLs, for example ``"speaking_coach"``.

        處理器名稱，出現在網址裡，例如 ``"speaking_coach"``。
        """

    @property
    @abstractmethod
    def title(self) -> str:
        """The Chinese display title shown as is by the frontend, for example ``"對話卡"``.

        中文名稱，前端直接顯示，例如 ``"對話卡"``。
        """

    @property
    @abstractmethod
    def supported_models(self) -> list[str]:
        """The Anki note types this handler owns.

        這個處理器負責的 Anki 筆記類型。
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
        """Create a note from a manually filled form, where ``parameters`` maps field names to values (D3).

        標準建立：手動填表，``parameters`` 就是 ``{欄位名: 值}``（D3）。

        ``CardService.create_note`` checks that the fields exist and that the first field is not a duplicate;
        fields left out stay empty. Media fields take the file name returned by ``POST /media`` and are wrapped
        in Anki's syntax when written (see :meth:`field_texts`).

        欄位是否存在、首欄位是否重複由 ``CardService.create_note`` 檢查；沒填的欄位留空。
        媒體欄位填 ``POST /media`` 回傳的檔名，寫入時包成 Anki 的寫法（見 ``field_texts``）。

        Args:
            card_service: the card service
                卡片服務
            relation_service: the relation service (unused by the standard create)
                關聯服務（標準建立不用）
            deck_name: the target deck, created if missing
                目標牌組，不存在時自動建立
            model_name: the note type (the service layer has confirmed it is ``create.note_type``)
                筆記類型（服務層已確認是 ``create.note_type``）
            parameters: the field values
                欄位值

        Returns:
            the created note IDs (always a list, since a card group creates several at once)
            建立的 Note ID（一律是陣列，成組卡片一次會建多張）

        Raises:
            DuplicateCardError: if the first field is a duplicate
                若首欄位重複
            FieldMismatchError: if a field does not belong to this note type
                若欄位不屬於這個筆記類型
            FieldNotEditableError: if a JSON field is given
                若填了 JSON 欄位
            MediaFieldInvalidError: if a media field is not a file name obtained from an upload
                若媒體欄位填的不是上傳取得的檔名
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

    async def draft_context(self, card_service: CardService, parameters: Mapping[str, object]) -> dict[str, object]:
        """Read from Anki what the LLM should know besides the user's input, before generating a draft.

        生成草稿之前，從 Anki 讀出使用者輸入以外、LLM 應該知道的資訊。

        Runs in the background together with ``execute_draft`` (only Anki reads, no DB session). The trilingual
        card uses it for where the new card will sit: the full deck path, the parent card, the titles already in
        that deck. The base implementation returns nothing. The service layer treats an Anki failure here like a
        missing context and still generates.

        和 ``execute_draft`` 一起在背景執行（只讀 Anki，不用 DB session）。三語卡用它讀出新卡會放在哪裡：完整
        牌組路徑、上層卡、那個牌組已有的標題。預設不回傳任何東西。這裡 Anki 失敗時，服務層當成沒有情境，照常生成。

        Args:
            card_service: the card service
                卡片服務
            parameters: the input validated by ``DRAFT_PARAMS``
                ``DRAFT_PARAMS`` 驗證後的輸入

        Returns:
            the context passed to ``execute_draft`` as ``context``
            以 ``context`` 傳給 ``execute_draft`` 的情境
        """
        return {}

    async def execute_draft(
        self,
        parameters: dict[str, object],
        existing_tags: list[str],
        context: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Generate a draft for the web preview flow with the LLM, without writing to Anki.

        網頁預覽流程的草稿：依使用者的輸入（已經 ``DRAFT_PARAMS`` 驗證）請 LLM 生成，不寫入 Anki。

        The return value is prefilled create parameters (shaped like ``CREATE_PARAMS``): the web lets the user
        tick and edit them, then sends them back unchanged to the create endpoint. It may also carry a ``tags``
        key (suggested tags), which the service layer takes out and tidies into ``suggested_tags``. The base
        implementation has no draft flow and raises ``NotImplementedError``.

        回傳值是預先填好的建立參數（``CREATE_PARAMS`` 的形狀）：網頁讓使用者勾選、修改後，原樣送回
        建卡端點。另可帶 ``tags`` 鍵（建議的標籤），服務層取出來整理成 ``suggested_tags``。基底實作沒有
        草稿流程，會拋出 ``NotImplementedError``。

        Args:
            parameters: the input validated by ``DRAFT_PARAMS``
                ``DRAFT_PARAMS`` 驗證後的輸入
            existing_tags: tags this kind of card has used (sorted by count, system tags excluded), for the LLM
                to prefer reusing
                這種卡片用過的標籤（依次數排序、不含系統標籤），給 LLM 優先沿用
            context: what ``draft_context`` read; ``None`` or empty when there is none
                ``draft_context`` 讀到的情境；沒有時為 ``None`` 或空的

        Returns:
            the draft create parameters, optionally with ``tags``
            建立參數的草稿（可帶 ``tags``）
        """
        raise NotImplementedError(f"{self.handler_name} 沒有草稿流程")

    async def find_draft_duplicate(
        self, card_service: CardService, parameters: Mapping[str, object]
    ) -> dict[str, object] | None:
        """Look up whether Anki already has the same card before generating a draft.

        生成草稿之前查 Anki 有沒有建過同一張卡。

        This runs before calling the LLM, to save unnecessary generation. The base implementation does not look
        anything up.

        在呼叫 LLM 之前執行，省下不必要的生成。預設不查。

        Args:
            card_service: the card service
                卡片服務
            parameters: the input validated by ``DRAFT_PARAMS``
                ``DRAFT_PARAMS`` 驗證後的輸入

        Returns:
            a summary of the old card (at least ``note_id``, the rest for the web to display), or ``None`` when
            there is no duplicate
            舊卡的摘要（至少有 ``note_id``，其餘給網頁顯示）；沒有重複時為 ``None``

        Raises:
            AnkiConnectError: if the lookup fails (the caller decides whether to skip the check)
                若查詢失敗（呼叫端決定要不要略過）
        """
        return None

    async def resolve_create_deck(
        self, card_service: CardService, deck: str, parameters: Mapping[str, object]
    ) -> str:
        """Return the deck the new card goes into; runs after parameter validation and before ``execute_create``.

        回傳新卡要放的牌組；在參數驗證之後、``execute_create`` 之前執行。

        The base implementation keeps the chosen deck. A handler can derive another one from the parameters (the
        trilingual card puts a child card into ``<parent deck>::<parent title>``) and create it here; the create
        response reports the deck returned.

        預設沿用選的牌組。handler 可以依參數改成別的牌組（三語卡的子卡放進 ``<上層卡的牌組>::<上層卡的標題>``），
        需要時在這裡建好牌組；建卡回應回報的就是這裡回傳的牌組。

        Args:
            card_service: the card service
                卡片服務
            deck: the chosen deck (the fixed deck already applied)
                選的牌組（已套用固定牌組）
            parameters: the create parameters (validated by ``CREATE_PARAMS``)
                建立參數（``CREATE_PARAMS`` 驗證後）

        Returns:
            the deck to create the card in
            要建卡的牌組
        """
        return deck

    async def after_create(
        self, card_service: CardService, parameters: Mapping[str, object], note_ids: list[int]
    ) -> str | None:
        """Run follow-up work after a successful create and before the sync.

        建卡成功之後、同步之前的後續處理。

        For example, deleting the old card on an overwrite; the deletion is pushed to AnkiWeb in the same sync.
        The base implementation does nothing. The new card already exists, so a failure here must not raise
        (the user would think nothing was created and create it again); return a warning instead, which is
        appended to the create response's ``sync_warning``.

        例如覆蓋時刪掉舊卡，刪除也會在同一次同步推上 AnkiWeb。預設不做事。新卡已經建立：這裡失敗時
        不能拋錯（使用者會以為沒建成而重建），改回傳提醒，接在建卡回應的 ``sync_warning``。

        Args:
            card_service: the card service
                卡片服務
            parameters: the create parameters (validated by ``CREATE_PARAMS``)
                建立參數（``CREATE_PARAMS`` 驗證後）
            note_ids: the notes just created
                剛建立的筆記

        Returns:
            a warning for the user, or ``None`` when there is none
            給使用者的提醒；沒有時為 ``None``
        """
        return None

    async def execute_read(
        self,
        card_service: CardService,
        relation_service: RelationService,
        note_id: int,
    ) -> dict[str, object]:
        """Read a single note, shaped like the generic read (§4.3).

        標準讀取：單一筆記（形狀同通用讀取，§4.3）。

        Card groups will later override this to include the whole group.

        成組卡片之後會覆寫成附上整組。

        Args:
            card_service: the card service
                卡片服務
            relation_service: the relation service (unused by the standard read)
                關聯服務（標準讀取不用）
            note_id: the note ID
                筆記 ID

        Returns:
            the note, see ``note_detail``
            筆記內容，見 ``note_detail``

        Raises:
            NoteNotFoundError: if the note does not exist
                若筆記不存在
        """
        return note_detail(await card_service.get_note(note_id))

    async def execute_update(
        self,
        card_service: CardService,
        relation_service: RelationService,
        note_id: int,
        parameters: dict[str, object],
    ) -> None:
        """Update a note's fields; only ``action="update_fields"`` is supported, and only changed fields are written.

        標準修改：只支援 ``action="update_fields"``，只寫有變的欄位。

        When ``RELATION_LABEL_FIELD`` changes, the labels at both ends of this note's relations are updated
        too (D12). Other actions are implemented by overriding handlers (for example the speaking card's
        ``add_audio``).

        ``RELATION_LABEL_FIELD`` 有變時，同步更新關聯表裡這張筆記兩端的標籤（D12）。
        其他動作由覆寫的 handler 實作（例如口說卡的 ``add_audio``）。

        Args:
            card_service: the card service
                卡片服務
            relation_service: the relation service
                關聯服務
            note_id: the note ID
                筆記 ID
            parameters: ``{"action": "update_fields", "fields": {field name: value}}``
                ``{"action": "update_fields", "fields": {欄位名: 值}}``

        Raises:
            FieldMismatchError: if a field does not belong to this note type
                若欄位不屬於這個筆記類型
            FieldNotEditableError: if a JSON field is given
                若改了 JSON 欄位
            HandlerOperationUnsupportedError: if the action is not ``update_fields``
                若動作不是 ``update_fields``
            InvalidNoteFieldsError: if ``fields`` is not a ``{field name: value}`` mapping
                若 ``fields`` 不是 ``{欄位名: 值}``
            MediaFieldInvalidError: if a media field is neither a file name obtained from an upload nor its
                original value
                若媒體欄位填的不是上傳取得的檔名，也不是原本的值
            NoteNotFoundError: if the note does not exist
                若筆記不存在
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
        """Delete the note first, then its relations.

        標準刪除：先刪筆記、再刪關聯。

        In the reverse order, a failed note deletion would leave the relations already gone and unrecoverable;
        deleting the note first means that if the relation deletion fails, the orphaned relations left behind
        are cleaned up by ``RelationService.sync_with_anki``.

        反過來的話，筆記刪除失敗時關聯已經沒了、無法復原；先刪筆記的話，萬一關聯刪除失敗，
        留下的孤兒關聯會被 ``RelationService.sync_with_anki`` 清掉。

        Args:
            card_service: the card service
                卡片服務
            relation_service: the relation service
                關聯服務
            note_id: the note ID
                筆記 ID
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
        """Return the notes of this handler's note types in the given deck.

        handler 的查詢：它負責的筆記類型、在指定牌組裡的筆記。

        Card groups may later override this to return only the parent cards.

        成組卡片之後可覆寫成只取母卡。

        Args:
            card_service: the card service
                卡片服務
            deck_name: the deck (subdecks included); ``None`` means all
                牌組（含子牌組）；``None`` 表示全部
            note_type: only one of its note types; ``None`` means all
                只看其中一種筆記類型；``None`` 表示全部

        Returns:
            the note IDs
            筆記 ID

        Raises:
            InvalidNoteQueryError: if ``note_type`` is not this handler's
                若 ``note_type`` 不歸這個 handler
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
        depth: int = 1,
    ) -> dict[str, object]:
        """Return the standard graph: one node per note, with no links (D12).

        標準圖譜：每張筆記一個節點，沒有連線（D12）。

        Node labels take ``label_field``, then ``GRAPH.default_label`` (only if it is a field of these note
        types); when neither applies or the note's field is empty, the nid is shown. When ``GRAPH.subtitle`` is
        declared, each node also carries ``translation`` (the subtitle).

        節點名稱依序取 ``label_field``、``GRAPH.default_label``（須是這些筆記類型的欄位）；沒指定或該筆記的
        欄位為空時顯示 nid。有宣告 ``GRAPH.subtitle`` 時節點另帶 ``translation``（副標）。

        Args:
            card_service: the card service
                卡片服務
            relation_service: the relation service (unused by the standard graph)
                關聯服務（標準圖譜不用）
            deck_name: the deck; ``None`` means all
                牌組；``None`` 表示全部
            note_type: only one of the handler's note types; ``None`` means all
                只看其中一種筆記類型；``None`` 表示全部
            label_field: the field used for node labels
                節點名稱用的欄位
            depth: how many levels a tree graph expands (``layout="tree"``); unused by the standard graph
                樹狀圖譜展開的層數（``layout="tree"``）；標準圖譜不用

        Returns:
            ``{"nodes", "links", "label_fields", "label_field"}``, where ``label_fields`` are the selectable
            label fields and ``label_field`` is the one actually used (``None`` means the nid)
            ``{"nodes", "links", "label_fields", "label_field"}``；``label_fields`` 是可選的名稱欄位，
            ``label_field`` 是實際採用的（``None`` 表示 nid）

        Raises:
            InvalidNoteQueryError: if ``note_type`` is not this handler's, or ``label_field`` is not a field of
                these note types
                若 ``note_type`` 不歸這個 handler，或 ``label_field`` 不是這些筆記類型的欄位
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
        """Return the note types in scope: the given one (which must be this handler's), or all of them.

        回傳查詢範圍內的筆記類型：指定的那一種（必須歸這個 handler），或全部。

        Args:
            note_type: only this one; ``None`` means all
                只看其中一種；``None`` 表示全部

        Returns:
            the note types
            筆記類型

        Raises:
            InvalidNoteQueryError: if ``note_type`` is not this handler's
                若不歸這個 handler
        """
        if note_type is None:
            return list(self.supported_models)
        if note_type not in self.supported_models:
            raise InvalidNoteQueryError(f"{note_type} 不歸「{self.title}」負責")
        return [note_type]

    async def after_json_update(
        self, card_service: CardService, note_id: int, field_name: str, items: list[object]
    ) -> None:
        """Handle the aftermath of the web writing a whole JSON field, before the sync.

        網頁整欄寫入 JSON 欄位之後（同步之前）呼叫。

        The base implementation does nothing; overriding handlers can maintain related fields (for example the
        person card's last contact date).

        預設不做事；覆寫的 handler 可以維護相關欄位（例如人脈卡的最後聯絡日）。

        Args:
            card_service: the card service
                卡片服務
            note_id: the note ID
                筆記 ID
            field_name: the JSON field written
                寫入的 JSON 欄位
            items: the list written
                寫入的清單
        """

    def reject_locked_fields(self, field_names: Iterable[str]) -> None:
        """Reject fields declared as JSON, which cannot be written directly as text (handlers plan D3).

        宣告為 JSON 的欄位不能直接以文字寫入（handlers 計畫書 D3）。

        Media fields are not blocked here: they may take a file name obtained from an upload, and
        :meth:`field_texts` checks them.

        媒體欄位不在這裡擋：它能填上傳取得的檔名，由 ``field_texts`` 檢查。

        Args:
            field_names: the names of the fields to be written
                要寫入的欄位名稱

        Raises:
            FieldNotEditableError: if any JSON field is present
                若含有 JSON 欄位
        """
        locked = [name for name in field_names if name in self.JSON_FIELDS]
        if locked:
            raise FieldNotEditableError(f"這些欄位不能直接修改：{'、'.join(locked)}")

    def field_texts(self, values: Mapping[str, object], current: Mapping[str, str] | None = None) -> dict[str, str]:
        """Convert submitted field values into the texts to write to Anki.

        把送來的欄位值轉成要寫進 Anki 的字串。

        Media fields are also checked and wrapped in Anki's syntax (media_upload plan D3).

        媒體欄位另外檢查並包成 Anki 的寫法（media_upload 計畫書 D3）。

        Args:
            values: field name to submitted value
                ``{欄位名: 值}``
            current: the fields' current values when updating (a media field sent back with its original value
                means unchanged); omitted when creating
                修改時欄位原本的值（媒體欄位送回原值代表不變）；建立時省略

        Returns:
            field name to the text to write
            ``{欄位名: 要寫入的字串}``

        Raises:
            MediaFieldInvalidError: if a media field is not a file name produced by ``POST /media``, or its kind
                does not match
                若媒體欄位填的不是 ``POST /media`` 產生的檔名，或種類不符
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
    """Convert a field value into Anki text, treating ``None`` as an empty string.

    把欄位值轉成 Anki 的字串；``None`` 視為空字串。

    Args:
        value: the field value
            欄位值

    Returns:
        the text
        字串
    """
    return "" if value is None else str(value)


def clean_html(html_str: object) -> str:
    """Strip the HTML tags Anki may have added and decode the ``&quot;`` and ``&nbsp;`` entities.

    移除 Anki 可能加入的 HTML 標籤，並還原 ``&quot;`` 與 ``&nbsp;`` 兩種跳脫字元。

    Other entities (for example ``&amp;``) are left as they are.

    其他跳脫字元（例如 ``&amp;``）維持原樣。

    Args:
        html_str: the raw value that may contain HTML; converted with ``str()`` first
            可能包含 HTML 的原始值，先以 ``str()`` 轉成字串

    Returns:
        the cleaned plain text
        清洗後的純文字
    """
    text = re.sub(r"<[^>]+>", "", str(html_str))
    return text.replace("&quot;", '"').replace("&nbsp;", " ")


def note_field_values(note: Mapping[str, object]) -> dict[str, str]:
    """Return the field values of a ``CardService.get_note`` result.

    回傳 ``CardService.get_note`` 結果的欄位值。

    Args:
        note: a note returned by ``CardService.get_note``
            ``CardService.get_note`` 的回傳

    Returns:
        field name to value; empty when the note has no ``fields`` mapping
        ``{欄位名: 值}``；筆記沒有 ``fields`` 字典時為空
    """
    fields = note.get("fields")
    if not isinstance(fields, dict):
        return {}
    return {name: field_text(field.get("value")) for name, field in fields.items()}


def note_detail(note: Mapping[str, object]) -> dict[str, object]:
    """Convert an AnkiConnect note into the API shape (§4.3), with fields listed in the note type's order.

    把 AnkiConnect 的筆記轉成 API 的形狀（§4.3），欄位依筆記類型的順序排成陣列。

    Args:
        note: a note returned by ``CardService.get_note`` (camelCase)
            ``CardService.get_note`` 的回傳（camelCase）

    Returns:
        ``{note_id, note_type, tags, cards, fields: [{name, value}]}``
        ``{note_id, note_type, tags, cards, fields: [{name, value}]}``
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
    """Return the value of a ``notesInfo`` field.

    回傳 ``notesInfo`` 欄位的值。

    Args:
        fields: the ``fields`` of a ``notesInfo`` note
            ``notesInfo`` 筆記的 ``fields``
        name: the field name; may be ``None``
            欄位名稱，可為 ``None``

    Returns:
        the value, or an empty string when ``name`` is not given or the field does not exist
        欄位值；沒指定或欄位不存在時為空字串
    """
    if not name or name not in fields:
        return ""
    return fields[name].get("value", "")


async def _field_names(card_service: CardService, note_types: list[str]) -> list[str]:
    """Return the field names of these note types, asked from Anki, in order and without duplicates.

    回傳這些筆記類型的欄位名（向 Anki 查，依順序、不重複）。
    """
    names: list[str] = []
    for note_type in note_types:
        for name in await card_service.anki_client.get_model_field_names(note_type):
            if name not in names:
                names.append(name)
    return names
