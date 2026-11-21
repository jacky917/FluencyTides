"""In-memory index of a story graph: node lookup, the path from the start to a node, and variables along it.

劇情圖在記憶體裡的索引：節點查詢、起點到節點的路徑、沿路徑累加變數。

See D10 and D13.

見 D10、D13。

**Path rule** (the frontend ``core/path.ts`` uses the same rule, so the selected path it draws is the path the save
variables follow): within the node's tab and ignoring ``back`` and ``cross_tab`` edges, first find the nodes
reachable from the start; then walk back from the target, and at each step pick, among in-edges whose source is
reachable, the one with the smallest ``order`` (a missing ``order`` counts as 0), then the earliest in the edge list.

**路徑的定義**（前端 ``core/path.ts`` 用同一套規則，畫出的選取路徑就是存檔變數走的路徑）：
在節點所屬分頁內、不含 ``back`` 與 ``cross_tab`` 邊，先找出從起點走得到的節點；再從目標往回走，
每一步在「來源走得到」的入邊中選 ``order`` 最小（沒有 ``order`` 當 0）、其次在邊清單中最前面的
那一條。

**Variable accumulation** (the frontend ``pathVariables`` uses the same rule): ``set`` overwrites; ``add`` applies
only when both the increment and the current value (missing counts as 0) are numbers (not booleans), otherwise it is
skipped with a WARNING. The schema already rejects non-numeric ``add``; this guards against old data.

**變數累加**（前端 ``pathVariables`` 同一套）：``set`` 覆寫；``add`` 只在「要加的值」與「目前的值
（沒有當 0）」都是數字（不是布林）時累加，否則略過並記 WARNING。schema 已經擋下非數字的
``add``，這裡是防禦舊資料。
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from typing import Any

from app.schemas.story import StoryEdge, StoryGraph, StoryNode

logger = logging.getLogger(__name__)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class GraphIndex:
    """The index of one story graph, built once per build.

    一份劇情圖的索引（每個版本建一次）。
    """

    def __init__(self, graph: StoryGraph) -> None:
        """Build the index of ``graph``.

        建立 ``graph`` 的索引。

        Args:
            graph: the story graph
                劇情圖
        """
        self.graph = graph
        self.nodes: dict[str, StoryNode] = {n.key: n for n in graph.nodes}
        self.tabs = {t.key: t for t in graph.tabs}
        self._in: dict[str, list[tuple[int, StoryEdge]]] = defaultdict(list)
        self._out: dict[str, list[StoryEdge]] = defaultdict(list)
        for i, e in enumerate(graph.edges):
            if e.kind in ("back", "cross_tab"):
                continue
            self._in[e.to].append((i, e))
            self._out[e.from_].append(e)
        self._reachable: dict[str, set[str]] = {}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "GraphIndex":
        """Build an index from the JSON of ``story_builds.graph``.

        由 ``story_builds.graph`` 的 JSON 建立。

        Args:
            data: the stored graph JSON
                存下來的劇情圖 JSON

        Returns:
            the index
            索引

        Raises:
            ValidationError: if ``data`` is not a valid ``StoryGraph``
                若 ``data`` 不是合法的 ``StoryGraph``
        """
        return cls(StoryGraph.model_validate(data))

    def tab_of(self, node_key: str) -> str | None:
        """Return the tab a node belongs to.

        回傳節點所屬分頁。

        Args:
            node_key: the node key
                節點鍵

        Returns:
            the tab key, or ``None`` if the node does not exist
            分頁鍵；節點不存在時為 ``None``
        """
        node = self.nodes.get(node_key)
        return node.tab if node else None

    def reachable(self, tab: str) -> set[str]:
        """Return the nodes reachable from the tab start.

        回傳從分頁起點走得到的節點。

        Only edges within the tab are followed (not ``back`` or ``cross_tab``); the result is cached per tab, so do
        not modify it.

        只沿分頁內的邊走（不含 ``back`` 與 ``cross_tab``）；結果依分頁快取，不要修改。

        Args:
            tab: the tab key; must exist in the graph
                分頁鍵；必須存在於圖中

        Returns:
            the reachable node keys, including the start
            走得到的節點鍵，含起點

        Raises:
            KeyError: if ``tab`` is not in the graph
                若 ``tab`` 不在圖中
        """
        if tab not in self._reachable:
            start = self.tabs[tab].start
            seen = {start}
            queue = deque([start])
            while queue:
                cur = queue.popleft()
                for e in self._out.get(cur, []):
                    target = self.nodes.get(e.to)  # 邊指向不存在的節點時忽略（匯入時已檢查，這裡防禦）
                    if e.to not in seen and target is not None and target.tab == tab:
                        seen.add(e.to)
                        queue.append(e.to)
            self._reachable[tab] = seen
        return self._reachable[tab]

    def path_edges(self, node_key: str) -> list[StoryEdge]:
        """Return the edges from the tab start to a node, start first (rule in the module docstring).

        回傳從分頁起點到節點的邊（起點在前；規則見模組說明）。

        Args:
            node_key: the target node
                目標節點

        Returns:
            the path edges; empty if the node does not exist, cannot be reached, or is the start itself
            路徑上的邊；節點不存在、走不到或就是起點時為空串列
        """
        node = self.nodes.get(node_key)
        if node is None or node.tab not in self.tabs:
            return []
        reach = self.reachable(node.tab)
        if node_key not in reach:
            return []
        start = self.tabs[node.tab].start
        path: list[StoryEdge] = []
        cur, guard = node_key, 0
        while cur != start and guard <= len(self.nodes):
            guard += 1
            candidates = [(e.order if e.order is not None else 0, i, e)
                          for i, e in self._in.get(cur, []) if e.from_ in reach]
            if not candidates:
                break
            _, _, edge = min(candidates, key=lambda c: (c[0], c[1]))
            path.append(edge)
            cur = edge.from_
        path.reverse()
        return path

    def path_nodes(self, node_key: str) -> list[str]:
        """Return the node keys on the path, including the start and the end.

        回傳路徑上的節點（含起點與終點）。

        Args:
            node_key: the target node
                目標節點

        Returns:
            the node keys from the start to ``node_key``; ``[node_key]`` if it is the tab start; empty if it does not
            exist or cannot be reached
            從起點到 ``node_key`` 的節點鍵；本身就是分頁起點時為 ``[node_key]``；不存在或走不到時為空串列
        """
        edges = self.path_edges(node_key)
        if not edges:
            node = self.nodes.get(node_key)
            return [node_key] if node and self.tabs.get(node.tab) and self.tabs[node.tab].start == node_key else []
        return [edges[0].from_] + [e.to for e in edges]

    def path_variables(self, node_key: str) -> dict[str, Any]:
        """Accumulate the ``effects`` along the path (rule in the module docstring).

        沿路徑累加 ``effects``（規則見模組說明）。

        Args:
            node_key: the target node
                目標節點

        Returns:
            the variables; empty if there is no path
            累加後的變數；沒有路徑時為空字典
        """
        variables: dict[str, Any] = {}
        for edge in self.path_edges(node_key):
            for eff in edge.effects:
                if eff.op == "set":
                    variables[eff.var] = eff.value
                elif eff.op == "add":
                    current = variables.get(eff.var, 0)
                    if not (_is_number(eff.value) and _is_number(current)):
                        logger.warning("略過非數字的 add：%s %r + %r（邊 %s → %s）",
                                       eff.var, current, eff.value, edge.from_, edge.to)
                        continue
                    variables[eff.var] = current + eff.value
        return variables
