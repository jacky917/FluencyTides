"""建卡紀錄 ``card_gen_log`` 與卡片關聯 ``rel_links`` 的狀態測試（SQLite 上跑真的 repository）。

State tests for the ``card_gen_log`` ↔ ``rel_links`` lifecycle in
``GeneratedLogRepository``, run against SQLite with the real repository.

照 ``test_relation_sync.py`` 的做法：``rel_links`` / ``rel_types`` 以 ``corpus_metadata.create_all`` 建，
另建最小的 ``card_gen_log`` 與 ``game_lines``；紀錄用 ``insert_repaired_record``（可攜的 INSERT，與管線同一套
``_ensure_link``）寫入，再呼叫要測的方法，最後讀表斷言每一列的 ``status`` / ``is_deleted``。
``create_or_restore_record`` 與 ``increment_failure_count`` 用了 MySQL 專屬的 ``ON DUPLICATE KEY UPDATE``，
不在這裡測（見 ``test_corpus_repository.py`` 的記錄 SQL 測試與 ``test_log_repository_mysql.py``）。
Following ``test_relation_sync.py``: the relation tables come from
``corpus_metadata``, records are seeded with the portable
``insert_repaired_record``, and assertions read the final row states. The
MySQL-only upserts are covered elsewhere.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.infrastructure.database.corpus_tables import corpus_metadata, rel_links_table
from scripts.common.database.log_repository import (
    _LINK_BATCH,
    PROJECT_JP_CORE_VERB,
    PROJECT_JP_VERB_PAIR,
    GeneratedLogRepository,
)

VP = PROJECT_JP_VERB_PAIR
CV = PROJECT_JP_CORE_VERB
GAME = "SabbatOfTheWitch"

_DDL = (
    "CREATE TABLE game_lines (id INTEGER PRIMARY KEY, game_id TEXT NOT NULL)",
    """CREATE TABLE card_gen_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        game_id TEXT NOT NULL,
        line_id INTEGER NOT NULL,
        term_key TEXT NOT NULL,
        card_type TEXT NOT NULL,
        master_note_id INTEGER NOT NULL,
        context_note_id INTEGER,
        cloze_note_id INTEGER,
        is_deleted INTEGER NOT NULL DEFAULT 0,
        delete_count INTEGER NOT NULL DEFAULT 0,
        failure_count INTEGER NOT NULL DEFAULT 0,
        llm_model TEXT,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (line_id, term_key, card_type)
    )""",
)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def engine():
    engine = create_async_engine("sqlite+aiosqlite://")

    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(corpus_metadata.create_all)
            for ddl in _DDL:
                await conn.execute(text(ddl))
            for line_id in range(100, 400):
                await conn.execute(text("INSERT INTO game_lines VALUES (:id, :game)"), {"id": line_id, "game": GAME})

    _run(setup())
    yield engine
    _run(engine.dispose())


@pytest.fixture
def sf(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


repo = GeneratedLogRepository()


def _call(sf, method, *args, **kwargs):
    """開一個 session 呼叫 repository 方法。Call a repository method in a fresh session."""
    async def call():
        async with sf() as session:
            return await getattr(repo, method)(session, *args, **kwargs)

    return _run(call())


def _seed(sf, line, lemma, master, context, cloze, project=VP):
    """寫一筆紀錄與它的關聯，回傳紀錄 id。Seed one record (and its links)."""
    return _call(
        sf, "insert_repaired_record", script_id=line, verb_lemma=lemma, master_note_id=master,
        context_note_id=context, cloze_note_id=cloze, project=project,
    )


def _records(sf) -> dict[int, dict]:
    async def read():
        async with sf() as session:
            rows = (await session.execute(text(
                "SELECT id, is_deleted, delete_count, failure_count, master_note_id, context_note_id, "
                "cloze_note_id, card_type FROM card_gen_log"
            ))).all()
        return {
            r[0]: {"is_deleted": bool(r[1]), "delete_count": r[2], "failure_count": r[3],
                   "notes": (r[4], r[5], r[6]), "card_type": r[7]}
            for r in rows
        }

    return _run(read())


def _links(sf) -> dict[tuple, list[str]]:
    """``(relation_type, src_id, dst_type, dst_id) → [status, ...]``。"""
    async def read():
        async with sf() as session:
            rows = (await session.execute(select(
                rel_links_table.c.relation_type, rel_links_table.c.src_id, rel_links_table.c.dst_type,
                rel_links_table.c.dst_id, rel_links_table.c.status,
            ))).all()
        out: dict[tuple, list[str]] = {}
        for relation_type, src, dst_type, dst, status in rows:
            out.setdefault((relation_type, src, dst_type, dst), []).append(status)
        return out

    return _run(read())


def _child_links(master, child, line, lemma="見る", kind="context"):
    """一張子卡的三條關係鍵。The three relation keys of one child note."""
    return [
        (f"core.has_{kind}", str(master), "note", str(child)),
        ("core.example_from", str(child), "script_line", str(line)),
        ("core.teaches", str(child), "term", f"ja-JP:{lemma}"),
    ]


def _master_term(master, lemma="見る"):
    return ("core.teaches", str(master), "term", f"ja-JP:{lemma}")


def _record_keys(master, context, cloze, line, lemma="見る"):
    keys = [_master_term(master, lemma)]
    if context is not None:
        keys += _child_links(master, context, line, lemma, "context")
    if cloze is not None:
        keys += _child_links(master, cloze, line, lemma, "cloze")
    return keys


def _status(links, keys):
    """這些關係的狀態（每條應只有一列）。Status of each key; one row per relation."""
    assert all(len(links[key]) == 1 for key in keys), {key: links.get(key) for key in keys}
    return {links[key][0] for key in keys}


# ============================================================ 寫入（種子資料本身）


def test_insert_repaired_record_writes_record_and_seven_links(sf):
    record_id = _seed(sf, 105, "見る", 1, 2, 3)
    assert _records(sf)[record_id]["notes"] == (1, 2, 3)
    links = _links(sf)
    assert len(links) == 7
    assert _status(links, _record_keys(1, 2, 3, 105)) == {"active"}


def test_sibling_record_reuses_the_shared_master_term_link(sf):
    """同一張母卡、同一個詞的第二句：母卡 → 詞已存在，不重複新增。"""
    _seed(sf, 105, "見る", 1, 2, 3)
    _seed(sf, 106, "見る", 1, 4, 5)
    assert _links(sf)[_master_term(1)] == ["active"]


# ============================================================ 軟刪除


def test_soft_delete_record_keeps_master_term_while_a_sibling_is_active(sf):
    a = _seed(sf, 105, "見る", 1, 2, 3)
    b = _seed(sf, 106, "見る", 1, 4, 5)
    other = _seed(sf, 105, "見る", 10, 11, 12, project=CV)  # 同句同詞、另一個卡片類型

    _call(sf, "soft_delete_record", 105, "見る", project=VP)

    records = _records(sf)
    assert records[a]["is_deleted"] and not records[b]["is_deleted"] and not records[other]["is_deleted"]
    links = _links(sf)
    assert _status(links, _child_links(1, 2, 105) + _child_links(1, 3, 105, kind="cloze")) == {"deleted"}
    # 兄弟紀錄 b 仍有效 → 母卡 → 詞保留；b 自己的關係完全不動
    assert _status(links, [_master_term(1)] + _record_keys(1, 4, 5, 106)[1:]) == {"active"}
    # 另一個卡片類型的紀錄不受影響
    assert _status(links, _record_keys(10, 11, 12, 105)) == {"active"}

    # 最後一筆也刪掉 → 母卡 → 詞才撤銷
    _call(sf, "soft_delete_record", 106, "見る", project=VP)
    assert _status(_links(sf), [_master_term(1)]) == {"deleted"}


def test_soft_delete_by_ids_only_touches_the_given_card_type(sf):
    a = _seed(sf, 105, "見る", 1, 2, 3)
    other = _seed(sf, 106, "見る", 10, 11, 12, project=CV)

    _call(sf, "soft_delete_by_ids", [a, other], project=VP)

    records = _records(sf)
    assert records[a]["is_deleted"] is True
    assert records[other]["is_deleted"] is False  # 另一個卡片類型的 id 被忽略
    links = _links(sf)
    assert _status(links, _record_keys(1, 2, 3, 105)) == {"deleted"}
    assert _status(links, _record_keys(10, 11, 12, 106)) == {"active"}


# ============================================================ 硬刪除


def test_hard_delete_by_ids_removes_rows_but_keeps_retired_links(sf):
    a = _seed(sf, 105, "見る", 1, 2, 3)
    b = _seed(sf, 106, "見る", 1, 4, 5)

    assert _call(sf, "hard_delete_by_ids", [a]) == 1

    assert set(_records(sf)) == {b}
    links = _links(sf)
    # rel_links 不刪列，只標 deleted；母卡 → 詞因 b 仍有效而保留
    assert _status(links, _child_links(1, 2, 105) + _child_links(1, 3, 105, kind="cloze")) == {"deleted"}
    assert _status(links, _record_keys(1, 4, 5, 106)) == {"active"}


# ============================================================ 以 note id 刪（刪卡工具）


def test_delete_by_note_ids_soft_counts_and_retires(sf):
    a = _seed(sf, 105, "見る", 1, 2, 3)

    assert _call(sf, "count_record_by_note_ids", 1, 3, 2, project=VP) == 1
    assert _call(sf, "delete_record_by_note_ids", 1, 3, 2, project=VP) == 1

    record = _records(sf)[a]
    assert record["is_deleted"] and record["delete_count"] == 1
    assert _status(_links(sf), _record_keys(1, 2, 3, 105)) == {"deleted"}
    # 已軟刪除的紀錄不再計入、也不會被再軟刪一次
    assert _call(sf, "count_record_by_note_ids", 1, 3, 2, project=VP) == 0
    assert _call(sf, "delete_record_by_note_ids", 1, 3, 2, project=VP) == 0
    assert _records(sf)[a]["delete_count"] == 1


def test_delete_by_note_ids_hard_on_an_already_soft_deleted_record(sf):
    a = _seed(sf, 105, "見る", 1, 2, 3)
    _call(sf, "soft_delete_by_ids", [a], project=VP)

    assert _call(sf, "delete_record_by_note_ids", 1, 3, 2, project=VP, hard=True) == 1

    assert _records(sf) == {}
    assert _status(_links(sf), _record_keys(1, 2, 3, 105)) == {"deleted"}


def test_delete_by_note_ids_matches_a_null_child(sf):
    """只有情境子卡的紀錄（cloze 是 NULL）也刪得到。A record with a NULL child matches."""
    a = _seed(sf, 105, "見る", 7, 8, None)

    assert _call(sf, "count_record_by_note_ids", 7, None, 8, project=VP) == 1
    assert _call(sf, "get_record_ids_by_note_ids", 7, None, 8, project=VP) == [a]
    assert _call(sf, "delete_record_by_note_ids", 7, None, 8, project=VP) == 1

    assert _records(sf)[a]["is_deleted"] is True
    assert _status(_links(sf), _record_keys(7, 8, None, 105)) == {"deleted"}


def test_delete_by_note_ids_does_not_match_null_against_a_value(sf):
    """NULL 只和 NULL 相等：給了 cloze id 就不會刪到 cloze 是 NULL 的紀錄。"""
    a = _seed(sf, 105, "見る", 7, 8, None)
    assert _call(sf, "delete_record_by_note_ids", 7, 9, 8, project=VP) == 0
    assert _records(sf)[a]["is_deleted"] is False


def test_delete_by_note_ids_ignores_another_card_type(sf):
    other = _seed(sf, 105, "見る", 1, 2, 3, project=CV)
    assert _call(sf, "delete_record_by_note_ids", 1, 3, 2, project=VP) == 0
    assert _records(sf)[other]["is_deleted"] is False
    assert _status(_links(sf), _record_keys(1, 2, 3, 105)) == {"active"}


def test_delete_by_note_ids_without_commit_rolls_back_with_the_caller(sf):
    """``commit=False``：呼叫端回滾時，紀錄與關係一起退回。"""
    a = _seed(sf, 105, "見る", 1, 2, 3)

    async def body():
        async with sf() as session:
            assert await repo.delete_record_by_note_ids(session, 1, 3, 2, project=VP, commit=False) == 1
            await session.rollback()

    _run(body())
    assert _records(sf)[a]["is_deleted"] is False
    assert _status(_links(sf), _record_keys(1, 2, 3, 105)) == {"active"}


# ============================================================ 清空整個卡片類型


def _count_link_updates(engine):
    """計算對 rel_links 發出的 UPDATE 數。Count UPDATE statements on rel_links."""
    calls = []

    def before(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().startswith("UPDATE rel_links"):
            calls.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", before)
    return calls


def test_clear_all_records_batches_more_than_500_notes(sf, engine):
    records = 200  # 600 個 note id，超過一批
    for i in range(records):
        _seed(sf, 100 + i, "見る", 10_000 + i, 20_000 + i, 30_000 + i)
    other = _seed(sf, 399, "見る", 1, 2, 3, project=CV)
    assert 3 * records > _LINK_BATCH

    updates = _count_link_updates(engine)
    _call(sf, "clear_all_records", project=VP)

    assert len(updates) == -(-3 * records // _LINK_BATCH)  # ceil：分批撤銷
    rows = _records(sf)
    assert all(row["is_deleted"] for rid, row in rows.items() if rid != other)
    assert rows[other]["is_deleted"] is False
    links = _links(sf)
    vp_keys = [key for i in range(records) for key in _record_keys(10_000 + i, 20_000 + i, 30_000 + i, 100 + i)]
    assert _status(links, vp_keys) == {"deleted"}
    assert _status(links, _record_keys(1, 2, 3, 399)) == {"active"}


def test_clear_all_records_hard_delete_only_removes_that_card_type(sf):
    a = _seed(sf, 105, "見る", 1, 2, 3)
    other = _seed(sf, 106, "見る", 10, 11, 12, project=CV)

    _call(sf, "clear_all_records", project=VP, hard_delete=True)

    assert set(_records(sf)) == {other}
    links = _links(sf)
    assert _status(links, _record_keys(1, 2, 3, 105)) == {"deleted"}
    assert _status(links, _record_keys(10, 11, 12, 106)) == {"active"}
    assert a not in _records(sf)


# ============================================================ 完整性修復


def test_repair_record_restore_points_at_the_new_notes(sf):
    a = _seed(sf, 105, "見る", 1, 2, 3)
    _call(sf, "soft_delete_by_ids", [a], project=VP)

    updated = _call(sf, "repair_record", a, master_note_id=1, context_note_id=20, cloze_note_id=30,
                    restore=True, project=VP)

    assert updated == 1
    record = _records(sf)[a]
    assert record["is_deleted"] is False and record["notes"] == (1, 20, 30)
    links = _links(sf)
    assert _status(links, _child_links(1, 2, 105) + _child_links(1, 3, 105, kind="cloze")) == {"deleted"}
    # 母卡 → 詞在軟刪除時撤銷過；修復後重新建立一條 active
    assert "active" in links[_master_term(1)]
    assert _status(links, _record_keys(1, 20, 30, 105)[1:]) == {"active"}


def test_repair_record_sync_retires_the_drifted_notes(sf):
    a = _seed(sf, 105, "見る", 1, 2, 3)

    updated = _call(sf, "repair_record", a, master_note_id=1, context_note_id=20, cloze_note_id=30,
                    restore=False, project=VP)

    assert updated == 1
    assert _records(sf)[a]["is_deleted"] is False
    links = _links(sf)
    assert _status(links, _child_links(1, 2, 105) + _child_links(1, 3, 105, kind="cloze")) == {"deleted"}
    assert _status(links, _record_keys(1, 20, 30, 105)) == {"active"}


def test_repair_record_of_another_card_type_updates_nothing(sf):
    other = _seed(sf, 105, "見る", 1, 2, 3, project=CV)
    updated = _call(sf, "repair_record", other, master_note_id=1, context_note_id=20, cloze_note_id=30,
                    restore=False, project=VP)
    assert updated == 0
    assert _records(sf)[other]["notes"] == (1, 2, 3)
    assert _status(_links(sf), _record_keys(1, 2, 3, 105)) == {"active"}


# ============================================================ 永久跳過


def test_mark_skipped_reports_the_matched_rows(sf):
    a = _seed(sf, 105, "見る", 1, 2, 3)
    other = _seed(sf, 105, "見る", 10, 11, 12, project=CV)

    assert _call(sf, "mark_skipped", 105, "見る", project=VP) == 1
    assert _call(sf, "mark_skipped", 999, "見る", project=VP) == 0  # 沒有紀錄 → 0，呼叫端會警告

    records = _records(sf)
    assert records[a]["failure_count"] == 9
    assert records[other]["failure_count"] == 0


# ============================================================ 刪卡工具：Anki 刪除失敗時兩張表一起回滾


def test_child_deleter_rolls_back_ledger_and_links_when_anki_fails(sf, monkeypatch):
    from scripts.local_anki.common.deletion import child_deleter as child_deleter_mod
    from scripts.local_anki.common.deletion.profiles import get_profile
    from tests.test_deletion_toolkit import DeleterFakeAnkiClient, note

    a = _seed(sf, 105, "開く", 100, 200, 300)
    vp = get_profile(VP)
    master_json = json.dumps([{"audio": "a.mp3", "avatar": "none", "cloze_note_id": 300, "context_note_id": 200}])
    client = DeleterFakeAnkiClient(
        notes_by_model={
            vp.master_model: [note(100, vp.master_model, {
                "Intransitive_Data_JSON": master_json, "Transitive_Data_JSON": "[]",
            })],
            vp.context_model: [note(200, vp.context_model, {"Master_Note_ID": "100"})],
            vp.cloze_model: [note(300, vp.cloze_model, {"Master_Note_ID": "100"})],
        },
        fail_delete=True,
    )

    async def safe_read_list(card_service, note_id, field_name):
        return json.loads(master_json) if field_name == "Intransitive_Data_JSON" else []

    async def remove_from_list(card_service, note_id, field_name, index):
        return True

    async def fake_integrity(profile, is_execute, client=None):
        return 0

    async def noop():
        pass

    monkeypatch.setattr(child_deleter_mod, "AnkiClient", lambda: client)
    monkeypatch.setattr(child_deleter_mod, "corpus_async_session_factory", sf)
    monkeypatch.setattr(child_deleter_mod, "dispose_corpus_engine", noop)
    monkeypatch.setattr(child_deleter_mod, "AnkiModelManager", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(child_deleter_mod, "CardService", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(child_deleter_mod, "run_integrity_check", fake_integrity)
    monkeypatch.setattr(child_deleter_mod.AnkiJsonFieldManager, "safe_read_list", safe_read_list)
    monkeypatch.setattr(child_deleter_mod.AnkiJsonFieldManager, "remove_from_list", remove_from_list)

    _run(child_deleter_mod.run_child_deletion(vp, dry_run=False, master_nid=100))

    assert client.deleted_notes == []
    record = _records(sf)[a]
    assert record["is_deleted"] is False and record["delete_count"] == 0
    assert _status(_links(sf), _record_keys(100, 200, 300, 105, lemma="開く")) == {"active"}
    # 母卡 JSON 用備份還原 / the master JSON was restored from the backup
    assert 100 in client.updated_fields
