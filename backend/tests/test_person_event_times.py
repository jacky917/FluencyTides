"""人脈卡的事件時間（工作經歷計畫書 person_career_goals_FEAT_2026-11-14 §15）：經歷、讚美素材的時間，
興趣的開始、現居地搬來的時間，學歷的入學與畢業，目標達成或放棄的那天。

Tests for the event times of the person card.
"""

import json
import re
from datetime import date
from pathlib import Path
from unittest import mock

import pytest

from app.bot.handlers import person_profile
from app.bot.handlers.person_profile import PersonStates
from app.bot.utils import person_profile_view as view
from app.core.exceptions import InvalidNoteFieldsError, JsonItemChangedError
from app.schemas.person_profile import (
    HISTORY_PERIOD_KEYS,
    PERSON_HISTORY_FIELDS,
    CredentialEntry,
    EducationEntry,
    SinceEntry,
    WhenNote,
    entry_detail,
    history_period,
    is_active,
    item_fingerprint,
)
from app.services import person_profile_ingest as ingest
from app.services import person_profile_topics as topics
from tests.test_bot_replies import _assert_tg_html, _callback, _fsm, _message, _run, _sent
from tests.test_person_career import _start
from tests.test_person_profile_bot import _card_service, _note, _note_service
from tests.test_person_profile_handler import PersonAnki, _act, _list, person
from tests.test_person_profile_ingest import _result

TODAY = date(2026, 11, 15)
FRONT = Path(__file__).resolve().parents[1] / "app" / "anki_models" / "Person_Profile_Dark_front.html"
YUSHAN = {"value": "去過玉山", "added": "2026-11-15", "ended": "", "when": "2019-08"}
BASS = {"value": "貝斯", "added": "2026-11-01", "ended": ""}
NTU = {"value": "台大資工", "added": "2026-11-01", "ended": "", "start": "2011", "finish": "2015"}


def _tap(data, state, note_service=None, card_service=None):
    query = _callback(data)
    _run(person_profile.on_person_button(query, state, note_service or _note_service(), card_service or _card_service()))
    return query


# =============================================================================
# 資料
# =============================================================================


class TestModels:
    def test_registry(self):
        assert PERSON_HISTORY_FIELDS["Experiences"].model is WhenNote
        assert PERSON_HISTORY_FIELDS["Praise"].model is WhenNote
        assert PERSON_HISTORY_FIELDS["Interests"].model is SinceEntry
        assert PERSON_HISTORY_FIELDS["Residence"].model is SinceEntry
        assert PERSON_HISTORY_FIELDS["Education"].model is EducationEntry
        for name, keys in HISTORY_PERIOD_KEYS.items():
            assert set(keys) <= set(PERSON_HISTORY_FIELDS[name].model.model_fields), name

    def test_times_are_normalized_and_empty_ones_not_stored(self):
        assert WhenNote.model_validate({"value": "x", "when": "2019/8"}).model_dump()["when"] == "2019-08"
        assert "since" not in SinceEntry.model_validate({"value": "x", "since": ""}).model_dump()
        dumped = EducationEntry.model_validate({"value": "台大", "start": "2011年9月", "finish": "2015"}).model_dump()
        assert (dumped["start"], dumped["finish"]) == ("2011-09", "2015")

    @pytest.mark.parametrize("model,key", [
        (WhenNote, "when"), (SinceEntry, "since"), (EducationEntry, "start"),
        (CredentialEntry, "since"), (CredentialEntry, "until"),
    ])
    def test_unreadable_time_is_rejected(self, model, key):
        with pytest.raises(ValueError, match="看不懂"):
            model.model_validate({"value": "x", key: "2019年春"})

    def test_finish_before_start_is_rejected(self):
        with pytest.raises(ValueError, match=re.escape("畢業（2010）不能早於入學（2014）")):
            EducationEntry.model_validate({"value": "台大", "start": "2014", "finish": "2010"})

    def test_graduated_school_is_still_current(self):
        # 畢業不叫 end：有 end 的會被當成失效（職位的離職）
        assert is_active(NTU)

    def test_period_text(self):
        assert history_period("Experiences", YUSHAN) == "2019-08"
        assert history_period("Interests", {**BASS, "since": "2015"}) == "2015 起"
        assert history_period("Education", NTU) == "2011～2015"
        assert history_period("Education", {**NTU, "start": ""}) == "～2015"
        assert history_period("Interests", BASS) == ""
        assert history_period("Food_Drink", {"value": "拉麵", "since": "2015"}) == ""
        assert entry_detail("Experiences", YUSHAN) == "去過玉山（2019-08）"
        assert entry_detail("Company", {"company": "QBC", "start": "2025", "note": "QA"}) == "QBC（2025～）：QA"


# =============================================================================
# 寫入
# =============================================================================


class TestHandler:
    def test_add_with_a_time(self):
        anki = PersonAnki([person(1, Display_Name="陳さん")])
        _act(anki, 1, action="add_history", field="Experiences", item={"value": "去過玉山", "when": "2019/8"})
        (entry,) = _list(anki, 1, "Experiences")
        assert entry["when"] == "2019-08" and entry["value"] == "去過玉山"

    def test_duplicate_fills_the_missing_time(self):
        anki = PersonAnki([person(1, Display_Name="陳さん", Interests=json.dumps([BASS]))])
        _act(anki, 1, action="add_history", field="Interests", item={"value": "貝斯", "since": "2015"})
        assert _list(anki, 1, "Interests") == [{**BASS, "since": "2015"}]

    def test_duplicate_with_another_time_is_reported_not_dropped(self):
        anki = PersonAnki([person(1, Display_Name="陳さん", Interests=json.dumps([{**BASS, "since": "2010"}]))])
        with pytest.raises(InvalidNoteFieldsError, match="時間和這次輸入的不同"):
            _act(anki, 1, action="add_history", field="Interests", item={"value": "貝斯", "since": "2015"})
        assert _list(anki, 1, "Interests") == [{**BASS, "since": "2010"}]

    def test_plain_duplicate_is_refused(self):
        """完全重複（沒有新的時間或說明）：報錯，免得使用者以為存了（§18 審查修正）。"""
        anki = PersonAnki([person(1, Display_Name="陳さん", Interests=json.dumps([{**BASS, "since": "2010"}]))])
        with pytest.raises(InvalidNoteFieldsError, match="已經有「貝斯（2010 起）」，沒有新增"):
            _act(anki, 1, action="add_history", field="Interests", item={"value": "貝斯"})
        assert _list(anki, 1, "Interests") == [{**BASS, "since": "2010"}]

    def test_filling_a_time_that_breaks_the_order_is_rejected(self):
        old = {"value": "東大", "added": "2026-01-01", "ended": "", "finish": "2015"}
        anki = PersonAnki([person(1, Display_Name="陳さん", Education=json.dumps([old]))])
        with pytest.raises(InvalidNoteFieldsError, match=re.escape("畢業（2015）不能早於入學（2018）")):
            _act(anki, 1, action="add_history", field="Education", item={"value": "東大", "start": "2018"})
        assert _list(anki, 1, "Education") == [old]

    def test_filling_a_single_value_field_does_not_end_it(self):
        home = {"value": "橫濱", "added": "2026-01-01", "ended": ""}
        anki = PersonAnki([person(1, Display_Name="陳さん", Residence=json.dumps([home]))])
        _act(anki, 1, action="add_history", field="Residence", item={"value": "橫濱", "since": "2023"})
        assert _list(anki, 1, "Residence") == [{**home, "since": "2023"}]

    def test_edit_and_clear_a_time(self):
        anki = PersonAnki([person(1, Display_Name="陳さん", Education=json.dumps([NTU]))])
        fp = item_fingerprint(NTU)
        _act(anki, 1, action="edit_history_period", field="Education", index=0, fingerprint=fp,
             values={"start": "2011-09", "finish": ""}, expected={"start": "2011", "finish": "2015"})
        (entry,) = _list(anki, 1, "Education")
        assert entry["start"] == "2011-09" and "finish" not in entry and entry["added"] == NTU["added"]

    def test_edit_rejects_a_changed_entry(self):
        anki = PersonAnki([person(1, Display_Name="陳さん", Experiences=json.dumps([YUSHAN]))])
        with pytest.raises(JsonItemChangedError, match="別處被改過"):
            _act(anki, 1, action="edit_history_period", field="Experiences", index=0,
                 fingerprint=item_fingerprint(YUSHAN), values={"when": "2020"}, expected={"when": "2018"})
        assert _list(anki, 1, "Experiences") == [YUSHAN]

    @pytest.mark.parametrize("field,values,match", [
        ("Experiences", {"since": "2020"}, "要送 when"),
        ("Company", {"start": "2020", "end": ""}, "沒有可以用這個動作改的時間"),
        ("Food_Drink", {"since": "2020"}, "沒有可以用這個動作改的時間"),
        ("Education", {"start": "2015", "finish": "2011"}, "畢業（2011）不能早於入學（2015）"),
    ])
    def test_edit_rejects_bad_input(self, field, values, match):
        anki = PersonAnki([person(1, Display_Name="陳さん", Experiences=json.dumps([YUSHAN]),
                                  Education=json.dumps([NTU]), Food_Drink=json.dumps([BASS]),
                                  Company=json.dumps([{"company": "QBC", "added": "", "ended": ""}]))])
        with pytest.raises(InvalidNoteFieldsError, match=re.escape(match)):
            _act(anki, 1, action="edit_history_period", field=field, index=0, fingerprint=None, values=values)

    def test_goal_closed_on_the_given_day(self):
        goal = {"kind": "證照", "item": "N1", "status": "open", "added": "2026-11-01"}
        anki = PersonAnki([person(1, Display_Name="陳さん", Goals_JSON=json.dumps([goal]))])
        _act(anki, 1, action="set_goal_status", index=0, status="done", closed="2026-11-07")
        assert _list(anki, 1, "Goals_JSON")[0]["closed"] == "2026-11-07"

    @pytest.mark.parametrize("closed", ["12-07", "2026-13-45", "2099-01-01"])
    def test_goal_closed_date_must_be_a_real_past_date(self, closed):
        goal = {"kind": "證照", "item": "N1", "status": "open", "added": "2026-11-01"}
        anki = PersonAnki([person(1, Display_Name="陳さん", Goals_JSON=json.dumps([goal]))])
        with pytest.raises(InvalidNoteFieldsError):
            _act(anki, 1, action="set_goal_status", index=0, status="done", closed=closed)
        assert "closed" not in _list(anki, 1, "Goals_JSON")[0]

    def test_open_goal_has_no_closed_day(self):
        from app.schemas.person_profile import Goal

        goal = Goal.model_validate({"item": "N1", "status": "open", "closed": "2026-11-01"})
        assert goal.closed == ""

    def test_editing_a_time_keeps_ended_and_hidden(self):
        entry = {**YUSHAN, "ended": "2026-05-01", "hidden": True}
        anki = PersonAnki([person(1, Display_Name="陳さん", Experiences=json.dumps([entry]))])
        _act(anki, 1, action="edit_history_period", field="Experiences", index=0,
             fingerprint=item_fingerprint(entry), values={"when": "2018"}, expected={"when": "2019-08"})
        assert _list(anki, 1, "Experiences") == [{**entry, "when": "2018"}]

    def test_expected_with_missing_keys_is_a_parameter_error(self):
        anki = PersonAnki([person(1, Display_Name="陳さん", Education=json.dumps([NTU]))])
        with pytest.raises(InvalidNoteFieldsError, match="原值要送"):
            _act(anki, 1, action="edit_history_period", field="Education", index=0, fingerprint=None,
                 values={"start": "2011", "finish": "2016"}, expected={"start": "2011"})


# =============================================================================
# TG
# =============================================================================


def _add(field, *answers):
    """新增一筆：依序輸入回答；回傳 (state, note_service, card_service, 最後一則回覆的 message)。"""
    state, note_service, card_service = _fsm(), _note_service(), _card_service()
    message, _ = _message()
    _run(person_profile.start_add(message, state, 5, field))
    for answer in answers:
        message, _ = _message(answer)
        _run(person_profile.process_add_text(message, state))
    return state, note_service, card_service, message


class TestTgAdd:
    def test_one_interest_asks_since(self):
        state, note_service, card_service, message = _add("Interests", "貝斯", "2015")
        assert _run(state.get_state()) == PersonStates.add_confirm.state
        assert "貝斯（2015 起）" in _sent(message.answer)[-1]
        _tap("pp:save", state, note_service, card_service)
        note_service.run_action.assert_awaited_once_with(
            5, "add_history", {"field": "Interests", "item": {"value": "貝斯", "since": "2015"}}
        )

    def test_several_interests_skip_since(self):
        state, _, _, message = _add("Interests", "貝斯、賽馬")
        assert _run(state.get_state()) == PersonStates.add_confirm.state
        assert "從什麼時候開始" not in "".join(_sent(message.answer))

    def test_education_asks_start_and_finish(self):
        state, note_service, card_service, message = _add("Education", "台大", "2011", "2015", "資工系")
        preview = _sent(message.answer)[-1]
        _assert_tg_html(preview)
        assert "台大（2011～2015）" in preview and "說明：資工系" in preview
        _tap("pp:save", state, note_service, card_service)
        note_service.run_action.assert_awaited_once_with(5, "add_history", {
            "field": "Education", "item": {"value": "台大", "start": "2011", "finish": "2015", "note": "資工系"},
        })

    def test_finish_before_start_asks_again(self):
        state, _, _, message = _add("Education", "台大資工", "2015", "2011")
        text = _sent(message.answer)[-1]
        assert "畢業（2011）不能早於入學（2015）" in text
        button = message.answer.await_args.kwargs["reply_markup"].inline_keyboard[0][0]
        assert (button.text, button.callback_data) == ("重新輸入入學", "pp:back")
        assert _run(state.get_state()) == PersonStates.add_step.state

    def test_unreadable_time_asks_again(self):
        state, _, _, message = _add("Experiences", "去過玉山", "去年夏天")
        assert "看不懂" in _sent(message.answer)[-1]
        assert _run(state.get_state()) == PersonStates.add_step.state


class TestTgEdit:
    def _list(self, field, items):
        state, note_service = _fsm(), _note_service()
        card_service = _card_service(_note(**{field: json.dumps(items)}))
        query = _tap(f"pp:ls:5:{view.CODE_OF_LIST[field]}", state, note_service, card_service)
        return state, note_service, card_service, query

    def test_list_offers_edit_for_fields_with_times(self):
        _, _, _, query = self._list("Experiences", [YUSHAN])
        assert "1. 去過玉山（2019-08）（2026-11-15 登錄）" in _sent(query.message.answer)[0]
        row = query.message.answer.await_args.kwargs["reply_markup"].inline_keyboard[0]
        assert (row[-1].text, row[-1].callback_data) == ("改 1", f"pp:ie:5:EX:0:{item_fingerprint(YUSHAN)}")

    def test_edit_a_time(self):
        state, note_service, card_service, _ = self._list("Experiences", [YUSHAN])
        query = _tap(f"pp:ie:5:EX:0:{item_fingerprint(YUSHAN)}", state, note_service, card_service)
        sent = _sent(query.message.answer)
        assert "修改經歷" in sent[0] and "目前：去過玉山" in sent[1]
        asked = _tap("pp:skip", state, note_service, card_service)  # 內容保留
        assert "目前：2019-08" in _sent(asked.message.answer)[-1]
        typed, _ = _message("2018")
        _run(person_profile.process_add_text(typed, state))
        assert "去過玉山（2018）" in _sent(typed.answer)[-1]
        _tap("pp:save", state, note_service, card_service)
        note_service.run_action.assert_awaited_once_with(5, "edit_item", {
            "field": "Experiences", "index": 0, "fingerprint": item_fingerprint(YUSHAN),
            "values": {"value": "去過玉山", "when": "2018"}, "expected": {"value": "去過玉山", "when": "2019-08"},
        })

    def test_skipping_keeps_and_clearing_clears(self):
        state, note_service, card_service, _ = self._list("Education", [NTU])
        _tap(f"pp:ie:5:ED:0:{item_fingerprint(NTU)}", state, note_service, card_service)
        _tap("pp:skip", state, note_service, card_service)  # 內容
        _tap("pp:skip", state, note_service, card_service)  # 入學
        typed, _ = _message("清空")
        _run(person_profile.process_add_text(typed, state))
        _tap("pp:skip", state, note_service, card_service)  # 說明
        _tap("pp:save", state, note_service, card_service)
        args = note_service.run_action.await_args.args
        assert args[1] == "edit_item" and args[2]["values"] == {
            "value": "台大資工", "start": "2011", "finish": "", "note": None,
        }

    def test_old_edit_time_button_opens_the_edit(self):
        """聊天紀錄裡舊的「修改時間」按鈕（pp:te）也進修改問答。"""
        state, note_service, card_service, _ = self._list("Experiences", [YUSHAN])
        query = _tap(f"pp:te:5:EX:0:{item_fingerprint(YUSHAN)}", state, note_service, card_service)
        assert "修改經歷" in _sent(query.message.answer)[0]
        assert _run(state.get_state()) == PersonStates.add_step.state

    def test_stale_field_code(self):
        query = _tap("pp:ie:5:ZZ:0:abc", _fsm())
        assert "按鈕已失效" in _sent(query.message.answer)[0]

    def test_long_press_menu_label(self):
        fp = item_fingerprint(NTU)
        message, _ = _start(f"pitm-Education-0_{fp}-PP_1", _note(Education=json.dumps([NTU])))
        rows = message.answer.await_args.kwargs["reply_markup"].inline_keyboard
        assert [(b.text, b.callback_data) for b in rows[1]] == [("修改", f"pp:ie:5:ED:0:{fp}")]


class TestTgGoalClosed:
    def test_typed_date(self):
        state, note_service = _fsm(), _note_service()
        with mock.patch.object(person_profile, "_today", return_value=TODAY):
            _tap("pp:gs:5:0:abc:x", state, note_service)
            typed, _ = _message("11-02")
            _run(person_profile.process_goal_closed(typed, state, note_service))
        note_service.run_action.assert_awaited_once_with(
            5, "set_goal_status", {"index": 0, "fingerprint": "abc", "status": "dropped", "closed": "2026-11-02"}
        )
        assert "已標成放棄（2026-11-02）" in _sent(typed.answer)[0]
        assert _run(state.get_state()) is None

    @pytest.mark.parametrize("text,error", [("下週", "看不懂"), ("2026-12-01", "還沒到")])
    def test_bad_date_asks_again(self, text, error):
        state, note_service = _fsm(), _note_service()
        with mock.patch.object(person_profile, "_today", return_value=TODAY):
            _tap("pp:gs:5:0:abc:d", state, note_service)
            typed, _ = _message(text)
            _run(person_profile.process_goal_closed(typed, state, note_service))
        assert error in _sent(typed.answer)[0]
        note_service.run_action.assert_not_awaited()
        assert _run(state.get_state()) == PersonStates.goal_closed.state

    def test_reopen_needs_no_date(self):
        state, note_service = _fsm(), _note_service()
        _tap("pp:gs:5:0:abc:o", state, note_service)
        note_service.run_action.assert_awaited_once_with(
            5, "set_goal_status", {"index": 0, "fingerprint": "abc", "status": "open"}
        )

    def test_day_buttons_carry_their_goal(self):
        """連按兩個目標的「達成」後，按第一則訊息的「昨天」仍是第一個目標（按鈕自己帶著是哪一個）。"""
        state, note_service = _fsm(), _note_service()
        with mock.patch.object(person_profile, "_today", return_value=TODAY):
            first = _tap("pp:gs:5:0:aaa:d", state, note_service)
            _tap("pp:gs:5:1:bbb:x", state, note_service)
            yesterday = first.message.answer.await_args.kwargs["reply_markup"].inline_keyboard[0][1]
            _tap(yesterday.callback_data, state, note_service)
        note_service.run_action.assert_awaited_once_with(
            5, "set_goal_status", {"index": 0, "fingerprint": "aaa", "status": "done", "closed": "2026-11-14"}
        )
        assert _run(state.get_state()) is None

    @pytest.mark.parametrize("data", ["pp:gc:5:0:abc:o:0", "pp:gc:5:0:abc:d:9", "pp:gc:0"])
    def test_stale_day_button(self, data):
        note_service = _note_service()
        query = _tap(data, _fsm(), note_service)
        assert "按鈕已失效" in _sent(query.message.answer)[0]
        note_service.run_action.assert_not_awaited()

    def test_month_day_after_today_means_last_year(self):
        state, note_service = _fsm(), _note_service()
        with mock.patch.object(person_profile, "_today", return_value=date(2027, 1, 3)):
            _tap("pp:gs:5:0:abc:d", state, note_service)
            typed, _ = _message("12-28")
            _run(person_profile.process_goal_closed(typed, state, note_service))
        assert note_service.run_action.await_args.args[2]["closed"] == "2026-12-28"

    def test_today_is_accepted(self):
        state, note_service = _fsm(), _note_service()
        with mock.patch.object(person_profile, "_today", return_value=TODAY):
            _tap("pp:gs:5:0:abc:d", state, note_service)
            typed, _ = _message("2026-11-15")
            _run(person_profile.process_goal_closed(typed, state, note_service))
        assert note_service.run_action.await_args.args[2]["closed"] == "2026-11-15"

    def test_failed_write_keeps_the_step_with_buttons(self):
        from app.infrastructure.anki.client import AnkiConnectError

        state, note_service = _fsm(), _note_service()
        note_service.run_action = mock.AsyncMock(side_effect=AnkiConnectError("Anki 沒有回應"))
        with mock.patch.object(person_profile, "_today", return_value=TODAY):
            _tap("pp:gs:5:0:abc:d", state, note_service)
            typed, _ = _message("11-02")
            _run(person_profile.process_goal_closed(typed, state, note_service))
        assert _run(state.get_state()) == PersonStates.goal_closed.state
        assert "還沒存" in _sent(typed.answer)[-1]
        rows = typed.answer.await_args.kwargs["reply_markup"].inline_keyboard
        assert rows[0][0].callback_data == "pp:gc:5:0:abc:d:0" and rows[1][-1].callback_data == "pp:cancel"

    def test_changed_goal_ends_the_step(self):
        state, note_service = _fsm(), _note_service()
        note_service.run_action = mock.AsyncMock(side_effect=JsonItemChangedError("這一筆已經不在了"))
        with mock.patch.object(person_profile, "_today", return_value=TODAY):
            _tap("pp:gs:5:0:abc:d", state, note_service)
            typed, _ = _message("11-02")
            _run(person_profile.process_goal_closed(typed, state, note_service))
        assert _run(state.get_state()) is None
        assert typed.answer.await_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "pp:ls:5:GL"


# =============================================================================
# AI
# =============================================================================


def _proposals(list_updates, lists=None):
    result = _result(followups=[], field_updates=[], list_updates=list_updates)
    return [p for p in ingest.proposals_from(result, {}, lists or {}, TODAY) if p.kind == "history_add"]


class TestIngest:
    def test_times_go_to_each_fields_keys(self):
        proposals = _proposals([
            {"field": "Experiences", "op": "add", "value": "爬過玉山", "time_start": "2026/9", "reason": "上個月"},
            {"field": "Education", "op": "add", "value": "台大資工", "time_start": "2011", "time_end": "2015", "reason": ""},
            {"field": "Residence", "op": "add", "value": "東京", "time_start": "2023", "time_end": "2024", "reason": ""},
        ])
        assert [p.data["item"] for p in proposals] == [
            {"value": "爬過玉山", "when": "2026-09"},
            {"value": "台大資工", "start": "2011", "finish": "2015"},
            {"value": "東京", "since": "2023"},
        ]
        assert proposals[0].data["text"] == "爬過玉山（2026-09）"

    @pytest.mark.parametrize("start,end", [("去年", ""), ("2015", "2011")])
    def test_unusable_times_are_dropped_not_the_entry(self, start, end):
        (p,) = _proposals([{"field": "Education", "op": "add", "value": "台大", "time_start": start, "time_end": end, "reason": ""}])
        assert p.data["item"] == {"value": "台大"}

    def test_several_interests_in_one_suggestion_get_no_time(self, caplog):
        (p,) = _proposals([{"field": "Interests", "op": "add", "value": "登山、潛水", "time_start": "2015", "reason": ""}])
        assert p.data["item"] == {"value": "登山、潛水"}
        assert "一次建議多項的時間不放" in caplog.text

    def test_unused_time_is_logged(self, caplog):
        (p,) = _proposals([{"field": "Experiences", "op": "add", "value": "爬過玉山", "time_start": "2019",
                            "time_end": "2020", "reason": ""}])
        assert p.data["item"] == {"value": "爬過玉山", "when": "2019"}
        assert "用不到的時間" in caplog.text

    def test_known_entries_show_the_added_day_apart_from_the_event_time(self):
        prompt = ingest.build_prompt({"Display_Name": "陳さん"}, {"Interests": [{**BASS, "since": "2015"}]}, "x", TODAY)
        assert "#1 貝斯（2015 起）（登錄 2026-11-01）" in prompt

    def test_known_entry_is_proposed_again_only_to_add_a_time(self):
        update = {"field": "Interests", "op": "add", "value": "貝斯", "time_start": "2015", "reason": "玩了十年"}
        assert [p.data["item"] for p in _proposals([update], {"Interests": [BASS]})] == [{"value": "貝斯", "since": "2015"}]
        assert _proposals([update], {"Interests": [{**BASS, "since": "2010"}]}) == []
        assert _proposals([{**update, "time_start": ""}], {"Interests": [BASS]}) == []

    def test_prompt_explains_event_times(self):
        prompt = ingest.build_prompt({"Display_Name": "陳さん"}, {"Experiences": [YUSHAN]}, "上個月爬了玉山", TODAY)
        # 各欄位的時間意義由欄位說明產生（欄位分工計畫書 D2）
        assert "記的是那件事的時間" in prompt and "`time_start`：入學。`time_end`：畢業" in prompt
        assert "去過玉山（2019-08）" in prompt  # 已知的資料附時間
        assert "job_start" not in prompt

    def test_prompt_examples_use_the_schema_keys(self):
        """提示詞的輸出範例和 LLM 的結構化輸出欄位一致（改名時漏改範例，LLM 會照舊的鍵寫，值被靜默丟掉）。"""
        from app.schemas.llm.person_profile import IngestListUpdate

        prompt = ingest.build_prompt({"Display_Name": "陳さん"}, {}, "x", TODAY)
        example = json.loads(prompt[prompt.rindex("```json") + len("```json"):prompt.rindex("```")])
        for update in example["list_updates"]:
            assert set(update) == set(IngestListUpdate.model_fields), update

    def test_topics_read_the_times(self):
        assert topics.history_text("Interests", [{**BASS, "since": "2015"}]) == "貝斯（2015 起）"


# =============================================================================
# 卡面
# =============================================================================


class TestCard:
    def test_period_keys_match_the_backend(self):
        source = FRONT.read_text(encoding="utf-8")
        block = re.search(r"var PERIOD_KEYS = \{(.*?)\};", source, re.DOTALL).group(1)
        keys = {name: tuple(re.findall(r"'(\w+)'", values)) for name, values in re.findall(r"(\w+): \[([^\]]*)\]", block)}
        assert keys == {name: value for name, value in HISTORY_PERIOD_KEYS.items() if name != "Company"}

    def test_lists_with_times_are_sorted_by_time(self):
        source = FRONT.read_text(encoding="utf-8")
        for label, name in (("經歷", "Experiences"), ("學歷", "Education"), ("讚美素材", "Praise")):
            assert f"historyRow('{label}', '{name}', {{ byTime: true }})" in source


class TestMigration:
    def test_only_templates_need_no_full_sync(self):
        from scripts.local_anki.Person_Profile_Dark import migrate_single_card as migration
        from tests.test_person_goals import _migration_client

        migration, client = _migration_client(migration.target_fields(), ["人脈卡"])
        plan = _run(migration.make_plan(client))
        assert not plan.needs_full_sync
        assert migration.Plan(add_fields=["X"]).needs_full_sync
