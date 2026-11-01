"""語料庫共用讀取層與卡片紀錄的關聯寫入（docs/wip/corpus_db_rebuild_FEAT_2026-10-31.md D9 的 A、B、H）。

Tests for the shared corpus read layer (``corpus_repository``) and for the
``rel_links`` rows that ``GeneratedLogRepository`` writes with each record.

讀取層的 SQL 是可攜的，用 SQLite 建最小的 ``game_*`` 表直接跑；``card_gen_log`` 的建立／重建
（``create_or_restore_record``、``increment_failure_count``）用了 MySQL 專屬語法（``ON DUPLICATE KEY
UPDATE``），這裡用記錄 SQL 的假 session 檢查寫了哪些關係、是不是同一個交易。刪除、修復、清空等可攜的
方法改在 ``test_log_repository_state.py`` 以 SQLite 真表測最終狀態；MySQL 專屬的部分另有
``test_log_repository_mysql.py``（``CORPUS_DB_TESTS=1``）。
The read layer runs on SQLite; the MySQL-only upserts are checked with a
recording fake session here. Portable ledger methods are state-tested in
``test_log_repository_state.py``; ``test_log_repository_mysql.py`` runs the
upserts on MySQL when opted in.
"""

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import scripts.common.env  # noqa: F401  # 載入 .env，供 app.core.config 使用

from app.infrastructure.database.corpus_tables import lemma_of, parse_term_key, term_key
from scripts.common.database import corpus_repository as cr
from scripts.common.database.log_repository import (
    PROJECT_JP_CORE_VERB,
    GeneratedLogRepository,
    _record_links,
)


def _run(coro):
    return asyncio.run(coro)


_DDL = (
    """CREATE TABLE game_profiles (game_id TEXT PRIMARY KEY, title_ja_jp TEXT NOT NULL, voice_dir TEXT,
       avatar_dir TEXT, legacy_source TEXT)""",
    """CREATE TABLE game_speakers (speaker_id INTEGER PRIMARY KEY, game_id TEXT, name_ja_jp TEXT,
       avatar_file TEXT)""",
    """CREATE TABLE game_lines (id INTEGER PRIMARY KEY, game_id TEXT, scene_id INTEGER, story_order INTEGER,
       kind TEXT, chapter_label TEXT, speaker_id INTEGER, text_ja_jp TEXT, ruby_ja_jp TEXT, voice_file TEXT,
       status TEXT, in_corpus INTEGER)""",
    """CREATE TABLE game_bgm_tracks (track_id INTEGER PRIMARY KEY, game_id TEXT, code TEXT, archive_index INTEGER,
       gallery_no INTEGER, title_ja_jp TEXT, title_zh_hant TEXT)""",
    "CREATE TABLE game_line_bgm (line_id INTEGER, ord INTEGER, track_id INTEGER, PRIMARY KEY (line_id, ord))",
)

# (track_id, code, title_ja, title_zh)：BGM51 是標題畫面的曲子，兩種語言的曲名都不明
_TRACKS = [(1, "BGM02", "気の合う仲間たち", "意氣相投的朋友們"), (2, "BGM05", "朝の陽気", "清晨的陽光"),
           (3, "BGM51", None, None)]
# (line_id, ord, track_id)：53197 播放中切換曲子（兩筆，故意倒序插入）；11、15 沒有 BGM；12 是選項畫面
_LINE_BGM = [(10, 0, 1), (53197, 1, 2), (53197, 0, 1), (13, 0, 1), (12, 0, 1), (20, 0, 3)]

# (id, story_order, kind, chapter, speaker_id, text, ruby, voice, status, in_corpus)
_LINES = [
    (10, 0, "line", "ch1", 1, "「おはよう」", None, "nen001", "active", 1),
    (11, 1, "line", "ch1", None, "朝だった。", None, None, "active", 1),
    # 舊 id 比較大、劇情順序在中間的還原台詞
    (53197, 2, "line", "ch1", 2, "「見て」", "「[み]見て」", "mak001", "active", 1),
    (12, 3, "choice", "ch1", None, "選択肢：A / B", None, None, "active", 0),
    (13, 4, "bgv", "ch1", 1, "（背景）", None, "nen_bgv", "active", 1),
    (14, 5, "line", "ch1", 1, "「停用」", None, None, "inactive", 1),
    (15, 6, "line", "ch1", 2, "「そう」", None, None, "active", 1),
    (20, 7, "line", "ch2", 1, "「次の章」", None, None, "active", 1),
]


@pytest.fixture
def session_factory():
    engine = create_async_engine("sqlite+aiosqlite://")

    async def setup():
        async with engine.begin() as conn:
            for ddl in _DDL:
                await conn.execute(text(ddl))
            await conn.execute(text(
                "INSERT INTO game_profiles VALUES ('SabbatOfTheWitch', 'サノバウィッチ', "
                "'voice/yuzusoft/SabbatOfTheWitch', 'avatar/yuzusoft/SabbatOfTheWitch', 'サノバウィッチ'), "
                "('NewGame', '新作', 'voice/x/NewGame', NULL, NULL)"
            ))
            await conn.execute(text(
                "INSERT INTO game_speakers VALUES (1, 'SabbatOfTheWitch', '寧々', NULL), "
                "(2, 'SabbatOfTheWitch', 'めぐる', 'meguru_custom.png')"
            ))
            for row in _LINES:
                await conn.execute(text(
                    "INSERT INTO game_lines (id, story_order, kind, chapter_label, speaker_id, text_ja_jp, ruby_ja_jp, "
                    "voice_file, status, in_corpus, game_id, scene_id) VALUES (:id, :o, :k, :c, :sp, :t, :r, :v, :st, :ic, "
                    "'SabbatOfTheWitch', 1)"
                ), dict(zip(("id", "o", "k", "c", "sp", "t", "r", "v", "st", "ic"), row)))
            for track_id, code, title_ja, title_zh in _TRACKS:
                await conn.execute(text(
                    "INSERT INTO game_bgm_tracks VALUES (:id, 'SabbatOfTheWitch', :code, NULL, NULL, :ja, :zh)"
                ), {"id": track_id, "code": code, "ja": title_ja, "zh": title_zh})
            for line_id, ord_, track_id in _LINE_BGM:
                await conn.execute(text("INSERT INTO game_line_bgm VALUES (:l, :o, :t)"),
                                   {"l": line_id, "o": ord_, "t": track_id})

    _run(setup())
    yield async_sessionmaker(engine, expire_on_commit=False)
    _run(engine.dispose())


def _call(session_factory, fn, *args, **kwargs):
    async def call():
        async with session_factory() as session:
            return await fn(session, *args, **kwargs)

    return _run(call())


class TestReadLayer:
    def test_get_lines_maps_the_old_columns_and_skips_non_corpus_lines(self, session_factory):
        """dialogue＝ruby 優先、role_name＝說話者、chapter＝chapter_label；選項畫面與停用的台詞不算語料。"""
        lines = _call(session_factory, cr.get_lines, [10, 11, 53197, 12, 14, 999])
        assert sorted(lines) == [10, 11, 53197]
        assert lines[53197].dialogue == "「[み]見て」" and lines[53197].text == "「見て」"
        assert (lines[10].speaker, lines[10].chapter, lines[10].voice_file) == ("寧々", "ch1", "nen001")
        assert lines[11].speaker is None
        assert set(_call(session_factory, cr.get_lines, [12, 14], corpus_only=False)) == {12, 14}

    def test_avatar_falls_back_to_speaker_name(self, session_factory):
        lines = _call(session_factory, cr.get_lines, [10, 11, 15])
        assert lines[10].avatar_name == "寧々.jpg"
        assert lines[15].avatar_name == "meguru_custom.png"
        assert lines[11].avatar_name is None

    def test_context_follows_story_order_within_the_chapter(self, session_factory):
        """前後文依 story_order：還原的台詞（大 id）在中間出現；選項與停用的不算；不跨章節。"""
        ctx = _call(session_factory, cr.get_context, 53197, 5, 5)
        assert [line.id for line in ctx] == [10, 11, 53197, 13, 15]
        assert [line.id for line in _call(session_factory, cr.get_context, 13, 1, 1)] == [53197, 13, 15]
        assert _call(session_factory, cr.get_context, 12, 2, 2) == []

    def test_iter_lines_and_chapters(self, session_factory):
        async def collect(session, chapter=None):
            return [line.id async for line in cr.iter_lines(session, "SabbatOfTheWitch", chapter)]

        assert _call(session_factory, collect) == [10, 11, 53197, 13, 15, 20]
        assert _call(session_factory, collect, "ch2") == [20]
        assert _call(session_factory, cr.list_chapters, "SabbatOfTheWitch") == ["ch1", "ch2"]

    def test_metadata_fetcher_format(self, session_factory):
        assert _call(session_factory, cr.get_line_metadata, [11, 15]) == {
            11: {"chapter": "ch1", "speaker": ""},
            15: {"chapter": "ch1", "speaker": "めぐる"},
        }

    def test_find_line_by_text_and_voice(self, session_factory):
        assert _call(session_factory, cr.find_line_by_text, "「見て」", "SabbatOfTheWitch").id == 53197
        assert _call(session_factory, cr.find_line_by_text, "「見て」", "NewGame") is None
        assert _call(session_factory, cr.find_line_by_voice, "nen001").id == 10


_BGM02 = cr.CorpusLineBgm("BGM02", 0, "気の合う仲間たち", "意氣相投的朋友們")


def _count_statements(session_factory) -> list[str]:
    """記下 session 執行的每一句 SQL（檢查 BGM 是一次批次查詢，不是每句一次）。Record executed SQL."""
    statements: list[str] = []
    engine = session_factory.kw["bind"].sync_engine
    event.listen(engine, "before_cursor_execute", lambda conn, cursor, sql, *rest: statements.append(sql))
    return statements


class TestLineBgm:
    def test_get_lines_carries_bgm_in_ord_order_with_one_extra_query(self, session_factory):
        """兩首的台詞依 ord 排列；沒有 BGM 的是空 tuple；曲名不明的照樣有代號。整批只多一次查詢。"""
        statements = _count_statements(session_factory)
        lines = _call(session_factory, cr.get_lines, [10, 11, 53197, 20])
        assert len(statements) == 2 and "game_line_bgm" in statements[1]
        assert lines[53197].bgm == (_BGM02, cr.CorpusLineBgm("BGM05", 1, "朝の陽気", "清晨的陽光"))
        assert lines[53197].bgm_codes == ("BGM02", "BGM05")
        assert lines[10].bgm == (_BGM02,)
        assert lines[11].bgm == () and lines[11].bgm_codes == ()
        assert lines[20].bgm == (cr.CorpusLineBgm("BGM51", 0, None, None),)

    def test_context_carries_bgm(self, session_factory):
        statements = _count_statements(session_factory)
        ctx = _call(session_factory, cr.get_context, 53197, 5, 5)
        assert len(statements) == 4  # 目標句、前文、後文、BGM
        assert [(line.id, line.bgm_codes) for line in ctx] == [
            (10, ("BGM02",)), (11, ()), (53197, ("BGM02", "BGM05")), (13, ("BGM02",)), (15, ())]

    def test_iter_lines_prefetches_the_scope(self, session_factory):
        async def collect(session, chapter=None):
            return [(line.id, line.bgm_codes) async for line in cr.iter_lines(session, "SabbatOfTheWitch", chapter)]

        assert _call(session_factory, collect) == [
            (10, ("BGM02",)), (11, ()), (53197, ("BGM02", "BGM05")), (13, ("BGM02",)), (15, ()), (20, ("BGM51",))]
        assert _call(session_factory, collect, "ch2") == [(20, ("BGM51",))]

    def test_finders_carry_bgm_and_metadata_is_unchanged(self, session_factory):
        assert _call(session_factory, cr.find_line_by_text, "「見て」").bgm_codes == ("BGM02", "BGM05")
        assert _call(session_factory, cr.find_line_by_voice, "nen001").bgm == (_BGM02,)
        assert _call(session_factory, cr.get_line_metadata, [10]) == {10: {"chapter": "ch1", "speaker": "寧々"}}

    def test_lines_built_without_bgm_default_to_empty(self):
        """既有的建構方式（不給 bgm）照樣能用。Existing constructors keep working."""
        line = cr.CorpusLine(1, "G", None, None, "", "", None, None, 0, "line")
        assert line.bgm == () and line.bgm_codes == ()


class TestGameProfiles:
    def test_profile_paths_and_es_source(self, session_factory, monkeypatch):
        monkeypatch.setattr(cr.settings, "GAME_ASSETS_ROOT", r"C:\material", raising=False)
        game = _call(session_factory, cr.get_game_profile, "SabbatOfTheWitch")
        assert game.voice_path() == Path(r"C:\material") / "voice/yuzusoft/SabbatOfTheWitch"
        assert game.media_prefix == "SabbatOfTheWitch_"
        assert game.es_source == "サノバウィッチ"
        new_game = _call(session_factory, cr.get_game_profile, "NewGame")
        assert new_game.es_source == "新作"  # 沒有舊 source 時用日文標題
        with pytest.raises(ValueError, match="頭像"):
            new_game.avatar_path()
        assert _call(session_factory, cr.list_game_ids) == ["NewGame", "SabbatOfTheWitch"]

    def test_unknown_game_lists_the_available_ones(self, session_factory):
        with pytest.raises(LookupError, match="SabbatOfTheWitch"):
            _call(session_factory, cr.get_game_profile, "Nope")

    def test_missing_assets_root_is_explained(self, session_factory, monkeypatch):
        monkeypatch.setattr(cr.settings, "GAME_ASSETS_ROOT", "", raising=False)
        game = _call(session_factory, cr.get_game_profile, "SabbatOfTheWitch")
        with pytest.raises(ValueError, match="GAME_ASSETS_ROOT"):
            game.voice_path()


# =============================================================================
# card_gen_log 與 rel_links（D9 的 B）
# =============================================================================


def test_term_key_round_trip():
    assert term_key("見る") == "ja-JP:見る"
    assert lemma_of("ja-JP:見る") == "見る"
    assert parse_term_key("zh-Hant:看") == ("zh-Hant", "看")
    assert lemma_of("zh-Hant:看", lang="zh-Hant") == "看"
    # 只切第一個冒號 / only the first colon separates
    assert parse_term_key("ja-JP:a:b") == ("ja-JP", "a:b")


@pytest.mark.parametrize("key", ["見る", "", ":見る", "ja-JP:", "ja-JP: ", "見:る", "ja_JP:見る"])
def test_parse_term_key_rejects_malformed_keys(key):
    with pytest.raises(ValueError):
        parse_term_key(key)
    with pytest.raises(ValueError):
        lemma_of(key)


def test_lemma_of_rejects_another_language():
    """別的語言不會被當成原形整串回傳。A key of another language is not a lemma."""
    with pytest.raises(ValueError):
        lemma_of("zh-Hant:見る")


def test_record_links_cover_the_four_system_relations():
    links = _record_links(line_id=105, key="ja-JP:見る", master=1, context=2, cloze=3)
    assert {(link.relation_type, link.src_id, link.dst_type, link.dst_id) for link in links} == {
        ("core.has_context", "1", "note", "2"),
        ("core.has_cloze", "1", "note", "3"),
        ("core.example_from", "2", "script_line", "105"),
        ("core.example_from", "3", "script_line", "105"),
        ("core.teaches", "2", "term", "ja-JP:見る"),
        ("core.teaches", "3", "term", "ja-JP:見る"),
        ("core.teaches", "1", "term", "ja-JP:見る"),
    }
    labels = {(link.relation_type, link.src_id): (link.src_label, link.dst_label) for link in links}
    assert labels[("core.has_context", "1")] == ("見る", "見る_context")
    assert labels[("core.teaches", "3")] == ("見る_cloze", "見る")


class _Result:
    def __init__(self, rows=(), scalar=None):
        self._rows = list(rows)
        self._scalar = scalar
        self.rowcount = 1
        self.lastrowid = 77

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def scalar(self):
        return self._scalar


class RecordingSession:
    """記錄每條 SQL；依 SQL 內容回答遊戲、既有紀錄與關係查詢。Records statements."""

    def __init__(self, *, previous=None, existing_link=None, remaining=0, records=()):
        self.previous = previous
        self.existing_link = existing_link
        self.remaining = remaining
        self.records = list(records)
        self.statements: list[tuple[str, object]] = []
        self.commits = 0

    async def execute(self, clause, params=None):
        sql = " ".join(str(clause).split())
        self.statements.append((sql, params))
        if sql.startswith("SELECT game_id FROM game_lines"):
            return _Result([("SabbatOfTheWitch",)])
        if sql.startswith("SELECT master_note_id, context_note_id, cloze_note_id FROM card_gen_log WHERE line_id"):
            return _Result([self.previous] if self.previous else [])
        if sql.startswith("SELECT relation_id, attrs FROM rel_links"):
            return _Result([self.existing_link] if self.existing_link else [])
        if sql.startswith("SELECT COUNT(*) FROM card_gen_log"):
            return _Result(scalar=self.remaining)
        if sql.startswith("SELECT id, card_type, line_id"):
            return _Result(self.records)
        return _Result()

    async def commit(self):
        self.commits += 1

    def sql(self, prefix):
        return [(sql, params) for sql, params in self.statements if sql.startswith(prefix)]


_RECORD = {
    "script_id": 105, "verb_lemma": "見る", "master_note_id": 1, "context_note_id": 2,
    "cloze_note_id": 3, "llm_model": "m",
}


class TestLedgerRelations:
    def test_create_writes_record_and_links_in_one_transaction(self):
        session = RecordingSession()
        _run(GeneratedLogRepository().create_or_restore_record(session, dict(_RECORD), project=PROJECT_JP_CORE_VERB))
        (insert_sql, params), = session.sql("INSERT INTO card_gen_log")
        assert params["game_id"] == "SabbatOfTheWitch"  # 由台詞查出
        assert (params["line_id"], params["term_key"], params["card_type"]) == (105, "ja-JP:見る", "jp_core_verb")
        links = session.sql("INSERT INTO rel_links")
        assert len(links) == 7
        assert all(p["created_by"] == "pipeline" and p["attrs"] == '{"card_type": "jp_core_verb"}' for _, p in links)
        assert session.commits == 1
        assert session.statements[-1][0].startswith("INSERT INTO rel_links")  # commit 在全部寫完之後

    def test_existing_handler_link_is_reused_not_duplicated(self):
        """網站 handler 已寫的母卡 → 子卡：找到就補 attrs.card_type，不再新增。"""
        session = RecordingSession(existing_link=(9, None))
        _run(GeneratedLogRepository().create_or_restore_record(session, dict(_RECORD), project=PROJECT_JP_CORE_VERB))
        assert session.sql("INSERT INTO rel_links") == []
        assert len(session.sql("UPDATE rel_links SET attrs")) == 7

    def test_regenerated_record_retires_the_old_children(self):
        """軟刪除後重新生成（note id 換了）：舊子卡的關係先標成 deleted。"""
        session = RecordingSession(previous=(1, 20, 30), remaining=1)
        _run(GeneratedLogRepository().create_or_restore_record(session, dict(_RECORD), project=PROJECT_JP_CORE_VERB))
        retired = {p["src_id"] for _, p in session.sql("UPDATE rel_links SET status")}
        assert retired == {"1", "20", "30"}  # 母卡 → 舊子卡、舊子卡 → 台詞／詞；母卡 → 詞因仍有紀錄而保留
        assert not any(p["dst_type"] == "term" and p["src_id"] == "1" for _, p in session.sql("UPDATE rel_links SET status"))

    def test_failure_records_write_no_links(self):
        session = RecordingSession()
        _run(GeneratedLogRepository().increment_failure_count(
            session, 105, "見る", 1, "m", project=PROJECT_JP_CORE_VERB,
        ))
        assert session.sql("INSERT INTO rel_links") == []
        (_, params), = session.sql("INSERT INTO card_gen_log")
        assert params["game_id"] == "SabbatOfTheWitch" and params["term_key"] == "ja-JP:見る"
