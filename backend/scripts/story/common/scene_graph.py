"""從語料庫的場景與場景連接組出劇情圖（schema v1）：與遊戲無關的部分。

Build a story graph (schema v1) from corpus scenes and scene links; the
game-agnostic part (docs/wip/story_flowchart_FEAT_2026-10-31.md D5).

輸入是 ``game_script_files``、``game_scenes``、``game_lines``、``game_scene_links`` 的列，
加上遊戲規則（:class:`GameRules`：分頁、標題、章節解析、補充連接）。步驟：

1. 清理連接：自迴圈、同一場景另有去向時的「結束」、選項場景的 ``next``、指向沒有台詞的場景
   （改指向下一個檔案的同名場景，或丟棄）。再補上遊戲規則提供的連接（例如通關後入口）。
2. 找往回跳的連接（DFS），當作 ``back`` 邊，不參與合併與分歧判定。
3. 合併：同一檔案內、沒有分歧、依序串接、章節相同的場景合成一個單元。
4. 依單元的出邊決定結構：選項 → ``choice`` 的分支；兩個以上去向 → 菱形 ``condition``；
   選項直接到合流標籤時補 0 句的虛擬 ``option``；跨分頁 → ``route_exit`` 卡片；
   ``unlock`` 連接 → 菱形（或同分頁的 AFTER 直接畫虛線）。
5. 類型、標題、章節、節點鍵、分頁起點、節點成員。

Steps: clean links, find back links, merge chains into units, derive
structure (choices, conditions, virtual options, route exits, unlocks), then
types, titles, chapters, keys, tab starts and membership rows.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable

from app.schemas.story import NodeLineRow, StoryEdge, StoryGraph, StoryNode, StoryTab

CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
#: 找出口錨點時最多往下走幾步（防止資料有迴圈時卡死；實際只走 1–3 步）。
#: Max steps when walking down to an exit anchor (cycle guard; real walks take 1-3).
ANCHOR_MAX_STEPS = 64
#: 推路線預設時，從帶旗標的邊沿單一出邊最多走幾步找出口卡片（分歧 → 菱形 → 出口通常 2–3 步）。
#: Max single-out-edge steps from a flagged edge to its route exit (usually 2-3).
PRESET_MAX_STEPS = 8


# ============================================================================
# 輸入列
# ============================================================================


@dataclass(frozen=True)
class FileRow:
    """``game_script_files`` 的一列。A script file row."""

    file_id: int
    file_name: str
    file_no: str
    title_ja: str
    title_zh: str | None
    file_order: int


@dataclass(frozen=True)
class SceneRow:
    """``game_scenes`` 的一列。A scene row."""

    scene_id: int
    file_id: int
    label: str
    scene_order: int


@dataclass(frozen=True)
class LineRow:
    """``game_lines`` 的一列（只取排版需要的欄位，且只有 ``status='active'``）。A line row."""

    id: int
    scene_id: int
    story_order: int
    kind: str
    chapter_label: str | None


@dataclass
class LinkRow:
    """``game_scene_links`` 的一列（或遊戲規則補上的連接）。A scene link row."""

    from_scene_id: int
    to_scene_id: int | None
    kind: str
    option_index: int | None = None
    option_text_ja: str | None = None
    option_text_zh: str | None = None
    condition_text_ja: str | None = None
    condition_text_zh: str | None = None
    condition_vars: dict[str, Any] | None = None
    effects: list[dict[str, Any]] | None = None
    evidence: str = "observed"
    flags: tuple[str, ...] | None = None
    note: str | None = None
    link_id: int | None = None


@dataclass
class ExtraLink:
    """遊戲規則補上的連接（資料庫還沒有的通關後入口、路線分歧等）。

    ``src``／``dst`` 是 ``(檔號或檔名, 標籤)``：``src`` 的標籤為 None 表示「這個檔案的結尾場景」，
    ``dst`` 的標籤為 None 表示「這個檔案的第一個場景」。若與既有連接同起點、同終點，則改為更新那一筆。

    A supplementary link supplied by the game rules. ``src``/``dst`` are
    ``(file_no or file_name, label)``; a None source label means the file's
    end scene, a None target label its first scene. When a link with the
    same endpoints exists, it is patched instead.
    """

    src: tuple[str, str | None]
    dst: tuple[str, str | None]
    kind: str = "jump"
    condition_vars: dict[str, Any] | None = None
    condition_text_ja: str | None = None
    condition_text_zh: str | None = None
    evidence: str = "inferred"
    flags: tuple[str, ...] | None = None
    note: str | None = None


@dataclass
class GameRules:
    """遊戲專屬的規則；擷取器提供，建圖程式不知道是哪款遊戲。Game-specific rules.

    Attributes:
        prefix: 節點鍵的前綴，例 ``sw``。Node-key prefix.
        main_tab: 主分頁（共通）；出口卡片的鍵在這個分頁省略來源。The main tab.
        tabs: 分頁定義 ``{key, title, titles?, order, avatar?}``。Tab definitions.
        tab_of_file: 檔案屬於哪個分頁。Which tab a file belongs to.
        is_after_file: 是否為通關後的 AFTER 檔案。Whether a file is an AFTER story.
        file_title: 檔案的顯示標題 ``(ja, zh)``。A file's display title.
        parse_chapter: 台詞的章節標籤 → ``(chapter, chapter_label)``；認不得時回 None。
        chapter_prefix: 某檔案的章節標籤前綴（例 ``RESTART · ``）。Chapter label prefix.
        relay_label: 依變數跳轉的中繼標籤（例 ``dummyN``）。Relay label pattern.
        choice_label: 選項畫面的標籤（例 ``dummyselectN``）。Choice-screen label.
        extra_links: 補充連接。Supplementary links.
        titles: 標題覆寫 ``{node_key: str | {"ja-JP": …, "zh-Hant": …}}``。Overrides.
        default_branch_label: 其他分支有條件、這條沒有時的標籤。Label of the fallback branch.
        unlock_label: ``unlock`` 邊沒有條件文字時的標籤。Default unlock label.
    """

    prefix: str
    main_tab: str
    tabs: list[dict[str, Any]]
    tab_of_file: Callable[[FileRow], str]
    is_after_file: Callable[[FileRow], bool] = lambda f: False
    file_title: Callable[[FileRow], tuple[str, str | None]] = lambda f: (f.title_ja, f.title_zh)
    parse_chapter: Callable[[str], tuple[str, str] | None] = lambda s: None
    chapter_prefix: Callable[[FileRow], str] = lambda f: ""
    relay_label: re.Pattern[str] = re.compile(r"^dummy\d+$")
    choice_label: re.Pattern[str] = re.compile(r"^dummyselect(\d+)$")
    extra_links: list[ExtraLink] = field(default_factory=list)
    titles: dict[str, Any] = field(default_factory=dict)
    default_branch_label: str = "その他"
    unlock_label: str = "通關後"


@dataclass
class BuildResult:
    """建圖結果。The build result."""

    graph: StoryGraph
    rows: list[NodeLineRow]
    warnings: list[str]
    stats: dict[str, Any]


# ============================================================================
# 內部結構
# ============================================================================


@dataclass
class _Node:
    key: str = ""
    tab: str = ""
    type: str = "scene"
    title: str | None = None
    titles: dict[str, str] | None = None
    chapter: str | None = None
    chapter_label: str | None = None
    condition: str | None = None
    target_tab: str | None = None
    scenes: list[int] = field(default_factory=list)
    lines: list[int] = field(default_factory=list)
    file: FileRow | None = None
    sort_key: tuple = ()
    forced_type: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Edge:
    src: int
    dst: int
    kind: str = "normal"
    order: int | None = None
    label: str | None = None
    label_zh: str | None = None
    flags: list[str] = field(default_factory=list)
    effects: list[dict[str, Any]] = field(default_factory=list)
    condition_vars: dict[str, Any] | None = None
    structural: bool = True


def _vars_key(vars_: dict[str, Any] | None) -> str:
    return json.dumps(sorted((vars_ or {}).items()), ensure_ascii=False)


def _format_vars(vars_: dict[str, Any] | None, example: bool) -> str | None:
    if not vars_:
        return None
    body = ", ".join(f"{k}={v}" for k, v in vars_.items())
    return f"例：{body}" if example else body


# ============================================================================
# 建圖
# ============================================================================


def build_story_graph(
    files: list[FileRow],
    scenes: list[SceneRow],
    lines: list[LineRow],
    links: list[LinkRow],
    rules: GameRules,
) -> BuildResult:
    """組出劇情圖與節點成員。Build the story graph and membership rows.

    Args:
        files: 這款遊戲的場景檔。Script files of the game.
        scenes: 場景標籤。Scenes.
        lines: ``status='active'`` 的台詞。Active lines.
        links: 場景連接。Scene links.
        rules: 遊戲規則。Game rules.

    Returns:
        劇情圖、成員列、警告與統計。The graph, rows, warnings and stats.
    """
    warnings: list[str] = []
    file_by_id = {f.file_id: f for f in files}
    scene_by_id = {s.scene_id: s for s in scenes}
    lines_by_scene: dict[int, list[LineRow]] = defaultdict(list)
    for line in sorted(lines, key=lambda r: r.story_order):
        if line.scene_id in scene_by_id:
            lines_by_scene[line.scene_id].append(line)

    def scene_key(sid: int) -> tuple:
        s = scene_by_id[sid]
        return (file_by_id[s.file_id].file_order, s.scene_order, sid)

    def scene_name(sid: int) -> str:
        s = scene_by_id[sid]
        return f"{file_by_id[s.file_id].file_name}*{s.label}"

    def is_relay(sid: int) -> bool:
        label = scene_by_id[sid].label
        return bool(rules.relay_label.match(label) or rules.choice_label.match(label))

    def has_lines(sid: int) -> bool:
        return bool(lines_by_scene.get(sid))

    # ---------- 章節（空白標籤沿用前一句） ----------
    scene_chapter: dict[int, tuple[str, str] | None] = {}
    last_label: str | None = None
    for line in sorted(lines, key=lambda r: r.story_order):
        label = (line.chapter_label or "").strip()
        if label:
            last_label = label
        if line.scene_id in scene_chapter or line.scene_id not in scene_by_id:
            continue
        parsed = rules.parse_chapter(last_label) if last_label else None
        if parsed:
            f = file_by_id[scene_by_id[line.scene_id].file_id]
            parsed = (parsed[0], rules.chapter_prefix(f) + parsed[1])
        scene_chapter[line.scene_id] = parsed

    # ---------- 1. 清理連接 ----------
    files_sorted = sorted(files, key=lambda f: f.file_order)
    scenes_by_file: dict[int, list[SceneRow]] = defaultdict(list)
    for s in sorted(scenes, key=lambda s: s.scene_order):
        scenes_by_file[s.file_id].append(s)
    label_index = {(s.file_id, s.label): s.scene_id for s in scenes}

    def redirect_empty(sid: int) -> int | None:
        """沒有台詞的非中繼場景：改指向下一個檔案的同名場景（遷移時同名標籤對錯檔案），否則丟棄。"""
        if has_lines(sid) or is_relay(sid):
            return sid
        s = scene_by_id[sid]
        f = file_by_id[s.file_id]
        for nxt in files_sorted:
            if nxt.file_order > f.file_order:
                cand = label_index.get((nxt.file_id, s.label))
                if cand and has_lines(cand):
                    warnings.append(f"連接指向沒有台詞的 {scene_name(sid)}，改指向 {scene_name(cand)}")
                    return cand
                break
        return None

    cleaned: list[LinkRow] = []
    for link in links:
        if link.from_scene_id not in scene_by_id:
            continue
        if not has_lines(link.from_scene_id) and not is_relay(link.from_scene_id):
            continue  # 沒有台詞的場景出發：不會走到
        if link.to_scene_id is not None:
            if link.to_scene_id not in scene_by_id:
                continue
            target = redirect_empty(link.to_scene_id)
            if target is None:
                warnings.append(f"丟棄指向沒有台詞的場景的連接 {scene_name(link.from_scene_id)} → {scene_name(link.to_scene_id)}")
                continue
            link.to_scene_id = target
            if link.to_scene_id == link.from_scene_id:
                warnings.append(f"丟棄自迴圈 {scene_name(link.from_scene_id)}")
                continue
        cleaned.append(link)

    def resolve(ref: tuple[str, str | None], as_source: bool) -> int | None:
        token, label = ref
        f = next((x for x in files_sorted if x.file_name == token or x.file_name.removesuffix(".ks") == token), None)
        if f is None:
            same_no = [x for x in files_sorted if x.file_no == token]
            f = same_no[0] if len(same_no) == 1 else None
        if f is None:
            return None
        if label is not None:
            return label_index.get((f.file_id, label))
        candidates = [s.scene_id for s in scenes_by_file[f.file_id] if has_lines(s.scene_id)]
        if not candidates:
            return None
        if not as_source:
            return candidates[0]
        ends = [sid for sid in candidates
                if not any(x.from_scene_id == sid and x.to_scene_id is not None for x in cleaned)]
        return (ends or candidates)[-1]

    for extra in rules.extra_links:
        src, dst = resolve(extra.src, True), resolve(extra.dst, False)
        if src is None or dst is None:
            warnings.append(f"補充連接 {extra.src} → {extra.dst} 找不到場景，略過")
            continue
        existing = next((x for x in cleaned if x.from_scene_id == src and x.to_scene_id == dst), None)
        if existing is None:
            existing = LinkRow(from_scene_id=src, to_scene_id=dst, kind=extra.kind, evidence=extra.evidence)
            cleaned.append(existing)
        existing.kind = extra.kind
        existing.evidence = extra.evidence
        for attr in ("condition_vars", "condition_text_ja", "condition_text_zh", "flags", "note"):
            value = getattr(extra, attr)
            if value is not None:
                setattr(existing, attr, value)

    out_links: dict[int, list[LinkRow]] = defaultdict(list)
    for link in cleaned:
        out_links[link.from_scene_id].append(link)
    final_links: list[LinkRow] = []
    for sid, group in out_links.items():
        has_choice = any(x.kind == "choice" for x in group)
        has_target = any(x.to_scene_id is not None and x.kind != "unlock" for x in group)
        for link in group:
            if has_choice and link.kind != "choice" and link.kind != "unlock":
                continue  # 選項場景的 next 是雜訊
            if link.to_scene_id is None and has_target:
                continue  # 另有去向時的「結束」
            if link.to_scene_id is None:
                continue
            final_links.append(link)

    # 同起點、同終點、同種類的重複只留一筆；jump 與 next 指向同一處時留 jump（有條件）
    dedup: dict[tuple, LinkRow] = {}
    for link in final_links:
        kind_group = "choice" if link.kind == "choice" else ("unlock" if link.kind == "unlock" else "flow")
        key = (link.from_scene_id, link.to_scene_id, kind_group,
               link.option_index if link.kind == "choice" else None)
        prev = dedup.get(key)
        if prev is None or (prev.kind == "next" and link.kind == "jump"):
            dedup[key] = link
    flow_links = list(dedup.values())

    # ---------- 2. 往回跳的連接 ----------
    used_scenes = {s.scene_id for s in scenes if has_lines(s.scene_id)}
    for link in flow_links:
        used_scenes.add(link.from_scene_id)
        if link.to_scene_id is not None:
            used_scenes.add(link.to_scene_id)
    adjacency: dict[int, list[LinkRow]] = defaultdict(list)
    for link in sorted(flow_links, key=lambda x: (scene_key(x.to_scene_id), x.option_index or 0)):
        if link.kind != "unlock":
            adjacency[link.from_scene_id].append(link)
    back: set[int] = set()
    color: dict[int, int] = {}
    for root in sorted(used_scenes, key=scene_key):
        if color.get(root):
            continue
        stack = [(root, iter(adjacency[root]))]
        color[root] = 1
        while stack:
            node, it = stack[-1]
            link = next(it, None)
            if link is None:
                color[node] = 2
                stack.pop()
                continue
            child = link.to_scene_id
            state = color.get(child, 0)
            if state == 1:
                back.add(id(link))
                warnings.append(f"往回跳的連接（當作 back 邊）：{scene_name(link.from_scene_id)} → {scene_name(child)}")
            elif state == 0:
                color[child] = 1
                stack.append((child, iter(adjacency[child])))

    structural = [x for x in flow_links if id(x) not in back and x.kind != "unlock"]
    back_links = [x for x in flow_links if id(x) in back]
    unlock_links = [x for x in flow_links if x.kind == "unlock"]
    s_out: dict[int, list[LinkRow]] = defaultdict(list)
    s_in: dict[int, list[LinkRow]] = defaultdict(list)
    for link in structural:
        s_out[link.from_scene_id].append(link)
        s_in[link.to_scene_id].append(link)

    # ---------- 3. 合併成單元 ----------
    parent: dict[int, int] = {}
    for sid in used_scenes:
        ins = s_in.get(sid, [])
        if len(ins) != 1 or ins[0].kind == "choice":
            continue
        p = ins[0].from_scene_id
        outs = s_out.get(p, [])
        if len(outs) != 1 or outs[0].kind == "choice":
            continue
        if scene_by_id[p].file_id != scene_by_id[sid].file_id:
            continue
        ch_p, ch_s = scene_chapter.get(p), scene_chapter.get(sid)
        if ch_p and ch_s and ch_p[0] != ch_s[0]:
            continue
        parent[sid] = p
    child_of = {p: c for c, p in parent.items()}

    nodes: list[_Node] = []
    unit_of: dict[int, int] = {}
    for sid in sorted(used_scenes, key=scene_key):
        if sid in parent:
            continue
        chain = [sid]
        while chain[-1] in child_of:
            chain.append(child_of[chain[-1]])
        node = _Node(scenes=chain, file=file_by_id[scene_by_id[sid].file_id], sort_key=scene_key(sid))
        ordered = sorted((line for s in chain for line in lines_by_scene.get(s, [])), key=lambda r: r.story_order)
        node.lines = [line.id for line in ordered]
        chapter = next((scene_chapter.get(s) for s in chain if scene_chapter.get(s)), None)
        if chapter:
            node.chapter, node.chapter_label = chapter
        node.tab = rules.tab_of_file(node.file)
        node.meta = {"scenes": [scene_name(s) for s in chain]}
        idx = len(nodes)
        nodes.append(node)
        for s in chain:
            unit_of[s] = idx

    edges: list[_Edge] = []

    def add_node(**kwargs: Any) -> int:
        nodes.append(_Node(**kwargs))
        return len(nodes) - 1

    # ---------- 4. 結構 ----------
    for idx in range(len(nodes)):
        node = nodes[idx]
        last = node.scenes[-1]
        outs = s_out.get(last, [])
        if not outs:
            continue
        choices = sorted((x for x in outs if x.kind == "choice"), key=lambda x: x.option_index or 0)
        if choices:
            node.forced_type = "choice"
            for i, link in enumerate(choices):
                edges.append(_Edge(src=idx, dst=unit_of[link.to_scene_id], kind="branch",
                                   order=link.option_index if link.option_index is not None else i,
                                   label=link.option_text_ja, label_zh=link.option_text_zh,
                                   effects=list(link.effects or [])))
            continue
        targets: dict[int, LinkRow] = {}
        for link in outs:
            targets.setdefault(unit_of[link.to_scene_id], link)
        if len(targets) == 1:
            dst, link = next(iter(targets.items()))
            edges.append(_Edge(src=idx, dst=dst, flags=_flags(link), condition_vars=link.condition_vars or None))
            continue
        if node.lines:
            cond = add_node(tab=node.tab, forced_type="condition", file=node.file,
                            sort_key=node.sort_key + (1,), meta={"after": node.meta["scenes"][-1]})
            nodes[cond].meta["anchor"] = idx
            edges.append(_Edge(src=idx, dst=cond))
        else:
            cond = idx
            node.forced_type = "condition"
        any_vars = any(link.condition_vars for link in targets.values())
        ordered_targets = sorted(targets.items(), key=lambda kv: (
            1 if kv[1].condition_vars else 0, _vars_key(kv[1].condition_vars), nodes[kv[0]].sort_key))
        texts: list[str] = []
        for i, (dst, link) in enumerate(ordered_targets):
            label_zh = link.condition_text_zh or link.condition_text_ja
            label = label_zh or _format_vars(link.condition_vars, link.evidence == "probed")
            if label is None and any_vars:
                label = rules.default_branch_label
            if link.condition_text_zh or link.condition_text_ja:
                texts.append(link.condition_text_zh or link.condition_text_ja or "")
            edges.append(_Edge(src=cond, dst=dst, kind="branch", order=i, label=label,
                               flags=_flags(link), condition_vars=link.condition_vars or None))
        vars_used = sorted({k for link in targets.values() for k in (link.condition_vars or {})})
        if vars_used:
            nodes[cond].condition = "依變數決定：" + ", ".join(vars_used)
        else:
            nodes[cond].condition = "；".join(texts) if texts else "依條件決定"

    for link in back_links:
        edges.append(_Edge(src=unit_of[link.from_scene_id], dst=unit_of[link.to_scene_id], kind="back",
                           structural=False))

    # 選項：目標只有這一條入邊時就是 option 節點，否則補 0 句的虛擬節點
    def in_count(target: int) -> int:
        return sum(1 for e in edges if e.dst == target and e.structural)

    for e in list(edges):
        if e.kind != "branch" or nodes[e.src].forced_type != "choice":
            continue
        target = nodes[e.dst]
        if in_count(e.dst) == 1 and target.forced_type is None:
            target.forced_type = "option"
            target.title, target.titles = e.label, _titles(e.label, e.label_zh)
            continue
        choice_node = nodes[e.src]
        choice_scene = scene_by_id[choice_node.scenes[-1]].label
        m = rules.choice_label.match(choice_scene)
        tag = m.group(1) if m else choice_scene
        virtual = add_node(tab=choice_node.tab, forced_type="option", file=choice_node.file,
                           title=e.label, titles=_titles(e.label, e.label_zh),
                           sort_key=choice_node.sort_key + (2, e.order or 0),
                           meta={"virtual": True, "opt": f"{tag}-{e.order}"})
        edges.append(_Edge(src=virtual, dst=e.dst))
        e.dst = virtual

    # ---------- 通關後入口 ----------
    def outs_of(idx: int) -> list[_Edge]:
        return sorted((e for e in edges if e.src == idx and e.structural), key=lambda e: (e.order or 0))

    def anchor_of(idx: int) -> int:
        cur = idx
        for _ in range(ANCHOR_MAX_STEPS):
            outs = outs_of(cur)
            node = nodes[cur]
            if node.forced_type in ("choice", "condition") or len(outs) > 1:
                cur = outs[0].dst
            elif node.forced_type == "option" and outs:
                cur = outs[0].dst
            else:
                return cur
        return cur

    exit_nodes: dict[tuple[str, str], int] = {}

    def exit_node(src_tab: str, dst_tab: str, anchor: _Node) -> int:
        key = (src_tab, dst_tab)
        if key not in exit_nodes:
            exit_nodes[key] = add_node(tab=src_tab, forced_type="route_exit", target_tab=dst_tab,
                                       file=anchor.file, sort_key=anchor.sort_key + (9,))
        return exit_nodes[key]

    for link in sorted(unlock_links, key=lambda x: scene_key(x.from_scene_id)):
        src, dst = unit_of[link.from_scene_id], unit_of[link.to_scene_id]
        flags = _flags(link)
        if "unlock" not in flags:
            flags.insert(0, "unlock")
        label = link.condition_text_zh or link.condition_text_ja or rules.unlock_label
        same_tab = nodes[src].tab == nodes[dst].tab
        if same_tab and rules.is_after_file(nodes[dst].file):
            edges.append(_Edge(src=src, dst=dst, flags=flags, label=label))
            continue
        anchor = anchor_of(src)
        cond = add_node(tab=nodes[anchor].tab, forced_type="condition", file=nodes[anchor].file,
                        condition=label, sort_key=nodes[anchor].sort_key + (3,),
                        meta={"unlock": True, "anchor": anchor})
        for e in outs_of(anchor):
            e.src, e.kind, e.order = cond, "branch", 0
            e.label = e.label or rules.default_branch_label
        edges.append(_Edge(src=anchor, dst=cond))
        target = dst if same_tab else exit_node(nodes[anchor].tab, nodes[dst].tab, nodes[anchor])
        order = 1 if any(e.src == cond for e in edges) else 0
        edges.append(_Edge(src=cond, dst=target, kind="branch", order=order, label=label, flags=flags))

    # ---------- 跨分頁 → 出口卡片 ----------
    for e in edges:
        if nodes[e.src].tab != nodes[e.dst].tab and nodes[e.dst].forced_type != "route_exit":
            dst_tab = nodes[e.dst].tab
            if e.kind == "back":
                e.kind = "cross_tab"
                continue
            e.dst = exit_node(nodes[e.src].tab, dst_tab, nodes[e.src])

    # ---------- 5. 類型 ----------
    in_deg: dict[int, int] = defaultdict(int)
    out_deg: dict[int, int] = defaultdict(int)
    for e in edges:
        if e.structural and e.kind != "cross_tab":
            in_deg[e.dst] += 1
            out_deg[e.src] += 1
    for idx, node in enumerate(nodes):
        if node.forced_type:
            node.type = node.forced_type
        elif in_deg[idx] >= 2:
            node.type = "merge"
        elif out_deg[idx] == 0 and node.lines and not rules.is_after_file(node.file) \
                and not _is_final_chapter(node.chapter_label):
            node.type = "ending"
        else:
            node.type = "scene"
    for e in edges:
        if e.kind == "normal" and nodes[e.dst].type == "merge":
            e.kind = "merge"

    # ---------- 節點鍵 ----------
    file_no_count: dict[str, int] = defaultdict(int)
    for f in files:
        file_no_count[f.file_no] += 1

    def file_key(f: FileRow) -> str:
        return f.file_no if file_no_count[f.file_no] == 1 else f.file_name.removesuffix(".ks")

    first_scene_of_file = {fid: group[0].scene_id for fid, group in scenes_by_file.items()
                           if group}
    first_used_of_file: dict[int, int] = {}
    for sid in sorted(used_scenes, key=scene_key):
        first_used_of_file.setdefault(scene_by_id[sid].file_id, sid)
    taken: set[str] = set()

    def claim(key: str) -> str:
        base, n = key, 2
        while key in taken:
            key, n = f"{base}~{n}", n + 1
        taken.add(key)
        return key

    p = rules.prefix
    for idx, node in enumerate(nodes):
        if not node.scenes:
            continue
        f = node.file
        fk = file_key(f)
        root = node.scenes[0]
        if root in (first_used_of_file.get(f.file_id), first_scene_of_file.get(f.file_id)) \
                and f"{p}:{fk}" not in taken:
            node.key = claim(f"{p}:{fk}")
        else:
            label = scene_by_id[root].label
            if fk == f.file_no and label.startswith(f.file_no + "_"):
                node.key = claim(f"{p}:{label}")
            else:
                node.key = claim(f"{p}:{fk}_{label.replace(' ', '_')}")
    for idx, node in enumerate(nodes):
        if node.scenes:
            continue
        if node.type == "route_exit":
            src_tab = node.tab
            node.key = claim(f"{p}:route:{node.target_tab}" if src_tab == rules.main_tab
                             else f"{p}:route:{src_tab}-{node.target_tab}")
        elif node.meta.get("virtual"):
            node.key = claim(f"{p}:{file_key(node.file)}#opt{node.meta['opt']}")
        elif node.meta.get("unlock"):
            node.key = claim(f"{nodes[node.meta['anchor']].key}#unlock")
        else:
            node.key = claim(f"{nodes[node.meta['anchor']].key}#cond")

    # ---------- 標題 ----------
    tab_titles = {t["key"]: t for t in rules.tabs}
    numbered: dict[int, list[int]] = defaultdict(list)
    for idx, node in sorted(enumerate(nodes), key=lambda kv: kv[1].sort_key):
        if node.type in ("scene", "choice") and node.scenes:
            numbered[node.file.file_id].append(idx)
    for idx, node in enumerate(nodes):
        if node.type == "condition":
            node.title = None
        elif node.type == "route_exit":
            t = tab_titles.get(node.target_tab or "", {})
            node.title, node.titles = t.get("title"), t.get("titles")
        elif node.type == "option" and node.title is not None:
            pass
        elif node.file is not None:
            ja, zh = rules.file_title(node.file)
            if node.type == "merge":
                ja, zh = ja + "（続）", (zh + "（續）" if zh else None)
            elif node.type == "ending":
                ja, zh = "エンド", "結局"
            else:
                group = numbered.get(node.file.file_id, [])
                if len(group) > 1 and idx in group:
                    mark = CIRCLED[group.index(idx)] if group.index(idx) < len(CIRCLED) else f"({group.index(idx) + 1})"
                    ja, zh = ja + mark, (zh + mark if zh else None)
            node.title, node.titles = ja, _titles(ja, zh)
        override = rules.titles.get(node.key)
        if isinstance(override, str):
            node.title, node.titles = override, None
        elif isinstance(override, dict):
            node.title = override.get("ja-JP", node.title)
            node.titles = {**(node.titles or {}), **override}

    # ---------- 分頁起點 ----------
    graph_tabs: list[StoryTab] = []
    for t in sorted(rules.tabs, key=lambda t: t["order"]):
        members = [i for i, n in enumerate(nodes) if n.tab == t["key"]]
        if not members:
            continue
        roots = [i for i in members if in_deg[i] == 0]
        start = min(roots or members, key=lambda i: nodes[i].sort_key)
        for i in roots:
            if i != start:
                warnings.append(f"分頁 {t['key']} 的 {nodes[i].key} 沒有入邊（不是起點）")
        graph_tabs.append(StoryTab(key=t["key"], title=t["title"], titles=t.get("titles"),
                                   order=t["order"], start=nodes[start].key, avatar=t.get("avatar")))

    # ---------- 輸出 ----------
    order = sorted(range(len(nodes)), key=lambda i: nodes[i].sort_key)
    out_nodes: list[StoryNode] = []
    rows: list[NodeLineRow] = []
    for i in order:
        node = nodes[i]
        meta = {k: v for k, v in node.meta.items() if k in ("scenes", "virtual")}
        out_nodes.append(StoryNode(
            key=node.key, tab=node.tab, type=node.type, title=node.title, titles=node.titles,
            chapter=node.chapter, chapter_label=node.chapter_label, line_count=len(node.lines),
            condition=node.condition if node.type == "condition" else None,
            target_tab=node.target_tab, meta=meta))
        rows.extend(NodeLineRow(node_key=node.key, seq=s, line_id=lid) for s, lid in enumerate(node.lines))
    out_edges = [
        StoryEdge(**{"from": nodes[e.src].key}, to=nodes[e.dst].key, kind=e.kind, order=e.order,
                  label=e.label, flags=e.flags, effects=e.effects, condition_vars=e.condition_vars)
        for e in sorted(edges, key=lambda e: (nodes[e.src].sort_key, e.order or 0, nodes[e.dst].sort_key))
    ]
    graph = StoryGraph(tabs=graph_tabs, nodes=out_nodes, edges=out_edges)
    stats = {
        "nodes": len(out_nodes),
        "edges": len(out_edges),
        "lines": len(rows),
        "tabs": {t.key: {"nodes": sum(1 for n in out_nodes if n.tab == t.key),
                         "lines": sum(n.line_count for n in out_nodes if n.tab == t.key)}
                 for t in graph_tabs},
        "types": dict(sorted(_count(n.type for n in out_nodes).items())),
    }
    return BuildResult(graph=graph, rows=rows, warnings=warnings, stats=stats)


def _flags(link: LinkRow) -> list[str]:
    flags = list(link.flags) if link.flags is not None else (
        ["inferred"] if link.evidence == "inferred" else [])
    if link.kind == "unlock" and "unlock" not in flags:
        flags.insert(0, "unlock")
    return flags


def _titles(ja: str | None, zh: str | None) -> dict[str, str] | None:
    titles = {k: v for k, v in (("ja-JP", ja), ("zh-Hant", zh)) if v}
    return titles or None


def _is_final_chapter(label: str | None) -> bool:
    """章節標籤是不是 Epilogue／After Story（擷取器的 ``parse_chapter`` 產生的英文標籤）。

    這些章節之後沒有出邊是正常的（故事本來就結束在這裡，或接到另一個檔案的通關後內容），不標成
    ``ending``，避免每個 After Story 的最後一段都變成結局卡片。
    Epilogue / After Story chapters naturally have no out-edges, so they are
    not typed as ``ending``.
    """
    return bool(label) and bool(re.search(r"epilogue|after", label or "", re.I))


def _count(values) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for v in values:
        out[v] += 1
    return dict(out)


def route_presets(graph: StoryGraph) -> dict[str, dict[str, Any]]:
    """從帶 ``condition_vars`` 的邊推出各路線的旗標預設：沿著單一出邊往下走到出口卡片為止。

    Derive per-route flag presets: from each edge carrying ``condition_vars``
    follow single out-edges until a ``route_exit`` card is reached.
    """
    by_key = {n.key: n for n in graph.nodes}
    outs: dict[str, list[StoryEdge]] = defaultdict(list)
    for e in graph.edges:
        if e.kind != "back":
            outs[e.from_].append(e)
    presets: dict[str, dict[str, Any]] = {}
    for e in graph.edges:
        if not e.condition_vars:
            continue
        cur = e.to
        for _ in range(PRESET_MAX_STEPS):
            node = by_key[cur]
            if node.type == "route_exit" and node.target_tab:
                presets.setdefault(node.target_tab, dict(e.condition_vars))
                break
            nxt = outs.get(cur, [])
            if len(nxt) != 1:
                break
            cur = nxt[0].to
    return presets


def to_mermaid(graph: StoryGraph) -> str:
    """把劇情圖輸出成 Mermaid（每個分頁一張圖），供人工核對。Mermaid export per tab."""
    shape = {
        "scene": ('(["', '"])'), "choice": ('{{"', '"}}'), "option": ('["', '"]'), "merge": ('["', '"]'),
        "condition": ('{"', '"}'), "ending": ('(("', '"))'), "route_exit": ('[/"', '"/]'),
    }
    parts = ["# 劇情流程圖（自動產生，供核對）\n"]
    ids = {n.key: f"n{i}" for i, n in enumerate(graph.nodes)}
    for tab in sorted(graph.tabs, key=lambda t: t.order):
        parts.append(f"\n## {tab.title}（{tab.key}，起點 `{tab.start}`）\n\n```mermaid\nflowchart TD\n")
        for n in graph.nodes:
            if n.tab != tab.key:
                continue
            text = n.title or n.condition or ""
            text = f"{text}<br>{n.key}" + (f"<br>{n.line_count} 句" if n.line_count else "")
            if n.chapter_label:
                text += f"<br>{n.chapter_label}"
            left, right = shape.get(n.type, ('["', '"]'))
            parts.append(f"  {ids[n.key]}{left}{text.replace(chr(34), '#quot;')}{right}\n")
        for e in graph.edges:
            if e.from_ not in ids or e.to not in ids:
                continue
            if next(n for n in graph.nodes if n.key == e.from_).tab != tab.key:
                continue
            arrow = "-.->" if ("inferred" in e.flags or "unlock" in e.flags or e.kind == "back") else "-->"
            label = (e.label or "") + (" 🔒" if "unlock" in e.flags else "") + (" ↩" if e.kind == "back" else "")
            label = label.strip().replace('"', "#quot;")
            parts.append(f"  {ids[e.from_]} {arrow}{f'|{chr(34)}{label}{chr(34)}|' if label else ''} {ids[e.to]}\n")
        parts.append("```\n")
    return "".join(parts)
