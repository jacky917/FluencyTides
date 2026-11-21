"""Capability declarations of a task handler (handlers plan D2).

Handler 的能力宣告（handlers 計畫書 D2）。

Each handler declares, as the ``CAPABILITIES`` class attribute, what the service layer may do with it:
create, read, update fields, delete, named actions, and the graph. Capabilities belong to the handler and apply
to every note type it owns. A declaration checks its own shape on construction (for example, offering create
without the web channel requires a reason); the registry checks at registration time that the declaration
matches the implementation.

每個 handler 以類別屬性 ``CAPABILITIES`` 宣告服務層能對它做什麼：建立、讀取、修改欄位、刪除、
具名動作與圖譜。能力綁在 handler 上，對它負責的所有筆記類型一體適用。宣告本身在建立時就檢查
形狀（例如開放建卡卻不能從網頁建時一定要寫原因）；宣告與實作是否一致由註冊表在註冊時檢查。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pydantic import BaseModel

#: The channels a card can be created from: the web, TG, and local scripts.
#: 建卡管道：網頁、TG、本機腳本。
Channel = Literal["web", "tg", "script"]
CHANNELS: tuple[str, ...] = ("web", "tg", "script")

#: The default reason for an operation that is not offered and has no reason given.
#: 不開放、又沒寫原因時的預設原因。
NOT_OFFERED = "尚未開放"


def _default_reason(instance: object, supported: bool, reason: str | None) -> None:
    """Fill in ``NOT_OFFERED`` for an operation that is not offered and has no reason.

    不開放又沒寫原因時補上 ``NOT_OFFERED``。

    An offered operation is left alone (the API's ``reason`` is null).

    開放時不補（API 的 ``reason`` 為 null）。
    """
    if not supported and not reason:
        object.__setattr__(instance, "reason", NOT_OFFERED)


@dataclass(frozen=True)
class Operation:
    """Whether one operation is offered.

    一項操作是否開放。

    An operation that is not offered always has a reason (in Chinese, shown as is by the frontend), which is
    ``NOT_OFFERED`` when none is given.

    不開放時必有原因（中文，前端直接顯示），沒寫就是 ``NOT_OFFERED``。

    Attributes:
        supported: whether the operation is offered
            是否開放
        reason: why it is not offered
            不開放的原因
    """

    supported: bool = False
    reason: str | None = None

    def __post_init__(self) -> None:
        """Fill in the default reason when needed.

        需要時補上預設原因。
        """
        _default_reason(self, self.supported, self.reason)


@dataclass(frozen=True)
class CreateCapability:
    """The card creation capability.

    建卡能力。

    Attributes:
        supported: whether the service layer (web and TG) can create cards
            服務層（網頁與 TG）能否建卡
        channels: the channels cards can actually be created from (a description for people)
            實際能從哪些管道建立（給人看的說明）
        flow: ``"form"`` means submitting one form is enough; ``"preview"`` means the LLM result is reviewed
            and ticked first (the web preview flow asks ``/draft`` for a draft through the ``DRAFT_PARAMS``
            form, then creates the card after ticking)
            ``"form"`` 一張表單送出即可；``"preview"`` 先看 LLM 結果再勾選（網頁的預覽流程先以
            ``DRAFT_PARAMS`` 的表單向 ``/draft`` 要草稿，勾選後再建卡）
        deck: ``"selected"`` creates in the chosen deck; ``"fixed"`` always uses ``fixed_deck``
            ``"selected"`` 建在指定牌組；``"fixed"`` 固定在 ``fixed_deck``
        fixed_deck: the deck when ``deck`` is ``"fixed"``
            ``deck`` 為 ``"fixed"`` 時的牌組
        note_type: the note type created
            建出的筆記類型
        reason: why it is not offered (``NOT_OFFERED`` when not given), or, when offered but not from the web,
            where to create instead
            不開放的原因（沒寫時為 ``NOT_OFFERED``），或開放但不能從網頁建立時的說明
    """

    supported: bool = False
    channels: tuple[Channel, ...] = ()
    flow: Literal["form", "preview"] | None = None
    deck: Literal["selected", "fixed"] | None = None
    fixed_deck: str | None = None
    note_type: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        """Check the shape of this declaration and fill in the default reason.

        檢查此宣告的形狀並補上預設原因。

        Raises:
            ValueError: if a channel is unknown, an offered create lacks ``note_type``, ``flow``, ``deck`` or
                (for a fixed deck) ``fixed_deck``, has neither ``web`` nor ``tg``, lacks ``web`` without a
                ``reason``, or ``web`` is listed while create is not offered
                若有不認得的管道、開放建卡卻缺 ``note_type``、``flow``、``deck`` 或（固定牌組時）
                ``fixed_deck``、沒有 ``web`` 也沒有 ``tg``、沒有 ``web`` 又沒寫 ``reason``，或不開放卻列了
                ``web``
        """
        unknown = [c for c in self.channels if c not in CHANNELS]
        if unknown:
            raise ValueError(f"不認得的建卡管道：{unknown}")
        _default_reason(self, self.supported, self.reason)
        if self.supported:
            if not self.note_type or self.flow is None or self.deck is None:
                raise ValueError("開放建卡時必須宣告 note_type、flow、deck")
            if self.deck == "fixed" and not self.fixed_deck:
                raise ValueError("deck 為 fixed 時必須宣告 fixed_deck")
            if not ({"web", "tg"} & set(self.channels)):
                raise ValueError("開放建卡時 channels 至少要有 web 或 tg（服務層的管道）")
            if "web" not in self.channels and not self.reason:
                raise ValueError("開放建卡但不能從網頁建卡時必須寫 reason，說明要去哪裡建")
        if "web" in self.channels and not self.supported:
            raise ValueError("channels 含 web 時必須開放建卡")


@dataclass(frozen=True)
class GraphCapability:
    """The graph capability.

    圖譜能力。

    Attributes:
        supported: whether the graph is offered
            是否開放圖譜
        links: whether the graph has relation links (only then does the frontend show operations such as
            creating links)
            是否有關聯連線（前端才顯示建立連線等操作）
        reason: why it is not offered
            不開放的原因
    """

    supported: bool = False
    links: bool = False
    reason: str | None = None

    def __post_init__(self) -> None:
        """Fill in the default reason and check ``links``.

        補上預設原因並檢查 ``links``。

        Raises:
            ValueError: if ``links`` is declared while the graph is not offered
                若不開放圖譜卻宣告了 ``links``
        """
        _default_reason(self, self.supported, self.reason)
        if self.links and not self.supported:
            raise ValueError("links 只能在開放圖譜時宣告")


@dataclass(frozen=True)
class HandlerCapabilities:
    """All capabilities of a handler.

    一個 handler 的全部能力。

    By default only reading is offered (the base class's standard read, which can read any note type).

    預設只開放讀取（基底的標準讀取，任何筆記類型都能讀）。

    Attributes:
        create: creating cards
            建卡
        read: reading a note
            讀取筆記
        update_fields: updating fields as text
            以文字修改欄位
        delete: deleting a note
            刪除筆記
        actions: the named update actions
            具名的修改動作
        graph: the graph
            圖譜
    """

    create: CreateCapability = field(default_factory=CreateCapability)
    read: Operation = field(default_factory=lambda: Operation(supported=True))
    update_fields: Operation = field(default_factory=Operation)
    #: Editing JSON fields through a web form (json_field_editor plan D2); field formats: see ``JSON_FIELD_SPECS``.
    #: 網頁以表單編輯 JSON 欄位（json_field_editor 計畫 D2）；欄位格式見 ``JSON_FIELD_SPECS``。
    update_json: Operation = field(default_factory=Operation)
    delete: Operation = field(default_factory=Operation)
    actions: tuple[str, ...] = ()
    #: Field search on the web card page (click a field, type a keyword); searchable fields: see
    #: ``BaseHandler.search_fields``.
    #: 網頁卡片頁的欄位搜尋（點欄位、輸入關鍵字）；可搜尋的欄位見 ``BaseHandler.search_fields``。
    search: Operation = field(default_factory=Operation)
    graph: GraphCapability = field(default_factory=GraphCapability)

    def to_dict(self) -> dict[str, object]:
        """Return the shape the API returns, with tuples turned into lists.

        轉成 API 回傳的形狀（tuple 轉成 list）。

        Returns:
            the capabilities as a dict
            能力字典
        """
        return _lists(asdict(self))

    def existing_note_ops(self) -> tuple[bool, bool, bool, bool, tuple[str, ...]]:
        """Return the capabilities on existing notes, which must match across handlers sharing a note type (D4).

        回傳對既有筆記的能力；同一筆記類型的多個 handler 必須一致（D4）。

        Returns:
            (can read, can update fields, can update JSON fields, can delete, the actions sorted by name)
            （能否讀取, 能否改欄位, 能否改 JSON 欄位, 能否刪除, 依名稱排序的動作）
        """
        return (
            self.read.supported,
            self.update_fields.supported,
            self.update_json.supported,
            self.delete.supported,
            tuple(sorted(self.actions)),
        )


@dataclass(frozen=True)
class JsonFieldSpec:
    """The format of one JSON field: the Pydantic model of each item and a title (json_field_editor plan D1).

    一個 JSON 欄位的格式：每一筆資料的 Pydantic 模型與標題（json_field_editor 計畫 D1）。

    The model should set ``extra="allow"``, so that keys in existing data that the model does not list are kept
    as they are when saved through the web.

    模型建議設 ``extra="allow"``：既有資料有模型沒列出的鍵時，經網頁儲存也原樣保留。

    Attributes:
        item: the Pydantic model of each item
            每一筆資料的 Pydantic 模型
        title: the field's display title
            欄位的顯示標題
    """

    item: type[BaseModel]
    title: str


#: Reserved field-search keys: ``*`` matches any field and ``#`` matches tags; ``SEARCH_FIELDS`` may not use them.
#: 欄位搜尋的保留鍵：``*`` 比對任何欄位，``#`` 比對標籤；``SEARCH_FIELDS`` 不能用。
ANY_FIELD_KEY = "*"
TAG_SEARCH_KEY = "#"


@dataclass(frozen=True)
class SearchField:
    """One field button of the web field search: the Anki fields it matches (any one containing the keyword).

    網頁欄位搜尋的一個欄位按鈕：搜尋時比對哪些 Anki 欄位（任一個包含關鍵字就算）。

    Attributes:
        key: the key used by the API (alphanumeric; ``*`` and ``#`` are reserved for "any field" and "tags")
            API 用的鍵（英數字；``*``、``#`` 保留給「任何欄位」與「標籤」）
        title: the label on the button
            按鈕上的名稱
        fields: the Anki fields matched (with several, any one containing the keyword is enough, for example
            "name" matching the names in each language)
            比對的 Anki 欄位（多個時任一個包含即可，例如「名字」比對各語言的姓名）
    """

    key: str
    title: str
    fields: tuple[str, ...]


@dataclass(frozen=True)
class GraphSpec:
    """Which fields graph nodes use (handlers plan D12).

    圖譜節點取哪些欄位（handlers 計畫書 D12）。

    For items left undeclared, the label shows the nid and there is no subtitle or part of speech.

    沒宣告的項目：名稱顯示 nid、沒有副標與詞性。

    Attributes:
        default_label: the field node labels use by default
            節點名稱預設取的欄位
        subtitle: the subtitle field (shown on hover, under the API's existing ``translation`` key)
            副標欄位（滑鼠移上去顯示，沿用 API 的 ``translation`` 鍵）
        pos: the part-of-speech field (used by relation graph nodes)
            詞性欄位（關聯圖譜的節點用）
    """

    default_label: str | None = None
    subtitle: str | None = None
    pos: str | None = None

    def field_names(self) -> tuple[str, ...]:
        """Return the field names this spec declares.

        回傳宣告用到的欄位名。

        Returns:
            the declared field names, unset items left out
            欄位名，不含沒宣告的項目
        """
        return tuple(name for name in (self.default_label, self.subtitle, self.pos) if name)


def _lists(value: object) -> object:
    """Turn the tuples in nested data into lists.

    把巢狀資料中的 tuple 轉成 list。
    """
    if isinstance(value, dict):
        return {key: _lists(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_lists(item) for item in value]
    return value
