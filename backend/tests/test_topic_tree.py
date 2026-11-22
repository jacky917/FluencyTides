"""Tests for the trilingual topic tree built from decks (``topic_tree``).

由牌組組出三語卡主題樹（``topic_tree``）的測試。

The deck layout follows the plan's example (``docs/wip/trilingual_topic_tree_FEAT_2026-11-22.md`` §1).

牌組結構照計畫書的範例（``docs/wip/trilingual_topic_tree_FEAT_2026-11-22.md`` §1）。
"""

import pytest

from app.services.task_handlers.topic_tree import PROMPT_LABEL_LENGTH, TreeCard, build_graph

ROOT = "口說三語"


def card(nid: int, deck: str, title: str | None, prompt: str = "p") -> TreeCard:
    return TreeCard(note_id=nid, deck=deck, title=title, prompt=prompt)


EXAMPLE = [
    card(1, ROOT, "婉拒加班"),
    card(2, f"{ROOT}::婉拒加班", "婉拒主管"),
    card(3, f"{ROOT}::婉拒加班::婉拒主管", "主管再三拜託"),
    card(4, f"{ROOT}::婉拒加班", "婉拒同事"),
    card(5, f"{ROOT}::婉拒加班", "婉拒飯局"),
    card(6, ROOT, "租屋"),
    card(7, f"{ROOT}::租屋", "反映漏水"),
    card(8, f"{ROOT}::租屋", "續約談租金"),
    card(9, ROOT, "職場求助"),
    card(10, "別的牌組", "不相干"),
]


def _by_id(graph):
    return {node["id"]: node for node in graph["nodes"]}


def _children(graph, node_id):
    return [link["target"] for link in graph["links"] if link["source"] == node_id]


def test_depth_one_shows_this_level_with_child_counts():
    graph = build_graph(EXAMPLE, ROOT, depth=1)
    nodes = _by_id(graph)

    assert nodes["root"]["kind"] == "root" and nodes["root"]["label"] == ROOT
    assert _children(graph, "root") == ["n:1", "n:6", "n:9"]
    assert [(nodes[i]["label"], nodes[i]["child_count"]) for i in ("n:1", "n:6", "n:9")] == [
        ("婉拒加班", 3), ("租屋", 2), ("職場求助", 0),
    ]
    assert nodes["n:1"]["child_deck"] == f"{ROOT}::婉拒加班"
    assert nodes["n:9"]["child_deck"] is None
    # 只展開一層：第二層的卡不在圖上
    assert "n:2" not in nodes


def test_depth_two_expands_children_but_folds_the_third_level():
    graph = build_graph(EXAMPLE, ROOT, depth=2)
    nodes = _by_id(graph)

    assert _children(graph, "n:1") == ["n:2", "n:4", "n:5"]
    assert nodes["n:2"]["depth"] == 2 and nodes["n:2"]["child_count"] == 1
    assert "n:3" not in nodes


def test_depth_zero_expands_everything():
    nodes = _by_id(build_graph(EXAMPLE, ROOT, depth=0))
    assert {"n:3", "n:7", "n:8"} <= set(nodes)
    assert "n:10" not in nodes  # 根以外的牌組不算


def test_entering_a_child_deck_makes_its_owner_the_root():
    """點進「婉拒加班」：根節點就是那張主題卡，點了可以開詳情。"""
    graph = build_graph(EXAMPLE, f"{ROOT}::婉拒加班", depth=1)
    root = _by_id(graph)["root"]

    assert (root["label"], root["note_id"]) == ("婉拒加班", 1)
    assert _children(graph, "root") == ["n:2", "n:4", "n:5"]


def test_deck_without_an_owning_card_is_a_deck_node():
    """純分類用的牌組（現有資料就是這樣）：畫成牌組節點、可以進入。"""
    cards = [
        card(1, "日常會話::日本語面接::最終面接", None, "面試官問你為什麼想進這家公司，請回答。"),
        card(2, "日常會話::お花屋さん", None, "你走進花店"),
    ]
    graph = build_graph(cards, "日常會話", depth=1)
    nodes = _by_id(graph)

    assert nodes["root"]["kind"] == "root" and "note_id" not in nodes["root"]
    assert [(nodes[i]["kind"], nodes[i]["label"], nodes[i]["child_count"]) for i in _children(graph, "root")] == [
        ("deck", "お花屋さん", 1), ("deck", "日本語面接", 1),
    ]


def test_all_decks_root_lists_top_level_decks_with_trilingual_cards():
    graph = build_graph(EXAMPLE, None, depth=1)
    nodes = _by_id(graph)

    assert nodes["root"]["label"] == "全部牌組"
    assert [nodes[i]["label"] for i in _children(graph, "root")] == ["別的牌組", ROOT]


def test_deck_names_match_case_insensitively():
    """Anki 比對牌組名不分大小寫：標題 Rent 擁有子牌組 rent。"""
    cards = [card(1, "Root", "Rent"), card(2, "Root::rent", "Leak")]
    nodes = _by_id(build_graph(cards, "Root", depth=1))
    assert nodes["n:1"]["child_count"] == 1
    assert not any(node["kind"] == "deck" for node in nodes.values())


def test_retitled_card_leaves_its_old_deck_as_a_deck_node():
    """卡在 Anki 改了標題、牌組沒跟著改：卡沒有子卡，舊牌組變成牌組節點，兩者並排看得出要對齊。"""
    cards = [card(1, "Root", "新標題"), card(2, "Root::舊標題", "子卡")]
    graph = build_graph(cards, "Root", depth=1)
    nodes = _by_id(graph)

    assert nodes["n:1"]["child_count"] == 0
    assert nodes["d:Root::舊標題"]["kind"] == "deck" and nodes["d:Root::舊標題"]["child_count"] == 1


def test_duplicate_title_only_first_card_owns_the_deck():
    cards = [card(5, "Root", "同名"), card(3, "Root", "同名"), card(9, "Root::同名", "子卡")]
    nodes = _by_id(build_graph(cards, "Root", depth=1))

    assert (nodes["n:3"]["child_count"], nodes["n:3"]["duplicate_title"]) == (1, False)
    assert (nodes["n:5"]["child_count"], nodes["n:5"]["duplicate_title"]) == (0, True)


def test_card_without_title_uses_the_start_of_the_prompt():
    long_prompt = "你剛走進花店，店員向你打招呼並詢問是否需要幫忙。請回答你只是看看。"
    nodes = _by_id(build_graph([card(1, "Root", None, long_prompt)], "Root", depth=1))

    assert nodes["n:1"]["label"] == long_prompt[:PROMPT_LABEL_LENGTH] + "…"
    assert nodes["n:1"]["child_count"] == 0


def test_title_matching_no_deck_has_no_children():
    nodes = _by_id(build_graph([card(1, "Root", "還沒有子卡")], "Root", depth=1))
    assert (nodes["n:1"]["child_count"], nodes["n:1"]["child_deck"]) == (0, None)


@pytest.mark.parametrize("root", ["Root::空的", "不存在"])
def test_empty_or_unknown_root_has_no_children(root):
    graph = build_graph([card(1, "Root", "a")], root, depth=1)
    assert graph["links"] == [] and _by_id(graph)["root"]["child_count"] == 0


# ----------------------------------------------------------------------
# handler：從 Anki 讀卡片與牌組，再組樹（Handler: read Anki, then build the tree）
# ----------------------------------------------------------------------

import asyncio  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest import mock  # noqa: E402

from app.core.exceptions import InvalidNoteQueryError  # noqa: E402
from app.services.task_handlers.speaking_trilingual_handler import SpeakingTrilingualHandler  # noqa: E402


def _note(nid: int, title: str, prompt: str = "p") -> SimpleNamespace:
    fields = {"Title": {"value": title}, "Prompt": {"value": prompt}}
    return SimpleNamespace(noteId=nid, cards=[nid * 10], fields=fields)


def _card_service(notes: list, decks: dict[int, str]):
    anki = SimpleNamespace(
        get_notes_info=mock.AsyncMock(return_value=notes),
        get_cards_info=mock.AsyncMock(return_value=[{"note": nid, "deckName": deck} for nid, deck in decks.items()]),
    )
    return SimpleNamespace(find_notes=mock.AsyncMock(return_value=[n.noteId for n in notes]), anki_client=anki)


def _read(card_service, deck, **kwargs):
    return asyncio.run(SpeakingTrilingualHandler().execute_read_graph(card_service, None, deck, **kwargs))


def test_handler_reads_from_the_parent_deck_and_builds_the_tree():
    """擁有根牌組的卡在上一層，所以從上一層搜尋；標題的 HTML 會去掉，不合規則的標題當成沒有。"""
    notes = [_note(1, "<b>婉拒加班</b>"), _note(2, "婉拒主管"), _note(3, "壞::標題", "沒標題的卡")]
    service = _card_service(notes, {1: ROOT, 2: f"{ROOT}::婉拒加班", 3: f"{ROOT}::婉拒加班"})

    graph = _read(service, f"{ROOT}::婉拒加班", depth=1)
    nodes = _by_id(graph)

    assert 'deck:"口說三語"' in service.find_notes.await_args.args[0]
    assert (nodes["root"]["label"], nodes["root"]["note_id"]) == ("婉拒加班", 1)
    assert [nodes[i]["label"] for i in _children(graph, "root")] == ["婉拒主管", "沒標題的卡"]
    assert (graph["label_fields"], graph["label_field"]) == ([], "Title")
    assert service.anki_client.get_cards_info.await_args.args[0] == [10, 20, 30]


def test_handler_without_cards_returns_only_the_root():
    service = _card_service([], {})
    graph = _read(service, ROOT)
    assert [node["id"] for node in graph["nodes"]] == ["root"]
    service.anki_client.get_notes_info.assert_not_awaited()


def test_handler_rejects_label_field():
    with pytest.raises(InvalidNoteQueryError):
        _read(_card_service([], {}), ROOT, label_field="Prompt")
