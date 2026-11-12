"""千戀＊萬花流程圖擷取器的遊戲規則測試（不連資料庫）。

Tests for the Senren*Banka extractor's game rules without a database:
tabs, after-story files, chapter parsing, official titles, coverage
warnings, template checks and that the shipped config validates.
"""

import json
import struct
from types import SimpleNamespace

import pytest

from app.infrastructure.krkr import savestruct
from app.services.story.registry import get_story_game
from scripts.story.common.scene_graph import FileRow
from scripts.story.extractors import senren_banka as extractor


def _file(no: str, name: str | None = None) -> FileRow:
    return FileRow(1, name or f"{no}・x.ks", no, "x", None, 1)


OFFICIAL = {
    "s001*com_part_1": {"node": "s001*com_part_1", "kind": "scene", "file_code": "001",
                        "title_jp": "久しぶりの穂織", "title_tw": "闊別多年的穗織"},
    "s001*001_01A": {"node": "s001*001_01A", "kind": "option", "file_code": "001",
                     "title_jp": "正直に言う", "title_tw": None},
    "s002*com_part_2": {"node": "s002*com_part_2", "kind": "scene", "file_code": "002",
                        "title_jp": None, "title_tw": "新生活"},
}


@pytest.mark.parametrize("no, tab", [("001", "common"), ("115", "yos"), ("211", "mak"), ("317", "mur"),
                                     ("425", "len"), ("508a", "sub"), ("900", "common")])
def test_tab_of_file(no, tab):
    assert extractor.tab_of_file(_file(no)) == tab


def test_after_story_files():
    assert [no for no in ("114", "115", "211", "317", "429", "513", "523", "522") if extractor.is_after_file(_file(no))] \
        == ["115", "211", "317", "429", "513", "523"]


@pytest.mark.parametrize("label, expected", [
    ("chapter　1-1", ("1", "CHAPTER 1")),
    ("chapter 9-2", ("9", "CHAPTER 9")),
    ("アフター", ("after", "After Story")),
    ("エピローグ", ("epilogue", "Epilogue")),
    ("その他", None),
])
def test_parse_chapter(label, expected):
    assert extractor.parse_chapter(label) == expected


def test_file_title_uses_first_official_scene_node_or_falls_back():
    rules = extractor.rules(OFFICIAL)
    assert rules.file_title(_file("001")) == ("久しぶりの穂織", "闊別多年的穗織")
    assert rules.file_title(FileRow(9, "777・y.ks", "777", "腳本名", None, 9)) == ("腳本名", None)


def test_official_titles_take_the_first_member_line():
    rows = [SimpleNamespace(node_key="sb:001", seq=1, line_id=11), SimpleNamespace(node_key="sb:001", seq=0, line_id=10),
            SimpleNamespace(node_key="sb:002", seq=0, line_id=20), SimpleNamespace(node_key="sb:001#opt0", seq=0, line_id=30)]
    nodes = [SimpleNamespace(key="sb:001", type="scene"), SimpleNamespace(key="sb:002", type="scene"),
             SimpleNamespace(key="sb:001#opt0", type="option")]
    result = SimpleNamespace(rows=rows, graph=SimpleNamespace(nodes=nodes))
    fc_of = {10: "s001*com_part_1", 11: "s001*001_01A", 20: "s002*com_part_2", 30: "s001*001_01A"}
    assert extractor.official_titles(result, fc_of, OFFICIAL) == {
        "sb:001": {"ja-JP": "久しぶりの穂織", "zh-Hant": "闊別多年的穗織"},  # seq 0 那句，不是 seq 1
        "sb:002": {"zh-Hant": "新生活"},  # 只有繁中標題
    }  # 選項節點不覆寫


def test_coverage_warnings():
    files = [_file("001"), _file("900", "900・謎.ks")]
    warnings = extractor.coverage_warnings(files, {1: "s001*com_part_1", 2: "s999*nope"}, OFFICIAL)
    assert any("900" in w and "共通" in w for w in warnings)
    assert any("900・謎.ks" in w and "沒有官方場景節點" in w for w in warnings)
    assert any("1 句台詞的 fc_node" in w and "s999*nope" in w for w in warnings)
    assert extractor.coverage_warnings([_file("001")], {1: "s001*com_part_1"}, OFFICIAL) == []


def test_shipped_config_validates_and_presets_cover_every_route():
    config = get_story_game("SenrenBanka").parse_config(extractor.CONFIG)
    assert set(config.route_presets) == set(extractor.TAB_BY_DIGIT.values()) - {"common"}
    assert {s.filename for s in config.saves.slots} == {"data_quick_01.bmp", "data_continue.bmp"}
    for ex in extractor.EXTRA_LINKS:
        assert ex.kind == "unlock" and ex.src[0] in {"114", "210", "316", "428", "512", "522"}


def test_load_official_nodes_reports_bad_files(tmp_path):
    with pytest.raises(SystemExit, match="不存在"):
        extractor.load_official_nodes(tmp_path)
    (tmp_path / "calib").mkdir()
    path = tmp_path / "calib" / "SB_flowchart_nodes.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(SystemExit, match="不是合法的 JSON"):
        extractor.load_official_nodes(tmp_path)
    path.write_text(json.dumps([{"node": "s001*a"}]), encoding="utf-8")
    with pytest.raises(SystemExit, match="格式不對"):
        extractor.load_official_nodes(tmp_path)
    path.write_text(json.dumps(list(OFFICIAL.values())), encoding="utf-8")
    assert set(extractor.load_official_nodes(tmp_path)) == set(OFFICIAL)


def _bmp() -> bytes:
    return b"BM" + struct.pack("<IHHI", 66, 0, 0, 54) + b"\x00" * 52


def test_check_template(tmp_path):
    with pytest.raises(SystemExit, match="不存在"):
        extractor.check_template(tmp_path / "none.bmp")
    bad = tmp_path / "bad.bmp"
    bad.write_bytes(b"{json}")
    with pytest.raises(SystemExit, match="不是 saveStruct"):
        extractor.check_template(bad)
    empty = tmp_path / "empty.bmp"
    empty.write_bytes(_bmp() + savestruct.pack_text("(const) %[\r\n]"))
    with pytest.raises(SystemExit, match="scenePlayer"):
        extractor.check_template(empty)
    good = tmp_path / "good.bmp"
    good.write_bytes(_bmp() + savestruct.pack_text(savestruct.dump(
        {"core": {"scenePlayer": {"scene": "a.ks*b", "point": 1}, "historyData": {"data": []}}, "user": {}})))
    extractor.check_template(good)
