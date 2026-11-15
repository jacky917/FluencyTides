"""人脈卡「每一筆都能修改」的測試（工作經歷計畫書 person_career_goals_FEAT_2026-11-14 §16）：
後端的 ``edit_item`` 與 TG 的修改問答。

Tests for editing any item of the person card.
"""

import json
import re

import pytest

from app.bot.handlers import person_profile
from app.bot.handlers.person_profile import PersonStates
from app.bot.utils import person_profile_view as view
from unittest import mock

from app.core.exceptions import InvalidNoteFieldsError, JsonItemChangedError, NoteNotFoundError
from app.schemas.person_profile import ITEM_EDIT_KEYS, is_active, item_fingerprint
from tests.test_bot_replies import _assert_tg_html, _callback, _fsm, _message, _run, _sent
from tests.test_person_career import _start
from tests.test_person_profile_bot import _card_service, _note, _note_service
from tests.test_person_profile_handler import PersonAnki, _act, _list, person

YUSHAN = {"value": "去過玉山", "added": "2026-11-01", "ended": "", "when": "2019-08", "hidden": True}
BASS = {"value": "貝斯", "added": "2026-11-01", "ended": ""}
QBC = {"company": "QBC", "title": "QA", "start": "2025", "added": "2026-11-08", "ended": ""}
GIFT = {"item": "咖啡豆", "direction": "given", "date": "2026-11-05", "reaction": "很喜歡"}
GOAL = {"kind": "證照", "item": "N1", "target": "2026-12", "status": "done", "added": "2026-11-01", "closed": "2026-11-10"}


def _edit(anki, field, values, index=0, expected=None):
    items = _list(anki, 1, field)
    _act(anki, 1, action="edit_item", field=field, index=index, fingerprint=item_fingerprint(items[index]),
         values=values, expected=expected)
    return _list(anki, 1, field)


def _tap(data, state, note_service=None, card_service=None):
    query = _callback(data)
    _run(person_profile.on_person_button(query, state, note_service or _note_service(), card_service or _card_service()))
    return query


# =============================================================================
# 後端
# =============================================================================


class TestEditItem:
    def test_fix_a_typo_keeps_the_other_keys(self):
        anki = PersonAnki([person(1, Display_Name="陳さん", Experiences=json.dumps([YUSHAN]))])
        (entry,) = _edit(anki, "Experiences", {"value": "爬過玉山", "when": "2019-08"},
                         expected={"value": "去過玉山", "when": "2019-08"})
        assert entry == {**YUSHAN, "value": "爬過玉山"}  # 登錄日、隱藏照舊

    def test_split_field_takes_one_value(self):
        anki = PersonAnki([person(1, Display_Name="陳さん", Interests=json.dumps([BASS]))])
        with pytest.raises(InvalidNoteFieldsError, match="只能改成一個"):
            _edit(anki, "Interests", {"value": "貝斯、賽馬", "since": ""})

    def test_duplicate_of_another_current_entry(self):
        anki = PersonAnki([person(1, Display_Name="陳さん", Interests=json.dumps([BASS, {**BASS, "value": "賽馬"}]))])
        with pytest.raises(InvalidNoteFieldsError, match="已經有「貝斯」"):
            _edit(anki, "Interests", {"value": "貝斯", "since": ""}, index=1)

    def test_ended_entry_may_match_a_current_one(self):
        old = {**BASS, "value": "攝影", "ended": "2026-01-01"}
        anki = PersonAnki([person(1, Display_Name="陳さん", Interests=json.dumps([BASS, old]))])
        items = _edit(anki, "Interests", {"value": "貝斯", "since": ""}, index=1)
        assert items[1]["value"] == "貝斯" and not is_active(items[1])

    def test_position_rename_and_back_to_current(self):
        past = {**QBC, "end": "2026-10", "ended": "2026-10-20"}
        anki = PersonAnki([person(1, Display_Name="陳さん", Company=json.dumps([past]))])
        (entry,) = _edit(anki, "Company", {"company": "QBC Japan", "title": "QA", "start": "2025", "end": "", "note": ""})
        assert entry["company"] == "QBC Japan" and is_active(entry) and "end" not in entry

    def test_past_position_keeps_its_end_and_ended(self):
        """只改職稱：以前的工作還是以前的工作，離職年月與劃掉日都不能被清掉（清掉就救不回來）。"""
        past = {**QBC, "end": "2026-10", "ended": "2026-10-20"}
        anki = PersonAnki([person(1, Display_Name="陳さん", Company=json.dumps([past]))])
        (entry,) = _edit(anki, "Company", {"company": "QBC", "title": "QA 主任", "start": "2025", "end": "2026-10",
                                           "note": ""})
        assert entry == {**past, "title": "QA 主任"} and not is_active(entry)

    def test_past_position_end_change_keeps_ended(self):
        past = {**QBC, "end": "2026-10", "ended": "2026-10-20"}
        anki = PersonAnki([person(1, Display_Name="陳さん", Company=json.dumps([past]))])
        (entry,) = _edit(anki, "Company", {"company": "QBC", "title": "QA", "start": "2025", "end": "2026-09",
                                           "note": ""})
        assert entry["end"] == "2026-09" and entry["ended"] == "2026-10-20"

    def test_position_duplicate_current(self):
        anki = PersonAnki([person(1, Display_Name="陳さん", Company=json.dumps([QBC, {**QBC, "company": "Sony"}]))])
        with pytest.raises(InvalidNoteFieldsError, match="同名的現職"):
            _edit(anki, "Company", {"company": "QBC", "title": "QA", "start": "", "end": "", "note": ""}, index=1)

    def test_json_list_item(self):
        anki = PersonAnki([person(1, Display_Name="陳さん", Gifts_JSON=json.dumps([GIFT]))])
        (gift,) = _edit(anki, "Gifts_JSON", {"direction": "given", "item": "咖啡豆", "date": "2026-11-04", "reaction": ""})
        assert gift == {"item": "咖啡豆", "direction": "given", "date": "2026-11-04"}

    def test_required_value_cannot_be_cleared(self):
        anki = PersonAnki([person(1, Display_Name="陳さん", Gifts_JSON=json.dumps([GIFT]))])
        with pytest.raises(InvalidNoteFieldsError):
            _edit(anki, "Gifts_JSON", {"direction": "given", "item": "", "date": "", "reaction": ""})
        assert _list(anki, 1, "Gifts_JSON") == [GIFT]

    def test_missing_optional_values_are_none(self):
        member = {"relation": "兒子", "name": "小明", "birth_year": 2020}
        anki = PersonAnki([person(1, Display_Name="陳さん", Family_JSON=json.dumps([member]))])
        (entry,) = _edit(anki, "Family_JSON", {"relation": "兒子", "name": "小明", "birth_year": None, "note": None})
        assert entry == {"relation": "兒子", "name": "小明"}

    def test_goal_keeps_its_status(self):
        anki = PersonAnki([person(1, Display_Name="陳さん", Goals_JSON=json.dumps([GOAL]))])
        (goal,) = _edit(anki, "Goals_JSON", {"kind": "證照", "item": "日本語能力試驗 N1", "target": "2026-12"})
        assert goal == {**GOAL, "item": "日本語能力試驗 N1"}

    @staticmethod
    def _talk(day):
        return {"date": day, "topics": "轉職", "updates": "", "place": "", "quote": ""}

    def test_interaction_date_later_moves_last_contact(self):
        anki = PersonAnki([person(1, Display_Name="陳さん", Interactions_JSON=json.dumps([{"date": "2026-10-01", "topics": "轉職"}]),
                                  Last_Contact="2026-10-01")])
        _edit(anki, "Interactions_JSON", self._talk("2026-10-05"))
        assert anki.value(1, "Last_Contact") == "2026-10-05"

    def test_interaction_date_corrected_earlier_follows_the_latest(self):
        """上次聯絡就是這一筆、日期往前更正：改成清單裡最新的日期，不留已經不存在的日期。"""
        talks = [{"date": "2026-09-20", "topics": "近況"}, {"date": "2026-10-05", "topics": "轉職"}]
        anki = PersonAnki([person(1, Display_Name="陳さん", Interactions_JSON=json.dumps(talks), Last_Contact="2026-10-05")])
        _edit(anki, "Interactions_JSON", self._talk("2026-09-01"), index=1)
        assert anki.value(1, "Last_Contact") == "2026-09-20"

    def test_last_contact_from_elsewhere_stays(self):
        anki = PersonAnki([person(1, Display_Name="陳さん", Interactions_JSON=json.dumps([{"date": "2026-10-01", "topics": "轉職"}]),
                                  Last_Contact="2026-10-10")])
        _edit(anki, "Interactions_JSON", self._talk("2026-09-01"))
        assert anki.value(1, "Last_Contact") == "2026-10-10"

    def test_false_matches_a_missing_key(self):
        """存檔時 False 不寫：原值送 False、卡上沒有這個鍵，不算被改過。"""
        day = {"label": "N1 考試", "date": "2026-12-06"}
        anki = PersonAnki([person(1, Display_Name="陳さん", Key_Dates_JSON=json.dumps([day]))])
        (entry,) = _edit(anki, "Key_Dates_JSON", {"label": "N1", "date": "2026-12-06", "yearly": False},
                         expected={"label": "N1 考試", "date": "2026-12-06", "yearly": False})
        assert entry == {"label": "N1", "date": "2026-12-06"}

    def test_resent_content_edit_is_applied_once(self):
        """存檔成功、回應沒送到又重按：指紋已經變了，但那一筆已經是改好的樣子，不報「別處被改過」。"""
        anki = PersonAnki([person(1, Display_Name="陳さん", Experiences=json.dumps([YUSHAN]))])
        params = {"action": "edit_item", "field": "Experiences", "index": 0, "fingerprint": item_fingerprint(YUSHAN),
                  "values": {"value": "爬過玉山", "when": "2019-08"}, "expected": {"value": "去過玉山", "when": "2019-08"}}
        _act(anki, 1, **params)
        _act(anki, 1, **params)
        assert _list(anki, 1, "Experiences") == [{**YUSHAN, "value": "爬過玉山"}]

    def test_resent_time_edit_is_applied_once(self):
        anki = PersonAnki([person(1, Display_Name="陳さん", Experiences=json.dumps([YUSHAN]))])
        values = {"value": "去過玉山", "when": "2019-09"}
        _edit(anki, "Experiences", values, expected={"value": "去過玉山", "when": "2019-08"})
        (entry,) = _edit(anki, "Experiences", values, expected={"value": "去過玉山", "when": "2019-08"})
        assert entry["when"] == "2019-09"

    def test_old_split_value_can_keep_its_text(self):
        """網頁上存成一筆的「桌遊、露營」：沒改內容、只補時間，不擋。"""
        both = {**BASS, "value": "桌遊、露營"}
        anki = PersonAnki([person(1, Display_Name="陳さん", Interests=json.dumps([both]))])
        (entry,) = _edit(anki, "Interests", {"value": "桌遊、露營", "since": "2020"})
        assert entry == {**both, "since": "2020"}

    def test_changed_elsewhere(self):
        anki = PersonAnki([person(1, Display_Name="陳さん", Gifts_JSON=json.dumps([GIFT]))])
        with pytest.raises(JsonItemChangedError, match="別處被改過"):
            _edit(anki, "Gifts_JSON", {"direction": "given", "item": "茶", "date": "", "reaction": ""},
                  expected={"direction": "given", "item": "紅茶", "date": "2026-11-05", "reaction": "很喜歡"})
        assert _list(anki, 1, "Gifts_JSON") == [GIFT]

    def test_missing_expected_key_counts_as_empty(self):
        anki = PersonAnki([person(1, Display_Name="陳さん", Gifts_JSON=json.dumps([{"item": "花", "direction": "given"}]))])
        (gift,) = _edit(anki, "Gifts_JSON", {"direction": "given", "item": "玫瑰", "date": "", "reaction": ""},
                        expected={"direction": "given", "item": "花", "date": None, "reaction": ""})
        assert gift["item"] == "玫瑰"

    @pytest.mark.parametrize("field,values,match", [
        ("Gifts_JSON", {"item": "花"}, "能改的是"),
        ("Photo", {"value": "x"}, "不是人脈卡的清單"),
    ])
    def test_bad_parameters(self, field, values, match):
        anki = PersonAnki([person(1, Display_Name="陳さん", Gifts_JSON=json.dumps([GIFT]))])
        with pytest.raises(InvalidNoteFieldsError, match=re.escape(match)):
            _act(anki, 1, action="edit_item", field=field, index=0, fingerprint=None, values=values)


# =============================================================================
# TG
# =============================================================================


def _begin(field, items, index=0):
    state, note_service = _fsm(), _note_service()
    card_service = _card_service(_note(**{field: json.dumps(items)}))
    code = view.CODE_OF_LIST[field]
    query = _tap(f"pp:ie:5:{code}:{index}:{item_fingerprint(items[index])}", state, note_service, card_service)
    return state, note_service, card_service, query


def _skip_to_preview(state, note_service, card_service):
    """一路略過到預覽（不靠步驟數）。Skip every step up to the preview."""
    for _ in range(10):
        if _run(state.get_state()) == PersonStates.add_confirm.state:
            return
        _tap("pp:skip", state, note_service, card_service)
    raise AssertionError("沒有走到預覽")


def _type(state, *texts):
    typed = None
    for text in texts:
        typed, _ = _message(text)
        _run(person_profile.process_add_text(typed, state))
    return typed


class TestTgEdit:
    def test_every_list_edits_what_the_backend_accepts(self):
        for name, keys in ITEM_EDIT_KEYS.items():
            assert tuple(step.key for step in view.edit_steps(name)) == keys, name
        assert "_followups" not in [step.key for step in view.edit_steps("Interactions_JSON")]

    def test_item_menu_has_edit_for_every_list(self):
        fp = item_fingerprint(GIFT)
        message, _ = _start(f"pitm-Gifts_JSON-0_{fp}-PP_1", _note(Gifts_JSON=json.dumps([GIFT])))
        rows = message.answer.await_args.kwargs["reply_markup"].inline_keyboard
        assert ("修改", f"pp:ie:5:G:0:{fp}") in [(b.text, b.callback_data) for row in rows for b in row]

    def test_history_menu_explains_edit_versus_end(self):
        fp = item_fingerprint(BASS)
        message, _ = _start(f"pitm-Interests-0_{fp}-PP_1", _note(Interests=json.dumps([BASS])))
        (text,) = _sent(message.answer)
        _assert_tg_html(text)
        assert "修改＝更正記錯的內容或時間" in text and "請用「劃掉」" in text

    def test_edit_a_gift(self):
        state, note_service, card_service, query = _begin("Gifts_JSON", [GIFT])
        sent = _sent(query.message.answer)
        assert "修改禮物" in sent[0] and "目前：送出" in sent[1] and "略過＝保留目前的值。" in sent[1]
        _tap("pp:skip", state, note_service, card_service)  # 方向（必填也能略過＝保留）
        _type(state, "衣索比亞咖啡豆")
        _tap("pp:skip", state, note_service, card_service)  # 日期
        last = _type(state, "清空")  # 反應
        preview = _sent(last.answer)[-1]
        _assert_tg_html(preview)
        assert "<b>修改禮物</b>" in preview and "衣索比亞咖啡豆" in preview
        _tap("pp:save", state, note_service, card_service)
        note_service.run_action.assert_awaited_once_with(5, "edit_item", {
            "field": "Gifts_JSON", "index": 0, "fingerprint": item_fingerprint(GIFT),
            "values": {"direction": "given", "item": "衣索比亞咖啡豆", "date": "2026-11-05", "reaction": ""},
            "expected": {"direction": "given", "item": "咖啡豆", "date": "2026-11-05", "reaction": "很喜歡"},
        })

    def test_required_value_cannot_be_cleared(self):
        state, note_service, card_service, _ = _begin("Gifts_JSON", [GIFT])
        _tap("pp:skip", state, note_service, card_service)
        typed = _type(state, "清空")
        assert "必填，不能清空" in _sent(typed.answer)[-1]
        assert _run(state.get_data())["step"] == 1

    def test_choice_shows_its_label(self):
        day = {"label": "結婚紀念日", "date": "2020-05-01", "yearly": True}
        state, note_service, card_service, _ = _begin("Key_Dates_JSON", [day])
        _tap("pp:skip", state, note_service, card_service)
        query = _tap("pp:skip", state, note_service, card_service)
        assert "目前：每年" in _sent(query.message.answer)[-1]
        _tap("pp:skip", state, note_service, card_service)
        _tap("pp:save", state, note_service, card_service)
        assert note_service.run_action.await_args.args[2]["values"]["yearly"] is True

    def test_clearing_a_number_sends_none(self):
        member = {"relation": "兒子", "name": "小明", "birth_year": 2020}
        state, note_service, card_service, _ = _begin("Family_JSON", [member])
        _tap("pp:skip", state, note_service, card_service)
        _tap("pp:skip", state, note_service, card_service)
        _type(state, "清空")
        _tap("pp:skip", state, note_service, card_service)
        _tap("pp:save", state, note_service, card_service)
        assert note_service.run_action.await_args.args[2]["values"] == {
            "relation": "兒子", "name": "小明", "birth_year": None, "note": None,
        }

    def test_several_interests_are_refused(self):
        state, _, _, _ = _begin("Interests", [BASS])
        typed = _type(state, "貝斯、賽馬")
        assert "只能改成一個" in _sent(typed.answer)[-1]
        assert _run(state.get_data())["step"] == 0

    def test_required_step_can_be_skipped(self):
        _, _, _, query = _begin("Gifts_JSON", [GIFT])
        rows = query.message.answer.await_args.kwargs["reply_markup"].inline_keyboard
        assert "pp:skip" in [b.callback_data for row in rows for b in row]

    def test_false_shows_no(self):
        """「只有一次」存檔時不寫 yearly：目前的值要顯示「只有一次」，不是空白。"""
        day = {"label": "N1 考試", "date": "2026-12-06"}
        state, note_service, card_service, _ = _begin("Key_Dates_JSON", [day])
        _tap("pp:skip", state, note_service, card_service)
        query = _tap("pp:skip", state, note_service, card_service)
        assert "目前：只有一次" in _sent(query.message.answer)[-1]

    def test_old_split_value_can_be_kept(self):
        state, _, _, _ = _begin("Interests", [{**BASS, "value": "桌遊、露營"}])
        _type(state, "桌遊、露營")  # 原樣再打一次也不擋
        assert _run(state.get_data())["step"] == 1

    def test_broken_value_must_be_retyped(self):
        state, note_service, card_service, _ = _begin("Gifts_JSON", [{**GIFT, "date": "2026/13/01"}])
        assert _run(state.get_data())["edit"]["invalid"] == ["date"]
        _tap("pp:skip", state, note_service, card_service)
        _tap("pp:skip", state, note_service, card_service)
        query = _tap("pp:skip", state, note_service, card_service)
        assert "不能保留" in _sent(query.message.answer)[-1]
        assert _run(state.get_data())["step"] == 2

    def test_broken_key_outside_the_edit_is_refused(self):
        _, _, _, query = _begin("Experiences", [{**YUSHAN, "added": "bad"}])
        assert "修改改不到的值格式不對（added）" in _sent(query.message.answer)[-1]

    def test_tg_edit_is_accepted_by_the_backend(self):
        """TG 送出的參數直接交給後端：卡上手改的 2021/4 在 TG 整理過，原值照原文送，後端比對得上。"""
        raw = {**QBC, "start": "2021/4"}
        state, note_service, card_service, _ = _begin("Company", [raw])
        _skip_to_preview(state, note_service, card_service)
        _tap("pp:save", state, note_service, card_service)
        (note_id, action, params) = note_service.run_action.await_args.args
        assert (note_id, action, params["expected"]["start"]) == (5, "edit_item", "2021/4")
        anki = PersonAnki([person(1, Display_Name="陳さん", Company=json.dumps([raw]))])
        _act(anki, 1, action=action, **params)
        assert _list(anki, 1, "Company") == [{**QBC, "start": "2021-04"}]

    def test_blocked_edit_offers_redo(self):
        """被擋下（例如和另一筆重複）：再按儲存結果一樣，所以給「重新修改」，回答留著。"""
        state, note_service, card_service, _ = _begin("Gifts_JSON", [GIFT])
        _skip_to_preview(state, note_service, card_service)
        note_service.run_action = mock.AsyncMock(side_effect=InvalidNoteFieldsError("禮物的內容不正確"))
        query = _tap("pp:save", state, note_service, card_service)
        rows = query.message.answer.await_args.kwargs["reply_markup"].inline_keyboard
        assert rows[0][0].callback_data == "pp:redo"
        assert _run(state.get_state()) == PersonStates.add_confirm.state
        query = _tap("pp:redo", state, note_service, card_service)
        assert _run(state.get_state()) == PersonStates.add_step.state and _run(state.get_data())["step"] == 0
        assert "目前：送出" in _sent(query.message.answer)[-1]

    def test_missing_card_ends_the_step(self):
        state, note_service, card_service, _ = _begin("Gifts_JSON", [GIFT])
        _skip_to_preview(state, note_service, card_service)
        note_service.run_action = mock.AsyncMock(side_effect=NoteNotFoundError("找不到這張卡"))
        query = _tap("pp:save", state, note_service, card_service)
        sent = _sent(query.message.answer)
        assert any("找不到這張卡" in text for text in sent) and not any("回答都還在" in text for text in sent)
        assert _run(state.get_state()) is None

    def test_stale_list_button_asks_to_reopen_the_list(self):
        """清單上的舊按鈕（那一筆改過內容）：TG 叫使用者重新打開清單，不是叫同步。"""
        state, note_service = _fsm(), _note_service()
        note_service.run_action = mock.AsyncMock(side_effect=JsonItemChangedError("這一筆已經變了或不在了，請同步後再試。"))
        query = _tap("pp:delok:5:G:0:zzz", state, note_service, _card_service(_note(Gifts_JSON=json.dumps([GIFT]))))
        (text,) = _sent(query.message.answer)
        assert "請重新打開清單" in text and "同步" not in text
        reopen = query.message.answer.await_args.kwargs["reply_markup"].inline_keyboard[0][0]
        assert reopen.callback_data == "pp:ls:5:G"

    def test_edit_an_interaction_without_followups_step(self):
        talk = {"date": "2026-10-01", "topics": "轉職"}
        state, note_service, card_service, _ = _begin("Interactions_JSON", [talk])
        for _ in range(5):
            _tap("pp:skip", state, note_service, card_service)
        assert _run(state.get_state()) == PersonStates.add_confirm.state
