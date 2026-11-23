"""網頁人脈卡「AI 整理」的測試（docs/wip/person_llm_fill_FEAT_2026-11-23.md）。

Tests for the web "AI 整理": the two prompt modes, the create-mode LLM result and proposals, checking edited
proposals, writing them (rejections reported, the rest still written; a new card created once), the draft slot
(``finish_apply``) and the API (start, retry, write).
"""

import asyncio
from datetime import date
from unittest import mock

import pytest

from app.core.exceptions import FluencyTidesError, JsonItemChangedError
from app.schemas.llm.person_profile import LLMPersonCreateResult, LLMPersonFillResult, LLMPersonIngestResult
from app.schemas.person_fill import ProposalIn, StartFillRequest
from app.services import person_profile_fill as fill
from app.services import person_profile_ingest as ingest
from app.services.create_session import CreateSession
from tests.test_handlers_api import ApiAnki, _error, client, relations  # noqa: F401 （pytest fixtures）
from tests.test_note_service import _model_note

TODAY = date(2026, 11, 23)
PERSON = "Person_Profile_Dark"


def _run(coro):
    return asyncio.run(coro)


def _update_result(**overrides) -> LLMPersonIngestResult:
    data = {
        "interaction": {"date": "2026-11-23", "topics": "登山、轉職", "updates": "", "place": "", "quote": ""},
        "followups": [{"item": "N1 考得如何？", "type": "ask", "due": ""}],
        "field_updates": [{"field": "Hometown", "value": "福岡", "mode": "replace", "reason": "說老家在福岡"}],
    }
    data.update(overrides)
    return LLMPersonIngestResult.model_validate(data)


def _create_result(**overrides) -> LLMPersonCreateResult:
    data = {
        "display_name": "田中さん",
        "interaction": None,
        "field_updates": [{"field": "Hometown", "value": "福岡", "mode": "replace", "reason": "說老家在福岡"}],
    }
    data.update(overrides)
    return LLMPersonCreateResult.model_validate(data)


# =============================================================================
# 提示詞的兩種模式（D3）
# =============================================================================


class TestPromptModes:
    def test_update_mode_lists_known_data_and_allows_striking_out(self):
        prompt = ingest.build_prompt({"Display_Name": "田中さん", "Hometown": "福岡"}, {}, "今天聊了登山", TODAY)
        assert "剛和「田中さん」見面" in prompt
        assert "這個人目前已知的資料" in prompt
        assert '`op` 是 `"end"`（劃掉）' in prompt
        assert "display_name" not in prompt

    def test_create_mode_asks_for_a_display_name_without_known_data(self):
        prompt = ingest.build_prompt({}, {}, "今天在 Geekly 認識田中さん", TODAY, "create")
        assert "整理成一張新的人脈卡" in prompt
        assert "`display_name`" in prompt
        assert "這個人目前已知的資料" not in prompt
        assert '`op` 是 `"end"`（劃掉）' not in prompt
        assert "建新卡沒有已知的項目" in prompt
        assert "今天在 Geekly 認識田中さん" in prompt


class TestIngestCall:
    def _client(self, parsed):
        client = mock.MagicMock()
        client.generate_structured_data = mock.AsyncMock(
            return_value=mock.MagicMock(parsed_data=parsed, model_name="m1", raw_content="{}")
        )
        return client

    def test_create_mode_uses_the_create_schema_and_given_route(self):
        client = self._client(_create_result().model_dump())
        route = mock.MagicMock(client=mock.AsyncMock(return_value=client))
        result = _run(ingest.ingest({}, {}, "x", TODAY, mode="create", llm_route=route))
        kwargs = client.generate_structured_data.await_args.kwargs
        assert kwargs["response_schema"] == LLMPersonCreateResult.model_json_schema()
        assert isinstance(result, LLMPersonCreateResult) and result.display_name == "田中さん"

    def test_fill_route_is_separate_from_telegram(self):
        assert fill.FILL_ROUTE.key == "person_profile.fill"
        assert ingest.INGEST_ROUTE.key == "person_profile"
        assert fill.FILL_ROUTE.capabilities == frozenset({"llm"})


# =============================================================================
# 建新卡的建議（D4）
# =============================================================================


class TestCreateProposals:
    def test_display_name_comes_first_and_no_interaction_is_fine(self):
        proposals = ingest.proposals_from(_create_result(), {}, {}, TODAY)
        assert [p.kind for p in proposals] == ["display_name", "field"]
        assert proposals[0].data == {"value": "田中さん"}

    def test_interaction_is_kept_when_given(self):
        result = _create_result(interaction={"date": "2026-11-20", "topics": "自我介紹", "updates": "", "place": "",
                                             "quote": ""})
        kinds = [p.kind for p in ingest.proposals_from(result, {}, {}, TODAY)]
        assert kinds == ["display_name", "interaction", "field"]

    def test_update_result_still_requires_an_interaction(self):
        with pytest.raises(ValueError):
            LLMPersonIngestResult.model_validate({"followups": []})


# =============================================================================
# 編輯過的建議（D7）
# =============================================================================


class TestNormalize:
    def test_replace_field_stores_the_tidied_value(self):
        data = fill.normalize("field", {"field": "Birthday", "value": "3/15", "mode": "replace", "reason": "r"})
        assert data == {"field": "Birthday", "value": "--03-15", "mode": "replace", "reason": "r"}

    @pytest.mark.parametrize(
        ("kind", "data", "message"),
        [
            ("display_name", {"value": "  "}, "稱呼不能是空的"),
            ("field", {"field": "Display_Name", "value": "x", "mode": "replace"}, "不能由 AI 整理寫入"),
            ("field", {"field": "Notes", "value": "", "mode": "append"}, "內容不能是空的"),
            ("field", {"field": "Notes", "value": "x", "mode": "other"}, "取代或接在後面"),
            ("history_add", {"field": "Nope", "item": {}}, "格式不對"),
            ("history_end", {"field": "Interests", "index": "1", "fingerprint": "f"}, "格式不對"),
        ],
    )
    def test_invalid_data_is_rejected_with_a_reason(self, kind, data, message):
        with pytest.raises(ValueError, match=message):
            fill.normalize(kind, data)

    def test_items_are_checked_with_the_handler_models(self):
        with pytest.raises(ValueError):
            fill.normalize("followup", {"item": "x", "type": "bad"})
        assert fill.normalize("followup", {"item": "介紹 Ken", "type": "my_promise", "due": ""}) == {
            "item": "介紹 Ken", "type": "my_promise",
        }


# =============================================================================
# 寫入（D6、D7）
# =============================================================================


class FakeNoteService:
    """記下寫入的動作；指定的動作會被拒絕。Records writes; listed actions are rejected."""

    def __init__(self, reject: tuple[str, ...] = ()) -> None:
        self.actions: list[tuple[int, str, dict]] = []
        self.updates: list[tuple[int, dict]] = []
        self.created: list[dict] = []
        self.reject = reject
        self.syncs = 0
        self.unsynced_writes = 0

    async def run_action(self, note_id, action, params, *, sync=True):
        if action in self.reject:
            raise JsonItemChangedError("這一筆已經變了")
        assert sync is False, "AI 整理的每一項寫入都不同步，最後才同步一次"
        self.actions.append((note_id, action, params))
        self.unsynced_writes += 1
        return None

    async def update_fields(self, handler_name, note_id, fields, *, sync=True):
        assert sync is False
        self.updates.append((note_id, dict(fields)))
        self.unsynced_writes += 1
        return {"sync_warning": None}

    async def create(self, handler_name, deck, note_type, params, *, sync=True, **kwargs):
        assert sync is False
        self.created.append({"deck": deck, "note_type": note_type, "params": dict(params)})
        self.unsynced_writes += 1
        return {"note_ids": [99], "deck": deck, "sync_warning": None}

    async def sync_after_write(self):
        self.syncs += 1
        self.unsynced_writes = 0
        return "同步提醒"


class FakeCardService:
    async def get_note(self, note_id):
        return {"modelName": PERSON, "fields": {"Display_Name": {"value": "田中さん"}, "Notes": {"value": "舊的"}},
                "tags": []}


def _proposals(*items: tuple[str, dict]) -> list[ProposalIn]:
    return [ProposalIn(kind=kind, data=data) for kind, data in items]


UPDATE_PROPOSALS = _proposals(
    ("interaction", {"date": "2026-11-23", "topics": "登山"}),
    ("followup", {"item": "N1 考得如何？", "type": "ask"}),
    ("field", {"field": "Notes", "value": "新的", "mode": "append", "reason": "r"}),
)


class TestApply:
    def test_update_writes_the_ticked_and_reports_rejections(self):
        notes = FakeNoteService(reject=("add_followup",))
        progress = fill.ApplyProgress(note_id=7)
        done = _run(fill.apply(notes, FakeCardService(), {"note_id": 7}, UPDATE_PROPOSALS, [0, 1, 2], progress))
        assert not done
        assert progress.saved == {0, 2}
        assert progress.errors == {1: "這一筆已經變了"}
        assert [action for _, action, _ in notes.actions] == ["add_interaction"]
        # 寫了兩項（互動、備註），只同步一次
        assert notes.syncs == 1 and notes.unsynced_writes == 0
        # 接在後面以寫入當下的欄位內容計算
        assert notes.updates[0][1]["Notes"].startswith("舊的") and notes.updates[0][1]["Notes"].endswith("新的")

    def test_retry_writes_only_the_rest(self):
        notes = FakeNoteService()
        progress = fill.ApplyProgress(note_id=7, saved={0, 2})
        done = _run(fill.apply(notes, FakeCardService(), {"note_id": 7}, UPDATE_PROPOSALS, [0, 1, 2], progress))
        assert done and progress.saved == {0, 1, 2} and progress.errors == {}
        assert [action for _, action, _ in notes.actions] == ["add_followup"]

    def test_invalid_edit_is_reported_without_writing_it(self):
        notes = FakeNoteService()
        proposals = _proposals(
            ("followup", {"item": "x", "type": "bad"}),
            ("interaction", {"date": "2026-11-23", "topics": "登山"}),
        )
        progress = fill.ApplyProgress(note_id=7)
        assert not _run(fill.apply(notes, FakeCardService(), {"note_id": 7}, proposals, [0, 1], progress))
        assert 0 in progress.errors and progress.saved == {1}

    def test_create_makes_the_card_once_then_adds_to_it(self):
        notes = FakeNoteService(reject=("add_followup",))
        proposals = _proposals(
            ("display_name", {"value": "田中さん"}),
            ("followup", {"item": "N1 考得如何？", "type": "ask"}),
            ("interaction", {"date": "2026-11-23", "topics": "登山"}),
        )
        params = {"mode": "create", "note_id": None, "deck": "人脈"}
        progress = fill.ApplyProgress.from_params(params)
        assert not _run(fill.apply(notes, FakeCardService(), params, proposals, [0, 1, 2], progress))
        assert notes.created == [{"deck": "人脈", "note_type": PERSON, "params": {"Display_Name": "田中さん"}}]
        assert progress.note_id == 99 and progress.saved == {0, 2} and progress.warning == "同步提醒"
        # 新卡與互動一起，最後才同步一次
        assert notes.syncs == 1 and notes.unsynced_writes == 0

        # 草稿記下了新卡：再送一次只補寫剩下的，不會再建一張
        resumed = fill.ApplyProgress.from_params({**params, **progress.to_params()})
        notes.reject = ()
        assert _run(fill.apply(notes, FakeCardService(), params, proposals, [0, 1, 2], resumed))
        assert len(notes.created) == 1
        assert [(note_id, action) for note_id, action, _ in notes.actions] == [
            (99, "add_interaction"), (99, "add_followup"),
        ]

    def test_create_needs_a_display_name(self):
        notes = FakeNoteService()
        proposals = _proposals(("display_name", {"value": ""}), ("interaction", {"date": "2026-11-23", "topics": "x"}))
        progress = fill.ApplyProgress(note_id=None)
        assert not _run(fill.apply(notes, FakeCardService(), {"deck": "人脈"}, proposals, [0, 1], progress))
        assert notes.created == [] and progress.errors == {0: "稱呼不能是空的"}

    def test_telegram_mode_still_stops_at_the_first_rejection(self):
        notes = FakeNoteService(reject=("add_followup",))
        ready = [{"kind": p.kind, "data": p.data} for p in UPDATE_PROPOSALS]
        saved: set[int] = set()
        with pytest.raises(FluencyTidesError):
            _run(ingest.apply_proposals(notes, FakeCardService(), 7, ready, [0, 1, 2], saved))
        assert saved == {0} and notes.updates == []
        # 中途失敗也會同步，已經寫進去的那一項照樣上傳
        assert notes.syncs == 1


# =============================================================================
# 草稿槽位（D5、D7）
# =============================================================================


class TestSession:
    def _ready(self) -> tuple[CreateSession, dict]:
        session = CreateSession()
        pending: list = []
        session._spawn = pending.append

        async def generate(parameters):
            return {"params": {"mode": "create", "note_id": None, "proposals": []}, "suggested_tags": []}

        async def flow():
            await session.start("person_profile", fill.draft_input("x", None, "人脈"), generate)
            await pending.pop()
            return session.snapshot()

        return session, _run(flow())

    def test_partial_write_keeps_the_draft_with_progress(self):
        session, draft = self._ready()

        async def flow():
            claimed = await session.begin_create("person_profile", draft["id"], draft["version"])
            return await session.finish_apply(claimed, done=False, progress={"note_id": 99, "saved": [0]})

        remaining = _run(flow())
        assert remaining["status"] == "ready"
        assert remaining["result"]["params"]["note_id"] == 99
        assert remaining["result"]["params"]["saved"] == [0]
        assert remaining["result"]["params"]["mode"] == "create"

    def test_done_clears_the_slot(self):
        session, draft = self._ready()

        async def flow():
            claimed = await session.begin_create("person_profile", draft["id"], draft["version"])
            return await session.finish_apply(claimed, done=True, progress={})

        assert _run(flow()) is None and session.snapshot() is None

    def test_draft_input_is_marked(self):
        assert fill.is_fill_draft(fill.draft_input("x", 5, "ignored")) is True
        assert fill.draft_input("x", 5, "ignored")["deck"] is None
        assert fill.is_fill_draft({"background": "剪髮"}) is False
        assert fill.is_fill_draft(None) is False


def test_start_request_needs_a_deck_for_a_new_card():
    with pytest.raises(ValueError, match="建新卡要選牌組"):
        StartFillRequest(text="x")
    assert StartFillRequest(text="x", note_id=3).deck is None


# =============================================================================
# API
# =============================================================================


class PersonAnki(ApiAnki):
    """寫入會套用到筆記上（寫完再讀得到新的值）。Writes are applied to the notes."""

    async def update_note_fields(self, note_id: int, fields: dict[str, str]) -> None:
        await super().update_note_fields(note_id, fields)
        for name, value in fields.items():
            self.notes[note_id].fields[name]["value"] = value


INTERESTS = '[{"value": "攝影", "added": "2026-01-01", "ended": ""}]'


@pytest.fixture
def anki():
    return PersonAnki([_model_note(1, PERSON, Display_Name="田中さん", Notes="舊的", Interests=INTERESTS)])


def _start(client, draft_slot, body):
    response = client.post("/api/v1/handlers/person_profile/fill", json=body)
    assert response.status_code == 202, response.json()
    while draft_slot:
        asyncio.run(draft_slot.pop(0))
    return client.get("/api/v1/drafts/current").json()


class TestApi:
    def test_fill_adds_to_an_existing_card(self, client, anki, draft_slot):
        with mock.patch.object(ingest, "ingest", mock.AsyncMock(return_value=_update_result())) as call:
            draft = _start(client, draft_slot, {"text": "今天聊了登山", "note_id": 1})
        assert call.await_args.kwargs["mode"] == "update"
        assert call.await_args.kwargs["llm_route"] is fill.FILL_ROUTE
        assert call.await_args.kwargs["optional_interaction"] is True
        assert draft["status"] == "ready" and draft["input"]["purpose"] == "fill"
        params = draft["result"]["params"]
        assert params["mode"] == "update" and params["note_id"] == 1
        assert [p["kind"] for p in params["proposals"]] == ["interaction", "followup", "field"]
        # 卡片上已有的資料跟著草稿，對話框在建議旁邊顯示
        assert params["known"]["fields"]["Notes"] == "舊的"

        # 改了待追蹤的內容再寫入
        proposals = params["proposals"]
        proposals[1]["data"]["item"] = "N1 的結果？"
        response = client.post(f"/api/v1/drafts/{draft['id']}/apply", json={
            "version": draft["version"], "proposals": proposals, "selected": [0, 1, 2],
        })
        assert response.status_code == 200, response.json()
        body = response.json()
        assert body["done"] is True and body["note_id"] == 1 and body["saved"] == [0, 1, 2] and body["draft"] is None
        fields = anki.notes[1].fields
        assert "登山" in fields["Interactions_JSON"]["value"]
        assert "N1 的結果？" in fields["Followups_JSON"]["value"]
        assert fields["Hometown"]["value"] == "福岡"
        assert client.get("/api/v1/drafts/current").json() is None

    def test_only_handlers_offering_fill_can_start(self, client, draft_slot):
        response = client.post("/api/v1/handlers/speaking_trilingual/fill", json={"text": "x", "deck": "Default"})
        assert _error(response) == (400, "HANDLER_OPERATION_UNSUPPORTED")

    def test_fill_shares_the_slot_with_preview_drafts(self, client, draft_slot):
        with mock.patch.object(ingest, "ingest", mock.AsyncMock(return_value=_update_result())):
            _start(client, draft_slot, {"text": "x", "note_id": 1})
        response = client.post("/api/v1/handlers/person_profile/fill", json={"text": "y", "note_id": 1})
        assert _error(response) == (409, "CREATE_SESSION_BUSY")

    def test_retry_keeps_the_card_and_takes_the_new_text(self, client, draft_slot):
        failing = mock.AsyncMock(side_effect=JsonItemChangedError("暫時失敗"))
        with mock.patch.object(ingest, "ingest", failing):
            draft = _start(client, draft_slot, {"text": "第一次", "note_id": 1})
        assert draft["status"] == "failed"
        with mock.patch.object(ingest, "ingest", mock.AsyncMock(return_value=_update_result())) as call:
            response = client.post(f"/api/v1/drafts/{draft['id']}/retry", json={
                "version": draft["version"], "params": {"text": "第二次"},
            })
            assert response.status_code == 202, response.json()
            while draft_slot:
                asyncio.run(draft_slot.pop(0))
        assert call.await_args.args[2] == "第二次"
        current = client.get("/api/v1/drafts/current").json()
        assert current["status"] == "ready" and current["input"]["note_id"] == 1

    def test_apply_refuses_preview_drafts(self, client, draft_slot):
        from app.services.create_session import create_session

        async def generate(parameters):
            return {"params": {}, "suggested_tags": []}

        async def start():
            await create_session.start("speaking_trilingual", {"background": "x"}, generate)

        asyncio.run(start())
        while draft_slot:
            asyncio.run(draft_slot.pop(0))
        draft = client.get("/api/v1/drafts/current").json()
        response = client.post(f"/api/v1/drafts/{draft['id']}/apply", json={
            "version": draft["version"], "proposals": [], "selected": [],
        })
        assert _error(response) == (400, "HANDLER_OPERATION_UNSUPPORTED")


# =============================================================================
# 家人、網頁補舊卡的互動可以沒有、不推測（2026-11-23 實測後的修正）
# =============================================================================


class TestQualityFixes:
    def test_family_rule_and_no_inference_rules_are_in_every_mode(self):
        for prompt in (
            ingest.build_prompt({"Display_Name": "x"}, {}, "t", TODAY),
            ingest.build_prompt({"Display_Name": "x"}, {}, "t", TODAY, optional_interaction=True),
            ingest.build_prompt({}, {}, "t", TODAY, "create"),
        ):
            assert "**家人**（放在 `family`）" in prompt
            assert "不能寫成「前一份工作」或「對六本木很熟」" in prompt
            assert "不要從文中的事實自己想出要問的問題" in prompt
            assert "照抄文中的原句" in prompt
            # 重新組織成一看就懂的說法、相對時間換成日期（2026-11-23 第二次實測後）
            assert "「包包的公司也在六本木」→「做包包的那家公司也在六本木」" in prompt
            assert "**相對時間一律換成日期**" in prompt
            assert "不要寫成「上上個月（2026-09）」" in prompt

    def test_telegram_still_asks_for_one_interaction_but_the_web_may_leave_it_out(self):
        telegram = ingest.build_prompt({"Display_Name": "x"}, {}, "t", TODAY)
        web = ingest.build_prompt({"Display_Name": "x"}, {}, "t", TODAY, optional_interaction=True)
        assert "**這次的互動**（一筆）" in telegram and "只是記下對他的了解" not in telegram
        assert "**這次的互動**（零或一筆）" in web and "只是記下對他的了解" in web

    def test_known_family_is_listed(self):
        lists = {"Family_JSON": [{"relation": "弟弟", "name": "健太", "note": "在大阪"}]}
        assert "- 弟弟（健太）：在大阪" in ingest.build_prompt({"Display_Name": "x"}, lists, "t", TODAY)

    def test_web_update_uses_the_optional_interaction_schema(self):
        client = mock.MagicMock()
        client.generate_structured_data = mock.AsyncMock(return_value=mock.MagicMock(
            parsed_data={"interaction": None}, model_name="m1", raw_content="{}",
        ))
        route = mock.MagicMock(client=mock.AsyncMock(return_value=client))
        result = _run(ingest.ingest({}, {}, "x", TODAY, llm_route=route, optional_interaction=True))
        assert result.interaction is None
        assert client.generate_structured_data.await_args.kwargs["response_schema"] == (
            LLMPersonFillResult.model_json_schema()
        )

    def test_family_members_become_proposals_unless_known(self):
        result = _update_result(family=[
            {"relation": "弟弟", "name": "", "note": "上上個月來日本", "reason": "弟弟上上個月來日本"},
            {"relation": "媽媽", "name": "", "note": "住院中", "reason": "媽媽住院"},
        ])
        known = {"Family_JSON": [{"relation": "媽媽", "name": "", "note": "住院中"}]}
        family = [p for p in ingest.proposals_from(result, {}, known, TODAY) if p.kind == "family"]
        assert [p.data for p in family] == [
            {"relation": "弟弟", "note": "上上個月來日本", "reason": "弟弟上上個月來日本"},
        ]
        assert ingest.proposal_action("family", family[0].data) == (
            "add_item", {"field": "Family_JSON", "item": {"relation": "弟弟", "note": "上上個月來日本"}},
        )

    def test_no_interaction_on_the_web_means_no_interaction_proposal(self):
        result = LLMPersonFillResult.model_validate({"interaction": None, "goals": [
            {"kind": "努力方向", "item": "學日文", "reason": "在努力學日文"},
        ]})
        assert [p.kind for p in ingest.proposals_from(result, {}, {}, TODAY)] == ["goal"]

    def test_edited_family_member_is_checked(self):
        assert fill.normalize("family", {"relation": "弟弟", "name": "", "note": "x", "reason": "r"}) == {
            "relation": "弟弟", "note": "x", "reason": "r",
        }
        with pytest.raises(ValueError):
            fill.normalize("family", {"relation": "", "note": "x"})

    def test_telegram_preview_shows_family_members(self):
        from app.bot.utils.person_profile_view import proposal_line

        line = proposal_line("family", {"relation": "弟弟", "name": "健太", "note": "在大阪", "reason": "r"})
        assert "<b>家人</b> 新增：弟弟（健太）：在大阪" in line


def test_known_summary_matches_what_the_prompt_lists():
    lists = {
        "Interests": [
            {"value": "攝影", "added": "2026-01-01", "ended": "2026-11-23"},
            {"value": "登山", "added": "2026-11-23", "ended": ""},
        ],
        "Family_JSON": [{"relation": "弟弟", "note": "在大阪"}],
        "Followups_JSON": [{"item": "N1 考得如何？", "status": "open"}, {"item": "已完成", "status": "done"}],
    }
    known = ingest.known_summary({"Display_Name": "田中さん", "Notes": "上次聊到想轉職", "Hometown": ""}, lists)
    assert known["fields"] == {"Notes": "上次聊到想轉職"}
    assert known["lists"] == {"Interests": {"current": ["登山"], "past": ["攝影"]}}
    assert known["family"] == ["弟弟：在大阪"]
    assert known["followups"] == ["N1 考得如何？"]
    prompt = ingest.build_prompt({"Display_Name": "田中さん"}, lists, "t", TODAY)
    assert "- 弟弟：在大阪" in prompt and "N1 考得如何？" in prompt and "已完成" not in prompt


def test_create_without_other_proposals_still_syncs_the_new_card():
    notes = FakeNoteService()
    proposals = _proposals(("display_name", {"value": "田中さん"}))
    progress = fill.ApplyProgress(note_id=None)
    assert _run(fill.apply(notes, FakeCardService(), {"deck": "人脈"}, proposals, [0], progress))
    assert notes.syncs == 1 and notes.unsynced_writes == 0


# =============================================================================
# 修改卡片原本的內容（2026-11-23 第三次實測後：原本的內容要看得到、點筆修改）
# =============================================================================


def test_known_items_carry_what_edit_item_needs():
    lists = {
        "Interests": [{"value": "攝影", "added": "2026-01-01", "ended": "2026-11-23"}],
        "Family_JSON": [{"relation": "妻子", "note": "日文系"}],
        "Followups_JSON": [{"item": "N1 考得如何？", "type": "ask", "status": "open"}, {"item": "完成的", "status": "done"}],
        "Interactions_JSON": [{"date": f"2026-0{m}-01", "topics": str(m)} for m in range(1, 6)],
    }
    items = ingest.known_items(lists)
    (interest,) = items["Interests"]
    assert interest["index"] == 0 and interest["active"] is False and interest["text"] == "攝影"
    assert interest["values"] == {"value": "攝影", "since": None}
    assert interest["fingerprint"]
    assert items["Family_JSON"][0]["values"] == {"relation": "妻子", "name": None, "birth_year": None, "note": "日文系"}
    assert [e["text"] for e in items["Followups_JSON"]] == ["N1 考得如何？"]
    assert [e["values"]["date"] for e in items["Interactions_JSON"]] == ["2026-05-01", "2026-04-01", "2026-03-01"]


def test_edits_of_the_original_content_are_checked():
    assert fill.normalize("edit_field", {"field": "Notes", "value": " 改過的 "}) == {"field": "Notes", "value": "改過的"}
    assert fill.normalize("edit_field", {"field": "Birthday", "value": "3/15"})["value"] == "--03-15"
    with pytest.raises(ValueError, match="不能在這裡修改"):
        fill.normalize("edit_field", {"field": "Display_Name", "value": "x"})
    data = {"field": "Interests", "index": 0, "fingerprint": "f", "values": {"value": "登山", "since": None},
            "expected": {"value": "攝影", "since": None}}
    assert fill.normalize("edit_item", data) == data
    with pytest.raises(ValueError, match="格式不對"):
        fill.normalize("edit_item", {**data, "index": "0"})


def test_editing_a_text_field_comes_before_appending_to_it():
    notes = FakeNoteService()
    proposals = _proposals(
        ("field", {"field": "Notes", "value": "新的", "mode": "append"}),
        ("edit_field", {"field": "Notes", "value": "改過的舊內容"}),
    )
    progress = fill.ApplyProgress(note_id=7)
    assert _run(fill.apply(notes, FakeCardService(), {"note_id": 7}, proposals, [0, 1], progress))
    assert notes.updates == [(7, {"Notes": "改過的舊內容<br>新的"})]


def test_several_appends_to_one_field_each_get_a_line():
    # 備註一件事一項（提示詞要求）：勾選的幾項依序接在後面，各自一行；沒勾的不寫
    notes = FakeNoteService()
    proposals = _proposals(
        ("field", {"field": "Notes", "value": "通勤搭丸之內線", "mode": "append"}),
        ("field", {"field": "Notes", "value": "沒過當兵", "mode": "append"}),
        ("field", {"field": "Notes", "value": "有駕照，但不常開", "mode": "append"}),
    )
    progress = fill.ApplyProgress(note_id=7)
    assert _run(fill.apply(notes, FakeCardService(), {"note_id": 7}, proposals, [0, 2], progress))
    assert notes.updates == [(7, {"Notes": "舊的<br>通勤搭丸之內線<br>有駕照，但不常開"})]


def test_original_entry_is_edited_with_edit_item(client, anki, draft_slot):
    with mock.patch.object(ingest, "ingest", mock.AsyncMock(return_value=_update_result())):
        draft = _start(client, draft_slot, {"text": "今天聊了登山", "note_id": 1})
    params = draft["result"]["params"]
    (original,) = params["known"]["items"]["Interests"]
    edit = {"kind": "edit_item", "data": {
        "field": "Interests", "index": original["index"], "fingerprint": original["fingerprint"],
        "values": {**original["values"], "value": "風景攝影"}, "expected": original["values"],
    }}
    proposals = [*params["proposals"], edit]
    response = client.post(f"/api/v1/drafts/{draft['id']}/apply", json={
        "version": draft["version"], "proposals": proposals, "selected": [len(proposals) - 1],
    })
    assert response.status_code == 200, response.json()
    assert response.json()["done"] is True
    assert "風景攝影" in anki.notes[1].fields["Interests"]["value"]
    assert "攝影" not in anki.notes[1].fields["Interests"]["value"].replace("風景攝影", "")
