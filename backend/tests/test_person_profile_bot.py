"""人脈卡 TG 流程的測試（人脈卡計畫書 §3.12、§3.13）。

Tests for the person profile Telegram flows.
"""

import json
from datetime import date
from unittest import mock

import pytest

from app.bot.handlers import commands, person_profile
from app.bot.handlers.person_profile import PersonStates
from app.bot.utils import person_profile_view as view
from app.bot.utils.deep_link_parser import DeepLinkParser
from app.schemas.person_profile import PERSON_JSON_ITEMS, PERSON_NOTE_TYPE, PersonCreateParams, item_fingerprint
from app.schemas.tg.deep_link import PersonLinkAction
from tests.test_bot_replies import NASTY, _assert_tg_html, _callback, _fsm, _message, _run, _sent

TODAY = date(2026, 10, 24)
FOLLOWUPS = [
    {"added": "2026-10-01", "item": "N1 考得如何？", "type": "ask", "due": "2026-12-07", "status": "open"},
    {"added": "2026-10-01", "item": "介紹 Ken 給他", "type": "my_promise", "status": "open"},
    {"added": "2026-06-07", "item": "寄履歷範本", "type": "my_promise", "status": "done"},
]


def _fields(**values: str) -> dict[str, str]:
    return {"Display_Name": "田中さん", "Card_ID": "PP_1", "TG_Bot": "bot", **values}


def _note(note_id: int = 5, **values: str) -> dict:
    fields = _fields(**values)
    return {"noteId": note_id, "modelName": PERSON_NOTE_TYPE, "fields": {k: {"value": v} for k, v in fields.items()}}


def _card_service(note: dict | None = None):
    service = mock.MagicMock()
    service.get_note = mock.AsyncMock(return_value=note or _note())
    service.update_note_fields = mock.AsyncMock()
    return service


def _note_service():
    service = mock.MagicMock()
    service.run_action = mock.AsyncMock(return_value=None)
    service.update_fields = mock.AsyncMock(return_value={"sync_warning": None})
    service.create = mock.AsyncMock(return_value={"note_ids": [5], "deck": "人脈", "sync_warning": None})
    return service


def _anki(note_ids=(5,)):
    client = mock.MagicMock()
    client.sync = mock.AsyncMock()
    client.find_notes = mock.AsyncMock(return_value=list(note_ids))
    return client


# =============================================================================
# 顯示與輸入規則（純函式）
# =============================================================================


class TestView:
    def test_search_query_escapes_and_covers_name_fields(self):
        query = view.search_query(' 田中*"x" ')
        assert query.startswith('note:"Person\\_Profile\\_Dark" (')
        for name in view.NAME_FIELDS:
            assert f'"{name}:*田中\\*\\"x\\"*"' in query

    def test_summary_lists_must_see(self):
        lists = {
            "Followups_JSON": FOLLOWUPS,
            "Interactions_JSON": [{"date": "2026-10-01", "topics": "轉職", "updates": "要考 N1"},
                                  {"date": "2026-06-07", "topics": "第一次見面"}],
            "Key_Dates_JSON": [{"date": "2026-12-07", "label": "N1 考試"}, {"date": "2023-05-20", "label": "紀念日", "yearly": True}],
        }
        text = view.summary_text(_fields(Company="Geekly", Dietary="不吃生魚", Last_Contact="2026-10-01"), lists, TODAY)
        _assert_tg_html(text)
        assert "<b>下次要問</b>：N1 考得如何？" in text
        assert "<b>我答應的</b>：介紹 Ken 給他" in text and "寄履歷範本" not in text
        assert "<b>飲食</b>：不吃生魚" in text
        assert "<b>上次</b>（2026-10-01）：轉職｜要考 N1" in text
        assert "N1 考試（12/07，還有 44 天）" in text and "紀念日" not in text
        assert "上次聯絡 23 天前" in text

    def test_summary_is_escaped(self):
        text = view.summary_text(
            _fields(Display_Name=NASTY, Dietary=NASTY),
            {"Followups_JSON": [{"item": NASTY, "type": "ask", "status": "open"}]},
            TODAY,
        )
        _assert_tg_html(text)
        assert NASTY not in text

    def test_empty_summary(self):
        assert "還沒有要注意的事" in view.summary_text(_fields(), {}, TODAY)

    def test_full_text_and_item_lines_are_escaped(self):
        text = view.full_text(_fields(Interests=NASTY, Work_Detail="<ul><li>A</li></ul>"), {"Gifts_JSON": [{"item": NASTY}]})
        _assert_tg_html(text)
        for field_name in PERSON_JSON_ITEMS:
            _assert_tg_html(view.item_line(field_name, {"item": NASTY, "topics": NASTY, "label": NASTY}))

    @pytest.mark.parametrize(
        ("typed", "expected"),
        [("2026-10-01", "2026-10-01"), ("2026/10/1", "2026-10-01"), ("10-01", "2026-10-01"), ("10/1", "2026-10-01"),
         ("2/30", None), ("明天", None)],
    )
    def test_parse_date_input(self, typed, expected):
        assert view.parse_date_input(typed, TODAY) == expected

    def test_add_steps_match_item_models(self):
        for field_name, steps in view.ADD_STEPS.items():
            model = PERSON_JSON_ITEMS[field_name][0]
            for step in steps:
                assert step.key == "_followups" or step.key in model.model_fields, (field_name, step.key)
            required = {name for name, info in model.model_fields.items() if info.is_required()}
            assert required <= {s.key for s in steps if s.required}, field_name

    def test_editable_fields_follow_create_params(self):
        assert view.EDITABLE_FIELDS == tuple(PersonCreateParams.model_fields)
        grouped = [name for _, names in view.FIELD_GROUPS for name in names]
        assert sorted(grouped) == sorted(view.EDITABLE_FIELDS)

    def test_callback_data_fits_telegram_limit(self):
        """callback data 上限 64 bytes：用最長的筆記 ID、序號與指紋。"""
        note_id, fp = 1792801212719, item_fingerprint({"item": "x" * 50, "date": "2026-10-01"})
        for data in (f"pp:delok:{note_id}:I:999:{fp}", f"pp:st:{note_id}:999:{fp}:d",
                     f"pp:ef:{note_id}:{len(view.EDITABLE_FIELDS) - 1}"):
            assert len(data.encode()) <= 64, data

    def test_text_to_field_html(self):
        assert view.text_to_field_html(" a<b>\nc ") == "a&lt;b&gt;<br>c"


class TestDeepLinks:
    @pytest.mark.parametrize(
        ("payload", "verb"),
        [("pview-Display_Name-none-PP_1", "view"), ("padd-Gifts_JSON-none-PP_1", "add"),
         ("pdel-Followups_JSON-0_14vynru-PP_1", "del"), ("pdone-Followups_JSON-1_r1tbnf-PP_1", "done"),
         ("pphoto-Photo-none-PP_1", "photo")],
    )
    def test_parse(self, payload, verb):
        action = DeepLinkParser.parse(payload)
        assert isinstance(action, PersonLinkAction) and action.verb == verb and action.card_id == "PP_1"

    def test_start_dispatches_to_person_flow(self):
        message, _ = _message("/start pdone-Followups_JSON-1_r1tbnf-PP_1")
        note_service = _note_service()
        _run(commands.command_start_handler(
            message, _anki(), mock.MagicMock(), _card_service(), state=_fsm(), note_service=note_service
        ))
        note_service.run_action.assert_awaited_once_with(
            5, "set_followup_status", {"index": 1, "fingerprint": "r1tbnf", "status": "done"}
        )
        assert "已標成完成" in _sent(message.answer)[0]

    def test_unknown_card_id(self):
        message, _ = _message("/start pview-Display_Name-none-PP_404")
        _run(commands.command_start_handler(
            message, _anki(note_ids=()), mock.MagicMock(), _card_service(), state=_fsm(), note_service=_note_service()
        ))
        assert "找不到這張卡片" in _sent(message.answer)[0]

    def test_delete_link_asks_for_confirmation(self):
        note = _note(Followups_JSON=json.dumps(FOLLOWUPS))
        message, _ = _message("/start pdel-Followups_JSON-0_14vynru-PP_1")
        note_service = _note_service()
        _run(commands.command_start_handler(
            message, _anki(), mock.MagicMock(), _card_service(note), state=_fsm(), note_service=note_service
        ))
        note_service.run_action.assert_not_awaited()
        (text,) = _sent(message.answer)
        _assert_tg_html(text)
        assert "要刪除這一筆待追蹤嗎" in text and "N1 考得如何" in text
        markup = message.answer.await_args.kwargs["reply_markup"]
        assert markup.inline_keyboard[0][0].callback_data == "pp:delok:5:F:0:14vynru"

    def test_stale_delete_link_is_refused(self):
        note = _note(Followups_JSON=json.dumps(FOLLOWUPS[1:]))
        message, _ = _message("/start pdel-Followups_JSON-0_14vynru-PP_1")
        _run(commands.command_start_handler(
            message, _anki(), mock.MagicMock(), _card_service(note), state=_fsm(), note_service=_note_service()
        ))
        assert "已經變了或不在了" in _sent(message.answer)[0]


# =============================================================================
# 流程
# =============================================================================


def _press(data: str, state, note_service, card_service):
    query = _callback(data)
    _run(person_profile.on_person_button(query, state, note_service, card_service))
    return query


class TestAddFlow:
    def test_add_followup(self):
        state, note_service, card_service = _fsm(), _note_service(), _card_service()
        message, _ = _message()
        _run(person_profile.start_add(message, state, 5, "Followups_JSON"))
        _press("pp:ch:1", state, note_service, card_service)  # 我答應的
        typed, _ = _message("介紹 Ken 給他")
        _run(person_profile.process_add_text(typed, state))
        _press("pp:skip", state, note_service, card_service)  # 期限略過
        assert _run(state.get_state()) == PersonStates.add_confirm.state
        _press("pp:save", state, note_service, card_service)
        note_service.run_action.assert_awaited_once_with(
            5, "add_followup", {"item": {"type": "my_promise", "item": "介紹 Ken 給他"}}
        )
        assert _run(state.get_state()) is None

    def test_add_interaction_with_followups(self):
        state, note_service, card_service = _fsm(), _note_service(), _card_service()
        message, _ = _message()
        _run(person_profile.start_add(message, state, 5, "Interactions_JSON"))
        for text in ("10/1", "轉職、京都旅行"):
            typed, _ = _message(text)
            _run(person_profile.process_add_text(typed, state))
        for _ in range(3):  # 近況、地點、一句話都略過
            _press("pp:skip", state, note_service, card_service)
        typed, _ = _message("N1 考得如何？\n\n下次一起登山")
        _run(person_profile.process_add_text(typed, state))
        _press("pp:save", state, note_service, card_service)
        calls = note_service.run_action.await_args_list
        assert calls[0].args == (5, "add_interaction", {"item": {"date": "2026-10-01", "topics": "轉職、京都旅行"}})
        assert [c.args[2]["item"]["item"] for c in calls[1:]] == ["N1 考得如何？", "下次一起登山"]

    def test_bad_date_is_asked_again(self):
        state = _fsm()
        message, _ = _message()
        _run(person_profile.start_add(message, state, 5, "Key_Dates_JSON"))
        typed, _ = _message("N1 考試")
        _run(person_profile.process_add_text(typed, state))
        bad, _ = _message("下個月")
        _run(person_profile.process_add_text(bad, state))
        assert "日期看不懂" in _sent(bad.answer)[0]
        assert _run(state.get_data())["step"] == 1

    def test_save_after_cancel_does_nothing(self):
        state, note_service, card_service = _fsm(), _note_service(), _card_service()
        query = _press("pp:save", state, note_service, card_service)
        note_service.run_action.assert_not_awaited()
        assert "已結束" in _sent(query.message.answer)[0]


class TestEditFlow:
    def _start(self, field_name: str, current: str = ""):
        state, note_service = _fsm(), _note_service()
        card_service = _card_service(_note(**{field_name: current}))
        index = view.EDITABLE_FIELDS.index(field_name)
        _press(f"pp:ef:5:{index}", state, note_service, card_service)
        return state, note_service, card_service

    def test_replace_single_line_field(self):
        state, note_service, card_service = self._start("Company", "Old")
        typed, _ = _message("Geekly & Co")
        _run(person_profile.process_edit_value(typed, state, note_service, card_service))
        note_service.update_fields.assert_awaited_once_with("person_profile", 5, {"Company": "Geekly &amp; Co"})

    def test_rejects_malformed_date(self):
        state, note_service, card_service = self._start("Met_Date")
        typed, _ = _message("昨天")
        _run(person_profile.process_edit_value(typed, state, note_service, card_service))
        note_service.update_fields.assert_not_awaited()
        assert "格式不對" in _sent(typed.answer)[0]

    def test_append_to_text_section(self):
        state, note_service, card_service = self._start("Interests", "登山")
        typed, _ = _message("精釀啤酒")
        _run(person_profile.process_edit_value(typed, state, note_service, card_service))
        assert _run(state.get_state()) == PersonStates.edit_mode.state
        _press("pp:em:a", state, note_service, card_service)
        note_service.update_fields.assert_awaited_once_with("person_profile", 5, {"Interests": "登山<br>精釀啤酒"})


class TestSummaryAndCreate:
    def test_summary_backfills_missing_ids(self):
        card_service = _card_service(_note(Card_ID="", TG_Bot=""))
        message, _ = _message()
        with mock.patch.object(person_profile.settings, "TG_BOT_USERNAME", "@FluencyBot"):
            _run(person_profile.send_summary(message, card_service, 5))
        (call,) = card_service.update_note_fields.await_args_list
        changes = call.args[1]
        assert changes["Card_ID"].startswith("PP_") and changes["TG_Bot"] == "FluencyBot"
        _assert_tg_html(_sent(message.answer)[0])

    def test_create_from_search_miss(self):
        state, note_service, card_service = _fsm(), _note_service(), _card_service()
        message, _ = _message("/p 佐藤")
        command = mock.MagicMock(args="佐藤")
        _run(person_profile.command_person(message, command, state, _anki(note_ids=()), card_service))
        assert "找不到「佐藤」" in _sent(message.answer)[0]
        _press("pp:new", state, note_service, card_service)
        typed, _ = _message("日語會話班")
        _run(person_profile.process_create_context(typed, state, note_service, card_service))
        note_service.create.assert_awaited_once()
        params = note_service.create.await_args.kwargs["params"]
        assert params["Display_Name"] == "佐藤" and params["Met_Where"] == "日語會話班"
