"""
Handler 的能力宣告（handlers 計畫書 D2）。

Capability declarations of a task handler (handlers plan D2).

每個 handler 以類別屬性 ``CAPABILITIES`` 宣告服務層能對它做什麼：建立、讀取、修改欄位、刪除、
具名動作與圖譜。能力綁在 handler 上，對它負責的所有筆記類型一體適用。宣告本身在建立時就檢查
形狀（例如開放建卡卻不能從網頁建時一定要寫原因）；宣告與實作是否一致由註冊表在註冊時檢查。

Each handler declares, as the ``CAPABILITIES`` class attribute, what the
service layer may do with it. The declaration validates its own shape on
construction; the registry checks it against the implementation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pydantic import BaseModel

#: 建卡管道：網頁、TG、本機腳本。Channels a card can be created from.
Channel = Literal["web", "tg", "script"]
CHANNELS: tuple[str, ...] = ("web", "tg", "script")

#: 不開放、又沒寫原因時的預設原因。Default reason for a closed operation.
NOT_OFFERED = "尚未開放"


def _default_reason(instance: object, supported: bool, reason: str | None) -> None:
    """不開放又沒寫原因時補上 ``NOT_OFFERED``；開放時不補（API 的 ``reason`` 為 null）。

    Fills ``NOT_OFFERED`` for a closed operation without a reason; an open
    one keeps None.
    """
    if not supported and not reason:
        object.__setattr__(instance, "reason", NOT_OFFERED)


@dataclass(frozen=True)
class Operation:
    """一項操作是否開放；不開放時必有原因（中文，前端直接顯示），沒寫就是 ``NOT_OFFERED``。

    Whether an operation is offered; a closed one always has a reason.
    """

    supported: bool = False
    reason: str | None = None

    def __post_init__(self) -> None:
        _default_reason(self, self.supported, self.reason)


@dataclass(frozen=True)
class CreateCapability:
    """建卡能力。Card creation capability.

    Attributes:
        supported: 服務層（網頁與 TG）能否建卡。Whether the service layer creates cards.
        channels: 實際能從哪些管道建立（給人看的說明）。Where cards can be created.
        flow: ``"form"`` 一張表單送出即可；``"preview"`` 先看 LLM 結果再勾選。The flow.
        deck: ``"selected"`` 建在指定牌組；``"fixed"`` 固定在 ``fixed_deck``。Deck rule.
        fixed_deck: ``deck`` 為 ``"fixed"`` 時的牌組。The fixed deck.
        note_type: 建出的筆記類型。The note type created.
        reason: 不開放（沒寫時為 ``NOT_OFFERED``）、或開放但不能從網頁建立時的說明。
            Why not, or where to create instead.
    """

    supported: bool = False
    channels: tuple[Channel, ...] = ()
    flow: Literal["form", "preview"] | None = None
    deck: Literal["selected", "fixed"] | None = None
    fixed_deck: str | None = None
    note_type: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
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
        if "web" in self.channels and not (self.supported and self.flow == "form"):
            raise ValueError("channels 含 web 時必須開放建卡且 flow 為 form")


@dataclass(frozen=True)
class GraphCapability:
    """圖譜能力。``links`` 表示有關聯連線（前端才顯示建立連線等操作）。

    Graph capability; ``links`` means the graph has relation links.
    """

    supported: bool = False
    links: bool = False
    reason: str | None = None

    def __post_init__(self) -> None:
        _default_reason(self, self.supported, self.reason)
        if self.links and not self.supported:
            raise ValueError("links 只能在開放圖譜時宣告")


@dataclass(frozen=True)
class HandlerCapabilities:
    """一個 handler 的全部能力。預設只開放讀取（基底的標準讀取，任何筆記類型都能讀）。

    All capabilities of a handler; by default only reading is offered.
    """

    create: CreateCapability = field(default_factory=CreateCapability)
    read: Operation = field(default_factory=lambda: Operation(supported=True))
    update_fields: Operation = field(default_factory=Operation)
    #: 網頁以表單編輯 JSON 欄位（json_field_editor 計畫 D2）；欄位格式見 ``JSON_FIELD_SPECS``。
    update_json: Operation = field(default_factory=Operation)
    delete: Operation = field(default_factory=Operation)
    actions: tuple[str, ...] = ()
    graph: GraphCapability = field(default_factory=GraphCapability)

    def to_dict(self) -> dict[str, object]:
        """轉成 API 回傳的形狀（tuple 轉成 list）。The shape returned by the API.

        Returns:
            能力字典。The capabilities as a dict.
        """
        return _lists(asdict(self))

    def existing_note_ops(self) -> tuple[bool, bool, bool, bool, tuple[str, ...]]:
        """對既有筆記的能力（讀取、改欄位、改 JSON 欄位、刪除、動作），同一筆記類型的多個 handler
        必須一致（D4）。

        The capabilities on existing notes, which must match across handlers
        sharing a note type.

        Returns:
            (能否讀取, 能否改欄位, 能否改 JSON 欄位, 能否刪除, 動作)。The five values.
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
    """一個 JSON 欄位的格式：每一筆資料的 Pydantic 模型與標題（json_field_editor 計畫 D1）。

    The format of one JSON field: the item model and a title.

    模型建議設 ``extra="allow"``：既有資料有模型沒列出的鍵時，經網頁儲存也原樣保留。
    """

    item: type[BaseModel]
    title: str


@dataclass(frozen=True)
class GraphSpec:
    """圖譜節點取哪些欄位（handlers 計畫書 D12）。沒宣告的項目：名稱顯示 nid、沒有副標與詞性。

    Which fields a graph node uses; unset means the note ID and no extras.

    Attributes:
        default_label: 節點名稱預設取的欄位。The default label field.
        subtitle: 副標欄位（滑鼠移上去顯示，沿用 API 的 ``translation`` 鍵）。The subtitle field.
        pos: 詞性欄位（關聯圖譜的節點用）。The part-of-speech field.
    """

    default_label: str | None = None
    subtitle: str | None = None
    pos: str | None = None

    def field_names(self) -> tuple[str, ...]:
        """宣告用到的欄位名。The field names this spec refers to.

        Returns:
            欄位名。Field names.
        """
        return tuple(name for name in (self.default_label, self.subtitle, self.pos) if name)


def _lists(value: object) -> object:
    """把巢狀資料中的 tuple 轉成 list。Turns nested tuples into lists."""
    if isinstance(value, dict):
        return {key: _lists(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_lists(item) for item in value]
    return value
