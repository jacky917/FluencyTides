"""網頁建卡草稿：全站一個槽位與一把鎖（docs/wip/server_drafts_FEAT_2026-11-04.md）。

Web create drafts: one site-wide slot and one lock.
"""

import asyncio
from unittest import mock

import pytest

from app.core.exceptions import (
    CreateSessionBusyError,
    DraftBusyError,
    DraftNotFailedError,
    DraftNotFoundError,
    DraftStateTooLargeError,
    DraftVersionConflictError,
    LLMGenerationError,
)
from app.core.config import settings
from app.core.dependencies import get_relation_service
from app.services import create_session as create_session_module
from app.services import speaking_trilingual_generate as generate_service
from app.services.create_session import MAX_STATE_BYTES, CreateSession, create_session
from tests.test_handler_base import V2
from tests.test_handlers_api import _error, anki, client, relations  # noqa: F401 （pytest fixtures）
from tests.test_note_service import _service
from tests.test_trilingual_create import TRILINGUAL, _draft

RESULT = {"params": {"prompt": "p"}, "suggested_tags": []}


def run_pending(slot: list) -> None:
    """執行收集到的背景生成。Run the collected background generations."""
    while slot:
        asyncio.run(slot.pop(0))


def start_draft(client, slot: list, params: dict | None = None, *, handler: str = "speaking_trilingual") -> dict:
    """開始生成並跑完背景生成，回傳目前的草稿。Start a draft, run the generation, return the draft."""
    response = client.post(f"/api/v1/handlers/{handler}/drafts", json={"params": params or {"background": "剪髮"}})
    assert response.status_code == 202, response.json()
    assert response.json()["status"] == "generating"
    run_pending(slot)
    return client.get("/api/v1/drafts/current").json()


def _session() -> tuple[CreateSession, list]:
    session = CreateSession()
    pending: list = []
    session._spawn = pending.append
    return session, pending


async def _ok(parameters):
    return RESULT


async def _fail(parameters):
    raise LLMGenerationError("模型 m 回傳的格式不對，請再試一次。")


# =============================================================================
# CreateSession：狀態轉換
# =============================================================================


class TestTransitions:
    def test_generate_then_create(self):
        session, pending = _session()

        async def flow():
            draft = await session.start("h", {"a": 1}, _ok)
            assert draft["status"] == "generating"
            assert draft["input"] == {"a": 1}
            await pending.pop()
            ready = session.snapshot()
            assert ready["status"] == "ready"
            assert ready["result"] == RESULT
            claimed = await session.begin_create("h", ready["id"], ready["version"])
            assert session.snapshot()["status"] == "creating"
            await session.finish_create(claimed, created=True)
            assert session.current is None

        asyncio.run(flow())

    def test_llm_failure_keeps_input_and_can_retry(self):
        session, pending = _session()

        async def flow():
            draft = await session.start("h", {"a": 1}, _fail)
            await pending.pop()
            failed = session.snapshot()
            assert failed["status"] == "failed"
            assert failed["error"] == {"error_code": "LLM_GENERATION_FAILED", "message": "模型 m 回傳的格式不對，請再試一次。"}
            retried = await session.retry(draft["id"], failed["version"], _ok, {"a": 2})
            assert retried["status"] == "generating"
            assert retried["error"] is None
            assert retried["input"] == {"a": 2}
            await pending.pop()
            assert session.snapshot()["status"] == "ready"

        asyncio.run(flow())

    def test_unexpected_error_is_stored(self):
        session, pending = _session()

        async def boom(parameters):
            raise RuntimeError("壞了")

        async def flow():
            await session.start("h", {}, boom)
            await pending.pop()
            assert session.snapshot()["error"]["error_code"] == "DRAFT_GENERATION_FAILED"

        asyncio.run(flow())

    def test_create_failure_returns_to_ready(self):
        session, pending = _session()

        async def flow():
            draft = await session.start("h", {}, _ok)
            await pending.pop()
            claimed = await session.begin_create("h", draft["id"], session.current.version)
            await session.finish_create(claimed, created=False)
            assert session.snapshot()["status"] == "ready"

        asyncio.run(flow())

    def test_form_create_claims_and_releases(self):
        session, _ = _session()

        async def flow():
            claimed = await session.begin_create("h", None, None)
            current = session.snapshot()
            assert current["status"] == "form_creating"
            assert current["input"] is None
            assert current["result"] is None
            with pytest.raises(CreateSessionBusyError):
                await session.start("h", {}, _ok)
            await session.finish_create(claimed, created=False)
            assert session.current is None

        asyncio.run(flow())

    def test_discard_only_when_ready_or_failed(self):
        session, pending = _session()

        async def flow():
            draft = await session.start("h", {}, _ok)
            with pytest.raises(DraftBusyError, match="還在生成"):
                await session.discard(draft["id"])
            await pending.pop()
            await session.discard(draft["id"])
            assert session.current is None

        asyncio.run(flow())

    def test_failed_draft_can_be_discarded(self):
        """草稿沒有自動過期：生成失敗的一定要能廢棄，否則全站建卡會被擋到後端重啟。"""
        session, pending = _session()

        async def flow():
            draft = await session.start("h", {}, _fail)
            await pending.pop()
            await session.discard(draft["id"])
            assert session.current is None
            await session.start("h", {}, _ok)  # 可以再生成
            pending.pop().close()

        asyncio.run(flow())

    def test_cannot_create_from_generating_or_failed(self):
        session, pending = _session()

        async def flow():
            draft = await session.start("h", {}, _fail)
            with pytest.raises(DraftBusyError, match="還在生成"):
                await session.begin_create("h", draft["id"], session.current.version)
            await pending.pop()
            with pytest.raises(DraftBusyError, match="生成失敗"):
                await session.begin_create("h", draft["id"], session.current.version)

        asyncio.run(flow())

    def test_create_requires_version(self):
        session, pending = _session()

        async def flow():
            draft = await session.start("h", {}, _ok)
            await pending.pop()
            with pytest.raises(DraftVersionConflictError):
                await session.begin_create("h", draft["id"], None)

        asyncio.run(flow())

    def test_retry_checks_version(self):
        session, pending = _session()

        async def flow():
            draft = await session.start("h", {"a": 1}, _fail)
            await pending.pop()
            with pytest.raises(DraftVersionConflictError):
                await session.retry(draft["id"], draft["version"], _ok)
            retried = await session.retry(draft["id"], session.current.version, _ok)
            assert retried["input"] == {"a": 1}  # 沒給新輸入就沿用
            pending.pop().close()

        asyncio.run(flow())

    def test_wrong_id_is_not_found(self):
        session, pending = _session()

        async def flow():
            await session.start("h", {}, _ok)
            await pending.pop()
            for call in (
                session.discard("other"),
                session.save_state("other", {}, 1),
                session.begin_create("h", "other", 1),
            ):
                with pytest.raises(DraftNotFoundError):
                    await call

        asyncio.run(flow())

    def test_draft_of_another_handler_cannot_be_created(self):
        session, pending = _session()

        async def flow():
            draft = await session.start("h", {}, _ok)
            await pending.pop()
            with pytest.raises(CreateSessionBusyError, match="另一種卡片"):
                await session.begin_create("other_handler", draft["id"], session.current.version)
            assert session.current.status == "ready"

        asyncio.run(flow())

    def test_retry_requires_failed(self):
        session, pending = _session()

        async def flow():
            draft = await session.start("h", {}, _ok)
            await pending.pop()
            with pytest.raises(DraftNotFailedError):
                await session.retry(draft["id"], session.current.version, _ok)

        asyncio.run(flow())

    def test_save_state_checks_version_and_size(self):
        session, pending = _session()

        async def flow():
            draft = await session.start("h", {}, _ok)
            await pending.pop()
            version = session.current.version
            saved = await session.save_state(draft["id"], {"chosen": [1]}, version)
            assert saved["state"] == {"chosen": [1]}
            assert saved["version"] == version + 1
            with pytest.raises(DraftVersionConflictError) as conflict:
                await session.save_state(draft["id"], {"chosen": [2]}, version)
            assert conflict.value.draft["state"] == {"chosen": [1]}  # 附上最新的草稿
            with pytest.raises(DraftStateTooLargeError):
                await session.save_state(draft["id"], {"x": "a" * MAX_STATE_BYTES}, version + 1)

        asyncio.run(flow())

    def test_result_dropped_when_slot_changed(self):
        """保險：結果回來時槽位裡不是同一份草稿，就丟掉結果。"""
        session, pending = _session()

        async def flow():
            await session.start("h", {}, _ok)
            session.current = None
            await pending.pop()
            assert session.current is None

        asyncio.run(flow())


# =============================================================================
# CreateSession：同時操作（D6）
# =============================================================================


class TestConcurrency:
    def test_two_generations_only_one_wins(self):
        session, pending = _session()

        async def flow():
            results = await asyncio.gather(
                session.start("h", {"n": 1}, _ok), session.start("h", {"n": 2}, _ok), return_exceptions=True
            )
            assert sum(isinstance(r, dict) for r in results) == 1
            busy = [r for r in results if isinstance(r, CreateSessionBusyError)]
            assert len(busy) == 1
            assert busy[0].draft["status"] == "generating"

        asyncio.run(flow())
        assert len(pending) == 1  # 只啟動一次生成
        pending.pop().close()

    def test_two_creates_only_one_writes(self):
        session, pending = _session()

        async def flow():
            draft = await session.start("h", {}, _ok)
            await pending.pop()
            version = session.current.version
            results = await asyncio.gather(
                session.begin_create("h", draft["id"], version),
                session.begin_create("h", draft["id"], version),
                return_exceptions=True,
            )
            assert sum(isinstance(r, str) for r in results) == 1
            # 後到的先被狀態擋下（建立中），前端顯示「建立中」而不是「衝突」
            assert sum(isinstance(r, DraftBusyError) for r in results) == 1

        asyncio.run(flow())

    def test_discard_while_creating_is_refused(self):
        session, pending = _session()

        async def flow():
            draft = await session.start("h", {}, _ok)
            await pending.pop()
            await session.begin_create("h", draft["id"], session.current.version)
            with pytest.raises(DraftBusyError, match="正在建立"):
                await session.discard(draft["id"])

        asyncio.run(flow())


# =============================================================================
# REST API
# =============================================================================


def test_no_draft_is_null(client):
    response = client.get("/api/v1/drafts/current")
    assert response.status_code == 200
    assert response.json() is None


def test_start_and_read_draft(client, anki, draft_slot):
    with mock.patch.object(generate_service, "generate", mock.AsyncMock(return_value=_draft())):
        draft = start_draft(client, draft_slot)
    assert draft["status"] == "ready"
    assert draft["handler"] == "speaking_trilingual"
    assert draft["input"]["background"] == "剪髮"
    assert draft["result"]["params"]["references_zh"][0]["familiarity"] == 8
    assert anki.added == []  # 草稿不寫入


def test_start_requires_valid_input(client):
    response = client.post("/api/v1/handlers/speaking_trilingual/drafts", json={"params": {"background": ""}})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "params", "background"]
    assert client.get("/api/v1/drafts/current").json() is None


@pytest.mark.parametrize("handler", ["vocabulary_mining", "speaking_coach"])
def test_start_refused_without_web_preview(client, handler):
    response = client.post(f"/api/v1/handlers/{handler}/drafts", json={"params": {}})
    assert _error(response) == (400, "HANDLER_OPERATION_UNSUPPORTED")


def test_old_endpoint_removed(client):
    response = client.post("/api/v1/handlers/speaking_trilingual/draft", json={"params": {"background": "x"}})
    assert response.status_code in (404, 405)


def test_llm_failure_is_stored_in_draft(client, draft_slot):
    failing = mock.AsyncMock(side_effect=LLMGenerationError("模型 m 回傳的格式不對，請再試一次。"))
    with mock.patch.object(generate_service, "generate", failing):
        draft = start_draft(client, draft_slot)
    assert draft["status"] == "failed"
    assert "回傳的格式不對" in draft["error"]["message"]


def test_second_generation_is_busy_with_draft(client, draft_slot):
    with mock.patch.object(generate_service, "generate", mock.AsyncMock(return_value=_draft())):
        first = start_draft(client, draft_slot)
    response = client.post("/api/v1/handlers/speaking_trilingual/drafts", json={"params": {"background": "爬山"}})
    assert _error(response) == (409, "CREATE_SESSION_BUSY")
    assert response.json()["draft"]["id"] == first["id"]


def test_form_create_blocked_while_draft_exists(client, anki, draft_slot):
    with mock.patch.object(generate_service, "generate", mock.AsyncMock(return_value=_draft())):
        start_draft(client, draft_slot)
    response = client.post("/api/v1/handlers/vocabulary_mining/notes", json={
        "deck": "Default", "note_type": V2, "params": {"Expression": "anyway"},
    })
    assert _error(response) == (409, "CREATE_SESSION_BUSY")
    assert response.json()["draft"]["handler"] == "speaking_trilingual"
    assert anki.added == []


def test_form_create_releases_slot(client, anki):
    response = client.post("/api/v1/handlers/vocabulary_mining/notes", json={
        "deck": "Default", "note_type": V2, "params": {"Expression": "anyway"},
    })
    assert response.status_code == 201
    assert client.get("/api/v1/drafts/current").json() is None


def test_form_create_failure_releases_slot(client):
    response = client.post("/api/v1/handlers/vocabulary_mining/notes", json={
        "deck": "Default", "note_type": V2, "params": {"Expression": ""},
    })
    assert response.status_code == 422
    assert client.get("/api/v1/drafts/current").json() is None


def _create_body(draft: dict, **extra) -> dict:
    return {
        "deck": "Default",
        "note_type": TRILINGUAL,
        "params": {"prompt": "朋友約你爬山", "references_ja": [{"content": "行こう", "familiarity": 7}]},
        "draft": {"id": draft["id"], "version": draft["version"]},
        **extra,
    }


def test_create_from_draft_clears_it(client, anki, draft_slot):
    with mock.patch.object(generate_service, "generate", mock.AsyncMock(return_value=_draft())):
        draft = start_draft(client, draft_slot)
    response = client.post("/api/v1/handlers/speaking_trilingual/notes", json=_create_body(draft))
    assert response.status_code == 201
    assert len(anki.added) == 1
    assert client.get("/api/v1/drafts/current").json() is None
    again = client.post("/api/v1/handlers/speaking_trilingual/notes", json=_create_body(draft))
    assert _error(again) == (404, "DRAFT_NOT_FOUND")


def test_create_failure_keeps_draft(client, anki, draft_slot):
    with mock.patch.object(generate_service, "generate", mock.AsyncMock(return_value=_draft())):
        draft = start_draft(client, draft_slot)
    body = _create_body(draft)
    body["params"]["references_ja"][0]["familiarity"] = 99
    response = client.post("/api/v1/handlers/speaking_trilingual/notes", json=body)
    assert response.status_code == 422
    current = client.get("/api/v1/drafts/current").json()
    assert current["status"] == "ready"
    assert anki.added == []


def test_create_with_stale_version_conflicts(client, draft_slot):
    with mock.patch.object(generate_service, "generate", mock.AsyncMock(return_value=_draft())):
        draft = start_draft(client, draft_slot)
    saved = client.patch(f"/api/v1/drafts/{draft['id']}", json={"state": {"x": 1}, "version": draft["version"]})
    assert saved.status_code == 200
    response = client.post("/api/v1/handlers/speaking_trilingual/notes", json=_create_body(draft))
    assert _error(response) == (409, "DRAFT_VERSION_CONFLICT")
    assert response.json()["draft"]["state"] == {"x": 1}


def test_patch_and_conflict(client, draft_slot):
    with mock.patch.object(generate_service, "generate", mock.AsyncMock(return_value=_draft())):
        draft = start_draft(client, draft_slot)
    ok = client.patch(f"/api/v1/drafts/{draft['id']}", json={"state": {"a": 1}, "version": draft["version"]})
    assert ok.json()["version"] == draft["version"] + 1
    stale = client.patch(f"/api/v1/drafts/{draft['id']}", json={"state": {"a": 2}, "version": draft["version"]})
    assert _error(stale) == (409, "DRAFT_VERSION_CONFLICT")
    assert stale.json()["draft"]["state"] == {"a": 1}


def test_generating_draft_cannot_be_discarded_or_patched(client, draft_slot):
    response = client.post("/api/v1/handlers/speaking_trilingual/drafts", json={"params": {"background": "x"}})
    draft = response.json()
    assert _error(client.delete(f"/api/v1/drafts/{draft['id']}")) == (409, "DRAFT_BUSY")
    patch = client.patch(f"/api/v1/drafts/{draft['id']}", json={"state": {}, "version": draft["version"]})
    assert _error(patch) == (409, "DRAFT_BUSY")


def test_retry_with_new_input(client, draft_slot):
    failing = mock.AsyncMock(side_effect=LLMGenerationError("壞了"))
    with mock.patch.object(generate_service, "generate", failing):
        failed = start_draft(client, draft_slot)
    with mock.patch.object(generate_service, "generate", mock.AsyncMock(return_value=_draft())):
        response = client.post(f"/api/v1/drafts/{failed['id']}/retry", json={
            "version": failed["version"], "params": {"background": "爬山"},
        })
        assert response.status_code == 202
        run_pending(draft_slot)
    current = client.get("/api/v1/drafts/current").json()
    assert current["status"] == "ready"
    assert current["input"]["background"] == "爬山"


def test_retry_validates_new_input(client, draft_slot):
    failing = mock.AsyncMock(side_effect=LLMGenerationError("壞了"))
    with mock.patch.object(generate_service, "generate", failing):
        failed = start_draft(client, draft_slot)
    response = client.post(f"/api/v1/drafts/{failed['id']}/retry", json={
        "version": failed["version"], "params": {"background": ""},
    })
    assert response.status_code == 422
    assert client.get("/api/v1/drafts/current").json()["status"] == "failed"


def test_discard(client, draft_slot):
    with mock.patch.object(generate_service, "generate", mock.AsyncMock(return_value=_draft())):
        draft = start_draft(client, draft_slot)
    assert client.delete(f"/api/v1/drafts/{draft['id']}").status_code == 204
    assert client.get("/api/v1/drafts/current").json() is None
    assert _error(client.delete(f"/api/v1/drafts/{draft['id']}")) == (404, "DRAFT_NOT_FOUND")


def test_tg_create_not_locked(client, anki, draft_slot):
    """鎖只在網頁的入口：TG 直接呼叫 NoteService.create，有草稿時照樣能建卡，草稿不受影響（D2、D10 過渡期）。"""
    with mock.patch.object(generate_service, "generate", mock.AsyncMock(return_value=_draft())):
        draft = start_draft(client, draft_slot)
    service = _service(anki)
    result = asyncio.run(service.create("vocabulary_mining", "Default", V2, {"Expression": "anyway"}))
    assert result["note_ids"]
    current = client.get("/api/v1/drafts/current").json()
    assert (current["id"], current["version"], current["status"]) == (draft["id"], draft["version"], "ready")


def test_failed_draft_discarded_then_new_generation(client, draft_slot):
    failing = mock.AsyncMock(side_effect=LLMGenerationError("壞了"))
    with mock.patch.object(generate_service, "generate", failing):
        failed = start_draft(client, draft_slot)
    assert client.delete(f"/api/v1/drafts/{failed['id']}").status_code == 204
    with mock.patch.object(generate_service, "generate", mock.AsyncMock(return_value=_draft())):
        assert start_draft(client, draft_slot)["status"] == "ready"


def test_create_from_generating_draft_refused(client, anki, draft_slot):
    response = client.post("/api/v1/handlers/speaking_trilingual/drafts", json={"params": {"background": "x"}})
    draft = response.json()
    created = client.post("/api/v1/handlers/speaking_trilingual/notes", json=_create_body(draft))
    assert _error(created) == (409, "DRAFT_BUSY")
    assert "還在生成" in created.json()["message"]
    assert anki.added == []


def test_create_from_draft_of_another_handler_refused(client, anki, draft_slot):
    with mock.patch.object(generate_service, "generate", mock.AsyncMock(return_value=_draft())):
        draft = start_draft(client, draft_slot)
    body = {
        "deck": "Default",
        "note_type": V2,
        "params": {"Expression": "x"},
        "draft": {"id": draft["id"], "version": draft["version"]},
    }
    response = client.post("/api/v1/handlers/vocabulary_mining/notes", json=body)
    assert _error(response) == (409, "CREATE_SESSION_BUSY")
    assert client.get("/api/v1/drafts/current").json()["status"] == "ready"


def test_refused_form_create_leaves_draft_untouched(client, anki, draft_slot):
    with mock.patch.object(generate_service, "generate", mock.AsyncMock(return_value=_draft())):
        draft = start_draft(client, draft_slot)
    client.post("/api/v1/handlers/vocabulary_mining/notes", json={
        "deck": "Default", "note_type": V2, "params": {"Expression": "anyway"},
    })
    current = client.get("/api/v1/drafts/current").json()
    assert (current["id"], current["version"], current["status"]) == (draft["id"], draft["version"], "ready")


def test_form_placeholder_shape_and_busy(client):
    """填表建卡佔用時，其他頁面輪詢讀得到（不會因為沒有輸入而 500），生成會被擋下。"""
    asyncio.run(create_session.begin_create("vocabulary_mining", None, None))
    current = client.get("/api/v1/drafts/current")
    assert current.status_code == 200
    body = current.json()
    assert (body["status"], body["input"], body["result"]) == ("form_creating", None, None)
    response = client.post("/api/v1/handlers/speaking_trilingual/drafts", json={"params": {"background": "x"}})
    assert _error(response) == (409, "CREATE_SESSION_BUSY")
    assert response.json()["draft"]["status"] == "form_creating"


def test_anki_failure_from_draft_returns_to_ready(client, anki, draft_slot):
    with mock.patch.object(generate_service, "generate", mock.AsyncMock(return_value=_draft())):
        draft = start_draft(client, draft_slot)

    async def rejected(notes):
        return [None]  # Anki 以首欄位重複拒絕

    with mock.patch.object(anki, "add_notes", rejected):
        response = client.post("/api/v1/handlers/speaking_trilingual/notes", json=_create_body(draft))
    assert _error(response) == (409, "DUPLICATE_CARD")
    current = client.get("/api/v1/drafts/current").json()
    assert current["status"] == "ready"
    assert current["version"] == draft["version"] + 2  # 建立中、退回各一次
    again = client.post("/api/v1/handlers/speaking_trilingual/notes", json=_create_body(current))
    assert again.status_code == 201


def test_retry_unknown_draft_is_not_found(client):
    response = client.post("/api/v1/drafts/nope/retry", json={"version": 1})
    assert _error(response) == (404, "DRAFT_NOT_FOUND")


def test_background_generation_does_not_touch_relations(client, draft_slot):
    """背景生成時請求早已結束，綁著請求 DB session 的 relation_service 不能用（D4）。"""

    class Closed:
        def __getattr__(self, name):
            raise AssertionError(f"背景生成用到了 relation_service.{name}")

    client.app.dependency_overrides[get_relation_service] = lambda: Closed()
    with mock.patch.object(generate_service, "generate", mock.AsyncMock(return_value=_draft())):
        assert start_draft(client, draft_slot)["status"] == "ready"


def test_drafts_require_api_key(client):
    with mock.patch.object(settings, "API_SECRET_KEY", "s3cret"):
        assert client.get("/api/v1/drafts/current").status_code == 401
        assert client.delete("/api/v1/drafts/x").status_code == 401
        assert client.get("/api/v1/drafts/current", headers={"X-API-Key": "s3cret"}).status_code == 200


# =============================================================================
# 背景 task 的正式路徑與安全網
# =============================================================================


def test_real_task_path_and_shutdown():
    session = CreateSession()

    async def flow():
        release = asyncio.Event()

        async def slow(parameters):
            await release.wait()
            return RESULT

        await session.start("h", {}, slow)
        task = session._task
        assert isinstance(task, asyncio.Task)
        release.set()
        await task
        assert session.current.status == "ready"
        assert session._task is None

        await session.discard(session.current.id)
        await session.start("h", {}, slow)
        running = session._task
        await session.shutdown()
        with pytest.raises(asyncio.CancelledError):
            await running

    asyncio.run(flow())


def test_unexpected_error_alerts_and_hides_details():
    session, pending = _session()

    async def boom(parameters):
        raise RuntimeError("內部細節")

    async def flow():
        with mock.patch.object(create_session_module, "_alert") as alert:
            await session.start("h", {}, boom)
            await pending.pop()
        assert alert.called
        error = session.snapshot()["error"]
        assert error["error_code"] == "DRAFT_GENERATION_FAILED"
        assert "內部細節" not in error["message"]

    asyncio.run(flow())


def test_task_dying_before_write_back_marks_failed():
    """安全網：背景 task 在寫回之前異常結束，草稿標成失敗，槽位不會一直卡在生成中。"""
    session = CreateSession()

    async def dying(*args):
        raise RuntimeError("寫回前就壞了")

    async def flow():
        with mock.patch.object(session, "_generate", dying), mock.patch.object(create_session_module, "_alert"):
            await session.start("h", {}, _ok)
            for _ in range(3):
                await asyncio.sleep(0)
        assert session.snapshot()["status"] == "failed"
        await session.discard(session.current.id)

    asyncio.run(flow())
