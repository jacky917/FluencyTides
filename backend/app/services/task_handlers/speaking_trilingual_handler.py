"""Trilingual speaking practice card handler (Speaking_Trilingual_Dark).

三語口說練習卡片處理器（Speaking_Trilingual_Dark）。

The architecture mirrors ``speaking_coach_handler.py``; the differences:

- One card covers ZH, JA, and EN practice; Prompt and Context are shared, while Recordings and References
  have one field per language.
- There is no Target_Language field: the language comes from the field suffix or a language code
  (``shared/trilingual_lang.py``).
- ``Prompt_Audios`` is a single JSON field whose items carry a ``lang`` key, with **at most one per
  language** (the same lang overwrites).

All field reads and writes go through ``CardService`` and ``AnkiJsonFieldManager``.

架構對齊 ``speaking_coach_handler.py``；差異：

- 一張卡片包含 ZH/JA/EN 三語練習；Prompt / Context 共通，
  Recordings / References 每語言獨立欄位。
- 不設 Target_Language 欄位——語言由欄位後綴或語言碼決定
  （``shared/trilingual_lang.py``）。
- ``Prompt_Audios`` 為單一 JSON 欄位，項目帶 ``lang`` 鍵，
  **每語言最多一條**（同 lang 覆蓋）。

欄位讀寫一律走 ``CardService`` / ``AnkiJsonFieldManager``。
"""

import json
import logging
import uuid
from datetime import date
from collections.abc import Mapping
from typing import TYPE_CHECKING, Literal, override

from pydantic import BaseModel, Field, field_validator

from app.core.config import settings
from app.core.exceptions import InvalidNoteQueryError, NoteNotFoundError, ParentNoteInvalidError
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager
from app.infrastructure.anki.search import note_types_query
from app.services import speaking_trilingual_generate as generate_service
from app.services.task_handlers.base import BaseHandler, clean_html, field_value
from app.services.task_handlers.capabilities import (
    CreateCapability,
    GraphCapability,
    HandlerCapabilities,
    Operation,
    SearchField,
)
from app.services.task_handlers.registry import register_handler
from app.services.task_handlers.shared.trilingual_familiarity import parse_familiarity
from app.services.task_handlers.shared.trilingual_title import (
    TITLE_FIELD,
    TITLE_MAX_LENGTH,
    normalize_title,
    usable_title,
)
from app.services.task_handlers.shared.trilingual_lang import (
    LANG_CODES,
    recordings_field,
    references_field,
)
from app.services.task_handlers.speaking_coach_handler import SPEAKING_ACTIONS

if TYPE_CHECKING:
    from app.services.card_service import CardService
    from app.services.relation_service import RelationService

logger = logging.getLogger(__name__)

# 建卡參數的上限：只防前端 bug 或手動呼叫 API 寫進超大內容（AnkiDroid 打開會很慢），
# 正常使用遠遠碰不到——LLM 一次約生成二十幾句，每句幾十字
MAX_REFERENCES_PER_LANG = 100
MAX_TEXT_LENGTH = 2000


class ReferenceDraft(BaseModel):
    """One reference answer given at creation; the create fills in ``date``, ``status``, and ``audios``.

    建卡時的一句參考答案；``date``、``status``、``audios`` 由建卡補上。
    """

    content: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH, description="句子")
    note: str = Field(
        default="",
        max_length=MAX_TEXT_LENGTH,
        description="說明：對象與場合、語氣的關鍵或場景補充（卡片背面以小字顯示）",
    )
    familiarity: int | None = Field(default=None, description="熟識度 0–9；省略表示未標")

    @field_validator("content", "note", mode="before")
    @classmethod
    def _strip(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("familiarity", mode="before")
    @classmethod
    def _familiarity(cls, value: object) -> int | None:
        # 規則與匯入、卡片相同（trilingual_familiarity.py）
        return parse_familiarity(value)


class SpeakingTrilingualCreateParams(BaseModel):
    """The create parameters of a trilingual speaking card (D3).

    三語對話卡的建立參數（D3）。

    The language is decided by the field, so there is no language parameter; ``tg_bot`` is passed by the
    service layer. In the web preview flow the draft (``execute_draft``) prefills these, and the user sends
    them back after ticking.

    語言由欄位決定，所以沒有語言參數；``tg_bot`` 由服務層傳入。網頁的預覽流程由草稿（``execute_draft``）
    預先填好，使用者勾選後送回。
    """

    title: str = Field(
        min_length=1,
        max_length=TITLE_MAX_LENGTH,
        title="標題",
        description="短標題（日文），也是子牌組的名稱（例如「残業を断る」）；不能含 :: 或引號",
    )
    prompt: str = Field(
        min_length=1,
        max_length=MAX_TEXT_LENGTH,
        title="情境提示",
        description="共通口說情境提示（三語共用）",
        json_schema_extra={"x-ui": "textarea"},
    )
    context: str = Field(
        default="",
        max_length=MAX_TEXT_LENGTH,
        title="情境",
        description="情境描述（選填）",
        json_schema_extra={"x-ui": "textarea"},
    )
    references_zh: list[ReferenceDraft] = Field(
        default_factory=list, max_length=MAX_REFERENCES_PER_LANG, title="中文說法"
    )
    references_ja: list[ReferenceDraft] = Field(
        default_factory=list, max_length=MAX_REFERENCES_PER_LANG, title="日文說法"
    )
    references_en: list[ReferenceDraft] = Field(
        default_factory=list, max_length=MAX_REFERENCES_PER_LANG, title="英文說法"
    )
    parent_note_id: int | None = Field(
        default=None,
        title="上層卡",
        description="建成這張三語卡的子卡：新卡放進「上層卡的牌組::上層卡的標題」（不存在就建立）；省略就放在選的牌組",
    )

    @field_validator("title", mode="before")
    @classmethod
    def _title(cls, value: object) -> object:
        # 標題也是子牌組名：去掉前後空白，不能含 :: 或引號（trilingual_title.py）
        return normalize_title(value) if isinstance(value, str) else value


class SpeakingTrilingualDraftParams(BaseModel):
    """The draft input of the web preview flow: the scene, how to answer, and the audience.

    網頁預覽流程的草稿輸入：場景、想怎麼答、對象。
    """

    background: str = Field(
        min_length=1,
        title="背景",
        description="發生什麼事、自己的處境",
        json_schema_extra={"x-ui": "textarea"},
    )
    intent: str = Field(
        default="",
        title="想怎麼答",
        description="選填；沒填時由 LLM 想出各種正反回應的自然說法",
        json_schema_extra={"x-ui": "textarea"},
    )
    audience: Literal[*generate_service.AUDIENCES] = Field(  # type: ignore[valid-type]
        default=generate_service.ANY_AUDIENCE, title="對象", description="不指定時生成對各種對象的說法"
    )
    audience_note: str = Field(default="", title="對象補充", description="選填，例：房東，年紀比我大")
    deck: str = Field(
        default="",
        title="牌組",
        description="要建卡的牌組（網頁自動帶入左側選的牌組）；讓 LLM 知道卡片在主題樹的位置",
        json_schema_extra={"x-ui": "deck"},
    )
    parent_note_id: str = Field(
        default="",
        pattern=r"^\d*$",
        title="放在哪張卡底下",
        description="選填；選了之後新卡成為那張卡的子卡，LLM 也會參考那張卡的情境",
        json_schema_extra={"x-ui": "parent-card"},
    )


#: How many sibling titles and decks at most go into the prompt.
#: 提示詞裡最多放幾個同一層的標題與分類。
MAX_PLACEMENT_SIBLINGS = 40

#: How many characters of the parent card's prompt go into the prompt.
#: 提示詞裡放上層卡情境的前幾個字。
MAX_PARENT_PROMPT_LENGTH = 300


def _parent_id(value: object) -> int | None:
    """Return the parent note ID from the draft input (a digit string; empty means none).

    從草稿輸入取出上層卡的 Note ID（數字字串；空的表示沒有）。
    """
    text = str(value or "").strip()
    return int(text) if text.isdigit() else None


@register_handler
class SpeakingTrilingualHandler(BaseHandler):
    """Trilingual speaking practice task handler.

    三語口說練習任務處理器。

    Supports Speaking_Trilingual_Dark: manages shared-scenario creation and updates of the per-language JSON
    fields (Recordings ×3, References ×3) and Prompt_Audios (with ``lang`` keys).

    支援 Speaking_Trilingual_Dark：管理共通情境的建立，以及
    per-language JSON 欄位（Recordings_×3 / References_×3）與
    Prompt_Audios（帶 lang 鍵）的更新。
    """

    CAPABILITIES = HandlerCapabilities(
        create=CreateCapability(
            supported=True,
            channels=("web",),
            flow="preview",
            deck="selected",
            note_type="Speaking_Trilingual_Dark",
        ),
        update_fields=Operation(reason="三語對話卡的欄位修改尚未開放"),
        delete=Operation(reason="三語對話卡的刪除尚未開放"),
        actions=SPEAKING_ACTIONS,
        search=Operation(supported=True),
        graph=GraphCapability(supported=True, layout="tree"),
    )
    CREATE_PARAMS = SpeakingTrilingualCreateParams
    DRAFT_PARAMS = SpeakingTrilingualDraftParams
    CREATE_TAGS = ("Speaking_Trilingual", "HandlerGenerated")
    #: The web field-search buttons, the short title first. References are JSON holding both sentences and notes,
    #: so a "contains" match finds both.
    #: 網頁欄位搜尋的按鈕，短標題排第一個。參考答案是 JSON，句子與說明都在裡面，「包含」比對會一起找到。
    SEARCH_FIELDS = (
        SearchField("title", "標題", (TITLE_FIELD,)),
        SearchField("prompt", "情境", ("Prompt",)),
        SearchField("context", "補充", ("Context",)),
        SearchField("refs", "說法（三語）", tuple(references_field(lang) for lang in LANG_CODES)),
        SearchField("zh", "中文", (references_field("ZH"),)),
        SearchField("ja", "日文", (references_field("JA"),)),
        SearchField("en", "英文", (references_field("EN"),)),
    )

    @property
    @override
    def handler_name(self) -> str:
        """The handler name used in URLs.

        處理器名稱，出現在網址裡。
        """
        return "speaking_trilingual"

    @property
    @override
    def title(self) -> str:
        """The Chinese display title.

        中文名稱。
        """
        return "三語對話卡"

    @property
    @override
    def supported_models(self) -> list[str]:
        """The Anki note types this handler owns.

        這個處理器負責的 Anki 筆記類型。
        """
        return ["Speaking_Trilingual_Dark"]

    @override
    async def execute_draft(
        self,
        parameters: dict[str, object],
        existing_tags: list[str],
        context: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Ask the LLM for trilingual sayings and suggested tags for the scene, shaped as a draft (nothing written).

        請 LLM 為場景生成三語說法與建議標籤，整理成建立參數的草稿（不寫入）。

        Sayings whose content is blank are dropped. The suggested title is kept only if it can name a deck
        (``trilingual_title.usable_title``); otherwise the draft's title is empty and the user fills it in before
        creating, where it is required.

        內容為空白的說法會被略過。建議的標題要能當牌組名才保留（``trilingual_title.usable_title``），否則草稿的
        標題是空的，由使用者在建卡前補上（建卡時必填）。

        Args:
            parameters: the input validated by ``SpeakingTrilingualDraftParams``
                ``SpeakingTrilingualDraftParams`` 驗證後的輸入
            existing_tags: tags trilingual cards have used, for the LLM to prefer reusing
                三語卡用過的標籤，讓 LLM 優先沿用
            context: where the card will sit, from :meth:`draft_context`; ``None`` or empty means unknown
                卡片會放在哪裡（:meth:`draft_context` 讀到的）；``None`` 或空的表示不知道

        Returns:
            a draft shaped like ``SpeakingTrilingualCreateParams``, plus ``tags`` (the suggested tags); the chosen
            parent card is carried over as ``parent_note_id``
            ``SpeakingTrilingualCreateParams`` 形狀的草稿，另帶 ``tags``（建議的標籤）
        """
        audience = generate_service.target_audience(str(parameters["audience"]), str(parameters["audience_note"]))
        draft = await generate_service.generate(
            str(parameters["background"]),
            str(parameters["intent"]),
            audience,
            existing_tags,
            generate_service.DraftPlacement(**dict(context)) if context else None,
        )
        references = {
            f"references_{lang.lower()}": [
                saying.model_dump() for saying in getattr(draft, lang.lower()) if saying.content.strip()
            ]
            for lang in LANG_CODES
        }
        # 草稿可以沒有標題：LLM 沒給或不合規則（含 :: 等）就留空，使用者送出前補上（建卡時才必填）
        title = usable_title(draft.title) or ""
        params = SpeakingTrilingualCreateParams.model_validate(
            {"title": title or "未命名", "prompt": draft.prompt, "context": draft.context, **references}
        ).model_dump()
        params["title"] = title
        # 生成前選的上層卡帶到預覽，建卡時照樣放進它的同名子牌組
        params["parent_note_id"] = _parent_id(parameters.get("parent_note_id"))
        return {**params, "tags": draft.tags}

    @override
    async def draft_context(self, card_service: "CardService", parameters: Mapping[str, object]) -> dict[str, object]:
        """Read where the new card will sit: the full deck path, the parent card, the titles already there.

        讀出新卡會放在哪裡：完整牌組路徑、上層卡、那裡已有的標題。

        The deck is the parent's same-named child deck when a parent is chosen, otherwise the chosen deck. A parent
        that cannot hold child cards (deleted, another note type, no title) is left out here; creating the card
        reports it. Without a parent, the card that owns the chosen deck (the topic tree's root) counts as the
        parent, since the new card becomes its child anyway.

        選了上層卡時是它的同名子牌組，否則是選的牌組。不能放子卡的上層卡（已刪除、其他筆記類型、沒有標題）這裡
        略過，建卡時才報錯。沒選上層卡時，擁有選的牌組的那張卡（主題樹的根）就算上層卡，因為新卡本來就會成為它的
        子卡。

        Args:
            card_service: the card service
                卡片服務
            parameters: the input validated by ``SpeakingTrilingualDraftParams``
                ``SpeakingTrilingualDraftParams`` 驗證後的輸入

        Returns:
            the keyword arguments of ``speaking_trilingual_generate.DraftPlacement``; empty without a deck
            ``speaking_trilingual_generate.DraftPlacement`` 的參數；沒有牌組時為空的
        """
        target = str(parameters.get("deck") or "").strip() or None
        parent_title: str | None = None
        parent_prompt = ""
        parent_id = _parent_id(parameters.get("parent_note_id"))
        if parent_id is not None:
            try:
                target, parent_title, parent_prompt = await self._parent_placement(card_service, parent_id)
            except (NoteNotFoundError, ParentNoteInvalidError) as exc:
                logger.info("三語卡草稿：上層卡 %s 不能放子卡，不提供給 LLM：%s", parent_id, exc)
        if target is None:
            return {}
        graph = await self.execute_read_graph(card_service, None, target, depth=1)
        nodes = [node for node in graph.get("nodes") or [] if isinstance(node, dict)]
        root = next((node for node in nodes if node.get("kind") == "root"), {})
        if parent_title is None and root.get("note_id"):
            parent_title = str(root.get("title") or root.get("label") or "") or None
            parent_prompt = str(root.get("prompt") or "")
        level = [node for node in nodes if node.get("depth") == 1]
        titles = [str(node.get("title") or node.get("label")) for node in level if node.get("kind") == "card"]
        decks = [str(node.get("label")) for node in level if node.get("kind") == "deck"]
        return {
            "deck_path": target,
            "parent_title": parent_title,
            "parent_prompt": parent_prompt[:MAX_PARENT_PROMPT_LENGTH],
            "sibling_titles": titles[:MAX_PLACEMENT_SIBLINGS],
            "sibling_decks": decks[:MAX_PLACEMENT_SIBLINGS],
        }

    async def _parent_placement(self, card_service: "CardService", parent_id: int) -> tuple[str, str, str]:
        """Return the deck a child of this card goes into, with the card's title and prompt.

        回傳這張卡的子卡要放的牌組，以及這張卡的標題與情境。

        Args:
            card_service: the card service
                卡片服務
            parent_id: the parent's note ID
                上層卡的 Note ID

        Returns:
            ``(<the parent's deck>::<its title>, title, prompt)``
            ``(<上層卡的牌組>::<它的標題>, 標題, 情境)``

        Raises:
            NoteNotFoundError: if the parent note or its deck cannot be found
                若找不到上層卡或它所在的牌組
            ParentNoteInvalidError: if the parent is not a trilingual card or has no usable title
                若上層卡不是三語卡，或沒有可用的標題
        """
        notes = await card_service.anki_client.get_notes_info([parent_id])
        if not notes:
            raise NoteNotFoundError(f"找不到上層卡（Note {parent_id}），可能已被刪除")
        parent = notes[0]
        if parent.modelName not in self.supported_models:
            raise ParentNoteInvalidError(f"上層卡必須是「{self.title}」，Note {parent_id} 是 {parent.modelName}")
        title = usable_title(clean_html(field_value(parent.fields, TITLE_FIELD)))
        if title is None:
            raise ParentNoteInvalidError("上層卡沒有可用的標題，請先在 Anki 幫它填上標題（Title），才能在它底下建子卡")
        cards_info = await card_service.anki_client.get_cards_info(parent.cards[:1]) if parent.cards else []
        parent_deck = next((str(info["deckName"]) for info in cards_info if isinstance(info, dict)), None)
        if parent_deck is None:
            raise NoteNotFoundError(f"讀不到上層卡（Note {parent_id}）所在的牌組")
        prompt = clean_html(field_value(parent.fields, "Prompt")).strip()
        return f"{parent_deck}::{title}", title, prompt

    @override
    async def resolve_create_deck(
        self, card_service: "CardService", deck: str, parameters: Mapping[str, object]
    ) -> str:
        """Put a child card into its parent's same-named child deck, creating that deck if needed.

        子卡放進上層卡的同名子牌組，沒有就建立。

        Without ``parent_note_id`` the chosen deck is kept. The parent must be a trilingual card with a usable
        title; the target is ``<the parent's deck>::<the parent's title>`` (the topic tree, plan D2, D6).
        ``createDeck`` returns the existing deck's ID when it already exists, so a second child card reuses it.

        沒有 ``parent_note_id`` 時沿用選的牌組。上層卡必須是有可用標題的三語卡；目標是
        ``<上層卡的牌組>::<上層卡的標題>``（主題樹，計畫書 D2、D6）。牌組已存在時 ``createDeck`` 回傳既有的 ID，
        所以第二張子卡會沿用同一個牌組。

        Args:
            card_service: the card service
                卡片服務
            deck: the chosen deck (ignored when a parent is given)
                選的牌組（有上層卡時忽略）
            parameters: the validated create parameters
                驗證後的建立參數

        Returns:
            the deck to create the card in
            要建卡的牌組

        Raises:
            NoteNotFoundError: if the parent note does not exist
                若上層卡不存在
            ParentNoteInvalidError: if the parent is not a trilingual card or has no usable title
                若上層卡不是三語卡，或沒有可用的標題
        """
        parent_id = parameters.get("parent_note_id")
        if not isinstance(parent_id, int):
            return deck
        target, _, _ = await self._parent_placement(card_service, parent_id)
        await card_service.anki_client.create_deck(target)
        logger.info("三語卡建成 Note %s 的子卡，放進牌組 %s", parent_id, target)
        return target

    @override
    async def execute_read_graph(
        self,
        card_service: "CardService",
        relation_service: "RelationService",
        deck_name: str | None = None,
        *,
        note_type: str | None = None,
        label_field: str | None = None,
        depth: int = 1,
    ) -> dict[str, object]:
        """Return the topic tree under the deck: cards, their child cards through same-named child decks.

        回傳牌組以下的主題樹：卡片，以及透過同名子牌組掛在底下的子卡。

        Reads every trilingual card under the parent of ``deck_name`` (the card that owns ``deck_name`` lives
        there) with one search, then builds the tree in memory (``topic_tree.build_graph``). Node labels are always
        the title, so ``label_field`` is not accepted.

        以一次搜尋讀出 ``deck_name`` 上一層以下的所有三語卡（擁有 ``deck_name`` 的卡在上一層），再在記憶體裡組樹
        （``topic_tree.build_graph``）。節點名稱固定是標題，所以不接受 ``label_field``。

        Args:
            card_service: the card service
                卡片服務
            relation_service: the relation service (unused)
                關聯服務（不使用）
            deck_name: the root deck; ``None`` means every deck
                根牌組；``None`` 表示全部牌組
            note_type: only one of the handler's note types; ``None`` means all
                只看其中一種筆記類型；``None`` 表示全部
            label_field: not applicable; rejected when given
                不適用，帶了就拒絕
            depth: how many levels to expand, ``0`` meaning all
                展開幾層，``0`` 表示全部

        Returns:
            ``topic_tree.build_graph``'s result plus ``label_fields`` (empty) and ``label_field`` (``Title``)
            ``topic_tree.build_graph`` 的結果，另帶 ``label_fields``（空的）與 ``label_field``（``Title``）

        Raises:
            InvalidNoteQueryError: if ``label_field`` is given, or ``note_type`` is not this handler's
                若帶了 ``label_field``，或 ``note_type`` 不歸這個 handler
        """
        from app.services.task_handlers.topic_tree import TreeCard, build_graph

        if label_field is not None:
            raise InvalidNoteQueryError("主題樹的節點名稱固定是標題，不能另選欄位")
        # 擁有根牌組的卡在上一層，所以從上一層讀起（最上層的牌組沒有上一層）
        search_deck = (deck_name.rpartition("::")[0] or deck_name) if deck_name else None
        note_ids = await card_service.find_notes(note_types_query(self.note_types_for(note_type), search_deck))
        notes = await card_service.anki_client.get_notes_info(note_ids) if note_ids else []
        first_cards = [note.cards[0] for note in notes if note.cards]
        cards_info = await card_service.anki_client.get_cards_info(first_cards) if first_cards else []
        deck_of = {info["note"]: info["deckName"] for info in cards_info if isinstance(info, dict)}
        tree_cards = [
            TreeCard(
                note_id=note.noteId,
                deck=str(deck_of[note.noteId]),
                title=usable_title(clean_html(field_value(note.fields, TITLE_FIELD))),
                prompt=clean_html(field_value(note.fields, "Prompt")).strip(),
            )
            for note in notes
            if note.noteId in deck_of
        ]
        return build_graph(tree_cards, deck_name, depth) | {"label_fields": [], "label_field": TITLE_FIELD}

    @override
    async def execute_create(
        self,
        card_service: "CardService",
        relation_service: "RelationService",
        deck_name: str,
        model_name: str,
        parameters: dict
    ) -> list[int]:
        """Create one trilingual speaking card with the given reference answers.

        建立一張三語口說卡片。

        Reference answers are written from the parameters (with today's date, enabled, and no audio); the other
        JSON fields start as empty lists.

        參考答案依參數寫入（補上今天、啟用、沒有音檔），其餘 JSON 欄位為空陣列。

        Args:
            card_service: the card service
                卡片服務
            relation_service: the relation service (unused)
                關聯服務（不使用）
            deck_name: the target deck
                目標牌組
            model_name: the target note type
                目標模型
            parameters: the parameters validated by ``SpeakingTrilingualCreateParams``, plus ``tg_bot``
                ``SpeakingTrilingualCreateParams`` 驗證後的參數與 ``tg_bot``

        Returns:
            the created note ID, as a one-element list
            建立成功的 Note ID（單元素陣列）
        """
        prompt_text = parameters.get("prompt", "")
        context_text = parameters.get("context", "")
        tg_bot = parameters.get("tg_bot", settings.TG_BOT_USERNAME or "")

        card_id = str(uuid.uuid4())
        empty_json_array = json.dumps([], ensure_ascii=False)

        fields = {
            TITLE_FIELD: parameters.get("title", ""),
            "Prompt": prompt_text,
            "Prompt_Audios": empty_json_array,
            "Context": context_text,
            "Card_ID": card_id,
            "TG_Bot": tg_bot,
        }
        today = date.today().isoformat()
        for lang in LANG_CODES:
            fields[recordings_field(lang)] = empty_json_array
            references = [
                {**_without_blank_note(ref), "status": 1, "date": today, "audios": []}
                for ref in parameters.get(f"references_{lang.lower()}") or []
            ]
            # 參考答案可能含 <、& 等字元：與語音流程、匯入相同，經 HTML 轉義後寫入
            fields[references_field(lang)] = AnkiJsonFieldManager.serialize(references)

        logger.info("建立三語口說卡片 (Model: %s) -> Prompt: %s", model_name, prompt_text)
        note_id = await card_service.create_note(
            deck_name=deck_name,
            model_name=model_name,
            fields=fields,
            tags=list(self.CREATE_TAGS),
        )
        return [note_id]

    @override
    async def execute_update(
        self,
        card_service: "CardService",
        relation_service: "RelationService",
        note_id: int,
        parameters: dict
    ) -> None:
        """Apply a task-specific update; JSON fields are read and written through ``AnkiJsonFieldManager``.

        客製化更新（皆以 AnkiJsonFieldManager 讀寫）。

        Supported ``parameters``:

        - ``{"action": "add_recording", "lang": "JA", "recording": {...}}``
        - ``{"action": "delete_recording", "lang": "JA", "index": 1}`` (an out-of-range index is ignored)
        - ``{"action": "add_audio", "field_name": "References_JA" | "Prompt_Audios",
          "index": "0" | "last" | "ZH" | "JA" | "EN", "audio": "...", "speaker": "...", "avatar": "..."}``
          (for ``Prompt_Audios`` the index must be a language code, and the same lang overwrites)
        - ``{"action": "update_fields", "fields": {"Context": "..."}}``

        parameters 支援：

        - ``{"action": "add_recording", "lang": "JA", "recording": {...}}``
        - ``{"action": "delete_recording", "lang": "JA", "index": 1}``（超出範圍的 index 忽略）
        - ``{"action": "add_audio", "field_name": "References_JA" | "Prompt_Audios",
          "index": "0" | "last" | "ZH" | "JA" | "EN", "audio": "...", "speaker": "...", "avatar": "..."}``
          （``Prompt_Audios`` 的 index 須為語言碼，同 lang 覆蓋）
        - ``{"action": "update_fields", "fields": {"Context": "..."}}``

        Args:
            card_service: the card service
                卡片服務
            relation_service: the relation service (unused)
                關聯服務（不使用）
            note_id: the target note ID
                目標 Note ID
            parameters: the update parameters (must contain ``action``)
                更新參數（需含 ``action``）

        Raises:
            ValueError: if a recording action lacks a valid ``lang``, ``add_audio`` lacks ``field_name``,
                ``index`` or ``audio`` (or its ``Prompt_Audios`` index is not a language code), or the action is
                not supported
                若錄音動作缺少合法的 ``lang``、``add_audio`` 缺少 ``field_name``、``index`` 或 ``audio``
                （或 ``Prompt_Audios`` 的 index 不是語言碼），或 action 不支援
        """
        action = parameters.get("action")

        if action == "update_fields":
            update_fields = parameters.get("fields", {})
            await card_service.update_note_fields(note_id, update_fields)
            return

        if action in ("add_recording", "delete_recording"):
            lang = parameters.get("lang")
            if lang not in LANG_CODES:
                raise ValueError(f"{action} 需要合法的 lang 參數 (ZH/JA/EN)，收到: {lang}")
            field_name = recordings_field(lang)
            recordings = await AnkiJsonFieldManager.safe_read_list(
                card_service, note_id, field_name
            )

            if action == "add_recording":
                new_rec = parameters.get("recording")
                if new_rec:
                    recordings.append(new_rec)
            else:
                index = parameters.get("index")
                if index is not None and 0 <= index < len(recordings):
                    recordings.pop(index)

            await AnkiJsonFieldManager.update_field(
                card_service, note_id, field_name, recordings
            )
            logger.info(
                "已更新三語口說卡片 %d 的 %s 欄位 (目前 %d 筆)",
                note_id, field_name, len(recordings),
            )
            return

        if action == "add_audio":
            field_name = parameters.get("field_name")
            index_str = parameters.get("index")
            audio_filename = parameters.get("audio")

            if not field_name or not index_str or not audio_filename:
                raise ValueError("add_audio 需要 field_name, index, audio 參數")

            items = await AnkiJsonFieldManager.safe_read_list(
                card_service, note_id, field_name
            )
            avatar_filename = parameters.get("avatar", "")
            speaker_name = parameters.get("speaker", "User")

            if field_name == "Prompt_Audios":
                # index 槽位為語言碼：同 lang 覆蓋（每語言最多一條，合計 ≤3）
                if index_str not in LANG_CODES:
                    raise ValueError(
                        f"Prompt_Audios 的 index 槽位須為語言碼 (ZH/JA/EN)，收到: {index_str}"
                    )
                new_audio_obj = {
                    "lang": index_str,
                    "audio": audio_filename,
                    "speaker": speaker_name,
                    "avatar": avatar_filename,
                }
                replaced = False
                for i, item in enumerate(items):
                    if isinstance(item, dict) and item.get("lang") == index_str:
                        items[i] = new_audio_obj
                        replaced = True
                        break
                if not replaced:
                    items.append(new_audio_obj)
            elif field_name.startswith("References_"):
                try:
                    idx = -1 if index_str in ("last", "none") else int(index_str)
                    if items:
                        ref_item = items[idx]
                        if isinstance(ref_item, dict):
                            if "audios" not in ref_item or not isinstance(ref_item["audios"], list):
                                ref_item["audios"] = []
                            ref_item["audios"].append({
                                "audio": audio_filename,
                                "speaker": speaker_name,
                                "avatar": avatar_filename,
                            })
                    else:
                        logger.warning(f"{field_name} 是空的，無法插入音檔: {audio_filename}")
                except (ValueError, IndexError):
                    logger.warning(f"{field_name} 找不到對應的 index: {index_str}")
            else:
                logger.warning(f"未知的音檔新增目標欄位: {field_name}")

            await AnkiJsonFieldManager.update_field(
                card_service, note_id, field_name, items
            )
            logger.info("已新增音檔 %s 到 %s (索引 %s)", audio_filename, field_name, index_str)
            return

        raise ValueError(f"不支援的更新操作: {action}")


def _without_blank_note(reference: dict[str, object]) -> dict[str, object]:
    """Return a reference answer without its empty or ``None`` values, consistent with imported data.

    參考答案省略空的說明與未標的熟識度（與匯入的資料一致）。

    In practice this drops an empty ``note`` and an unrated ``familiarity``; any other key whose value is
    ``""`` or ``None`` is dropped too.

    實際上就是省略空的 ``note`` 與未標的 ``familiarity``；其他值為 ``""`` 或 ``None`` 的鍵也一併省略。
    """
    return {key: value for key, value in reference.items() if value not in ("", None)}
