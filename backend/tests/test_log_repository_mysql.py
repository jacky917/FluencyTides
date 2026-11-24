"""``card_gen_log`` 的 MySQL 專屬寫入（``ON DUPLICATE KEY UPDATE``）對真的 MySQL 跑（預設跳過）。

Integration tests for the MySQL-only ledger upserts
(``create_or_restore_record``, ``increment_failure_count``). Opt in with
``CORPUS_DB_TESTS=1``; skipped otherwise so CI never needs MySQL.

**不碰任何真表**：每個測試在一條專屬連線上依 ``schema.sql`` 建 ``TEMPORARY`` 的 ``game_lines`` / ``card_gen_log`` /
``rel_links``。MySQL 的暫存表在該連線內會遮住同名的真表，連線關閉就消失；寫入前先確認三張表都是空的
（真表一定有資料），任何一張沒遮住就中止。連線用 ``MYSQL_DATABASE``（例：
``MYSQL_DATABASE=fluencytides_corpus``）。
No real table is touched: each test creates TEMPORARY tables on a dedicated
connection, which shadow the real ones for that connection only, and aborts
unless all three are empty before writing.
"""

import asyncio
import os

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("CORPUS_DB_TESTS") != "1", reason="設定 CORPUS_DB_TESTS=1 才跑")

GAME = "SabbatOfTheWitch"
VP = "jp_verb_pair"


def _run(body):
    import scripts.common.env  # noqa: F401  # 載入 .env

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import settings

    async def main():
        engine = create_async_engine(settings.mysql_async_url, poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                # 遮住真表的暫存表（只存在於這條連線）/ session-only tables shadowing the real ones
                await conn.execute(text(
                    "CREATE TEMPORARY TABLE game_lines (id BIGINT UNSIGNED PRIMARY KEY, game_id VARCHAR(64) NOT NULL)"
                ))
                for table in ("card_gen_log", "rel_links"):
                    await conn.execute(text(_temporary_ddl(table)))
                for table in ("game_lines", "card_gen_log", "rel_links"):
                    count = (await conn.execute(text(f"SELECT COUNT(*) FROM {table}"))).scalar()
                    if count != 0:
                        raise RuntimeError(f"{table} 不是空的暫存表，中止以免寫到真表")
                await conn.execute(text("INSERT INTO game_lines VALUES (105, :game), (106, :game)"), {"game": GAME})
                await conn.commit()
                session = AsyncSession(bind=conn, expire_on_commit=False)
                try:
                    return await body(session, text)
                finally:
                    await session.close()
        finally:
            await engine.dispose()

    return asyncio.run(main())


def _temporary_ddl(table: str) -> str:
    """schema.sql 的建表 SQL 改成 ``TEMPORARY``、拿掉外鍵（暫存表不支援外鍵）。

    The schema.sql DDL as a TEMPORARY table without foreign keys.
    """
    import re

    from scripts.common.database.init_db import SCHEMA_PATH, table_ddl

    ddl = table_ddl(SCHEMA_PATH.read_text(encoding="utf-8"), table)
    ddl = ddl.replace(f"CREATE TABLE IF NOT EXISTS {table} ", f"CREATE TEMPORARY TABLE {table} ", 1)
    lines = [line for line in ddl.splitlines() if not line.strip().startswith("CONSTRAINT ")]
    ddl = re.sub(r",\s*\n\)", "\n)", "\n".join(lines))
    assert ddl.startswith(f"CREATE TEMPORARY TABLE {table} ") and "FOREIGN KEY" not in ddl
    return ddl


def _record(context, cloze, *, line=105, master=1):
    return {
        "script_id": line, "verb_lemma": "見る", "master_note_id": master,
        "context_note_id": context, "cloze_note_id": cloze, "llm_model": "test-model",
    }


async def _link_states(session, text):
    rows = (await session.execute(text(
        "SELECT relation_type, src_id, dst_id, status FROM rel_links ORDER BY relation_id"
    ))).all()
    return [tuple(row) for row in rows]


def test_regenerate_retires_old_children_and_keeps_one_master_term():
    from scripts.common.database.log_repository import GeneratedLogRepository

    repo = GeneratedLogRepository()

    async def body(session, text):
        await repo.create_or_restore_record(session, _record(2, 3), project=VP)
        await repo.soft_delete_record(session, 105, "見る", project=VP)
        await repo.create_or_restore_record(session, _record(20, 30), project=VP)

        row = (await session.execute(text(
            "SELECT is_deleted, delete_count, failure_count, context_note_id, cloze_note_id FROM card_gen_log"
        ))).one()
        assert tuple(row) == (0, 1, 0, 20, 30)
        states = await _link_states(session, text)
        active = {(t, s, d) for t, s, d, st in states if st == "active"}
        assert ("core.has_context", "1", "20") in active and ("core.has_cloze", "1", "30") in active
        assert not any(s in ("2", "3") for _, s, _ in active)
        assert not any(d in ("2", "3") for _, _, d in active)
        assert sum(1 for t, s, d in active if t == "core.teaches" and s == "1") == 1

    _run(body)


def test_failure_count_then_success_resets_it():
    from scripts.common.database.log_repository import GeneratedLogRepository

    repo = GeneratedLogRepository()

    async def body(session, text):
        await repo.increment_failure_count(session, 106, "見る", 1, "m", project=VP)
        await repo.increment_failure_count(session, 106, "見る", 1, "m", project=VP)
        assert (await session.execute(text("SELECT failure_count FROM card_gen_log"))).scalar() == 2
        assert await _link_states(session, text) == []  # 失敗紀錄不寫關係
        await repo.create_or_restore_record(session, _record(2, None, line=106), project=VP)
        row = (await session.execute(text("SELECT failure_count, cloze_note_id FROM card_gen_log"))).one()
        assert tuple(row) == (0, None)

    _run(body)


def test_delete_by_note_ids_matches_null_child_on_mysql():
    """可攜的 NULL 比對在 MySQL 上一樣成立。The portable NULL-safe match works on MySQL."""
    from scripts.common.database.log_repository import GeneratedLogRepository

    repo = GeneratedLogRepository()

    async def body(session, text):
        await repo.create_or_restore_record(session, _record(2, None), project=VP)
        assert await repo.count_record_by_note_ids(session, 1, None, 2, project=VP) == 1
        assert await repo.delete_record_by_note_ids(session, 1, None, 2, project=VP) == 1
        assert {st for *_, st in await _link_states(session, text)} == {"deleted"}

    _run(body)


def test_existing_handler_link_is_reused():
    from scripts.common.database.log_repository import GeneratedLogRepository

    repo = GeneratedLogRepository()

    async def body(session, text):
        await session.execute(text(
            "INSERT INTO rel_links (relation_type, src_type, src_id, src_label, dst_type, dst_id, dst_label, "
            "attrs, status, created_by) VALUES ('core.has_context', 'note', '1', '見る', 'note', '2', "
            "'見る_context', NULL, 'active', 'jp_verb_pair')"
        ))
        await session.commit()
        await repo.create_or_restore_record(session, _record(2, 3), project=VP)
        rows = (await session.execute(text(
            "SELECT attrs FROM rel_links WHERE relation_type = 'core.has_context'"
        ))).all()
        assert len(rows) == 1 and "jp_verb_pair" in str(rows[0][0])

    _run(body)


def _scratch_rel_links_ddl(table: str) -> str:
    """schema.sql 的 ``rel_links`` 建表 SQL 改成名稱不重複的一般表、拿掉外鍵。

    The ``rel_links`` DDL as a uniquely named regular table without foreign keys.
    """
    import re

    from scripts.common.database.init_db import SCHEMA_PATH, table_ddl

    ddl = table_ddl(SCHEMA_PATH.read_text(encoding="utf-8"), "rel_links")
    ddl = ddl.replace("CREATE TABLE IF NOT EXISTS rel_links ", f"CREATE TABLE {table} ", 1)
    lines = [line for line in ddl.splitlines() if not line.strip().startswith("CONSTRAINT ")]
    ddl = re.sub(r",\s*\n\)", "\n)", "\n".join(lines))
    assert ddl.startswith(f"CREATE TABLE {table} ") and "FOREIGN KEY" not in ddl
    return ddl


def test_handler_link_committed_after_the_snapshot_is_reused(monkeypatch):
    """handler 在另一條連線 commit 的關係，即使本交易的快照比它早，也要沿用、不重複新增。

    重現建卡管線的情況：腳本的交易在呼叫建卡 API 之前就讀過資料庫，REPEATABLE READ 讓之後的一般
    SELECT 都讀那份快照。暫存表只有建立它的連線看得到，所以這裡用一張名稱不重複的一般表，測完就刪
    （真的 ``rel_links`` 不會被碰到）。docs/wip/rel_links_dup_FIX_2026-11-24.md。
    A relation the handler committed on another connection after this
    transaction's snapshot must still be found, not inserted again.
    """
    import uuid

    import scripts.common.env  # noqa: F401  # 載入 .env

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import settings
    from scripts.common.database import log_repository as lr

    table = f"zz_test_rel_links_{uuid.uuid4().hex[:12]}"
    monkeypatch.setattr(lr, "REL_LINKS", table)
    link = lr._Link("core.has_context", "note", "1", "見る", "note", "2", "見る_context")

    async def main():
        engine = create_async_engine(settings.mysql_async_url, poolclass=NullPool)
        try:
            async with engine.connect() as setup:
                await setup.execute(text(_scratch_rel_links_ddl(table)))
                await setup.commit()
            try:
                async with engine.connect() as pipeline, engine.connect() as handler:
                    session = AsyncSession(bind=pipeline, expire_on_commit=False)
                    try:
                        # 腳本的交易先讀過資料庫，快照定在這裡 / the pipeline's snapshot starts here
                        assert (await session.execute(text(f"SELECT COUNT(*) FROM {table}"))).scalar() == 0
                        await handler.execute(text(
                            f"INSERT INTO {table} (relation_type, src_type, src_id, src_label, dst_type, dst_id, "
                            "dst_label, attrs, status, created_by) VALUES ('core.has_context', 'note', '1', '見る', "
                            "'note', '2', '見る_context', NULL, 'active', 'jp_verb_pair')"
                        ))
                        await handler.commit()
                        await lr._ensure_link(session, link, VP)
                        await session.commit()
                    finally:
                        await session.close()
                    rows = (await handler.execute(text(f"SELECT attrs FROM {table}"))).all()
                    await handler.commit()
                    return rows
            finally:
                async with engine.connect() as cleanup:
                    await cleanup.execute(text(f"DROP TABLE IF EXISTS {table}"))
                    await cleanup.commit()
        finally:
            await engine.dispose()

    rows = asyncio.run(main())
    assert len(rows) == 1 and "jp_verb_pair" in str(rows[0][0])
