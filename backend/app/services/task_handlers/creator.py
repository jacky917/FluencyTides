"""Base creator module: how cards are created.

建卡流程（creator）的基底模組。

A creator is one way to create cards. It declares the create flow, its parameters and drafts, and points to the
handler of the cards it produces; a note type has exactly one handler but may have several creators
(handler/creator split plan, ``handler_note_type_split_FEAT_2026-11-28.md``, D1, D2, D5). What a card can do once it exists (read,
update, delete, actions, recording evaluation) is the handler's job, so a creator reaches it only through
:attr:`BaseCreator.handler`.

creator 是一種建卡方式：宣告建卡流程、參數與草稿，並指向它產出的卡片的 handler。一種筆記類型恰好
一個 handler，但可以有好幾個 creator（handler 拆分計畫書 ``handler_note_type_split_FEAT_2026-11-28.md`` D1、D2、D5）。
卡片建好之後能做什麼（讀取、修改、刪除、動作、錄音評分）是 handler 的事，creator 只能經
``BaseCreator.handler`` 碰到它。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

from app.services.task_handlers.capabilities import CreatorCapabilities

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import BaseModel

    from app.services.card_service import CardService
    from app.services.relation_service import RelationService
    from app.services.task_handlers.base import BaseHandler


class BaseCreator(ABC):
    """Base class for creators.

    creator 的基底類別。

    The registry binds each creator to its handler (``HANDLER``) once every module has registered
    (``HandlerRegistry.check_creators``). A standard implementation being present does not mean it is offered:
    ``CAPABILITIES`` decides, and the registry checks that the declaration is backed by an implementation.

    所有模組都註冊完之後，註冊表把每個 creator 綁到它的 handler（``HANDLER``，見
    ``HandlerRegistry.check_creators``）。標準實作寫好不代表開放：開不開放由 ``CAPABILITIES`` 決定，
    註冊表檢查宣告是否有實作支撐。

    A creator may also offer ``execute_generate``: required when ``create.channels`` contains ``script`` (the
    registry checks that it is callable), and also called by the TG expression flow, whose test guards it. It is
    not declared here because its parameters differ between creators.

    creator 另可提供 ``execute_generate``：``create.channels`` 含 ``script`` 時必須有（註冊表檢查它可以呼叫），
    TG 的外語糾錯流程也會呼叫它（由測試守住）。各 creator 的參數不同，所以不在這裡宣告。

    Attributes:
        CAPABILITIES: how this creator creates cards; nothing is offered by default
            這個 creator 怎麼建卡；預設都不開放
        HANDLER: the name of the handler this creator produces cards for and is listed under; ``create.note_type``,
            when set, must be one of its note types
            這個 creator 產出的卡片的 handler 名稱，也是它列在底下的那一個；``create.note_type`` 有值時必須是
            它的筆記類型
        PRODUCES: the note types this creator writes; empty means just ``create.note_type``. Each must have a
            handler, and ``create.note_type``, when set, must be among them.
            這個 creator 會寫出的筆記類型；空的表示只有 ``create.note_type``。每一種都必須有 handler，
            ``create.note_type`` 有值時也必須在其中。
        CREATE_TAGS: the tags the standard create adds
            標準建立加上的標籤
        CREATE_PARAMS: the create parameter model of a creator that overrides the create (handlers plan D3); the
            service layer validates parameters and builds the web form with it. The standard create does not use
            it (its parameters are the note fields).
            覆寫建立的 creator 的建立參數模型（handlers 計畫書 D3）；服務層以它驗證參數、產生網頁表單。標準
            建立不用它（參數就是筆記欄位）。
        DRAFT_PARAMS: the input model the web preview flow (``create.flow="preview"``) uses to ask the LLM for a
            draft; the service layer validates the input and builds the web form with it, then passes it to
            :meth:`execute_draft`
            網頁預覽流程（``create.flow="preview"``）向 LLM 要草稿的輸入模型；服務層以它驗證輸入、產生網頁
            表單，再交給 ``execute_draft``
    """

    CAPABILITIES: ClassVar[CreatorCapabilities] = CreatorCapabilities()
    HANDLER: ClassVar[str] = ""
    PRODUCES: ClassVar[tuple[str, ...]] = ()
    CREATE_TAGS: ClassVar[tuple[str, ...]] = ("HandlerGenerated",)
    CREATE_PARAMS: ClassVar[type[BaseModel] | None] = None
    DRAFT_PARAMS: ClassVar[type[BaseModel] | None] = None

    def __init__(self) -> None:
        """Create an unbound creator; the registry binds its handler later.

        建立還沒綁定 handler 的 creator；之後由註冊表綁定。
        """
        self._handler: BaseHandler | None = None

    @property
    @abstractmethod
    def creator_name(self) -> str:
        """The creator name used in create URLs and drafts, for example ``"speaking_coach"``.

        creator 名稱，出現在建卡網址與草稿裡，例如 ``"speaking_coach"``。
        """

    @property
    def title(self) -> str:
        """The Chinese display title; the handler's title unless overridden.

        中文名稱；沒有覆寫時沿用 handler 的名稱。

        The default is available only once the registry has bound the handler; before that it raises
        ``RuntimeError`` like :attr:`handler`.

        預設值要等註冊表綁定 handler 之後才讀得到；之前讀會和 ``handler`` 一樣拋出 ``RuntimeError``。
        """
        return self.handler.title

    @property
    def handler(self) -> BaseHandler:
        """The handler this creator produces cards for (bound by the registry).

        這個 creator 產出的卡片的 handler（由註冊表綁定）。

        Raises:
            RuntimeError: if the registry has not bound it yet
                若註冊表還沒綁定
        """
        if self._handler is None:
            raise RuntimeError(f"Creator '{self.creator_name}' 還沒綁定 handler（要先呼叫 check_creators）")
        return self._handler

    def bind(self, handler: BaseHandler) -> None:
        """Bind the handler this creator produces cards for; called by the registry.

        綁定這個 creator 產出的卡片的 handler；由註冊表呼叫。

        Args:
            handler: the handler named by ``HANDLER``
                ``HANDLER`` 指的 handler
        """
        self._handler = handler

    def produced_note_types(self) -> tuple[str, ...]:
        """Return the note types this creator writes.

        回傳這個 creator 會寫出的筆記類型。

        Returns:
            ``PRODUCES`` when declared, otherwise ``create.note_type``; empty when neither is set
            有宣告 ``PRODUCES`` 就用它，否則為 ``create.note_type``；兩者都沒有時為空
        """
        if self.PRODUCES:
            return self.PRODUCES
        note_type = self.CAPABILITIES.create.note_type
        return (note_type,) if note_type else ()

    # =========================================================================
    # 標準建立與草稿
    # Standard create and drafts
    # =========================================================================

    async def execute_create(
        self,
        card_service: CardService,
        relation_service: RelationService,
        deck_name: str,
        model_name: str,
        parameters: dict[str, object],
    ) -> list[int]:
        """Create a note from a manually filled form, where ``parameters`` maps field names to values.

        標準建立：手動填表，``parameters`` 就是 ``{欄位名: 值}``。

        This is the standard create of handlers plan D3.

        這是 handlers 計畫書 D3 的標準建立。

        ``CardService.create_note`` checks that the fields exist and that the first field is not a duplicate;
        fields left out stay empty. JSON fields are rejected and media fields are written as declared by the
        handler: they take the file name returned by ``POST /media`` and are wrapped in Anki's syntax (see
        ``BaseHandler.field_texts``).

        欄位是否存在、首欄位是否重複由 ``CardService.create_note`` 檢查；沒填的欄位留空。JSON 欄位與媒體欄位
        依 handler 的宣告處理：JSON 欄位不能填，媒體欄位填 ``POST /media`` 回傳的檔名，寫入時包成 Anki 的寫法
        （見 ``BaseHandler.field_texts``）。

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
        self.handler.reject_locked_fields(parameters)
        fields = self.handler.field_texts(parameters)
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
        raise NotImplementedError(f"{self.creator_name} 沒有草稿流程")

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

        The base implementation keeps the chosen deck. A creator can derive another one from the parameters (the
        trilingual card puts a child card into ``<parent deck>::<parent title>``) and create it here; the create
        response reports the deck returned.

        預設沿用選的牌組。creator 可以依參數改成別的牌組（三語卡的子卡放進 ``<上層卡的牌組>::<上層卡的標題>``），
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
