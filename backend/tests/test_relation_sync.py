"""同步清理孤兒關聯的測試（設計書 relation_sync_and_search_escape_FIX_2026-10-19.md §3）。

Tests for ``RelationService.sync_with_anki``: the AnkiWeb sync runs first
and its failure is tolerated and reported, an empty Anki answer is refused
(even with ``force``), deleting more than half needs ``force``, relations
created after the scan started are kept, deletions are batched and atomic,
an unreported row count falls back to the estimate, tens of thousands of note
IDs do not hit SQLite's parameter limit, and the API maps refusals to 409.

關聯改存語料庫的 ``rel_links``（docs/wip/corpus_db_rebuild_FEAT_2026-10-31.md D9 的 C）：測試用 SQLite 建
``corpus_metadata`` 的表；「刪除」是把 ``status`` 標成 ``deleted``，所以計數只算 active 的列。
檔案後段另外測對外名稱與 ``core.*`` / ``user.*`` 的對照、圖譜回應格式不變。
Relations now live in ``rel_links``; "deleting" marks rows ``deleted``.
"""

import asyncio
from datetime import datetime
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, insert, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.exceptions import (
    FluencyTidesError,
    RelationSyncEmptyAnkiError,
    RelationSyncNeedsForceError,
)
from app.infrastructure.anki.client import AnkiConnectError
from app.infrastructure.database.corpus_tables import corpus_metadata, rel_links_table, rel_types_table
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
            await conn.run_sync(corpus_metadata.create_all)

    _run(setup())
    yield async_sessionmaker(engine, expire_on_commit=False)
    _run(engine.dispose())


def _link(source, target, *, relation_type="user.parent", created_at=None, dst_type="note", **extra):
    """一列 rel_links 的值（筆記 ID 存字串）。Values of one rel_links row."""
    values = {
        "relation_type": relation_type, "src_type": "note", "src_id": None if source is None else str(source),
        "src_label": "s", "dst_type": dst_type, "dst_id": None if target is None else str(target),
        "dst_label": "t", "created_by": "user", **extra,
    }
    if created_at is not None:
        values["created_at"] = created_at
    return values


async def _insert_link(session_factory, source, target, **kwargs):
    async with session_factory() as session:
        await session.execute(insert(rel_links_table).values(**_link(source, target, **kwargs)))
        await session.commit()


def _seed(session_factory, pairs, created_at=OLD, **kwargs):
    async def seed():
        async with session_factory() as session:
            for source, target in pairs:
                await session.execute(
                    insert(rel_links_table).values(**_link(source, target, created_at=created_at, **kwargs))
                )
            await session.commit()

    _run(seed())


def _count(session_factory) -> int:
    """有效（active）的關聯數；刪除只會把 status 標成 deleted。Active relations."""
    async def count():
        async with session_factory() as session:
            return (await session.execute(
                select(func.count()).select_from(rel_links_table).where(rel_links_table.c.status == "active")
            )).scalar_one()

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
        await _insert_link(session_factory, 1, 99, relation_type="core.has_cloze")
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
        await _insert_link(session_factory, 1, 99, relation_type="core.has_cloze")
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
            if getattr(statement, "is_update", False):
                deletes += 1
                if deletes == 2:
                    raise OperationalError("UPDATE", {}, Exception("disk I/O error"))
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
        await _insert_link(session_factory, 7, 6)
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


def test_unreported_rowcount_falls_back_to_the_estimate(session_factory, caplog):
    """資料庫沒回報刪除筆數（rowcount 為 None）：資料照樣刪除，回報預估的筆數而不是 0，並記 WARNING。"""
    _seed(session_factory, [(1, 2), (3, 4), (5, 6)])

    def hide_rowcount(session):
        original = session.execute

        async def execute(statement, *args, **kwargs):
            outcome = await original(statement, *args, **kwargs)
            if getattr(statement, "is_update", False):
                return mock.MagicMock(rowcount=None)
            return outcome

        session.execute = execute

    with caplog.at_level("WARNING", logger=relation_service.__name__):
        result = _sync(session_factory, [1, 2], wrap_session=hide_rowcount)
    assert result.deleted == 2 and _count(session_factory) == 1
    assert any("未回報刪除筆數" in record.getMessage() for record in caplog.records)


def test_empty_anki_with_failed_ankiweb_sync_points_to_the_sync(session_factory):
    """Anki 回 0 張且 AnkiWeb 同步也失敗：拒絕訊息指出先處理同步（很可能就是原因）。"""
    _seed(session_factory, [(1, 2)])
    anki = _anki([], sync=mock.AsyncMock(side_effect=AnkiConnectError("需要完整同步")))
    with pytest.raises(RelationSyncEmptyAnkiError, match="AnkiWeb 同步也失敗"):
        _sync(session_factory, anki=anki)
    assert _count(session_factory) == 1


# =============================================================================
# 語料庫 rel_links 的對照（corpus_db_rebuild D9 的 C）
# =============================================================================


def test_sync_also_retires_pipeline_links_of_orphan_notes(session_factory):
    """建卡管線寫的「筆記 → 台詞／詞」：筆記已不在 Anki 時一樣標成 deleted；另一端不是筆記，不算孤兒筆記。"""
    _seed(session_factory, [(1, 53000)], relation_type="core.example_from", dst_type="script_line")
    _seed(session_factory, [(2, "ja-JP:見る")], relation_type="core.teaches", dst_type="term")
    result = _sync(session_factory, [2])
    assert (result.deleted, result.orphan_notes, result.considered) == (1, 1, 2)
    assert _count(session_factory) == 1


def test_already_deleted_links_are_ignored(session_factory):
    """已標成 deleted 的列不在考慮範圍，也不會被重算。"""
    _seed(session_factory, [(1, 2)], status="deleted")
    _seed(session_factory, [(3, 4)])
    result = _sync(session_factory, [3, 4])
    assert (result.deleted, result.considered) == (0, 1)


def _service_call(session_factory, method, *args, **kwargs):
    async def call():
        async with session_factory() as session:
            return await getattr(RelationService(session), method)(*args, **kwargs)

    return _run(call())


def _rows(session_factory, table):
    async def read():
        async with session_factory() as session:
            return [dict(row._mapping) for row in (await session.execute(select(table))).all()]

    return _run(read())


def _seed_type(session_factory, relation_type, *, owner="system", dst="note"):
    async def seed():
        async with session_factory() as session:
            await session.execute(insert(rel_types_table).values(
                relation_type=relation_type, owner=owner, name_ja_jp="x", name_zh_hant="x",
                src_types=["note"], dst_types=[dst],
            ))
            await session.commit()

    _run(seed())


@pytest.mark.parametrize(
    ("public", "stored", "owner"),
    [("has_context", "core.has_context", None), ("has_cloze", "core.has_cloze", None),
     ("Synonym", "user.synonym", "user"), ("conversation", "user.conversation", "user"),
     ("travel", "user.travel", "user")],
)
def test_public_names_map_to_namespaced_types(session_factory, public, stored, owner):
    """對外的名稱存成 core.* / user.*；回應照舊用對外名稱；使用者的類型自動註冊成 owner=user。"""
    from app.schemas.relation import CardRelationCreate

    if owner is None:  # 系統類型由遷移登記
        _seed_type(session_factory, stored)
    created = _service_call(session_factory, "create_relation", CardRelationCreate(
        source_note_id=1, target_note_id=None, relation_type=public, source_label="a", target_label="b",
    ))
    assert created.relation_type == public.lower()
    assert (created.source_note_id, created.target_note_id) == (1, None)
    (link,) = _rows(session_factory, rel_links_table)
    assert (link["relation_type"], link["src_type"], link["src_id"], link["dst_id"]) == (stored, "note", "1", None)
    assert link["created_by"] == "user" and link["status"] == "active"
    types = {row["relation_type"]: row["owner"] for row in _rows(session_factory, rel_types_table)}
    assert types[stored] == (owner or "system")


def test_handler_relations_carry_card_type(session_factory):
    """卡片 handler 寫的關係帶 created_by 與 attrs.card_type（與建卡管線同一份資料）。"""
    from app.schemas.relation import CardRelationCreate

    _seed_type(session_factory, "core.has_context")
    _service_call(session_factory, "create_relation", CardRelationCreate(
        source_note_id=10, target_note_id=11, relation_type="has_context", source_label="見る",
        target_label="見る_context",
    ), created_by="jp_core_verb", attrs={"card_type": "jp_core_verb"})
    (link,) = _rows(session_factory, rel_links_table)
    assert link["created_by"] == "jp_core_verb" and link["attrs"] == {"card_type": "jp_core_verb"}


def test_batch_create_registers_types_once_and_returns_public_names(session_factory):
    """批次建立（JSON 匯入用）：同一類型只註冊一次，回應用對外名稱。"""
    from app.schemas.relation import CardRelationCreate

    created = _service_call(session_factory, "batch_create_relations", [
        CardRelationCreate(source_note_id=1, relation_type="synonym", source_label="a", target_label="b"),
        CardRelationCreate(target_note_id=1, relation_type="synonym", source_label="b", target_label="a"),
    ])
    assert [(r.relation_type, r.source_note_id, r.target_note_id) for r in created] == [
        ("synonym", 1, None), ("synonym", None, 1),
    ]
    assert [row["relation_type"] for row in _rows(session_factory, rel_types_table)] == ["user.synonym"]


def test_graph_and_type_list_keep_the_old_shape(session_factory):
    """圖譜只看筆記 → 筆記的有效關係，連線標籤照舊（Has_context）；類型清單只列筆記 → 筆記的類型。"""
    _seed_type(session_factory, "core.has_context")
    _seed_type(session_factory, "core.example_from", dst="script_line")
    _seed_type(session_factory, "user.synonym", owner="user")
    _seed(session_factory, [(1, 2)], relation_type="core.has_context")
    _seed(session_factory, [(1, 53000)], relation_type="core.example_from", dst_type="script_line")
    _seed(session_factory, [(1, 3)], relation_type="user.synonym", status="deleted")

    graph = _service_call(session_factory, "get_graph_data", None, None)
    assert [(link["source"], link["target"], link["label"]) for link in graph["links"]] == [("s", "t", "Has_context")]
    assert {node["id"]: node["note_id"] for node in graph["nodes"]} == {"s": 1, "t": 2}
    assert _service_call(session_factory, "get_all_relation_types") == ["has_context", "synonym"]
    (relation,) = _service_call(session_factory, "get_relations_by_note_ids", {2})
    assert (relation.relation_type, relation.source_note_id, relation.target_note_id) == ("has_context", 1, 2)


def test_note_deletion_and_label_updates_touch_every_relation_of_the_note(session_factory):
    """刪卡把這張筆記當任一端的關係都標成 deleted；改名稱只動這張筆記那一端的標籤。"""
    _seed(session_factory, [(1, 2), (3, 1)])
    _seed(session_factory, [(1, 53000)], relation_type="core.example_from", dst_type="script_line")
    _seed(session_factory, [(4, 5)])
    assert _service_call(session_factory, "update_labels_for_note", 1, "new") == 3
    labels = sorted((row["src_label"], row["dst_label"]) for row in _rows(session_factory, rel_links_table))
    assert labels == [("new", "t"), ("new", "t"), ("s", "new"), ("s", "t")]
    assert _service_call(session_factory, "delete_relations_for_note", 1) == 3
    assert _count(session_factory) == 1


def test_delete_by_nodes_matches_both_directions(session_factory):
    """依標籤刪除：兩個方向都標成 deleted，只限指定的關係類型。"""
    from app.schemas.relation import CardRelationDelete

    async def seed():
        async with session_factory() as session:
            for src_label, dst_label, relation_type in (("a", "b", "user.synonym"), ("b", "a", "user.synonym"),
                                                        ("a", "b", "user.parent")):
                await session.execute(insert(rel_links_table).values(
                    **_link(1, 2, relation_type=relation_type, src_label=src_label, dst_label=dst_label)
                ))
            await session.commit()

    _run(seed())
    result = _service_call(session_factory, "delete_relation_by_nodes", CardRelationDelete(
        source_label="a", target_label="b", relation_type="synonym",
    ))
    assert result == {"deleted_count": 2}
    assert _count(session_factory) == 1


@pytest.mark.parametrize("name", ["", "   ", "core.example_from", "CORE.teaches", "user.synonym", "a b", "x;drop", "a" * 60])
def test_invalid_user_relation_names_are_rejected(session_factory, name):
    """使用者不能用系統命名空間、空白或怪字元；不會自動註冊任何類型。Invalid names → 400, nothing registered."""
    from app.core.exceptions import InvalidRelationTypeError
    from app.schemas.relation import CardRelationCreate

    with pytest.raises(InvalidRelationTypeError) as caught:
        _service_call(session_factory, "create_relation", CardRelationCreate.model_construct(
            source_note_id=1, target_note_id=2, relation_type=name, source_label="a", target_label="b",
        ))
    assert caught.value.status_code == 400
    assert _rows(session_factory, rel_types_table) == []
    assert _rows(session_factory, rel_links_table) == []


def test_unicode_user_relation_name_is_allowed():
    assert relation_service.storage_relation_type(" 同義-語_2 ") == "user.同義-語_2"


def test_missing_system_type_is_not_auto_registered(session_factory):
    """``core.*`` 不存在時報錯，不會被當成使用者類型自動建立。A missing core type raises."""
    from app.schemas.relation import CardRelationCreate

    with pytest.raises(FluencyTidesError):
        _service_call(session_factory, "create_relation", CardRelationCreate(
            source_note_id=1, target_note_id=2, relation_type="has_context", source_label="a", target_label="b",
        ))
    assert _rows(session_factory, rel_types_table) == []


def test_create_schema_rejects_an_empty_relation_type():
    from pydantic import ValidationError

    from app.schemas.relation import CardRelationCreate

    with pytest.raises(ValidationError):
        CardRelationCreate(source_note_id=1, relation_type="")


def test_batch_create_is_all_or_nothing(session_factory):
    """一筆失敗就全部回滾，不會只留下一半。A failing batch leaves nothing behind."""
    from app.core.exceptions import InvalidRelationTypeError
    from app.schemas.relation import CardRelationCreate

    _seed_type(session_factory, "core.has_context")
    with pytest.raises(InvalidRelationTypeError):
        _service_call(session_factory, "batch_create_relations", [
            CardRelationCreate(source_note_id=1, target_note_id=2, relation_type="has_context"),
            CardRelationCreate.model_construct(source_note_id=1, target_note_id=3, relation_type="core.teaches",
                                               source_label="", target_label=""),
        ])
    assert _rows(session_factory, rel_links_table) == []


def test_note_deletion_unreported_rowcount_falls_back_to_the_estimate(session_factory):
    """驅動沒回報 rowcount 時，刪除數是預估值而不是 0。Unknown rowcount is not reported as 0."""
    _seed(session_factory, [(1, 2), (3, 1)])

    async def call():
        async with session_factory() as session:
            original = session.execute

            async def execute(statement, *args, **kwargs):
                result = await original(statement, *args, **kwargs)
                if str(statement).lstrip().upper().startswith("UPDATE"):
                    return mock.MagicMock(rowcount=-1)
                return result

            session.execute = execute
            return await RelationService(session).delete_relations_for_note(1)

    assert _run(call()) == 2
    assert _count(session_factory) == 0
