"""千戀＊萬花語料庫匯入的測試：組資料（build／check）與寫入（write，假連線）。

Tests for the Senren*Banka corpus import: the in-memory build and checks,
and ``write()`` against a fake connection (re-run guards, rollback, id
offsets, deletes scoped to this game).
"""

import re

import pytest

from scripts.database.corpus_rebuild import import_senren_banka as importer

F1 = "001・アーサー王ver1.07.ks"
F2 = "508a・サブ－こはるんver1.00.ks"


def _row(order, scene, point, kind="line", **kw):
    f, label = scene.split("*", 1)
    base = {"story_order": order, "kind": kind, "scene": scene, "file": f, "label": label, "point": point,
            "chapter": "chapter　1-1", "speaker_jp": None, "text_jp": f"台詞{order}", "text_tw": f"台詞{order}",
            "voice": "", "is_h": 0, "h_label": "", "thumb": "0123456789abcdef", "bgm_codes": [],
            "choice_options": None, "hist_matched": 1}
    base.update(kw)
    return base


def _src():
    rows = [
        _row(0, f"{F1}*com_part_1", 3, speaker_jp="芳乃", voice="yos001_001.ogg", bgm_codes=["BGM01", "BGM02"]),
        _row(1, f"{F1}*com_part_1", 4, text_jp="「志那都荘」", chapter=None, segment="x"),
        _row(2, f"{F1}*dummyselect1", 0, kind="choice", text_jp=None, text_tw="選擇項：說實話/敷衍過去",
             hist_matched=0, choice_options=[{"idx": 0, "jp": "正直に言う"}, {"idx": 1, "jp": "誤魔化す"}]),
        _row(3, f"{F1}*001_01A", None, kind="auto", voice="a001.ogg|b002.ogg", hist_matched=0),
        _row(4, f"{F2}*sub_part_8a:2", 1, speaker_jp="小春", hist_matched=4, chapter="アフター"),
    ]
    links = [
        {"from_scene": f"{F1}*dummyselect1", "to_scene": f"{F1}*001_01A", "to_point": 1, "kind": "choice",
         "option_index": 0, "option_text_ja_jp": "正直に言う", "option_text_zh_hant": "說實話",
         "condition_text_zh_hant": None, "raw_expression": 'SetBranchFlags("s001*com_part_1_sel",1)',
         "via": None, "evidence": "observed", "note": None},
        {"from_scene": f"{F1}*001_01A", "to_scene": f"{F1}*missing_label", "to_point": None, "kind": "next",
         "evidence": "observed"},
        {"from_scene": f"{F2}*sub_part_8a:2", "to_scene": None, "kind": "next", "evidence": "observed",
         "note": "結局"},
    ]
    titles = [{"file_index": 3, "code": "BGM02", "music_room_no": 2, "title_jp": "曲2", "title_tw": "曲2"},
              {"file_index": 1, "code": "BGM01", "music_room_no": 1, "title_jp": "曲1", "title_tw": None}]
    speakers = [{"name_ja_jp": "小春", "name_zh_hant": "小春", "name_zh_hant_src": "game", "voice_prefix": "koh",
                 "avatar_file": "小春.png", "line_count": 5},
                {"name_ja_jp": "芳乃", "name_zh_hant": "芳乃", "name_zh_hant_src": "game", "voice_prefix": "yos",
                 "avatar_file": "芳乃.png", "line_count": 9}]
    script = [{"file": F1, "label": "com_part_1", "text_ja_jp": "「志那都荘」",
               "ruby_ja_jp": "「[し]志[な]那[つ]都荘%1;」"}]
    return {"rows": rows, "links": links, "titles": titles, "speakers": speakers, "script": script}


# ---------- 小工具 ----------


def test_parse_branch_effects():
    assert importer.parse_branch_effects('SetBranchFlags("s001*com_part_1_sel",1)') == [
        {"var": "s001*com_part_1_sel", "op": "set", "value": 1}]
    assert importer.parse_branch_effects(None) == []


def test_file_no_and_title():
    assert importer.file_no(F2) == "508a"
    assert importer.file_title(F1) == "アーサー王"
    assert importer.file_title("425レナ・呪いの真実.ks") == "レナ・呪いの真実"


# ---------- build ----------


def test_build_passes_checks_and_keeps_story_order():
    src = _src()
    c = importer.build(src)
    assert importer.check(c, src) == []
    assert [x["story_order"] for x in c.lines] == [0, 1, 2, 3, 4]
    assert [x["seq"] for x in c.lines] == [0, 1, 0, 0, 0]
    assert all(x["origin"] == "game_only" and x["game_id"] == "SenrenBanka" for x in c.lines)


def test_line_voice_speaker_and_inherited_chapter():
    line, next_line = importer.build(_src()).lines[:2]
    assert line["voice_file"] == "yos001_001"
    assert line["speaker_id"] == 1  # 說話者依台詞數排序：芳乃（9 句）是 1 號
    assert next_line["chapter_label"] == "chapter　1-1"  # 空的沿用前一句
    assert next_line["attrs"] == {"segment": "x"}


def test_ruby_comes_from_script_without_style_codes():
    assert importer.build(_src()).lines[1]["ruby_ja_jp"] == "「[し]志[な]那[つ]都荘」"


def test_choice_row_becomes_choice_line_outside_the_corpus():
    choice = importer.build(_src()).lines[2]
    assert choice["kind"] == "choice" and choice["in_corpus"] == 0
    assert choice["text_ja_jp"] == "選択肢：正直に言う / 誤魔化す"
    assert choice["attrs"]["choice_options"][1]["jp"] == "誤魔化す"


def test_auto_row_without_save_point_keeps_all_voices():
    auto = importer.build(_src()).lines[3]
    assert auto["kind"] == "line" and auto["point"] is None
    assert auto["voice_file"] == "a001" and auto["attrs"]["voices"] == ["a001", "b002"]


def test_anchor_method_follows_hist_matched():
    assert [x["anchor_method"] for x in importer.build(_src()).lines] == ["backlog", "backlog", None, None, "fill"]


def test_speakers_tracks_and_line_bgm():
    c = importer.build(_src())
    assert [s["name_ja_jp"] for s in c.speakers] == ["芳乃", "小春"]
    assert c.speakers[0]["avatar_file"] == "芳乃.png"
    assert [t["code"] for t in c.tracks] == ["BGM01", "BGM02"]  # 依 bgm.xp3 序號
    assert c.line_bgm == [(1, 0, 1), (1, 1, 2)]


def test_links_effects_missing_scene_and_ending():
    c = importer.build(_src())
    choice = next(x for x in c.links if x["kind"] == "choice")
    assert choice["effects"] == [{"var": "s001*com_part_1_sel", "op": "set", "value": 1}]
    assert any(s["label"] == "missing_label" for s in c.scenes)  # 連到錨點表沒有的標籤時補一個場景
    assert next(x for x in c.links if x["to_scene_id"] is None)["note"] == "結局"


def test_duplicate_self_loop_and_bad_links_are_counted():
    src = _src()
    src["links"] += [dict(src["links"][0]),
                     {"from_scene": f"{F1}*001_01A", "to_scene": f"{F1}*001_01A", "kind": "next", "evidence": "observed"},
                     {"from_scene": "no-star", "to_scene": None, "kind": "next", "evidence": "observed"}]
    c = importer.build(src)
    assert len(c.links) == 3
    assert c.report["links_skipped"] == {"duplicate": 1, "self_loop": 1, "bad_from": 1}
    assert any("起點" in p for p in importer.check(c, src))


# ---------- check 的負面測例 ----------


def _problems(mutate):
    src = _src()
    mutate(src)
    return importer.check(importer.build(src), src)


def test_check_unknown_speaker():
    assert any("說話者" in p for p in _problems(lambda s: s["rows"][1].update(speaker_jp="不存在")))


def test_check_story_order_gap():
    assert any("story_order" in p for p in _problems(lambda s: s["rows"][4].update(story_order=9)))


def test_check_duplicate_save_point_in_scene():
    assert any("存檔點重複" in p for p in _problems(lambda s: s["rows"][1].update(point=3)))


def test_check_ruby_count_mismatch():
    def add(s):
        s["script"].append({"file": F1, "label": "x", "text_ja_jp": "無", "ruby_ja_jp": "[む]無"})
    assert any("注音" in p for p in _problems(add))


def test_check_too_long_voice():
    assert any("voice_file" in p for p in _problems(lambda s: s["rows"][0].update(voice="v" * 70 + ".ogg")))


def test_check_unknown_bgm_code():
    assert any("BGM 代號" in p for p in _problems(lambda s: s["rows"][0].update(bgm_codes=["BGM99"])))


def test_check_unknown_hist_matched():
    assert any("hist_matched" in p for p in _problems(lambda s: s["rows"][0].update(hist_matched=7)))


def test_bad_scene_aborts_with_row_context():
    src = _src()
    src["rows"][1]["scene"] = "沒有星號"
    with pytest.raises(SystemExit, match="不是 <檔名>\\*<標籤>"):
        importer.build(src)


# ---------- write（假連線）----------


class FakeCursor:
    """記錄 SQL、依查詢回答固定值的假 cursor。A fake cursor answering by query."""

    def __init__(self, conn):
        self.conn = conn
        self._result = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.log.append((sql, params))
        if "FROM story_builds" in sql:
            self._result = (self.conn.builds,)
        elif sql.startswith("SELECT COUNT(*) FROM") or sql.startswith("SELECT COUNT(*) FROM rel_links"):
            table = re.search(r"FROM (\w+)", sql).group(1)
            self._result = (self.conn.refs.get(table, 0),)
        elif "COALESCE(MAX" in sql:
            table = re.search(r"FROM (\w+)", sql).group(1)
            self._result = (self.conn.max_ids.get(table, 0),)
        elif sql.startswith("SELECT COUNT(*), MIN(id), MAX(id) FROM game_lines"):
            ids = [r[0] for r in self.conn.inserted.get("game_lines", [])]
            self._result = (len(ids), min(ids), max(ids))

    def executemany(self, sql, rows):
        self.conn.log.append((sql, None))
        table = re.search(r"INSERT INTO (\w+)", sql).group(1)
        cols = re.search(r"\(([^)]*)\) VALUES", sql).group(1).replace(" ", "").split(",")
        self.conn.inserted.setdefault(table, []).extend(rows)
        self.conn.columns[table] = cols

    def fetchone(self):
        return self._result


class FakeConn:
    def __init__(self, refs=None, builds=0, max_ids=None):
        self.refs, self.builds, self.max_ids = refs or {}, builds, max_ids or {}
        self.log, self.inserted, self.columns = [], {}, {}
        self.committed = self.rolled_back = self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def _write(monkeypatch, conn, drop_story_builds=False):
    monkeypatch.setattr(importer, "connect", lambda database: conn)
    return importer.write(importer.build(_src()), "fluencytides_corpus", drop_story_builds)


@pytest.mark.parametrize("table", ["card_gen_log", "nlp_line_terms", "nlp_line_readings", "rel_links"])
def test_write_refuses_when_record_tables_reference_the_game(monkeypatch, table):
    conn = FakeConn(refs={table: 3})
    with pytest.raises(SystemExit, match="不能重匯"):
        _write(monkeypatch, conn)
    assert not any(sql.startswith("DELETE") for sql, _ in conn.log)
    assert conn.rolled_back and not conn.committed and conn.closed


def test_write_refuses_existing_story_builds_without_flag(monkeypatch):
    conn = FakeConn(builds=1)
    with pytest.raises(SystemExit, match="--drop-story-builds"):
        _write(monkeypatch, conn)
    assert not any(sql.startswith("DELETE") for sql, _ in conn.log)
    assert conn.rolled_back and not conn.committed


def test_write_drops_story_builds_only_with_flag(monkeypatch):
    conn = FakeConn(builds=1)
    _write(monkeypatch, conn, drop_story_builds=True)
    assert any(sql.startswith("DELETE FROM story_builds") for sql, _ in conn.log)
    assert conn.committed


def test_write_deletes_are_scoped_to_this_game(monkeypatch):
    conn = FakeConn()
    _write(monkeypatch, conn)
    deletes = [(sql, params) for sql, params in conn.log if sql.startswith("DELETE")]
    assert deletes and all(params == ("SenrenBanka",) for _, params in deletes)


def test_write_offsets_ids_after_existing_games(monkeypatch):
    conn = FakeConn(max_ids={"game_script_files": 100, "game_scenes": 400, "game_speakers": 80,
                             "game_bgm_tracks": 50, "game_scene_links": 500, "game_lines": 53242})
    assert _write(monkeypatch, conn) == {"lines": 5, "id_range": [53243, 53247]}
    rows = {t: [dict(zip(conn.columns[t], r)) for r in conn.inserted[t]] for t in conn.inserted if t in conn.columns}
    scene_ids = {r["scene_id"] for r in rows["game_scenes"]}
    file_ids = {r["file_id"] for r in rows["game_script_files"]}
    speaker_ids = {r["speaker_id"] for r in rows["game_speakers"]}
    assert min(file_ids) == 101 and min(scene_ids) == 401 and min(speaker_ids) == 81
    assert {r["file_id"] for r in rows["game_scenes"]} <= file_ids
    assert {r["scene_id"] for r in rows["game_lines"]} <= scene_ids
    assert {r["speaker_id"] for r in rows["game_lines"]} - {None} <= speaker_ids
    for link in rows["game_scene_links"]:
        assert link["from_scene_id"] in scene_ids and (link["to_scene_id"] is None or link["to_scene_id"] in scene_ids)
    line_ids = {r["id"] for r in rows["game_lines"]}
    track_ids = {r["track_id"] for r in rows["game_bgm_tracks"]}
    assert all(lid in line_ids and tid in track_ids for lid, _, tid in conn.inserted["game_line_bgm"])
    assert conn.committed and not conn.rolled_back
