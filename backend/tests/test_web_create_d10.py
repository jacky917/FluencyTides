"""網頁補上對話卡與外語糾錯的建卡（docs/wip/server_drafts_FEAT_2026-11-04.md D10.1、D10.2）。

Web creation of speaking cards and expression corrections.
"""


import uuid
from unittest import mock

import pytest

from app.infrastructure.anki.client import AnkiConnectError
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager
from app.schemas.llm.expression import LLMExpressionCorrectionResult, LLMMicroPoint
from app.infrastructure.anki.search import deck_term, field_term, note_type_term
from app.services.task_handlers.expression_handler import (
    ExpressionCorrectionHandler,
    _children_query,
    duplicate_query,
)
from tests.test_drafts import run_pending, start_draft
from tests.test_handlers_api import ApiAnki, DuplicateAnki, _error, anki, client, relations  # noqa: F401 （fixtures）
from tests.test_note_service import _model_note

EXPRESSION = "expression_correction"
MASTER = "Expression_Master_Dark"
MICRO = "Expression_Micro_Dark"
TEXT = "私は昨日に学校へ行きました"


def _point(phrase: str) -> LLMMicroPoint:
    return LLMMicroPoint(
        original_phrase="",
        target_phrase=phrase,
        native_translation="我昨天去了學校",
        context_hint="跟朋友聊昨天",
        context_sentence=f"{phrase}。",
        error_hint="時間詞不加に",
    )


RESULT = LLMExpressionCorrectionResult(
    error_comparison="私は<u>昨日に</u>学校へ行きました",
    grammar_correction="私は昨日学校へ行きました",
    reorganized_expression="昨日は学校に行ってきました",
    grammar_micro_points=[_point("昨日学校へ")],
    reorganized_micro_points=[_point("行ってきました"), _point("昨日は")],
)


@pytest.fixture
def llm():
    """外語糾錯的 LLM：回傳固定的結果，並記下有沒有被呼叫。The expression LLM, mocked."""
    with mock.patch.object(ExpressionCorrectionHandler, "execute_generate", mock.AsyncMock(return_value=RESULT)) as m:
        yield m


def _with_old_card(client, **master_values) -> ApiAnki:
    """Anki 裡已有同一句原文的母卡（20）與一張子卡（21）。An Anki with an existing group."""
    values = {"My_Original": TEXT, "Correct_Answer": "私は昨日<br>学校へ", "Reorganized_Expression": "A &amp; B"}
    anki = ApiAnki([
        _model_note(20, MASTER, **{**values, **master_values}),
        _model_note(21, MICRO, Master_Note_ID="20"),
    ])
    client.holder["anki"] = anki
    return anki


def _start(client, decision: str | None = None, **params):
    body = {"params": {"original_text": TEXT, **params}}
    if decision:
        body["duplicate_decision"] = decision
    return client.post(f"/api/v1/handlers/{EXPRESSION}/drafts", json=body)


def _create(client, draft: dict, **overrides):
    params = {**draft["result"]["params"], **overrides}
    return client.post(f"/api/v1/handlers/{EXPRESSION}/notes", json={
        "deck": "x", "note_type": MASTER, "params": params,
        "draft": {"id": draft["id"], "version": draft["version"]},
    })


# =============================================================================
# D10.1 對話卡：填表建卡，含參考回答
# =============================================================================


class TestSpeakingCoach:
    def test_create_with_references(self, client, anki):
        response = client.post("/api/v1/handlers/speaking_coach/notes", json={
            "deck": "Default",
            "note_type": "Speaking_Coach_Dark",
            "params": {"prompt": "Order a coffee", "target_language": "ja-jp", "references": ["A <b>&", "", " B "]},
        })
        assert response.status_code == 201, response.json()
        note = anki.added[0]
        fields = note.fields
        assert note.deckName == "Default"
        assert set(note.tags) >= {"Speaking_Coach", "HandlerGenerated"}
        assert fields["Target_Language"] == "ja-JP"
        uuid.UUID(fields["Card_ID"])  # uuid4，不是 TG 的 SC_時間戳
        # 參考回答經 HTML 轉義後寫入（Anki 的編輯器才不會把內容裡的標籤當成 HTML）；每筆的形狀與 TG 建卡相同
        assert "&lt;b&gt;" in fields["References"] and "<b>" not in fields["References"]
        references = AnkiJsonFieldManager.parse_field_string(fields["References"])
        assert [r["content"] for r in references] == ["A <b>&", "B"]
        assert all(r["status"] == 1 and r["audios"] == [] and r["date"] for r in references)
        assert fields["Recordings"] == "[]" and fields["Prompt_Audios"] == "[]"

    def test_without_references(self, client, anki):
        response = client.post("/api/v1/handlers/speaking_coach/notes", json={
            "deck": "Default", "note_type": "Speaking_Coach_Dark", "params": {"prompt": "Hi"},
        })
        assert response.status_code == 201
        assert AnkiJsonFieldManager.parse_field_string(anki.added[0].fields["References"]) == []

    def test_blocked_while_a_draft_exists(self, client, anki, draft_slot):
        with mock.patch(
            "app.services.speaking_trilingual_generate.generate", mock.AsyncMock(side_effect=RuntimeError("x"))
        ):
            start_draft(client, draft_slot)
        response = client.post("/api/v1/handlers/speaking_coach/notes", json={
            "deck": "Default", "note_type": "Speaking_Coach_Dark", "params": {"prompt": "Hi"},
        })
        assert _error(response) == (409, "CREATE_SESSION_BUSY")
        assert anki.added == []


# =============================================================================
# D10.2 外語糾錯：預覽流程、生成前查重複、覆蓋舊卡
# =============================================================================


class TestExpressionDraft:
    def test_draft_schema(self, client):
        handlers = {h["name"]: h for h in client.get("/api/v1/handlers").json()}
        schema = handlers[EXPRESSION]["draft_schema"]
        assert schema["required"] == ["original_text"]
        assert list(schema["properties"]) == [
            "native_language", "target_language", "original_text", "context", "source_tag",
            "user_grammar_correction", "user_reorganization",
        ]
        assert handlers[EXPRESSION]["create_schema"] is None

    def test_generate_without_duplicate(self, client, anki, draft_slot, llm):
        draft = start_draft(client, draft_slot, {"original_text": TEXT, "context": "朋友"}, handler=EXPRESSION)
        assert draft["status"] == "ready"
        params = draft["result"]["params"]
        assert params["original_text"] == TEXT and params["context"] == "朋友"
        assert params["native_language"] == "中文" and params["target_language"] == "日文"
        assert params["llm_result"] == RESULT.model_dump()
        assert params["selected_indices"] is None
        assert params["allow_duplicate"] is False and params["replace_note_id"] is None
        assert draft["result"]["suggested_tags"] == []
        assert "duplicate_decision" not in draft["input"]
        assert anki.added == []  # 草稿不寫入

    def test_duplicate_found_before_llm(self, client, draft_slot, llm):
        _with_old_card(client)
        response = _start(client)
        assert _error(response) == (409, "DUPLICATE_FOUND")
        assert response.json()["duplicate"] == {
            "note_id": 20,
            "original_text": TEXT,
            "correct_answer": "私は昨日\n学校へ",
            "reorganized_expression": "A & B",
        }
        assert client.get("/api/v1/drafts/current").json() is None
        assert draft_slot == []
        llm.assert_not_called()

    def test_overwrite_decision_goes_into_params(self, client, draft_slot, llm):
        _with_old_card(client)
        assert _start(client, "overwrite").status_code == 202
        run_pending(draft_slot)
        draft = client.get("/api/v1/drafts/current").json()
        assert draft["input"]["duplicate_decision"] == "overwrite"
        assert draft["input"]["duplicate_note_id"] == 20
        params = draft["result"]["params"]
        # 舊卡在新卡建好之後才刪：建立當下兩張原文相同，也要允許重複
        assert params["allow_duplicate"] is True and params["replace_note_id"] == 20

    def test_allow_decision(self, client, draft_slot, llm):
        _with_old_card(client)
        assert _start(client, "allow").status_code == 202
        run_pending(draft_slot)
        params = client.get("/api/v1/drafts/current").json()["result"]["params"]
        assert params["allow_duplicate"] is True and params["replace_note_id"] is None

    def test_decision_ignored_without_duplicate(self, client, draft_slot, llm):
        assert _start(client, "overwrite").status_code == 202
        run_pending(draft_slot)
        draft = client.get("/api/v1/drafts/current").json()
        assert "duplicate_decision" not in draft["input"]
        assert draft["result"]["params"]["allow_duplicate"] is False

    def test_lookup_failure_skips_the_check(self, client, draft_slot, llm):
        anki = _with_old_card(client)
        anki.find_notes = mock.AsyncMock(side_effect=AnkiConnectError("connection refused"))
        assert _start(client).status_code == 202
        run_pending(draft_slot)
        draft = client.get("/api/v1/drafts/current").json()
        assert draft["status"] == "ready"
        # 略過了檢查：記下原因讓網頁提醒；建立參數照常不允許重複（真的重複時由 Anki 擋下）
        assert "connection refused" in draft["input"]["duplicate_check_skipped"]
        assert "duplicate_check_skipped" not in draft["result"]["params"]
        assert draft["result"]["params"]["allow_duplicate"] is False

    def test_busy_slot_is_reported_before_duplicate(self, client, draft_slot, llm):
        start_draft(client, draft_slot, {"original_text": "別の文"}, handler=EXPRESSION)
        anki = _with_old_card(client)
        anki.find_notes = mock.AsyncMock(wraps=anki.find_notes)
        assert _error(_start(client)) == (409, "CREATE_SESSION_BUSY")
        anki.find_notes.assert_not_called()

    def test_retry_with_new_input_rechecks_and_keeps_decision(self, client, draft_slot):
        _with_old_card(client)
        with mock.patch.object(
            ExpressionCorrectionHandler, "execute_generate", mock.AsyncMock(side_effect=RuntimeError("x"))
        ):
            assert _start(client, "overwrite").status_code == 202
            run_pending(draft_slot)
        failed = client.get("/api/v1/drafts/current").json()
        assert failed["status"] == "failed"
        with mock.patch.object(ExpressionCorrectionHandler, "execute_generate", mock.AsyncMock(return_value=RESULT)):
            response = client.post(f"/api/v1/drafts/{failed['id']}/retry", json={
                "version": failed["version"], "params": {"original_text": TEXT, "context": "改了情境"},
            })
            assert response.status_code == 202, response.json()
            run_pending(draft_slot)
        draft = client.get("/api/v1/drafts/current").json()
        assert draft["input"]["context"] == "改了情境"
        assert draft["result"]["params"]["replace_note_id"] == 20

    def test_retry_with_text_matching_another_card_asks_again(self, client, draft_slot, llm):
        """對 A 卡選了覆蓋，改成另一句原文後對到 B 卡：不能沿用，要再問（否則會刪掉沒看過的卡）。"""
        _with_old_card(client)
        llm.side_effect = RuntimeError("x")
        assert _start(client, "overwrite").status_code == 202
        run_pending(draft_slot)
        failed = client.get("/api/v1/drafts/current").json()
        assert failed["status"] == "failed"

        other = ApiAnki([
            _model_note(30, MASTER, My_Original="別の文", Correct_Answer="別の修正"),
            _model_note(31, MICRO, Master_Note_ID="30"),
        ])
        client.holder["anki"] = other
        llm.side_effect = None
        llm.reset_mock()
        body = {"version": failed["version"], "params": {"original_text": "別の文"}}
        response = client.post(f"/api/v1/drafts/{failed['id']}/retry", json=body)
        assert _error(response) == (409, "DUPLICATE_FOUND")
        assert response.json()["duplicate"]["note_id"] == 30
        assert client.get("/api/v1/drafts/current").json()["version"] == failed["version"]

        # 看過 B 卡、帶著決定用同一個版本重送：照常生成，覆蓋的是 B 卡
        response = client.post(
            f"/api/v1/drafts/{failed['id']}/retry", json={**body, "duplicate_decision": "overwrite"}
        )
        assert response.status_code == 202, response.json()
        run_pending(draft_slot)
        draft = client.get("/api/v1/drafts/current").json()
        assert draft["result"]["params"]["replace_note_id"] == 30
        llm.assert_called_once()

    def test_invalid_decision_is_rejected(self, client, draft_slot, llm):
        _with_old_card(client)
        response = _start(client, "replace")
        assert response.status_code == 422
        llm.assert_not_called()

    def test_retry_with_new_text_that_duplicates_asks_again(self, client, draft_slot):
        with mock.patch.object(
            ExpressionCorrectionHandler, "execute_generate", mock.AsyncMock(side_effect=RuntimeError("x"))
        ):
            start_draft(client, draft_slot, {"original_text": "別の文"}, handler=EXPRESSION)
        failed = client.get("/api/v1/drafts/current").json()
        _with_old_card(client)
        response = client.post(f"/api/v1/drafts/{failed['id']}/retry", json={
            "version": failed["version"], "params": {"original_text": TEXT},
        })
        assert _error(response) == (409, "DUPLICATE_FOUND")
        assert client.get("/api/v1/drafts/current").json()["status"] == "failed"


class TestExpressionCreate:
    def test_create_selected_micro_cards(self, client, anki, draft_slot, llm):
        draft = start_draft(client, draft_slot, {"original_text": TEXT}, handler=EXPRESSION)
        response = _create(client, draft, selected_indices=[2])
        assert response.status_code == 201, response.json()
        assert response.json()["deck"] == "日本語::外語糾錯::母卡片"
        models = [(n.modelName, n.deckName) for n in anki.added]
        assert models == [
            (MASTER, "日本語::外語糾錯::母卡片"),
            (MICRO, "日本語::外語糾錯::子卡片::重新組織"),
        ]
        assert anki.added[1].fields["Target_Phrase"] == "行ってきました"
        assert client.get("/api/v1/drafts/current").json() is None

    def test_overwrite_deletes_old_group_after_create(self, client, draft_slot, llm):
        anki = _with_old_card(client)
        assert _start(client, "overwrite").status_code == 202
        run_pending(draft_slot)
        draft = client.get("/api/v1/drafts/current").json()
        events: list[str] = []
        delete_notes, sync = anki.delete_notes, anki.sync

        async def record_delete(notes):
            events.append("delete")
            await delete_notes(notes)

        async def record_sync(*args, **kwargs):
            events.append("sync")
            await sync(*args, **kwargs)

        anki.delete_notes, anki.sync = record_delete, record_sync
        response = _create(client, draft)
        assert response.status_code == 201, response.json()
        assert len(anki.added) == 4  # 母卡＋三張子卡
        assert sorted(anki.deleted) == [20, 21]
        # 子卡只找這張母卡的（假 Anki 不看欄位，所以檢查查詢本身）
        assert _children_query(20) in anki.queries
        assert response.json()["sync_warning"] is None
        # 刪除在同步之前：與新卡同一次同步推上 AnkiWeb
        assert events == ["delete", "sync"]

    def test_overwrite_with_html_characters_in_text(self, client, draft_slot, llm):
        """原文含 & 與 <：Anki 欄位存的是 HTML，兩邊經同一種正規化後比對，仍會刪。"""
        anki = _with_old_card(client, My_Original="A &amp; B &lt;x&gt;")
        assert _start(client, "overwrite", original_text="A & B <x>").status_code == 202
        run_pending(draft_slot)
        response = _create(client, client.get("/api/v1/drafts/current").json())
        assert response.status_code == 201
        assert sorted(anki.deleted) == [20, 21]

    def test_only_master_when_nothing_selected(self, client, anki, draft_slot, llm):
        draft = start_draft(client, draft_slot, {"original_text": TEXT}, handler=EXPRESSION)
        response = _create(client, draft, selected_indices=[])
        assert response.status_code == 201
        assert [n.modelName for n in anki.added] == [MASTER]

    def test_unexpected_after_create_error_is_a_warning(self, client, anki, draft_slot, llm):
        """建立後的後續處理有程式錯誤：卡已寫入，不能報成建立失敗（否則會再建一組）。"""
        draft = start_draft(client, draft_slot, {"original_text": TEXT}, handler=EXPRESSION)
        with mock.patch.object(
            ExpressionCorrectionHandler, "after_create", mock.AsyncMock(side_effect=RuntimeError("bug"))
        ):
            response = _create(client, draft)
        assert response.status_code == 201
        assert "後續處理失敗" in response.json()["sync_warning"]
        assert client.get("/api/v1/drafts/current").json() is None

    def test_overwrite_refuses_a_different_card(self, client, draft_slot, llm):
        anki = _with_old_card(client, My_Original="別の文")
        # 假 Anki 只依筆記類型找，會把它當成重複；選「允許重複」，再直接改建立參數假裝要覆蓋這張原文不同的卡
        assert _start(client, "allow").status_code == 202
        run_pending(draft_slot)
        draft = client.get("/api/v1/drafts/current").json()
        response = _create(client, draft, replace_note_id=20, allow_duplicate=True)
        assert response.status_code == 201
        assert anki.deleted == []
        assert "沒有刪除" in response.json()["sync_warning"]

    def test_overwrite_skips_a_card_already_gone(self, client, draft_slot, llm):
        anki = _with_old_card(client)
        assert _start(client, "overwrite").status_code == 202
        run_pending(draft_slot)
        draft = client.get("/api/v1/drafts/current").json()
        del anki.notes[20]
        response = _create(client, draft)
        assert response.status_code == 201
        assert anki.deleted == [] and response.json()["sync_warning"] is None

    def test_delete_failure_is_a_warning(self, client, draft_slot, llm):
        anki = _with_old_card(client)
        assert _start(client, "overwrite").status_code == 202
        run_pending(draft_slot)
        draft = client.get("/api/v1/drafts/current").json()
        anki.delete_notes = mock.AsyncMock(side_effect=AnkiConnectError("busy"))
        response = _create(client, draft)
        assert response.status_code == 201
        warning = response.json()["sync_warning"]
        assert "Note ID 20" in warning and "刪除失敗" in warning and "busy" in warning

    def test_failed_create_keeps_old_card(self, client, draft_slot, llm):
        _with_old_card(client)
        assert _start(client, "overwrite").status_code == 202
        run_pending(draft_slot)
        draft = client.get("/api/v1/drafts/current").json()
        duplicate_anki = DuplicateAnki(list(client.holder["anki"].notes.values()))
        client.holder["anki"] = duplicate_anki
        response = _create(client, draft)
        assert response.status_code == 409
        assert duplicate_anki.deleted == []
        assert client.get("/api/v1/drafts/current").json()["status"] == "ready"


class TestExpressionQueries:
    """假 Anki 只依筆記類型找、不看欄位：查詢條件本身要另外驗證，寫壞了會查錯或刪錯卡。"""

    def test_duplicate_query(self):
        assert duplicate_query(TEXT) == (
            f"{deck_term('日本語::外語糾錯::母卡片')} {note_type_term(MASTER)} {field_term('My_Original', TEXT)}"
        )

    def test_children_query_only_matches_this_master(self):
        assert _children_query(20) == f"{note_type_term(MICRO)} {field_term('Master_Note_ID', '20')}"
