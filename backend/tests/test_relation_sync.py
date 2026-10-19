"""同步清理孤兒關聯的測試（docs/wip/relation_sync_and_search_escape_FIX_2026-10-19.md §3）。

Tests for ``RelationService.sync_with_anki``: an empty Anki answer is
refused (even with ``force``), deleting more than half needs ``force``,
relations created after the scan started are kept, deletions are batched and
atomic, and tens of thousands of note IDs do not hit SQLite's parameter limit.
"""

import asyncio
from datetime import datetime
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from app.core.exceptions import (
    FluencyTidesError,
    RelationSyncEmptyAnkiError,
    RelationSyncNeedsForceError,
)
from app.infrastructure.anki.client import AnkiConnectError
from app.infrastructure.database.models import CardRelation
from app.services import relation_service
from app.services.relation_service import (
    SYNC_DELETE_BATCH,
    SYNC_GUARD_MIN_ROWS,
    RelationService,
    RelationSyncResult,
)

# 早於任何掃描的建立時間（考慮範圍內的關聯）
OLD = datetime(2020, 1, 1)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def session_factory():
    engine = create_async_engine("sqlite+aiosqlite://")

    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

    _run(setup())
    yield async_sessionmaker(engine, expire_on_commit=False)
    _run(engine.dispose())


def _seed(session_factory, pairs, created_at=OLD):
    async def seed():
        async with session_factory() as session:
            for source, target in pairs:
                session.add(CardRelation(
                    source_note_id=source, target_note_id=target, relation_type="parent",
                    source_label="s", target_label="t", created_at=created_at,
                ))
            await session.commit()

    _run(seed())


def _count(session_factory) -> int:
    async def count():
        async with session_factory() as session:
            return (await session.execute(select(func.count()).select_from(CardRelation))).scalar_one()

    return _run(count())


def _anki(note_ids=None, *, find_notes=None, sync=None):
    """只有 sync 與 find_notes 的假 AnkiClient。A fake client with sync and find_notes."""
    return mock.MagicMock(
        sync=sync or mock.AsyncMock(return_value=None),
        find_notes=find_notes or mock.AsyncMock(return_value=note_ids),
    )


def _sync(session_factory, note_ids=None, *, force=False, anki=None, wrap_session=None):
    anki = anki or _anki(note_ids)

    async def sync():
        async with session_factory() as session:
            if wrap_session:
                wrap_session(session)
            return await RelationService(session).sync_with_anki(anki, force=force)

    return _run(sync())


@pytest.mark.parametrize("force", [False, True])
def test_empty_anki_answer_is_refused_even_with_force(session_factory, force):
    """Anki 回傳 0 張筆記：拒絕，一筆都不刪（原本會刪光整張表）；force 也無效。"""
    _seed(session_factory, [(1, 2), (3, 4)])
    with pytest.raises(RelationSyncEmptyAnkiError, match="0 張筆記"):
        _sync(session_factory, [], force=force)
    assert _count(session_factory) == 2


def test_orphans_are_deleted(session_factory):
    """起點或終點已不在 Anki 的關聯被刪除；終點為 NULL 的不算孤兒；先與 AnkiWeb 同步。"""
    _seed(session_factory, [(1, 2), (1, 3), (4, None), (5, 6)])
    anki = _anki([1, 2, 4, 5])
    result = _sync(session_factory, anki=anki)
    assert result == RelationSyncResult(
        scanned_notes=4, orphan_notes=2, deleted=2, considered=4, ankiweb_synced=True
    )
    assert _count(session_factory) == 2
    anki.sync.assert_awaited_once_with(raise_errors=True)


def test_ankiweb_sync_failure_is_tolerated_and_reported(session_factory):
    """與 AnkiWeb 同步失敗不中止清理，以本機收藏為準，結果標出 ankiweb_synced=False。"""
    _seed(session_factory, [(1, 2), (3, 4)])
    anki = _anki([1, 2], sync=mock.AsyncMock(side_effect=AnkiConnectError("AnkiWeb 登入失效")))
    result = _sync(session_factory, anki=anki)
    assert (result.deleted, result.ankiweb_synced) == (1, False)


def test_anki_unreachable_deletes_nothing(session_factory):
    """取筆記時連不上 Anki：錯誤往上拋，一筆都不刪。"""
    _seed(session_factory, [(1, 2), (3, 4)])
    anki = _anki(find_notes=mock.AsyncMock(side_effect=AnkiConnectError("連線被拒")))
    with pytest.raises(AnkiConnectError):
        _sync(session_factory, anki=anki)
    assert _count(session_factory) == 2


def test_deleting_more_than_half_needs_force(session_factory):
    """要刪的超過一半且超過門檻：拒絕並帶出數量；force 才刪。"""
    rows = SYNC_GUARD_MIN_ROWS + 5
    _seed(session_factory, [(1000 + i, 1) for i in range(rows)])
    with pytest.raises(RelationSyncNeedsForceError, match="超過一半") as refused:
        _sync(session_factory, [1])
    assert (refused.value.to_delete, refused.value.considered) == (rows, rows)
    assert _count(session_factory) == rows

    result = _sync(session_factory, [1], force=True)
    assert result.deleted == rows and _count(session_factory) == 0


@pytest.mark.parametrize(
    ("orphans", "healthy"),
    [
        (3, 0),  # 不到門檻：即使全刪也照常刪，避免小資料量時清不掉
        (SYNC_GUARD_MIN_ROWS, 0),  # 剛好等於門檻：不擋
        (30, 30),  # 剛好一半：不擋
        (100, 150),  # 大量但不到一半：不擋
    ],
)
def test_deletions_within_the_guard_are_not_refused(session_factory, orphans, healthy):
    """門檻（> SYNC_GUARD_MIN_ROWS 且 > 一半）以外的刪除照常執行，包含剛好落在邊界上的。"""
    _seed(session_factory, [(1000 + i, 1) for i in range(orphans)] + [(1, 1)] * healthy)
    assert _sync(session_factory, [1]).deleted == orphans
    assert _count(session_factory) == healthy


def test_relations_created_after_the_scan_started_are_kept(session_factory):
    """掃描期間剛生成的子卡：筆記不在掃描結果裡，它的新關聯不能被當成孤兒刪掉。"""
    _seed(session_factory, [(1, 2)])

    async def find_notes_while_generating(_query):
        # 模擬取 Anki 筆記時，另一個流程剛建立了新筆記 99 與它的關聯（created_at 由資料庫填入＝現在）
        async with session_factory() as session:
            session.add(CardRelation(source_note_id=1, target_note_id=99, relation_type="has_cloze"))
            await session.commit()
        return [1, 2]

    anki = _anki(find_notes=mock.AsyncMock(side_effect=find_notes_while_generating))
    result = _sync(session_factory, anki=anki)
    assert result.deleted == 0
    assert _count(session_factory) == 2


def test_scan_time_is_taken_before_reading_anki(session_factory, monkeypatch):
    """掃描時間必須在取 Anki 筆記「之前」取：邊際縮到 1 秒，取筆記期間新增關聯並等超過一秒。

    順序正確時截止點早於新關聯一秒以上，不在考慮範圍；若掃描時間在取筆記之後才取，截止點會
    不早於新關聯的 created_at（秒精度）。SQLite 以字串比較時間，``'…:00' < '…:00.000000'``
    成立，同一秒也算「早於」，所以邊際不能設 0（那樣兩種順序都會刪）。
    """
    monkeypatch.setattr(relation_service, "SYNC_CLOCK_MARGIN_SECONDS", 1)
    _seed(session_factory, [(1, 2)])

    async def slow_find_notes(_query):
        async with session_factory() as session:
            session.add(CardRelation(source_note_id=1, target_note_id=99, relation_type="has_cloze"))
            await session.commit()
        await asyncio.sleep(1.1)
        return [1, 2]

    result = _sync(session_factory, anki=_anki(find_notes=mock.AsyncMock(side_effect=slow_find_notes)))
    assert (result.deleted, result.considered) == (0, 1)
    assert _count(session_factory) == 2


def test_many_orphans_are_deleted_in_batches(session_factory):
    """孤兒筆記超過一批（SYNC_DELETE_BATCH）時分批刪除，全部刪掉。"""
    orphans = SYNC_DELETE_BATCH * 2 + 50
    _seed(session_factory, [(10_000 + i, 1) for i in range(orphans)])
    result = _sync(session_factory, [1], force=True)
    assert result.deleted == orphans and _count(session_factory) == 0


def test_failure_mid_batches_deletes_nothing(session_factory, monkeypatch):
    """第二批刪除失敗：已執行的第一批不會留下（同一個 transaction，未提交）。"""
    monkeypatch.setattr(relation_service, "SYNC_DELETE_BATCH", 2)
    _seed(session_factory, [(10 + i, 1) for i in range(6)])

    def fail_second_delete(session):
        original = session.execute
        deletes = 0

        async def execute(statement, *args, **kwargs):
            nonlocal deletes
            if getattr(statement, "is_delete", False):
                deletes += 1
                if deletes == 2:
                    raise OperationalError("DELETE", {}, Exception("disk I/O error"))
            return await original(statement, *args, **kwargs)

        session.execute = execute

    with pytest.raises(OperationalError):
        _sync(session_factory, [1], wrap_session=fail_second_delete)
    assert _count(session_factory) == 6


def test_tens_of_thousands_of_notes(session_factory):
    """筆記數超過 SQLite 的參數上限（32,766）照常運作：只以孤兒 ID 當參數、分批刪除。"""
    _seed(session_factory, [(1, 2), (3, 70_000)])
    result = _sync(session_factory, list(range(1, 40_001)))
    assert result.deleted == 1 and _count(session_factory) == 1


def _api(service):
    from app.api.relations import router
    from app.core.dependencies import get_anki_client, get_relation_service
    from app.main import fluencytides_error_handler

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.add_exception_handler(FluencyTidesError, fluencytides_error_handler)
    app.dependency_overrides[get_relation_service] = lambda: service
    app.dependency_overrides[get_anki_client] = lambda: mock.MagicMock()
    return TestClient(app)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (RelationSyncEmptyAnkiError("Anki 回傳 0 張筆記"), "RELATION_SYNC_EMPTY_ANKI"),
        (RelationSyncNeedsForceError("將刪除 30 / 40 筆", to_delete=30, considered=40), "RELATION_SYNC_NEEDS_FORCE"),
    ],
)
def test_api_returns_409_with_the_refusal_code(error, code):
    """API：拒絕時回 409 與各自的 error_code（網頁靠它決定能否強制）；force 參數傳到服務層。"""
    service = mock.MagicMock(sync_with_anki=mock.AsyncMock(side_effect=error))
    response = _api(service).post("/api/v1/relations/sync?force=true")
    assert response.status_code == 409
    assert response.json()["error_code"] == code
    assert service.sync_with_anki.await_args.kwargs == {"force": True}


def test_api_success_response_and_default_force():
    """API：預設 force=False；回應帶掃描張數、孤兒筆記數與 AnkiWeb 同步結果。"""
    result = RelationSyncResult(scanned_notes=17_394, orphan_notes=1, deleted=2, considered=10, ankiweb_synced=False)
    service = mock.MagicMock(sync_with_anki=mock.AsyncMock(return_value=result))
    response = _api(service).post("/api/v1/relations/sync")
    assert response.status_code == 200
    assert response.json() == {
        "deleted_count": 2, "scanned_notes": 17_394, "orphan_notes": 1, "ankiweb_synced": False,
    }
    assert service.sync_with_anki.await_args.kwargs == {"force": False}


def test_api_returns_the_real_empty_anki_refusal_end_to_end(session_factory):
    """API 接真的服務層：Anki 回 0 張時即使 force=true 也回 409，資料不變。"""
    from app.api.relations import router
    from app.core.dependencies import get_anki_client, get_relation_service
    from app.main import fluencytides_error_handler

    _seed(session_factory, [(1, 2)])
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.add_exception_handler(FluencyTidesError, fluencytides_error_handler)

    async def service():
        async with session_factory() as session:
            yield RelationService(session)

    app.dependency_overrides[get_relation_service] = service
    app.dependency_overrides[get_anki_client] = lambda: _anki([])
    response = TestClient(app).post("/api/v1/relations/sync?force=true")
    assert response.status_code == 409
    assert response.json()["error_code"] == "RELATION_SYNC_EMPTY_ANKI"
    assert _count(session_factory) == 1


def test_new_relations_to_an_orphan_note_are_kept(session_factory):
    """刪除條件同樣限定掃描開始前建立：掃描期間新建、指向孤兒筆記的關聯不在這次清理範圍。"""
    _seed(session_factory, [(5, 6)])

    async def find_notes_while_linking(_query):
        async with session_factory() as session:
            session.add(CardRelation(source_note_id=7, target_note_id=6, relation_type="parent"))
            await session.commit()
        return [5, 7]

    result = _sync(session_factory, anki=_anki(find_notes=mock.AsyncMock(side_effect=find_notes_while_linking)))
    assert result.deleted == 1
    assert _count(session_factory) == 1


def test_failed_ankiweb_sync_is_reported_without_orphans(session_factory):
    """沒有孤兒時提早回傳，也要帶出 AnkiWeb 同步失敗。"""
    _seed(session_factory, [(1, 2)])
    anki = _anki([1, 2], sync=mock.AsyncMock(side_effect=AnkiConnectError("AnkiWeb 逾時")))
    result = _sync(session_factory, anki=anki)
    assert (result.deleted, result.ankiweb_synced) == (0, False)


def test_ankiweb_sync_runs_before_reading_notes(session_factory):
    """先與 AnkiWeb 同步、再取筆記：否則清理依據的是同步前的舊收藏。"""
    order: list[str] = []
    anki = _anki(
        sync=mock.AsyncMock(side_effect=lambda **_: order.append("sync")),
        find_notes=mock.AsyncMock(side_effect=lambda _q: order.append("find") or [1]),
    )
    _sync(session_factory, anki=anki)
    assert order == ["sync", "find"]
