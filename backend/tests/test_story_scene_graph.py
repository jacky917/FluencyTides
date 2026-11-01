"""通用建圖程式（scene → 劇情圖）與建置包 schema 檢查的測試（劇情流程圖 D4、D5、§7 測試）。

Tests for the scene-graph builder and the package invariants, using a
miniature Sabbat-like fixture (tests/story_fixtures.py).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import story_fixtures as F  # noqa: E402

from app.schemas.story import (  # noqa: E402
    NodeLineRow,
    StoryEdge,
    StoryGraph,
    StoryNode,
    StoryTab,
    validate_graph,
    validate_node_lines,
)
from scripts.story.common.scene_graph import build_story_graph, route_presets, to_mermaid  # noqa: E402


@pytest.fixture(scope="module")
def built():
    return build_story_graph(*F.rows(), F.rules())


def _nodes(result):
    return {n.key: n for n in result.graph.nodes}


def _out(result, key):
    return sorted((e for e in result.graph.edges if e.from_ == key), key=lambda e: e.order or 0)


def test_package_invariants_hold(built):
    assert validate_graph(built.graph) == []
    assert validate_node_lines(built.graph, built.rows) == []


def test_every_line_assigned_once(built):
    _, _, lines, _ = F.rows()
    assert sorted(r.line_id for r in built.rows) == sorted(line.id for line in lines)


def test_chain_merge_respects_chapters(built):
    nodes = _nodes(built)
    # chapter11 + chapter12 同章節 → 一個節點；chapter21 換章 → 另一個
    assert nodes["sw:001"].line_count == 8
    assert nodes["sw:001"].chapter_label == "CHAPTER 1"
    assert nodes["sw:001_chapter21"].chapter_label == "CHAPTER 2"
    assert nodes["sw:001"].title == "始まり①" and nodes["sw:001_chapter21"].title == "始まり②"


def test_choice_screen_folds_into_previous_scene(built):
    nodes = _nodes(built)
    choice = nodes["sw:009"]
    assert choice.type == "choice"
    assert choice.line_count == 7  # chapter32 的 6 句 + 選項畫面 1 句
    edges = _out(built, "sw:009")
    assert [(e.kind, e.order, e.label) for e in edges] == [("branch", 0, "悩む"), ("branch", 1, "変更する")]
    assert edges[1].effects[1].op == "add"


def test_options_and_merge(built):
    nodes = _nodes(built)
    assert nodes["sw:009_01A"].type == "option" and nodes["sw:009_01A"].title == "悩む"
    assert nodes["sw:009_01A"].titles == {"ja-JP": "悩む", "zh-Hant": "糾結"}
    assert nodes["sw:009_01com"].type == "merge"
    assert nodes["sw:009_01com"].title == "休日デート（続）"


def test_relay_becomes_condition_with_ordered_branches(built):
    nodes = _nodes(built)
    cond = nodes["sw:009_01com#cond"]
    assert cond.type == "condition" and cond.line_count == 0
    assert "sel_flag" in cond.condition
    edges = _out(built, cond.key)
    assert [e.to for e in edges] == ["sw:009_02A", "sw:009_02B"]
    assert edges[0].condition_vars == {"sel_flag": 0}


def test_four_way_choice_and_back_edge(built):
    edges = _out(built, "sw:019")
    assert [e.order for e in edges] == [0, 1, 2, 3]
    back = [e for e in built.graph.edges if e.kind == "back"]
    assert [(e.from_, e.to) for e in back] == [("sw:019_01A", "sw:019")]
    assert any("往回跳" in w for w in built.warnings)


def test_route_split_exits_and_ending(built):
    nodes = _nodes(built)
    cond = _out(built, "sw:019_01com")[0].to
    assert nodes[cond].type == "condition"
    targets = [e.to for e in _out(built, cond)]
    assert targets[0] == "sw:019_normal_end"  # 沒有條件的去向排第一
    assert nodes["sw:019_normal_end"].type == "ending" and nodes["sw:019_normal_end"].title == "ノーマルエンド"
    exit_nene = nodes["sw:route:nene"]
    assert exit_nene.type == "route_exit" and exit_nene.tab == "common" and exit_nene.target_tab == "nene"
    assert route_presets(built.graph) == {"nene": {"nen_flag": 4}, "meguru": {"meg_flag": 4}}


def test_cross_tab_link_becomes_exit_and_new_tab_starts_fresh(built):
    tabs = {t.key: t for t in built.graph.tabs}
    assert tabs["nene"].start == "sw:101"
    assert not [e for e in built.graph.edges if e.to == "sw:101"]


def test_unlock_cross_tab_inserts_condition_with_fallback(built):
    nodes = _nodes(built)
    unlock = nodes["sw:009_02com#unlock"]
    assert unlock.type == "condition" and unlock.condition == "任一路線通關後"
    edges = _out(built, unlock.key)
    assert edges[0].to == "sw:019" and edges[0].label == "その他"
    assert edges[1].to == "sw:route:wakana"
    assert set(edges[1].flags) == {"unlock", "inferred"}


def test_unlock_to_after_story_is_plain_dashed_edge(built):
    edge = next(e for e in built.graph.edges if e.to == "sw:117")
    assert edge.from_ == "sw:102" and edge.flags == ["unlock"] and edge.kind == "normal"
    nodes = _nodes(built)
    assert nodes["sw:102"].type == "scene"  # Epilogue 不是 ending
    assert nodes["sw:117"].chapter_label == "After Story"


def test_duplicate_file_numbers_use_file_stem(built):
    assert "sw:020.共通－パーティ寧々" in _nodes(built)


def test_empty_scene_link_dropped_and_noise_removed(built):
    assert any("沒有台詞" in w for w in built.warnings)
    assert any("自迴圈" in w for w in built.warnings)
    # 選項場景的 next（→ 101）被丟掉：101 沒有入邊
    assert not [e for e in built.graph.edges if e.to == "sw:101"]


def test_empty_scene_link_redirects_to_next_file():
    files, scenes, lines, links = F.rows()
    # 讓 019 chapter47 的上一個檔案多一個同名的空場景，連接指向它時要改指 019 的 chapter47
    from scripts.story.common.scene_graph import LinkRow, SceneRow

    scenes = scenes + [SceneRow(99, 2, "chapter47", 99)]
    links = [x for x in links if not (x.from_scene_id == 28 and x.to_scene_id == 30)] + [LinkRow(28, 99, "next")]
    result = build_story_graph(files, scenes, lines, links, F.rules())
    assert any("改指向" in w for w in result.warnings)
    assert validate_graph(result.graph) == []


def test_keys_are_stable_across_rebuilds(built):
    again = build_story_graph(*F.rows(), F.rules())
    assert [n.key for n in again.graph.nodes] == [n.key for n in built.graph.nodes]
    assert again.graph.to_json_dict() == built.graph.to_json_dict()


def test_mermaid_export_mentions_every_tab(built):
    text = to_mermaid(built.graph)
    for tab in built.graph.tabs:
        assert f"## {tab.title}" in text


# ---------- schema 檢查 ----------


def _graph(nodes, edges, tabs=None):
    return StoryGraph(tabs=tabs or [StoryTab(key="t", title="T", order=1, start="a")],
                      nodes=[StoryNode(key=k, tab="t", type=ty) for k, ty in nodes],
                      edges=[StoryEdge(**{"from": a}, to=b, kind=kind) for a, b, kind in edges])


def test_validate_rejects_missing_endpoint_and_cycle():
    g = _graph([("a", "scene"), ("b", "scene")], [("a", "b", "normal"), ("b", "a", "normal"), ("a", "zz", "normal")])
    errors = validate_graph(g)
    assert any("端點不存在" in e for e in errors)
    assert any("迴圈" in e for e in errors)


def test_validate_allows_back_edges():
    g = _graph([("a", "scene"), ("b", "scene")], [("a", "b", "normal"), ("b", "a", "back")])
    assert validate_graph(g) == []


def test_validate_option_must_follow_choice_or_condition():
    g = _graph([("a", "scene"), ("b", "option")], [("a", "b", "normal")])
    assert any("選項" in e for e in validate_graph(g))
    g = _graph([("a", "choice"), ("b", "option")], [("a", "b", "branch")])
    assert validate_graph(g) == []


def test_validate_types_and_duplicate_keys():
    g = _graph([("a", "scene"), ("a", "weird"), ("c", "x-custom")], [])
    errors = validate_graph(g)
    assert any("重複" in e for e in errors)
    assert any("weird" in e for e in errors)
    assert not any("x-custom" in e for e in errors)


def test_validate_node_lines_rules():
    g = StoryGraph(tabs=[StoryTab(key="t", title="T", order=1, start="a")],
                   nodes=[StoryNode(key="a", tab="t", type="scene", line_count=2),
                          StoryNode(key="b", tab="t", type="scene", line_count=1)], edges=[])
    rows = [NodeLineRow(node_key="a", seq=0, line_id=1), NodeLineRow(node_key="a", seq=2, line_id=2),
            NodeLineRow(node_key="b", seq=0, line_id=1)]
    errors = validate_node_lines(g, rows)
    assert any("同時屬於" in e for e in errors)
    assert any("seq" in e for e in errors)
