"""人脈卡 TG 流程的測試（人脈卡計畫書 §3.12、§3.13；登錄日計畫書 §4.4、§4.5）。

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
from app.schemas.person_profile import (
    PERSON_HISTORY_FIELDS,
    PERSON_JSON_ITEMS,
    PERSON_NOTE_TYPE,
    PersonCreateParams,
    item_fingerprint,
)
from app.schemas.tg.deep_link import PersonLinkAction
from tests.test_bot_replies import NASTY, _assert_tg_html, _callback, _fsm, _message, _run, _sent

TODAY = date(2026, 10, 24)
FOLLOWUPS = [
    {"added": "2026-10-01", "item": "N1 考得如何？", "type": "ask", "due": "2026-12-07", "status": "open"},
    {"added": "2026-10-01", "item": "介紹 Ken 給他", "type": "my_promise", "status": "open"},
    {"added": "2026-06-07", "item": "寄履歷範本", "type": "my_promise", "status": "done"},
]


def _entries(*values: str, **dates: str) -> list[dict[str, str]]:
    """會變的欄位的清單（都是有效的）。History entries."""
    return [{"value": v, "added": dates.get("added", "2026-01-01"), "ended": ""} for v in values]


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
            "Dietary": _entries("不吃生魚") + [{"value": "不喝酒", "added": "", "ended": "2026-05-01"}],
            "Company": [{"company": "Geekly", "title": "エンジニア", "added": "", "ended": ""},
                        {"company": "舊公司", "title": "", "added": "", "ended": "2025-01-01"}],
            "Relationship": _entries("同事"),
        }
        text = view.summary_text(_fields(Last_Contact="2026-10-01"), lists, TODAY)
        _assert_tg_html(text)
        # 會變的欄位只顯示有效的
        assert "Geekly・エンジニア｜同事｜上次聯絡" in text and "舊公司" not in text and "不喝酒" not in text
        assert "<b>下次要問</b>：N1 考得如何？" in text
        assert "<b>我答應的</b>：介紹 Ken 給他" in text and "寄履歷範本" not in text
        assert "<b>飲食</b>：不吃生魚" in text
        assert "<b>上次</b>（2026-10-01）：轉職｜要考 N1" in text
        assert "N1 考試（12/07，還有 44 天）" in text and "紀念日" not in text
        assert "上次聯絡 23 天前" in text

    def test_summary_is_escaped(self):
        text = view.summary_text(
            _fields(Display_Name=NASTY),
            {"Followups_JSON": [{"item": NASTY, "type": "ask", "status": "open"}], "Dietary": _entries(NASTY),
             "Company": [{"company": NASTY, "title": NASTY}]},
            TODAY,
        )
        _assert_tg_html(text)
        assert NASTY not in text

    def test_broken_dietary_is_flagged_in_the_summary(self):
        """飲食限制（過敏）不能因為格式壞了就從見面前必看消失。"""
        text = view.summary_text(_fields(Dietary="不吃生魚"), {"Dietary": []}, TODAY)
        assert "<b>飲食</b>：⚠️ 資料格式壞了" in text
        assert "資料格式壞了" in view.full_text(_fields(Dietary="不吃生魚"), {})

    def test_empty_summary(self):
        assert "還沒有要注意的事" in view.summary_text(_fields(), {}, TODAY)

    def test_full_text_and_item_lines_are_escaped(self):
        text = view.full_text(_fields(Notes="<ul><li>A</li></ul>"),
                              {"Gifts_JSON": [{"item": NASTY}], "Interests": _entries(NASTY)})
        _assert_tg_html(text)
        for field_name in [*PERSON_JSON_ITEMS, *PERSON_HISTORY_FIELDS]:
            for item in ({"item": NASTY, "topics": NASTY, "label": NASTY, "value": NASTY, "company": NASTY},
                         {"value": NASTY, "added": NASTY, "ended": NASTY}):
                _assert_tg_html(view.item_line(field_name, item))

    def test_full_text_shows_current_history_only(self):
        lists = {"Interests": _entries("登山") + [{"value": "攝影", "added": "", "ended": "2026-01-01"}]}
        text = view.full_text(_fields(), lists)
        assert "• 興趣：登山" in text and "攝影" not in text

    def test_history_item_lines(self):
        assert view.item_line("Residence", {"value": "東京", "added": "2026-01-01", "ended": ""}) == "東京（2026-01-01 登錄）"
        assert view.item_line("Residence", {"value": "大阪", "added": "", "ended": "2025-03-01"}) == (
            "<s>大阪</s>（日期不明，2025-03-01 劃掉）"
        )
        assert view.item_line("Company", {"company": "Geekly", "title": "PM", "added": ""}) == "Geekly・PM（日期不明）"

    @pytest.mark.parametrize(
        ("typed", "expected"),
        [("2026-10-01", "2026-10-01"), ("2026/10/1", "2026-10-01"), ("10-01", "2026-10-01"), ("10/1", "2026-10-01"),
         ("2/30", None), ("明天", None)],
    )
    def test_parse_date_input(self, typed, expected):
        assert view.parse_date_input(typed, TODAY) == expected

    def test_add_steps_match_item_models(self):
        assert set(view.ADD_STEPS) == {*PERSON_JSON_ITEMS, *PERSON_HISTORY_FIELDS}
        for field_name, steps in view.ADD_STEPS.items():
            model = PERSON_JSON_ITEMS[field_name][0] if field_name in PERSON_JSON_ITEMS else PERSON_HISTORY_FIELDS[field_name].model
            # 會變的欄位不問日期（D6）
            assert not {"added", "ended"} & {s.key for s in steps}
            for step in steps:
                assert step.key == "_followups" or step.key in model.model_fields, (field_name, step.key)
            required = {name for name, info in model.model_fields.items() if info.is_required()}
            assert required <= {s.key for s in steps if s.required}, field_name

    def test_editable_fields_are_the_text_fields(self):
        """改資料只有文字欄位：會變的欄位走清單操作，Title 不再使用（§4.5）。"""
        expected = tuple(n for n in PersonCreateParams.model_fields if n not in PERSON_HISTORY_FIELDS and n != "Title")
        assert view.EDITABLE_FIELDS == expected
        grouped = [name for _, names in view.FIELD_GROUPS for name in names]
        assert sorted(grouped) == sorted(view.EDITABLE_FIELDS)

    def test_every_list_has_a_code_and_sections_point_to_lists(self):
        assert set(view.LIST_CODES.values()) == {*PERSON_JSON_ITEMS, *PERSON_HISTORY_FIELDS}
        for names in view.SECTION_FIELDS.values():
            assert all(name in view.CODE_OF_LIST for name in names)
        displayed = [name for _, names in view.DISPLAY_GROUPS for name in names]
        assert set(PERSON_HISTORY_FIELDS) <= set(displayed)

    def test_callback_data_fits_telegram_limit(self):
        """callback data 上限 64 bytes：用最長的筆記 ID、序號與指紋。"""
        note_id, fp = 1792801212719, item_fingerprint({"item": "x" * 50, "date": "2026-10-01"})
        for data in (f"pp:delok:{note_id}:SN:999:{fp}", f"pp:st:{note_id}:999:{fp}:d",
                     f"pp:hx:{note_id}:SN:999:{fp}:e", f"pp:ef:{note_id}:{len(view.EDITABLE_FIELDS) - 1}"):
            assert len(data.encode()) <= 64, data

    def test_card_links_fit_telegram_start_limit(self):
        """深連結的 start 參數上限 64 字元：用最長的欄位名、三位數序號、最長的指紋與 Card_ID。"""
        from app.services.task_handlers.person_profile_handler import new_card_id

        longest = max([*view.CODE_OF_LIST, *view.SECTION_FIELDS], key=len)
        for verb in ("pdel", "pend", "pdone", "padd", "psec"):
            payload = f"{verb}-{longest}-999_zzzzzzz-{new_card_id()}"
            assert len(payload) <= 64, payload
            assert isinstance(DeepLinkParser.parse(payload), PersonLinkAction)

    def test_person_verbs_match_the_link_model(self):
        from typing import get_args

        from app.bot.utils.deep_link_parser import PERSON_VERBS

        assert set(PERSON_VERBS.values()) == set(get_args(PersonLinkAction.model_fields["verb"].annotation))

    def test_text_to_field_html(self):
        assert view.text_to_field_html(" a<b>\nc ") == "a&lt;b&gt;<br>c"


class TestDeepLinks:
    @pytest.mark.parametrize(
        ("payload", "verb"),
        [("pview-Display_Name-none-PP_1", "view"), ("padd-Gifts_JSON-none-PP_1", "add"),
         ("pdel-Followups_JSON-0_14vynru-PP_1", "del"), ("pdone-Followups_JSON-1_r1tbnf-PP_1", "done"),
         ("pend-Interests-1_1ks3weh-PP_1", "end"), ("psec-work-none-PP_1", "section"),
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

    def test_end_link_ends_the_entry(self):
        message, _ = _message("/start pend-Interests-1_1ks3weh-PP_1")
        note_service = _note_service()
        _run(commands.command_start_handler(
            message, _anki(), mock.MagicMock(), _card_service(), state=_fsm(), note_service=note_service
        ))
        note_service.run_action.assert_awaited_once_with(
            5, "set_history_status", {"field": "Interests", "index": 1, "fingerprint": "1ks3weh", "ended": True}
        )
        assert "已劃掉" in _sent(message.answer)[0]
        undo = message.answer.await_args.kwargs["reply_markup"].inline_keyboard[0][0]
        assert undo.callback_data == "pp:hx:5:IN:1:1ks3weh:o"

    def test_end_link_only_for_history_fields(self):
        message, _ = _message("/start pend-Followups_JSON-1_r1tbnf-PP_1")
        note_service = _note_service()
        _run(commands.command_start_handler(
            message, _anki(), mock.MagicMock(), _card_service(), state=_fsm(), note_service=note_service
        ))
        note_service.run_action.assert_not_awaited()
        assert "不支援" in _sent(message.answer)[0]

    def test_section_link_asks_which_field(self):
        message, _ = _message("/start psec-work-none-PP_1")
        _run(commands.command_start_handler(
            message, _anki(), mock.MagicMock(), _card_service(), state=_fsm(), note_service=_note_service()
        ))
        assert "要新增哪一項" in _sent(message.answer)[0]
        rows = message.answer.await_args.kwargs["reply_markup"].inline_keyboard
        assert [r[0].callback_data for r in rows[:-1]] == ["pp:add:5:CO", "pp:add:5:WK", "pp:add:5:ED"]
        assert rows[0][0].text == "+ 職位"

    def test_unknown_section_link(self):
        message, _ = _message("/start psec-nope-none-PP_1")
        _run(commands.command_start_handler(
            message, _anki(), mock.MagicMock(), _card_service(), state=_fsm(), note_service=_note_service()
        ))
        assert "不支援" in _sent(message.answer)[0]

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
    def test_stale_choice_keeps_the_buttons(self):
        """失效的選項按鈕：說明失效，但不拿掉這一步的按鈕，也不離開這一步。"""
        state, note_service, card_service = _fsm(), _note_service(), _card_service()
        message, _ = _message()
        _run(person_profile.start_add(message, state, 5, "Followups_JSON"))
        query = _press("pp:ch:9", state, note_service, card_service)
        assert "按鈕已失效" in _sent(query.message.answer)[0]
        query.message.edit_reply_markup.assert_not_awaited()
        assert _run(state.get_state()) == PersonStates.add_step.state

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

    def test_add_history_asks_only_the_value(self):
        """D6：會變的欄位只問內容，日期由系統填；單值欄位預覽說明會劃掉原本的。"""
        state, note_service, card_service = _fsm(), _note_service(), _card_service()
        message, _ = _message()
        _run(person_profile.start_add(message, state, 5, "Residence"))
        typed, _ = _message("橫濱")
        _run(person_profile.process_add_text(typed, state))
        assert _run(state.get_state()) == PersonStates.add_confirm.state
        preview = _sent(typed.answer)[0]
        _assert_tg_html(preview)
        assert "登錄日：今天" in preview and "自動劃掉" in preview
        _press("pp:save", state, note_service, card_service)
        note_service.run_action.assert_awaited_once_with(
            5, "add_history", {"field": "Residence", "item": {"value": "橫濱"}}
        )

    def test_add_position(self):
        state, note_service, card_service = _fsm(), _note_service(), _card_service()
        message, _ = _message()
        _run(person_profile.start_add(message, state, 5, "Company"))
        typed, _ = _message("Sony")
        _run(person_profile.process_add_text(typed, state))
        _press("pp:skip", state, note_service, card_service)  # 職稱略過
        _press("pp:save", state, note_service, card_service)
        note_service.run_action.assert_awaited_once_with(5, "add_history", {"field": "Company", "item": {"company": "Sony"}})

    def test_add_closeness_by_button(self):
        state, note_service, card_service = _fsm(), _note_service(), _card_service()
        message, _ = _message()
        _run(person_profile.start_add(message, state, 5, "Closeness"))
        _press("pp:ch:3", state, note_service, card_service)  # 選項的序號從 0 起算：第 4 個是「4」
        _press("pp:save", state, note_service, card_service)
        note_service.run_action.assert_awaited_once_with(5, "add_history", {"field": "Closeness", "item": {"value": "4"}})

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


class TestHistoryList:
    def test_list_shows_all_with_end_and_restore_buttons(self):
        items = _entries("登山") + [{"value": "攝影", "added": "2025-01-01", "ended": "2026-05-01"}]
        state, note_service = _fsm(), _note_service()
        card_service = _card_service(_note(Interests=json.dumps(items)))
        query = _press("pp:ls:5:IN", state, note_service, card_service)
        (text,) = _sent(query.message.answer)
        _assert_tg_html(text)
        assert "1. 登山（2026-01-01 登錄）" in text and "2. <s>攝影</s>（2025-01-01 登錄，2026-05-01 劃掉）" in text
        rows = query.message.answer.await_args.kwargs["reply_markup"].inline_keyboard
        assert [b.text for b in rows[0]] == ["刪 1", "劃掉 1"]
        assert rows[1][1].callback_data == f"pp:hx:5:IN:1:{item_fingerprint(items[1])}:o"

    def test_end_button(self):
        state, note_service, card_service = _fsm(), _note_service(), _card_service()
        query = _press("pp:hx:5:RS:0:abc:e", state, note_service, card_service)
        note_service.run_action.assert_awaited_once_with(
            5, "set_history_status", {"field": "Residence", "index": 0, "fingerprint": "abc", "ended": True}
        )
        assert "現居地" in _sent(query.message.answer)[0]

    def test_restore_button(self):
        state, note_service, card_service = _fsm(), _note_service(), _card_service()
        _press("pp:hx:5:RS:0:abc:o", state, note_service, card_service)
        assert note_service.run_action.await_args.args[2]["ended"] is False

    @pytest.mark.parametrize("data", ["pp:hx:5:F:0:abc:e", "pp:hx:5:RS:0:abc:x"])
    def test_bad_end_buttons_are_stale(self, data):
        state, note_service, card_service = _fsm(), _note_service(), _card_service()
        query = _press(data, state, note_service, card_service)
        note_service.run_action.assert_not_awaited()
        assert "按鈕已失效" in _sent(query.message.answer)[0]

    def test_broken_list_is_not_shown_as_empty(self):
        """還沒轉換的舊文字：清單頁與選單標出格式壞了，不要顯示成「還沒有」。"""
        state, note_service = _fsm(), _note_service()
        card_service = _card_service(_note(Interests="登山、攝影"))
        query = _press("pp:ls:5:IN", state, note_service, card_service)
        (text,) = _sent(query.message.answer)
        assert "資料格式壞了" in text and "還沒有" not in text
        query = _press("pp:lists:5", state, note_service, card_service)
        buttons = [b for row in query.message.answer.await_args.kwargs["reply_markup"].inline_keyboard for b in row]
        assert any(b.text == "興趣（格式壞了）" for b in buttons)

    def test_end_button_for_a_non_history_list_is_stale(self):
        state, note_service, card_service = _fsm(), _note_service(), _card_service()
        query = _press("pp:hx:5:F:0:abc:e", state, note_service, card_service)
        note_service.run_action.assert_not_awaited()
        assert "按鈕已失效" in _sent(query.message.answer)[0]

    def test_lists_menu_counts_current_entries(self):
        items = _entries("登山", "咖啡") + [{"value": "攝影", "added": "", "ended": "2026-05-01"}]
        state, note_service = _fsm(), _note_service()
        query = _press("pp:lists:5", state, note_service, _card_service(_note(Interests=json.dumps(items))))
        buttons = [b for row in query.message.answer.await_args.kwargs["reply_markup"].inline_keyboard for b in row]
        assert any(b.text == "興趣（2）" and b.callback_data == "pp:ls:5:IN" for b in buttons)
        assert any(b.text == "職位（0）" for b in buttons)

    def test_delete_confirmation_suggests_ending(self):
        items = _entries("東京")
        state, note_service = _fsm(), _note_service()
        fp = item_fingerprint(items[0])
        query = _press(f"pp:del:5:RS:0:{fp}", state, note_service, _card_service(_note(Residence=json.dumps(items))))
        (text,) = _sent(query.message.answer)
        _assert_tg_html(text)
        assert "要刪除這一筆現居地嗎" in text and "劃掉" in text


class TestEditFlow:
    def _start(self, field_name: str, current: str = ""):
        state, note_service = _fsm(), _note_service()
        card_service = _card_service(_note(**{field_name: current}))
        index = view.EDITABLE_FIELDS.index(field_name)
        _press(f"pp:ef:5:{index}", state, note_service, card_service)
        return state, note_service, card_service

    def test_replace_single_line_field(self):
        state, note_service, card_service = self._start("Hometown", "Old")
        typed, _ = _message("福岡 & 博多")
        _run(person_profile.process_edit_value(typed, state, note_service, card_service))
        note_service.update_fields.assert_awaited_once_with("person_profile", 5, {"Hometown": "福岡 &amp; 博多"})

    def test_rejects_malformed_date(self):
        state, note_service, card_service = self._start("Met_Date")
        typed, _ = _message("昨天")
        _run(person_profile.process_edit_value(typed, state, note_service, card_service))
        note_service.update_fields.assert_not_awaited()
        assert "格式不對" in _sent(typed.answer)[0]

    def test_append_to_plain_text_section(self):
        state, note_service, card_service = self._start("Notes", "第一次見面很緊張")
        typed, _ = _message("其實很健談")
        _run(person_profile.process_edit_value(typed, state, note_service, card_service))
        assert _run(state.get_state()) == PersonStates.edit_mode.state
        _press("pp:em:a", state, note_service, card_service)
        note_service.update_fields.assert_awaited_once_with(
            "person_profile", 5, {"Notes": "第一次見面很緊張<br>其實很健談"}
        )


class TestSummaryAndCreate:
    def test_summary_backfills_missing_ids(self):
        card_service = _card_service(_note(Card_ID="", TG_Bot=""))
        card_service.anki_client.sync = mock.AsyncMock()
        message, _ = _message()
        with mock.patch.object(person_profile.settings, "TG_BOT_USERNAME", "@FluencyBot"):
            _run(person_profile.send_summary(message, card_service, 5))
        (call,) = card_service.update_note_fields.await_args_list
        changes = call.args[1]
        assert changes["Card_ID"].startswith("PP_") and changes["TG_Bot"] == "FluencyBot"
        # 補上的 ID 要同步到 AnkiWeb，手機上的卡面才有 TG 按鈕
        card_service.anki_client.sync.assert_awaited_once()
        _assert_tg_html(_sent(message.answer)[0])

    def test_summary_with_ids_does_not_sync(self):
        card_service = _card_service()
        card_service.anki_client.sync = mock.AsyncMock()
        message, _ = _message()
        _run(person_profile.send_summary(message, card_service, 5))
        card_service.update_note_fields.assert_not_awaited()
        card_service.anki_client.sync.assert_not_awaited()

    def test_backfill_survives_sync_failure(self):
        from app.infrastructure.anki.client import AnkiConnectError

        card_service = _card_service(_note(Card_ID=""))
        card_service.anki_client.sync = mock.AsyncMock(side_effect=AnkiConnectError("offline"))
        message, _ = _message()
        _run(person_profile.send_summary(message, card_service, 5))
        assert "田中さん" in _sent(message.answer)[0]

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
