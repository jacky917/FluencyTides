"""劇情流程圖的資料格式：建置包（graph schema v1、遊戲設定）與 API 的回應。

Story-flowchart data contracts: the build package (graph schema v1, game
config) and the API responses (docs/wip/story_flowchart_FEAT_2026-10-31.md
D3, D4, D11, D12, D13).

匯入腳本（``scripts/story/``）與後端 API 共用同一份 schema；``validate_graph`` 與
``validate_node_lines`` 是建置包的不變式檢查，匯入前一定要通過。
The import script and the API share these models; ``validate_graph`` and
``validate_node_lines`` are the package invariants checked before import.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: 建置包格式版本；後端遇到不認得的版本就拒絕匯入。Supported package schema version.
SCHEMA_VERSION = 1

#: 核心節點類型（前端各有預設外觀）；另外允許 ``x-<名稱>`` 的自訂類型。Core node types.
CORE_NODE_TYPES = ("scene", "choice", "option", "merge", "condition", "ending", "route_exit")
#: 邊的種類。Edge kinds.
EdgeKind = Literal["normal", "branch", "merge", "cross_tab", "back"]
#: 邊的旗標。Edge flags.
EdgeFlag = Literal["inferred", "unlock"]

_CUSTOM_TYPE = re.compile(r"^x-[a-z0-9][a-z0-9_-]*$")
#: 相對物件鍵：以 ``/`` 分段，每段只有英數與 ``_.-``（``.``／``..`` 另外擋）。Relative object key.
_OBJECT_KEY = re.compile(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*$")
#: 不含路徑的檔名（會進 ``Content-Disposition``）。A bare filename.
_BARE_FILENAME = r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}$"


# ============================================================================
# 劇情圖（D4）
# ============================================================================


class StoryTab(BaseModel):
    """一個分頁（共通、各路線）。One tab (common route or a character route)."""

    key: str
    title: str
    titles: dict[str, str] | None = None
    order: int
    start: str
    avatar: str | None = None


class StoryNode(BaseModel):
    """劇情圖的一個節點。A node of the story graph.

    ``meta`` 是擷取器的自由欄位，前端只在除錯時顯示。``target_tab`` 只有 ``route_exit``
    使用：點了要切到哪個分頁。``type`` 不用 Literal：核心類型之外允許 ``x-<名稱>``，由
    :func:`validate_graph` 檢查並收集成錯誤訊息。
    ``meta`` is free-form extractor data; ``target_tab`` is only used by
    ``route_exit`` nodes. ``type`` stays a string (``x-*`` custom types are
    allowed) and is checked by :func:`validate_graph`.
    """

    key: str
    tab: str
    type: str
    title: str | None = None
    titles: dict[str, str] | None = None
    chapter: str | None = None
    chapter_label: str | None = None
    line_count: int = Field(0, ge=0)
    condition: str | None = None
    target_tab: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class EdgeEffect(BaseModel):
    """選這條邊會怎麼改變數：``set`` 覆寫（數字或字串）、``add`` 累加（只能是數字，不能是布林）。

    How taking an edge changes a game variable: ``set`` overwrites, ``add``
    accumulates and must be a number (not a bool).
    """

    var: str
    op: Literal["set", "add"]
    value: int | float | str

    @model_validator(mode="before")
    @classmethod
    def _add_needs_number(cls, data: Any) -> Any:
        # 在型別轉換前檢查原始值：否則 True 會被轉成 1、"4" 留成字串，累加時才出錯
        if isinstance(data, dict) and data.get("op") == "add":
            value = data.get("value")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"變數 {data.get('var')} 的 add 需要數字，收到 {value!r}")
        return data


class StoryEdge(BaseModel):
    """劇情圖的一條邊；JSON 的鍵是 ``from``。A story edge (JSON key ``from``)."""

    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    to: str
    kind: EdgeKind = "normal"
    order: int | None = None
    label: str | None = None
    flags: list[EdgeFlag] = Field(default_factory=list)
    effects: list[EdgeEffect] = Field(default_factory=list)
    condition_vars: dict[str, Any] | None = None


class StoryGraph(BaseModel):
    """整份劇情圖（schema v1）。The whole story graph."""

    schema_version: int = SCHEMA_VERSION
    tabs: list[StoryTab]
    nodes: list[StoryNode]
    edges: list[StoryEdge]

    def to_json_dict(self) -> dict[str, Any]:
        """轉成 JSON 用的 dict（``from`` 鍵、省略空的選填欄位）。JSON-ready dict."""
        return self.model_dump(by_alias=True, exclude_none=True)


def is_valid_node_type(node_type: str) -> bool:
    """核心類型或 ``x-<名稱>`` 自訂類型。Core or ``x-`` custom type."""
    return node_type in CORE_NODE_TYPES or bool(_CUSTOM_TYPE.match(node_type))


def validate_graph(graph: StoryGraph) -> list[str]:
    """檢查劇情圖的不變式；回傳錯誤訊息（空清單代表通過）。

    Check the graph invariants and return error messages (empty = valid):
    unique keys, known tabs and types, edge endpoints exist and stay inside a
    tab (except ``cross_tab``), every tab is a DAG once ``back`` edges are
    removed, every ``option`` is entered only from a ``choice`` or
    ``condition``. Edge kinds/flags and non-negative ``line_count`` are
    already enforced by the models.
    """
    errors: list[str] = []
    if graph.schema_version != SCHEMA_VERSION:
        errors.append(f"不支援的 schema_version {graph.schema_version}")

    tab_keys = [t.key for t in graph.tabs]
    if len(set(tab_keys)) != len(tab_keys):
        errors.append("分頁 key 重複")
    tabs = set(tab_keys)

    nodes: dict[str, StoryNode] = {}
    for node in graph.nodes:
        if node.key in nodes:
            errors.append(f"節點 key 重複：{node.key}")
        nodes[node.key] = node
        if node.tab not in tabs:
            errors.append(f"節點 {node.key} 的分頁 {node.tab} 不存在")
        if not is_valid_node_type(node.type):
            errors.append(f"節點 {node.key} 的類型 {node.type} 不合法")
        if node.type == "route_exit" and node.target_tab and node.target_tab not in tabs:
            errors.append(f"出口 {node.key} 指向不存在的分頁 {node.target_tab}")

    for tab in graph.tabs:
        start = nodes.get(tab.start)
        if start is None:
            errors.append(f"分頁 {tab.key} 的起點 {tab.start} 不存在")
        elif start.tab != tab.key:
            errors.append(f"分頁 {tab.key} 的起點 {tab.start} 不在這個分頁")

    adjacency: dict[str, list[str]] = {k: [] for k in nodes}
    for edge in graph.edges:
        src, dst = nodes.get(edge.from_), nodes.get(edge.to)
        if src is None or dst is None:
            errors.append(f"邊 {edge.from_} → {edge.to} 的端點不存在")
            continue
        if edge.kind != "cross_tab" and src.tab != dst.tab:
            errors.append(f"邊 {edge.from_} → {edge.to} 跨分頁卻不是 cross_tab")
        if dst.type == "option" and src.type not in ("choice", "condition"):
            errors.append(f"選項 {edge.to} 的入邊來自 {src.type} 節點 {edge.from_}")
        if edge.kind not in ("back", "cross_tab"):
            adjacency[edge.from_].append(edge.to)

    cycle = _find_cycle(adjacency)
    if cycle:
        errors.append("去掉 back 邊後仍有迴圈：" + " → ".join(cycle))
    return errors


def _find_cycle(adjacency: dict[str, list[str]]) -> list[str] | None:
    """以迭代 DFS 找一個迴圈；沒有時回 None。Find one cycle, or None."""
    WHITE, GREY, BLACK = 0, 1, 2
    color = dict.fromkeys(adjacency, WHITE)
    for root in adjacency:
        if color[root] != WHITE:
            continue
        stack: list[tuple[str, int]] = [(root, 0)]
        path = [root]
        color[root] = GREY
        while stack:
            node, i = stack[-1]
            children = adjacency[node]
            if i < len(children):
                stack[-1] = (node, i + 1)
                child = children[i]
                if color[child] == GREY:
                    return path[path.index(child):] + [child]
                if color[child] == WHITE:
                    color[child] = GREY
                    stack.append((child, 0))
                    path.append(child)
            else:
                color[node] = BLACK
                stack.pop()
                path.pop()
    return None


class NodeLineRow(BaseModel):
    """``node_lines.jsonl`` 的一列：某節點的第 ``seq`` 句是哪句台詞。One membership row."""

    node_key: str
    seq: int
    line_id: int


def validate_node_lines(graph: StoryGraph, rows: list[NodeLineRow]) -> list[str]:
    """檢查節點成員：節點存在、每句只屬於一個節點、``seq`` 從 0 連續、句數與 ``line_count`` 一致。

    Check membership rows: known nodes, each line in one node only, ``seq``
    contiguous from 0, counts equal ``line_count``.
    """
    errors: list[str] = []
    nodes = {n.key: n for n in graph.nodes}
    seen_lines: dict[int, str] = {}
    seqs: dict[str, list[int]] = {}
    for row in rows:
        if row.node_key not in nodes:
            errors.append(f"成員列指向不存在的節點 {row.node_key}")
            continue
        if row.line_id in seen_lines:
            errors.append(f"台詞 {row.line_id} 同時屬於 {seen_lines[row.line_id]} 與 {row.node_key}")
        seen_lines[row.line_id] = row.node_key
        seqs.setdefault(row.node_key, []).append(row.seq)
    for key, node in nodes.items():
        got = sorted(seqs.get(key, []))
        if got != list(range(len(got))):
            errors.append(f"節點 {key} 的 seq 不是從 0 連續")
        if len(got) != node.line_count:
            errors.append(f"節點 {key} 的 line_count={node.line_count}，成員列有 {len(got)} 句")
    return errors


# ============================================================================
# 遊戲設定（D3，``story_builds.config``）
# ============================================================================


class LanguageOption(BaseModel):
    """一種台詞語言。One text language."""

    code: str
    label: str


class LineAttrSpec(BaseModel):
    """抽屜要顯示的台詞屬性。A line attribute shown in the drawer."""

    key: str
    label: str
    kind: Literal["flag", "text"] = "text"


class CardTypeSpec(BaseModel):
    """一種會出現在流程圖上的卡片類型。A card type annotated on the flowchart.

    ``note_types`` 是這種卡片的 Anki 筆記類型名稱，卡片頁用來決定要不要顯示
    「在流程圖中顯示」（D17）。
    ``note_types`` lets the card page decide whether to offer the reverse lookup.
    """

    card_type: str
    label: str
    note_types: list[str] = Field(default_factory=list)


class CardsAnnotationConfig(BaseModel):
    """卡片註記的設定。Card annotation config."""

    card_types: list[CardTypeSpec] = Field(default_factory=list)


class AnnotationsConfig(BaseModel):
    """各種註記的設定。Annotation configs keyed by kind."""

    cards: CardsAnnotationConfig | None = None


class SaveSlot(BaseModel):
    """一個存檔欄位；``filename`` 是下載檔名，只能是不含路徑的檔名。One save slot (bare filename)."""

    id: str
    label: str
    filename: str = Field(pattern=_BARE_FILENAME)


class SavesConfig(BaseModel):
    """存檔設定；``template`` 是相對的 MinIO 物件鍵。Save config; ``template`` is a relative object key."""

    template: str
    slots: list[SaveSlot]
    install_hint: str | None = None

    @model_validator(mode="after")
    def _template_is_relative_key(self) -> "SavesConfig":
        key = self.template
        if not _OBJECT_KEY.match(key) or any(part in (".", "..") for part in key.split("/")):
            raise ValueError(f"存檔樣板必須是相對的物件鍵（例 story/<遊戲>/templates/quick.jpg），收到 {key!r}")
        return self


class StoryConfig(BaseModel):
    """流程圖設定（跟著版本走）。The flowchart config stored per build."""

    model_config = ConfigDict(extra="allow")

    languages: list[LanguageOption] = Field(default_factory=list)
    default_theme: str | None = None
    line_attrs: list[LineAttrSpec] = Field(default_factory=list)
    annotations: AnnotationsConfig = Field(default_factory=AnnotationsConfig)
    saves: SavesConfig | None = None
    route_presets: dict[str, dict[str, Any]] = Field(default_factory=dict)


class BuildManifest(BaseModel):
    """建置包的 ``manifest.json``。The build package manifest.

    ``assets.templates``：物件鍵 → 建置包內的相對路徑；``assets.thumbs_dir``：縮圖來源資料夾
    （不算進 ``source_hash``，檔名就是雜湊，內容不會變）。
    ``assets.templates`` maps object keys to package-relative files;
    ``assets.thumbs_dir`` is a source folder excluded from the hash.
    """

    schema_version: int = SCHEMA_VERSION
    game_id: str
    extractor: str
    config: dict[str, Any]
    stats: dict[str, Any] = Field(default_factory=dict)
    assets: dict[str, Any] = Field(default_factory=dict)
    note: str | None = None


# ============================================================================
# API 回應（D11–D13）
# ============================================================================


class StoryCapabilities(BaseModel):
    """前端依此決定要顯示哪些功能。Feature switches for the frontend."""

    saves: bool = False
    thumbs: bool = False
    annotations: list[str] = Field(default_factory=list)
    route_presets: bool = False
    languages: list[str] = Field(default_factory=list)


class StoryGameSummary(BaseModel):
    """``GET /story/games`` 的一項。One game in the list."""

    game_id: str
    title: str
    titles: dict[str, str]
    build_id: int
    capabilities: StoryCapabilities
    card_types: list[CardTypeSpec] = Field(default_factory=list)


class StoryManifest(BaseModel):
    """``GET /story/games/{game}``。The per-game manifest."""

    game_id: str
    titles: dict[str, str]
    build_id: int
    capabilities: StoryCapabilities
    languages: list[LanguageOption]
    line_attrs: list[LineAttrSpec]
    annotation_kinds: list[str]
    card_types: list[CardTypeSpec] = Field(default_factory=list)
    save_slots: list[SaveSlot] = Field(default_factory=list)
    save_install_hint: str | None = None
    route_presets: list[str] = Field(default_factory=list)
    default_theme: str | None = None
    stats: dict[str, Any] = Field(default_factory=dict)


class CardAnnotation(BaseModel):
    """一句台詞上某種卡片、某個詞的註記。Cards of one type and term on a line."""

    card_type: str
    label: str
    verb: str | None = None
    note_ids: list[int]


class StoryLine(BaseModel):
    """抽屜的一句台詞（D12）。One line in the drawer."""

    line_id: int
    seq: int
    kind: str
    anchor_key: str | None = None
    point: int | None = None
    speaker: str | None = None
    speakers: dict[str, str] = Field(default_factory=dict)
    texts: dict[str, str]
    voice: str | None = None
    thumb: str | None = None
    chapter_label: str | None = None
    attrs: dict[str, Any] = Field(default_factory=dict)
    annotations: dict[str, list[CardAnnotation]] = Field(default_factory=dict)
    can_save: bool = False


class StoryLinesPage(BaseModel):
    """一頁台詞。A page of lines."""

    node_key: str
    build_id: int
    total: int
    offset: int
    lines: list[StoryLine]
    next_annotated_seq: int | None = None


class StoryLocation(BaseModel):
    """``locate`` 的結果。Where a line lives in the graph."""

    game_id: str
    build_id: int
    tab: str
    node_key: str
    line_id: int
    seq: int


class StoryAnnotations(BaseModel):
    """各節點的註記統計與搜尋索引。Per-node annotation stats and search index."""

    build_id: int
    nodes: dict[str, dict[str, dict[str, int]]]
    index: dict[str, dict[str, dict[str, list[str]]]]


class SaveRequest(BaseModel):
    """``POST /story/games/{game}/saves``。The save request."""

    line_id: int
    slot: str = "quick"
    route_preset: str | None = None
