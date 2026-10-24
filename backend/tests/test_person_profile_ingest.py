"""人脈卡「用一段話記錄」的測試（人脈卡計畫書 §3.13 P5）：整理結果的過濾、提示內容與 TG 流程。

Tests for the person profile "record in one paragraph" flow.
"""

from datetime import date
from unittest import mock

from app.bot.handlers import person_profile
from app.bot.handlers.person_profile import PersonStates
from app.bot.utils import person_profile_view as view
from app.core.exceptions import LLMServiceError
from app.schemas.llm.person_profile import LLMPersonIngestResult
from app.services import person_profile_ingest as ingest
from tests.test_bot_replies import NASTY, _assert_tg_html, _callback, _fsm, _message, _run, _sent
from tests.test_person_profile_bot import _card_service, _note, _note_service

TODAY = date(2026, 10, 24)


def _result(**overrides) -> LLMPersonIngestResult:
    data = {
        "interaction": {"date": "2026-10-23", "topics": "轉職、京都", "updates": "下個月考 N1", "place": "中野", "quote": ""},
        "followups": [
            {"item": "N1 考得如何？", "type": "ask", "due": "2026-12-07"},
            {"item": "介紹 Ken 給他", "type": "my_promise", "due": ""},
        ],
        "field_updates": [{"field": "Interests", "value": "登山", "mode": "append", "reason": "週末都去爬山"}],
    }
    data.update(overrides)
    return LLMPersonIngestResult.model_validate(data)


# =============================================================================
# 整理結果 → 建議
# =============================================================================


class TestProposals:
    def test_all_kinds_in_order(self):
        proposals = ingest.proposals_from(_result(), {"Display_Name": "田中さん"}, TODAY)

        assert [p.kind for p in proposals] == ["interaction", "followup", "followup", "field"]
        assert proposals[0].data == {"date": "2026-10-23", "topics": "轉職、京都", "updates": "下個月考 N1", "place": "中野"}
        assert proposals[1].data == {"item": "N1 考得如何？", "type": "ask", "due": "2026-12-07"}
        assert proposals[2].data == {"item": "介紹 Ken 給他", "type": "my_promise"}
        assert proposals[3].data == {"field": "Interests", "value": "登山", "mode": "append", "reason": "週末都去爬山"}

    def test_bad_interaction_date_becomes_today(self):
        result = _result(interaction={"date": "昨天", "topics": "轉職"})
        (first, *_) = ingest.proposals_from(result, {}, TODAY)
        assert first.data["date"] == "2026-10-24"

    def test_interaction_without_content_is_skipped(self):
        result = _result(interaction={"date": "2026-10-24", "topics": "", "place": "中野"}, followups=[], field_updates=[])
        assert ingest.proposals_from(result, {}, TODAY) == []

    def test_followup_with_bad_due_is_dropped(self):
        result = _result(followups=[{"item": "問考試", "type": "ask", "due": "下個月"}])
        assert [p.kind for p in ingest.proposals_from(result, {}, TODAY)] == ["interaction", "field"]

    def test_field_filters(self):
        result = _result(field_updates=[
            {"field": "Display_Name", "value": "田中", "mode": "replace", "reason": ""},  # 不開放
            {"field": "Nope", "value": "x", "mode": "replace", "reason": ""},  # 不存在
            {"field": "Company", "value": "Geekly", "mode": "replace", "reason": ""},  # 和目前相同
            {"field": "Interests", "value": "登山", "mode": "append", "reason": ""},  # 已經有了
            {"field": "Birthday", "value": "五月", "mode": "replace", "reason": ""},  # 格式不對
            {"field": "Title", "value": "部長", "mode": "append", "reason": ""},  # 單一值：改成取代
            {"field": "Hometown", "value": "  ", "mode": "replace", "reason": ""},  # 空的
        ])
        fields = {"Company": "Geekly", "Interests": "攝影<br>登山"}
        fields_proposed = [p.data for p in ingest.proposals_from(result, fields, TODAY) if p.kind == "field"]
        assert fields_proposed == [{"field": "Title", "value": "部長", "mode": "replace", "reason": ""}]

    def test_field_html(self):
        assert ingest.field_html("攝影", "登山 & <露營>", "append") == "攝影<br>登山 &amp; &lt;露營&gt;"
        assert ingest.field_html("", "登山", "append") == "登山"
        assert ingest.field_html("A 社", "B 社\n新創", "replace") == "B 社<br>新創"


class TestPrompt:
    def test_plain_text(self):
        assert ingest.plain_text("<ul><li>A &amp; B</li><li>C</li></ul>") == "A & B；C"
        assert ingest.plain_text("x" * 10, 4) == "xxxx…"

    def test_prompt_contains_context(self):
        prompt = ingest.build_prompt(
            {"Display_Name": "田中さん", "Company": "Geekly", "Interests": ""},
            {
                "Followups_JSON": [{"item": "N1 考得如何？", "status": "open"}, {"item": "寄履歷", "status": "done"}],
                "Interactions_JSON": [{"date": f"2026-0{m}-01", "topics": f"話題{m}"} for m in range(1, 6)],
            },
            "今天喝咖啡",
            TODAY,
        )
        assert "2026-10-24（星期六）" in prompt
        assert "公司（Company）：Geekly" in prompt and "（Interests）：" not in prompt
        assert "N1 考得如何？" in prompt and "寄履歷" not in prompt
        assert "話題5" in prompt and "話題3" in prompt and "話題2" not in prompt
        assert "今天喝咖啡" in prompt
        for name in ingest.UPDATABLE_FIELDS:
            assert f"`{name}`" in prompt
        assert "`Display_Name`" not in prompt

    def test_route_declares_llm_and_stt(self):
        assert ingest.INGEST_ROUTE.key == "person_profile"
        assert ingest.INGEST_ROUTE.capabilities == {"llm", "stt"}


class TestPreview:
    def test_preview_is_escaped(self):
        proposals = [
            {"kind": "interaction", "data": {"date": "2026-10-24", "topics": NASTY, "quote": NASTY}},
            {"kind": "followup", "data": {"item": NASTY, "type": "their_promise", "due": "2026-11-01"}},
            {"kind": "field", "data": {"field": "Interests", "value": NASTY, "mode": "append", "reason": NASTY}},
        ]
        text = view.ingest_preview(proposals, [True, False, True])
        _assert_tg_html(text)
        assert NASTY not in text
        assert "⬜ 2. 📌 對方答應的" in text and "（期限 2026-11-01）" in text
        assert "<b>興趣</b> 接在後面" in text


# =============================================================================
# TG 流程
# =============================================================================


def _press(data: str, state, note_service=None, card_service=None):
    query = _callback(data)
    _run(person_profile.on_person_button(query, state, note_service or _note_service(), card_service or _card_service()))
    return query


def _ingested(state, card_service, result=None, text="今天跟他喝咖啡"):
    """開始流程、送出一段話，回傳那則訊息。"""
    _press("pp:ing:5", state, card_service=card_service)
    message, status = _message(text)
    with mock.patch.object(ingest, "ingest", mock.AsyncMock(return_value=result or _result())) as llm:
        _run(person_profile.process_ingest_text(message, state, card_service))
    return message, status, llm


class TestIngestFlow:
    def test_start_asks_for_text(self):
        state = _fsm()
        query = _press("pp:ing:5", state)
        assert _run(state.get_state()) == PersonStates.ingest_text.state
        (text,) = _sent(query.message.answer)
        _assert_tg_html(text)
        assert "田中さん" in text

    def test_preview_toggle_and_save(self):
        state, note_service = _fsm(), _note_service()
        card_service = _card_service(_note(Interests="攝影"))
        message, status, llm = _ingested(state, card_service)

        assert llm.await_args.args[2] == "今天跟他喝咖啡"
        assert _run(state.get_state()) == PersonStates.ingest_confirm.state
        status.delete.assert_awaited_once()
        (preview,) = _sent(message.answer)
        assert "✅ 1." in preview and "✅ 4." in preview

        # 取消勾選第二項（N1 的待追蹤）
        query = _press("pp:ig:1", state)
        assert "⬜ 2." in _sent(query.message.edit_text)[0]

        _press("pp:igsave", state, note_service, card_service)
        actions = [(c.args[1], c.args[2]) for c in note_service.run_action.await_args_list]
        assert actions == [
            ("add_interaction", {"item": {"date": "2026-10-23", "topics": "轉職、京都", "updates": "下個月考 N1", "place": "中野"}}),
            ("add_followup", {"item": {"item": "介紹 Ken 給他", "type": "my_promise"}}),
        ]
        note_service.update_fields.assert_awaited_once_with("person_profile", 5, {"Interests": "攝影<br>登山"})
        assert _run(state.get_state()) is None

    def test_two_appends_to_one_field_are_combined(self):
        state, note_service = _fsm(), _note_service()
        card_service = _card_service(_note(Interests="攝影"))
        result = _result(followups=[], field_updates=[
            {"field": "Interests", "value": "登山", "mode": "append", "reason": ""},
            {"field": "Interests", "value": "露營", "mode": "append", "reason": ""},
        ])
        _ingested(state, card_service, result)
        _press("pp:igsave", state, note_service, card_service)
        note_service.update_fields.assert_awaited_once_with("person_profile", 5, {"Interests": "攝影<br>登山<br>露營"})

    def test_save_with_nothing_picked(self):
        state, note_service = _fsm(), _note_service()
        card_service = _card_service()
        _ingested(state, card_service, _result(followups=[], field_updates=[]))
        _press("pp:ig:0", state)
        query = _press("pp:igsave", state, note_service, card_service)
        assert "沒有勾選" in _sent(query.message.answer)[0]
        note_service.run_action.assert_not_awaited()
        assert _run(state.get_state()) == PersonStates.ingest_confirm.state

    def test_stale_buttons(self):
        state = _fsm()
        query = _press("pp:igsave", state)
        assert "已結束" in _sent(query.message.answer)[0]
        query = _press("pp:ig:0", state)
        assert "已結束" in _sent(query.message.answer)[0]

    def test_llm_failure_keeps_waiting(self):
        state, card_service = _fsm(), _card_service()
        _press("pp:ing:5", state, card_service=card_service)
        message, status = _message("今天喝咖啡")
        with mock.patch.object(ingest, "ingest", mock.AsyncMock(side_effect=LLMServiceError("額度用完 <x>"))):
            _run(person_profile.process_ingest_text(message, state, card_service))
        (text,) = _sent(status.edit_text)
        _assert_tg_html(text)
        assert "整理失敗" in text and "請再傳一次" in text
        assert _run(state.get_state()) == PersonStates.ingest_text.state

    def test_nothing_found(self):
        state, card_service = _fsm(), _card_service()
        result = _result(interaction={"date": "2026-10-24", "topics": ""}, followups=[], field_updates=[])
        _, status, _ = _ingested(state, card_service, result)
        assert "沒有整理出" in _sent(status.edit_text)[0]
        assert _run(state.get_state()) == PersonStates.ingest_text.state

    def test_voice_is_transcribed_then_ingested(self):
        state, card_service = _fsm(), _card_service()
        _press("pp:ing:5", state, card_service=card_service)
        message, status = _message()
        message.bot = mock.MagicMock()
        message.bot.get_file = mock.AsyncMock(return_value=mock.MagicMock(file_path="voice/1.oga"))
        message.bot.download_file = mock.AsyncMock()
        with (
            mock.patch.object(ingest, "transcribe", mock.AsyncMock(return_value="今天聊了轉職")) as stt,
            mock.patch.object(ingest, "ingest", mock.AsyncMock(return_value=_result())) as llm,
        ):
            _run(person_profile.process_ingest_voice(message, state, card_service))
        stt.assert_awaited_once()
        assert llm.await_args.args[2] == "今天聊了轉職"
        assert any("聽到：今天聊了轉職" in t for t in _sent(status.edit_text))
        assert _run(state.get_state()) == PersonStates.ingest_confirm.state

    def test_voice_transcription_failure(self):
        state, card_service = _fsm(), _card_service()
        _press("pp:ing:5", state, card_service=card_service)
        message, status = _message()
        message.bot = mock.MagicMock()
        message.bot.get_file = mock.AsyncMock(return_value=mock.MagicMock(file_path="voice/1.oga"))
        message.bot.download_file = mock.AsyncMock()
        from app.core.exceptions import STTServiceError

        with mock.patch.object(ingest, "transcribe", mock.AsyncMock(side_effect=STTServiceError("沒有設定"))):
            _run(person_profile.process_ingest_voice(message, state, card_service))
        assert "語音轉文字失敗" in _sent(status.edit_text)[-1]
        assert _run(state.get_state()) == PersonStates.ingest_text.state

    def test_summary_has_ingest_button(self):
        buttons = [b.callback_data for row in person_profile.summary_keyboard(5).inline_keyboard for b in row]
        assert "pp:ing:5" in buttons
