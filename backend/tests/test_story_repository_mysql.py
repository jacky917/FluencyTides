"""劇情流程圖 SQL 的整合測試：對真的語料庫執行（預設跳過）。

Integration tests for the story SQL against a real corpus database with an
active build. Opt in with ``STORY_DB_TESTS=1``; skipped otherwise so CI
never needs MySQL.

資料庫和後端相同，是 ``MYSQL_DATABASE``；本機的 ``MYSQL_DATABASE`` 還沒切到新語料庫時，設定
``STORY_CORPUS_DATABASE=fluencytides_corpus``（環境變數或 ``backend/.env``）明確覆寫。
The database is ``MYSQL_DATABASE``; set ``STORY_CORPUS_DATABASE`` only as an
explicit local override before cutover.
"""

import asyncio
import os

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("STORY_DB_TESTS") != "1", reason="設定 STORY_DB_TESTS=1 才跑")

GAME = "SabbatOfTheWitch"


def _run(coro_fn):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.infrastructure.database.story_corpus_database import story_database_url
    from app.services.story.repository import MySQLStoryRepository

    async def main():
        engine = create_async_engine(story_database_url(), poolclass=NullPool)
        try:
            repo = MySQLStoryRepository(async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False))
            return await coro_fn(repo)
        finally:
            await engine.dispose()

    return asyncio.run(main())


def test_active_build_lines_annotations_locate():
    async def body(repo):
        build = await repo.active_build(GAME)
        assert build is not None
        graph = await repo.graph(build.build_id)
        start = graph["tabs"][0]["start"]
        total, lines = await repo.node_lines(build.build_id, start, 0, 50)
        assert total > 0 and [x.seq for x in lines] == list(range(len(lines)))
        counts = await repo.annotation_counts(build.build_id)
        assert counts and all(n > 0 for _, _, n in counts)
        verbs = await repo.verb_index(build.build_id)
        assert verbs
        node_key, card_type, _ = counts[0]
        _, node_lines = await repo.node_lines(build.build_id, node_key, 0, 2000)
        links = await repo.card_links([x.line_id for x in node_lines])
        assert links
        located = await repo.lines_of_note(links[0].note_id)
        assert links[0].line_id in located
        found = await repo.line_in_build(build.build_id, links[0].line_id)
        assert found and found[0] == node_key
        assert await repo.next_annotated_seq(build.build_id, node_key, -1) is not None

    _run(body)
