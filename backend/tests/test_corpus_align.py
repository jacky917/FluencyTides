"""語料庫遷移的對齊與解析測試（scripts/database/corpus_rebuild）。

Tests for the corpus rebuild alignment and parsing helpers.
"""

from scripts.database.corpus_rebuild.align import (
    Anchor,
    OldLine,
    align,
    file_key,
    norm_text,
    voice_base,
)
from scripts.database.corpus_rebuild.content import parse_effects

F = "001.test.ks"


def _a(idx, label, text, voice="", role=""):
    return Anchor(idx, f"{F}*{label}", text, voice, role)


def _o(id_, text, voice="", role="", chapter="c1"):
    return OldLine(id_, F, chapter, role, text, voice)


def test_norm_text_strips_ruby_and_whitespace():
    assert norm_text("[かい]海[どう]道 秀\n明") == "海道秀明"


def test_voice_base_drops_variant_suffix():
    assert voice_base("nen001_027_ref") == "nen001_027"
    assert voice_base("") == ""


def test_file_key_nfc_and_json_suffix():
    assert file_key("001.テスト.ks.json") == "001.テスト.ks"


def test_duplicates_follow_story_order_inside_label():
    anchors = [_a(0, "a", "「始め」"), _a(1, "a", "「………」"), _a(2, "a", "「中」"),
               _a(3, "a", "「………」"), _a(4, "a", "「終わり」")]
    olds = [_o(10, "「始め」"), _o(11, "「………」"), _o(12, "「中」"), _o(13, "「………」"), _o(14, "「終わり」")]
    res = align(anchors, olds)
    assert res.match == {0: 10, 1: 11, 2: 12, 3: 13, 4: 14}


def test_labels_in_different_order_and_voice_disambiguates():
    # 錨點先 b 後 a，舊表先 a 後 b；重複台詞靠語音分辨
    anchors = [_a(0, "b", "「次」"), _a(1, "b", "「うん」", "nen002"),
               _a(2, "a", "「前」"), _a(3, "a", "「うん」", "nen001")]
    olds = [_o(1, "「前」"), _o(2, "「うん」", "nen001"), _o(3, "「次」"), _o(4, "「うん」", "nen002")]
    res = align(anchors, olds)
    assert res.match == {0: 3, 1: 4, 2: 1, 3: 2}


def test_inserted_anchor_and_deleted_old_are_left_out():
    anchors = [_a(0, "a", "一"), _a(1, "a", "選択肢：A / B"), _a(2, "a", "三")]
    olds = [_o(1, "一"), _o(2, "二（消えた）"), _o(3, "三")]
    res = align(anchors, olds)
    assert res.match == {0: 1, 2: 3}
    assert res.unmatched_old({1, 2, 3}) == [2]


def test_typo_is_not_matched_by_text():
    anchors = [_a(0, "a", "前"), _a(1, "a", "何か"), _a(2, "a", "後")]
    olds = [_o(1, "前"), _o(2, "何が"), _o(3, "後")]
    res = align(anchors, olds)
    assert 1 not in res.match


def test_parse_effects():
    assert parse_effects("f.sel_flag = 0 , f.wak_flag ++") == [
        {"var": "sel_flag", "op": "set", "value": 0},
        {"var": "wak_flag", "op": "add", "value": 1},
    ]
    assert parse_effects("f.nen_flag += 2, f.x --") == [
        {"var": "nen_flag", "op": "add", "value": 2},
        {"var": "x", "op": "add", "value": -1},
    ]
    assert parse_effects(None) == []
