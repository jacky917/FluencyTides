"""Story-flowchart data contracts: the build package and the API responses.

劇情流程圖的資料格式：建置包（graph schema v1、遊戲設定）與 API 的回應。

The build package covers graph schema v1 and the game config; see
docs/wip/story_flowchart_FEAT_2026-10-31.md D3, D4, D11, D12, and D13.

建置包包含 graph schema v1 與遊戲設定；詳見 docs/wip/story_flowchart_FEAT_2026-10-31.md
D3、D4、D11、D12、D13。

The import script (``scripts/story/``) and the backend API share the same
schema. ``validate_graph`` and ``validate_node_lines`` are the invariant checks
of the build package and must pass before import.

匯入腳本（``scripts/story/``）與後端 API 共用同一份 schema；``validate_graph`` 與
``validate_node_lines`` 是建置包的不變式檢查，匯入前一定要通過。
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: The build package format version; the backend refuses to import any version it does not recognise.
#: 建置包格式版本；後端遇到不認得的版本就拒絕匯入。
SCHEMA_VERSION = 1

#: The core node types (each has a default look in the frontend); custom ``x-<name>`` types are also allowed.
#: 核心節點類型（前端各有預設外觀）；另外允許 ``x-<名稱>`` 的自訂類型。
CORE_NODE_TYPES = ("scene", "choice", "option", "merge", "condition", "ending", "route_exit")
#: The edge kinds.
#: 邊的種類。
EdgeKind = Literal["normal", "branch", "merge", "cross_tab", "back"]
#: The edge flags.
#: 邊的旗標。
EdgeFlag = Literal["inferred", "unlock"]

_CUSTOM_TYPE = re.compile(r"^x-[a-z0-9][a-z0-9_-]*$")
#: A relative object key: segments separated by ``/``, each containing only letters, digits, and ``_.-``
#: (``.`` / ``..`` are rejected separately).
#: 相對物件鍵：以 ``/`` 分段，每段只有英數與 ``_.-``（``.``／``..`` 另外擋）。
_OBJECT_KEY = re.compile(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*$")
#: A filename without a path (it goes into ``Content-Disposition``).
#: 不含路徑的檔名（會進 ``Content-Disposition``）。
_BARE_FILENAME = r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}$"


# ============================================================================
# 劇情圖（D4）
# ============================================================================


class StoryTab(BaseModel):
    """One tab (the common route or a character route).

    一個分頁（共通、各路線）。
    """

    key: str
    title: str
    titles: dict[str, str] | None = None
    order: int
    start: str
    avatar: str | None = None


class StoryNode(BaseModel):
    """A node of the story graph.

    劇情圖的一個節點。

    ``meta`` is a free-form field of the extractor; the frontend shows it only
    for debugging. ``target_tab`` is used only by ``route_exit``: the tab to
    switch to when clicked. ``type`` is not a Literal, because ``x-<name>`` is
    allowed besides the core types; :func:`validate_graph` checks it and
    collects the problems as error messages.

    ``meta`` 是擷取器的自由欄位，前端只在除錯時顯示。``target_tab`` 只有 ``route_exit``
    使用：點了要切到哪個分頁。``type`` 不用 Literal：核心類型之外允許 ``x-<名稱>``，由
    ``validate_graph`` 檢查並收集成錯誤訊息。
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
    """How taking an edge changes a game variable.

    選這條邊會怎麼改變數。

    ``set`` overwrites (a number or a string); ``add`` accumulates (it must be a
    number, not a bool).

    ``set`` 覆寫（數字或字串）、``add`` 累加（只能是數字，不能是布林）。
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
    """An edge of the story graph; its JSON key is ``from``.

    劇情圖的一條邊；JSON 的鍵是 ``from``。
    """

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
    """The whole story graph (schema v1).

    整份劇情圖（schema v1）。
    """

    schema_version: int = SCHEMA_VERSION
    tabs: list[StoryTab]
    nodes: list[StoryNode]
    edges: list[StoryEdge]

    def to_json_dict(self) -> dict[str, Any]:
        """Convert this graph to a dict ready for JSON.

        轉成 JSON 用的 dict。

        Returns:
            the dict, using the ``from`` key and omitting optional fields that are ``None``
            使用 ``from`` 鍵、省略為 ``None`` 的選填欄位的 dict
        """
        return self.model_dump(by_alias=True, exclude_none=True)


def is_valid_node_type(node_type: str) -> bool:
    """Return whether a node type is a core type or an ``x-<name>`` custom type.

    回傳節點類型是否為核心類型或 ``x-<名稱>`` 自訂類型。

    Args:
        node_type: the node type to check
            要檢查的節點類型

    Returns:
        ``True`` if it is a core type or matches the ``x-<name>`` custom form
        若為核心類型或符合 ``x-<名稱>`` 自訂格式則為 ``True``
    """
    return node_type in CORE_NODE_TYPES or bool(_CUSTOM_TYPE.match(node_type))


def validate_graph(graph: StoryGraph) -> list[str]:
    """Check the invariants of a story graph and return the error messages.

    檢查劇情圖的不變式並回傳錯誤訊息。

    The checks are: a supported ``schema_version``; unique tab and node keys;
    every node's tab exists and its type is valid; a ``route_exit`` target tab
    exists; every tab's start node exists and belongs to that tab; edge
    endpoints exist and stay inside one tab (except ``cross_tab``); every
    ``option`` is entered only from a ``choice`` or ``condition``; and the graph
    has no cycle once ``back`` and ``cross_tab`` edges are removed. Edge
    kinds/flags and a non-negative ``line_count`` are already enforced by the
    models.

    檢查項目：``schema_version`` 為支援的版本；分頁與節點 key 不重複；每個節點的分頁存在、類型合法；
    ``route_exit`` 指向的分頁存在；每個分頁的起點存在且屬於該分頁；邊的端點存在且不跨分頁
    （``cross_tab`` 除外）；``option`` 只能從 ``choice`` 或 ``condition`` 進入；去掉 ``back`` 與
    ``cross_tab`` 邊後沒有迴圈。邊的種類／旗標與 ``line_count`` 非負已由模型保證。

    Args:
        graph: the story graph to check
            要檢查的劇情圖

    Returns:
        the error messages; an empty list means the graph is valid
        錯誤訊息；空清單代表通過
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
    """Find one cycle with an iterative DFS.

    以迭代 DFS 找一個迴圈。

    Args:
        adjacency: node key mapped to the keys of its child nodes
            節點 key → 子節點 key

    Returns:
        the nodes on the cycle, starting and ending with the same node, or ``None`` if there is no cycle
        迴圈上的節點（頭尾為同一個節點）；沒有時回 ``None``
    """
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
    """One row of ``node_lines.jsonl``: which line is the ``seq``-th line of a node.

    ``node_lines.jsonl`` 的一列：某節點的第 ``seq`` 句是哪句台詞。
    """

    node_key: str
    seq: int
    line_id: int


def validate_node_lines(graph: StoryGraph, rows: list[NodeLineRow]) -> list[str]:
    """Check the node membership rows and return the error messages.

    檢查節點成員並回傳錯誤訊息。

    The checks are: the node exists, each line belongs to only one node,
    ``seq`` is contiguous from 0, and the number of lines equals
    ``line_count``.

    檢查項目：節點存在、每句只屬於一個節點、``seq`` 從 0 連續、句數與 ``line_count`` 一致。

    Args:
        graph: the story graph the rows belong to
            成員列所屬的劇情圖
        rows: the membership rows from ``node_lines.jsonl``
            ``node_lines.jsonl`` 的成員列

    Returns:
        the error messages; an empty list means the rows are valid
        錯誤訊息；空清單代表通過
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
    """One language of the line text.

    一種台詞語言。
    """

    code: str
    label: str


class LineAttrSpec(BaseModel):
    """A line attribute shown in the drawer.

    抽屜要顯示的台詞屬性。
    """

    key: str
    label: str
    kind: Literal["flag", "text"] = "text"


class CardTypeSpec(BaseModel):
    """A card type that is annotated on the flowchart.

    一種會出現在流程圖上的卡片類型。

    ``note_types`` are the Anki note type names of this card type; the card
    page uses them to decide whether to offer "show in flowchart" (D17).

    ``note_types`` 是這種卡片的 Anki 筆記類型名稱，卡片頁用來決定要不要顯示
    「在流程圖中顯示」（D17）。
    """

    card_type: str
    label: str
    note_types: list[str] = Field(default_factory=list)


class CardsAnnotationConfig(BaseModel):
    """The card annotation config.

    卡片註記的設定。
    """

    card_types: list[CardTypeSpec] = Field(default_factory=list)


class AnnotationsConfig(BaseModel):
    """The annotation configs, keyed by kind.

    各種註記的設定。
    """

    cards: CardsAnnotationConfig | None = None


class SaveSlot(BaseModel):
    """One save slot.

    一個存檔欄位。

    ``filename`` is the download filename and must be a bare filename without a
    path.

    ``filename`` 是下載檔名，只能是不含路徑的檔名。
    """

    id: str
    label: str
    filename: str = Field(pattern=_BARE_FILENAME)


class SavesConfig(BaseModel):
    """The save config; ``template`` is a relative MinIO object key.

    存檔設定；``template`` 是相對的 MinIO 物件鍵。
    """

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
    """The flowchart config, stored with each build.

    流程圖設定（跟著版本走）。
    """

    model_config = ConfigDict(extra="allow")

    languages: list[LanguageOption] = Field(default_factory=list)
    default_theme: str | None = None
    line_attrs: list[LineAttrSpec] = Field(default_factory=list)
    annotations: AnnotationsConfig = Field(default_factory=AnnotationsConfig)
    saves: SavesConfig | None = None
    route_presets: dict[str, dict[str, Any]] = Field(default_factory=dict)
    #: Whether this game provides BGM audio files (``story/<game>/bgm/<code>.ogg`` / ``.mp3``). Off by
    #: default: track titles and per-line BGM are unaffected; only ``capabilities.bgm`` is false and the
    #: frontend shows no BGM play button. Turn it on in the extractor (``--enable-bgm-audio``) only after the
    #: audio files are uploaded; see the story flowchart plan §12 "BGM reservation".
    #: 這款遊戲有沒有提供 BGM 音檔（``story/<遊戲>/bgm/<代號>.ogg``／``.mp3``）。預設關閉：曲名與
    #: 每句的 BGM 不受影響，只是 ``capabilities.bgm`` 為 false、前端不顯示 BGM 播放鍵。音檔上傳好以後
    #: 才在擷取器打開（``--enable-bgm-audio``），見劇情流程圖計畫書 §12「BGM 預留」。
    bgm_audio: bool = False


class BuildManifest(BaseModel):
    """The ``manifest.json`` of a build package.

    建置包的 ``manifest.json``。

    ``assets.templates`` maps object keys to paths inside the package.
    ``assets.thumbs_dir`` is the thumbnail source folder (not counted in
    ``source_hash``; the filenames are hashes, so the content never changes).
    ``assets.voices_dir`` is the voice source folder
    (``<game_lines.voice_file>.mp3``, also not counted in ``source_hash``), with
    the object key prefix ``voices_prefix``. ``assets.bgm_dir`` is the BGM source
    folder (``<game_bgm_tracks.code>.ogg`` or ``.mp3``, not counted in
    ``source_hash``), with the object key prefix ``bgm_prefix``.

    ``assets.templates``：物件鍵 → 建置包內的相對路徑；``assets.thumbs_dir``：縮圖來源資料夾
    （不算進 ``source_hash``，檔名就是雜湊，內容不會變）；``assets.voices_dir``：語音來源資料夾
    （``<game_lines.voice_file>.mp3``，同樣不算進 ``source_hash``），物件鍵前綴是 ``voices_prefix``；
    ``assets.bgm_dir``：BGM 來源資料夾（``<game_bgm_tracks.code>.ogg`` 或 ``.mp3``，不算進
    ``source_hash``），物件鍵前綴是 ``bgm_prefix``。
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
    """The feature switches the frontend uses to decide which features to show.

    前端依此決定要顯示哪些功能。
    """

    saves: bool = False
    thumbs: bool = False
    voices: bool = False
    #: Whether BGM can be played (``GET .../bgm/{code}``): true only when the config's ``bgm_audio`` is on
    #: and there is an asset source.
    #: 可以播放 BGM（``GET .../bgm/{code}``）：設定的 ``bgm_audio`` 打開、而且有素材來源才是 true。
    bgm: bool = False
    annotations: list[str] = Field(default_factory=list)
    route_presets: bool = False
    languages: list[str] = Field(default_factory=list)


class StoryGameSummary(BaseModel):
    """One game in the list returned by ``GET /story/games``.

    ``GET /story/games`` 的一項。
    """

    game_id: str
    title: str
    titles: dict[str, str]
    build_id: int
    capabilities: StoryCapabilities
    card_types: list[CardTypeSpec] = Field(default_factory=list)


class StoryManifest(BaseModel):
    """The per-game manifest returned by ``GET /story/games/{game}``.

    ``GET /story/games/{game}`` 的回應。
    """

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
    """The annotation of one card type and one term on a line.

    一句台詞上某種卡片、某個詞的註記。
    """

    card_type: str
    label: str
    verb: str | None = None
    note_ids: list[int]


class StoryLineBgm(BaseModel):
    """One BGM track played during a line.

    一句台詞播放的一首 BGM（``game_line_bgm`` 一列 join ``game_bgm_tracks``）。

    It is one ``game_line_bgm`` row joined with ``game_bgm_tracks``. Like
    ``texts`` / ``speakers``, ``titles`` is keyed by BCP 47, and languages
    without a track title are omitted (for example, the title-screen BGM51 has
    a title in neither language, so ``titles`` is empty and the frontend falls
    back to showing ``code``). ``code`` is also the name of the BGM audio file
    (``GET .../bgm/{code}``).

    ``titles`` 和 ``texts``／``speakers`` 一樣以 BCP 47 為鍵，沒有曲名的語言省略（例：標題畫面的
    BGM51 兩種語言都沒有，``titles`` 是空的，前端退回顯示 ``code``）。``code`` 也是 BGM 音檔的名稱
    （``GET .../bgm/{code}``）。
    """

    code: str
    #: The track's order within the line, counting from 0; a line that switches tracks while playing has
    #: two entries, 0 and 1.
    #: 同一句的第幾首（0 起算）；播放中切換曲子的台詞有 0 和 1 兩筆。
    ord: int = 0
    titles: dict[str, str] = Field(default_factory=dict)
    #: The number on the EXTRA music gallery screen; ``None`` for tracks not in the gallery.
    #: EXTRA 音樂鑑賞畫面的編號；不在鑑賞畫面的是 None。
    gallery_no: int | None = None


class StoryLine(BaseModel):
    """One line in the drawer (D12).

    抽屜的一句台詞（D12）。

    BGM is a formal field: ``bgm`` is in ``ord`` order (an empty list when there
    is no BGM), ``bgm_detect`` is how it was detected (``A`` / ``實測`` /
    ``B推定``; ``None`` for choice screens and the like), and ``bgm_may_change``
    means the BGM may switch while this line plays.

    BGM 是正式欄位：``bgm`` 依 ``ord`` 排列（沒有 BGM 時是空陣列），``bgm_detect`` 是偵測方式
    （``A``／``實測``／``B推定``，選項畫面等為 None），``bgm_may_change`` 表示這句播放中 BGM 可能切換。
    """

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
    bgm: list[StoryLineBgm] = Field(default_factory=list)
    bgm_detect: str | None = None
    bgm_may_change: bool = False


class StoryLinesPage(BaseModel):
    """A page of lines.

    一頁台詞。
    """

    node_key: str
    build_id: int
    total: int
    offset: int
    lines: list[StoryLine]
    next_annotated_seq: int | None = None


class StoryLocation(BaseModel):
    """The result of ``locate``: where a line lives in the graph.

    ``locate`` 的結果。
    """

    game_id: str
    build_id: int
    tab: str
    node_key: str
    line_id: int
    seq: int


class StoryAnnotations(BaseModel):
    """The per-node annotation statistics and the search index.

    各節點的註記統計與搜尋索引。
    """

    build_id: int
    nodes: dict[str, dict[str, dict[str, int]]]
    index: dict[str, dict[str, dict[str, list[str]]]]


class SaveRequest(BaseModel):
    """The save request, ``POST /story/games/{game}/saves``.

    ``POST /story/games/{game}/saves`` 的請求。
    """

    line_id: int
    slot: str = "quick"
    route_preset: str | None = None
