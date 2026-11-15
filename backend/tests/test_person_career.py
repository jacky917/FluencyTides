"""人脈卡工作經歷與逐筆隱藏的測試（工作經歷計畫書 person_career_goals_FEAT_2026-11-14 §4、§5）。

Tests for the person card's work history and per-item hiding.
"""

import json
from datetime import date
from unittest import mock

import pytest
from pydantic import TypeAdapter, ValidationError

from app.bot.handlers import commands, person_profile
from app.bot.utils import person_profile_view as view
from app.core.clock import today_iso
from app.schemas.person_profile import (
    PERSON_HISTORY_FIELDS,
    PERSON_JSON_ITEMS,
    Gift,
    Position,
    active_texts,
    entry_detail,
    hidden_tag,
    hidden_text_fields,
    is_active,
    is_hidden,
    item_fingerprint,
    position_period,
)
from app.services import person_profile_topics
from app.services.tag_rules import is_system_tag
from app.services.task_handlers.person_profile_handler import set_history_status
from tests.test_bot_replies import _assert_tg_html, _callback, _fsm, _message, _run, _sent
from tests.test_person_profile_bot import _anki, _card_service, _note, _note_service
from tests.test_person_profile_handler import PersonAnki, _act, _list, person

QBC = {"company": "QBC", "title": "QA", "start": "2025", "added": "2026-11-08", "ended": ""}
SONY = {"company": "Sony", "title": "", "start": "2019-04", "end": "2021-03", "added": "", "ended": ""}
OLD = {"company": "舊公司", "title": "", "added": "", "ended": "2025-01-10"}


# =============================================================================
# 格式（§4.1）
# =============================================================================


class TestPositionFormat:
    @pytest.mark.parametrize("value", ["", "2021", "2021-04", "2021-04-01"])
    def test_start_accepts_year_month_or_date(self, value):
        assert Position(company="A", start=value).start == value

    @pytest.mark.parametrize(
        ("typed", "stored"),
        [("2021/04", "2021-04"), ("2021-4", "2021-04"), ("2021年4月", "2021-04"), ("2021年4月1日", "2021-04-01")],
    )
    def test_start_is_normalized(self, typed, stored):
        """網頁與 TG 同一套整理規則（normalize_period）。"""
        assert Position(company="A", start=typed).start == stored

    @pytest.mark.parametrize("value", ["21-04", "去年", "?", "2021-13", "2021-00", "2021-04-32", "2021-02-31",
                                       "2021-123", "1800"])
    def test_start_rejects_other_forms(self, value):
        with pytest.raises(ValidationError):
            Position(company="A", start=value)

    @pytest.mark.parametrize(("typed", "stored"), [("?", "?"), ("？", "?"), ("2021-03", "2021-03")])
    def test_end_accepts_unknown(self, typed, stored):
        assert Position(company="A", end=typed).end == stored

    @pytest.mark.parametrize("value", ["??", "2021-13", "不知道"])
    def test_end_rejects_other_forms(self, value):
        with pytest.raises(ValidationError):
            Position(company="A", end=value)

    def test_end_before_start_is_rejected(self):
        with pytest.raises(ValidationError, match="不能早於到職"):
            Position(company="A", start="2022", end="2021-12")

    @pytest.mark.parametrize(("start", "end"), [("2021-04", "2021"), ("2021", "2021-01"), ("2021", "?")])
    def test_same_year_or_unknown_end_is_fine(self, start, end):
        """精度不同時比共同前綴：同一年算合理；不知道的離職不比。"""
        Position(company="A", start=start, end=end)

    def test_past_and_current(self):
        """現職＝沒有離職年月、也沒有劃掉；只有職位有 end 這個鍵。"""
        assert is_active(QBC)
        assert not is_active(SONY) and not is_active({**QBC, "end": "?"}) and not is_active(OLD)
        assert active_texts("Company", [QBC, SONY, OLD]) == ["QBC・QA"]
        assert is_active({"value": "登山", "added": "", "ended": ""})

    @pytest.mark.parametrize(
        ("item", "expected"),
        [(QBC, "2025～"), (SONY, "2019-04～2021-03"), ({**SONY, "end": "?"}, "2019-04～？"),
         (OLD, "～2025-01"), ({"company": "A"}, "")],
    )
    def test_period(self, item, expected):
        assert position_period(item) == expected

    def test_entry_detail_adds_period_and_note(self):
        assert entry_detail("Company", {**QBC, "note": "測試自動化"}) == "QBC・QA（2025～）：測試自動化"
        assert entry_detail("Company", {"company": "A"}) == "A"
        assert entry_detail("Interests", {"value": "登山", "start": "2020"}) == "登山"

    def test_fingerprint_ignores_period_note_and_hidden(self):
        """補填年月、說明或隱藏後，卡片上的舊連結仍對得上同一筆。"""
        base = {"company": "QBC", "title": "QA", "added": "", "ended": ""}
        changed = {**base, "start": "2025", "end": "2026-01", "note": "x", "hidden": True}
        assert item_fingerprint(base) == item_fingerprint(changed)


class TestStoredShape:
    def test_web_write_leaves_out_empty_optional_keys(self):
        """網頁整欄寫入（TypeAdapter + model_dump(mode="json")）和 TG 存下來的形狀相同。"""
        rows = [{"company": "QBC", "title": "QA", "start": "", "end": "", "note": "", "hidden": False,
                 "added": "", "ended": ""},
                {"company": "Sony", "start": "2019", "hidden": True, "added": "", "ended": ""}]
        dumped = [item.model_dump(mode="json") for item in TypeAdapter(list[Position]).validate_python(rows)]
        assert dumped == [
            {"company": "QBC", "title": "QA", "added": "", "ended": ""},
            {"company": "Sony", "title": "", "start": "2019", "hidden": True, "added": "", "ended": ""},
        ]

    def test_hidden_is_strict(self):
        """卡面只認 true：其他看起來像真的值寫入時就擋下，不要在下次網頁儲存時偷偷變成隱藏。"""
        with pytest.raises(ValidationError):
            Gift(item="花", hidden="yes")


class TestHiddenFormat:
    @pytest.mark.parametrize("model", [Gift, Position, *[spec.model for spec in PERSON_HISTORY_FIELDS.values()]])
    def test_hidden_is_left_out_unless_true(self, model):
        sample = {"item": "花", "company": "A", "value": "3"}
        assert "hidden" not in model.model_validate(sample).model_dump()
        assert model.model_validate({**sample, "hidden": True}).model_dump()["hidden"] is True

    def test_every_list_can_be_hidden_on_the_web_form(self):
        for model, _ in PERSON_JSON_ITEMS.values():
            assert list(model.model_json_schema()["properties"])[-1] == "hidden"

    def test_is_hidden(self):
        assert is_hidden({"hidden": True}) and not is_hidden({"hidden": "true"}) and not is_hidden("x")

    def test_text_field_tags(self):
        assert hidden_tag("Appearance") == "人脈卡隱藏::Appearance"
        assert hidden_text_fields(["PersonProfile", "人脈卡隱藏::Appearance", "人脈卡隱藏::Notes"]) == {
            "Appearance", "Notes",
        }

    def test_hidden_tags_are_system_tags(self):
        assert is_system_tag("人脈卡隱藏::Appearance")


# =============================================================================
# 動作：離職、恢復現職、隱藏（§4.2、§5）
# =============================================================================


class TestActions:
    def test_leaving_fills_this_month(self):
        entry = dict(QBC)
        set_history_status("Company", entry, ended=True, today="2026-11-14")
        assert (entry["ended"], entry["end"]) == ("2026-11-14", "2026-11")

    def test_leaving_keeps_a_known_end(self):
        entry = {**QBC, "end": "2026-10"}
        set_history_status("Company", entry, ended=True, today="2026-11-14")
        assert entry["end"] == "2026-10"

    def test_restore_makes_it_current_again(self):
        entry = {**QBC, "end": "2026-11", "ended": "2026-11-14"}
        set_history_status("Company", entry, ended=False)
        assert is_active(entry)
        assert "end" not in entry

    def test_restore_clears_a_stray_end_on_other_fields(self):
        """is_active 不分欄位都看 end：取消劃掉也不分欄位都拿掉，免得救不回來。"""
        entry = {"value": "登山", "added": "", "ended": "", "end": "2020"}
        assert not is_active(entry)
        set_history_status("Interests", entry, ended=False)
        assert is_active(entry)

    def test_other_fields_do_not_get_an_end(self):
        entry = {"value": "登山", "added": "", "ended": ""}
        set_history_status("Interests", entry, ended=True, today="2026-11-14")
        assert entry == {"value": "登山", "added": "", "ended": "2026-11-14"}

    def test_same_name_position_fills_the_missing_start(self):
        """和現職同名：不另外新增，把到職年月補進那一筆。"""
        current = {"company": "QBC", "title": "QA", "added": "2026-11-08", "ended": ""}
        anki = PersonAnki([person(1, Display_Name="田中さん", Company=json.dumps([current]))])
        _act(anki, 1, action="add_history", field="Company", item={"company": "QBC", "title": "QA", "start": "2025"})
        assert _list(anki, 1, "Company") == [{**current, "start": "2025"}]

    def test_exact_duplicate_position_is_an_error(self):
        """完全重複：丟錯，TG 不會回「已儲存」。"""
        from app.core.exceptions import InvalidNoteFieldsError

        anki = PersonAnki([person(1, Display_Name="田中さん", Company=json.dumps([QBC]))])
        with pytest.raises(InvalidNoteFieldsError, match="已經有這份現職"):
            _act(anki, 1, action="add_history", field="Company", item={"company": "QBC", "title": "QA", "start": "2021"})
        assert anki.updated == []

    def test_same_name_as_a_past_job_is_a_new_entry(self):
        """以前做過、又回去：和以前的工作同名不算重複。"""
        anki = PersonAnki([person(1, Display_Name="田中さん", Company=json.dumps([SONY]))])
        _act(anki, 1, action="add_history", field="Company", item={"company": "Sony"})
        assert [is_active(e) for e in _list(anki, 1, "Company")] == [False, True]

    def test_add_position_with_start_keeps_the_shape(self):
        anki = PersonAnki([person(1, Display_Name="田中さん")])
        _act(anki, 1, action="add_history", field="Company", item={"company": "QBC", "title": "QA", "start": "2025"})
        assert _list(anki, 1, "Company") == [
            {"company": "QBC", "title": "QA", "start": "2025", "added": today_iso(), "ended": ""}
        ]

    @pytest.mark.parametrize("field", ["Company", "Gifts_JSON"])
    def test_set_hidden_and_back(self, field):
        item = dict(QBC) if field == "Company" else {"item": "花", "direction": "given"}
        anki = PersonAnki([person(1, Display_Name="田中さん", **{field: json.dumps([item])})])
        fp = item_fingerprint(item)
        _act(anki, 1, action="set_hidden", field=field, index=0, fingerprint=fp, hidden=True)
        assert _list(anki, 1, field) == [{**item, "hidden": True}]
        _act(anki, 1, action="set_hidden", field=field, index=0, fingerprint=fp, hidden=False)
        assert _list(anki, 1, field) == [item]

    def test_set_hidden_on_a_malformed_entry(self):
        from app.core.exceptions import InvalidNoteFieldsError

        anki = PersonAnki([person(1, Display_Name="田中さん", Interests=json.dumps(["登山"]))])
        with pytest.raises(InvalidNoteFieldsError, match="興趣的格式不對"):
            _act(anki, 1, action="set_hidden", field="Interests", index=0, hidden=True)
        assert anki.updated == []

    def test_set_hidden_on_a_broken_list_does_not_overwrite(self):
        from app.core.exceptions import AnkiFieldCorruptedError

        anki = PersonAnki([person(1, Display_Name="田中さん", Company="{壞掉")])
        with pytest.raises(AnkiFieldCorruptedError):
            _act(anki, 1, action="set_hidden", field="Company", index=0, hidden=True)
        assert anki.updated == []

    def test_set_hidden_rejects_unknown_fields(self):
        from app.core.exceptions import InvalidNoteFieldsError

        anki = PersonAnki([person(1, Display_Name="田中さん")])
        with pytest.raises(InvalidNoteFieldsError):
            _act(anki, 1, action="set_hidden", field="Notes", index=0, hidden=True)


# =============================================================================
# 顯示（TG、AI 話題）
# =============================================================================


class TestDisplay:
    def test_summary_lists_only_current_jobs(self):
        text = view.summary_text({"Display_Name": "陳さん"}, {"Company": [SONY, QBC, OLD]}, date(2026, 11, 14))
        assert "QBC・QA" in text and "Sony" not in text and "舊公司" not in text

    def test_full_text_lists_past_jobs(self):
        text = view.full_text({"Display_Name": "陳さん"}, {"Company": [SONY, QBC, OLD]})
        _assert_tg_html(text)
        assert "• 職位：QBC・QA" in text
        assert "• 以前的工作：Sony（2019-04～2021-03）、舊公司（～2025-01）" in text

    def test_new_text_is_escaped(self):
        nasty = '<b>&"x'
        item = {"company": nasty, "title": nasty, "start": "2019", "end": "2020", "note": nasty, "added": "", "ended": ""}
        _assert_tg_html(view.item_line("Company", item))
        text = view.full_text({"Display_Name": "陳さん"}, {"Company": [item]})
        _assert_tg_html(text)
        assert nasty not in text

    def test_topics_get_the_period(self):
        line = person_profile_topics.history_text("Company", [QBC, SONY])
        assert line == "QBC・QA（2025～）；以前：Sony（2019-04～2021-03）"

    @pytest.mark.parametrize(
        ("typed", "expected"),
        [("2021", "2021"), ("2021-4", "2021-04"), ("2021/04", "2021-04"), ("2021年4月", "2021-04"),
         ("2021-04-01", "2021-04-01"), ("2021年4月1日", "2021-04-01"), ("2021-13", None), ("2021-00", None),
         ("2021-04-31", None), ("2021-123", None), ("去年", None), ("1800", None), ("", None)],
    )
    def test_parse_period_input(self, typed, expected):
        assert view.parse_period_input(typed) == expected

    def test_toggle_labels(self):
        assert view.history_toggle_label("Company", True) == "離職"
        assert view.history_toggle_label("Company", False) == "恢復現職"
        assert view.history_toggle_label("Interests", True) == "劃掉"


# =============================================================================
# 長按的選單（§5.4）
# =============================================================================


def _start(payload: str, note: dict, note_service=None):
    message, _ = _message(f"/start {payload}")
    note_service = note_service or _note_service()
    _run(commands.command_start_handler(
        message, _anki(), mock.MagicMock(), _card_service(note), state=_fsm(), note_service=note_service
    ))
    return message, note_service


class TestLongPressMenus:
    def test_item_menu_offers_hide_leave_and_delete(self):
        fp = item_fingerprint(QBC)
        message, note_service = _start(f"pitm-Company-0_{fp}-PP_1", _note(Company=json.dumps([QBC])))
        note_service.run_action.assert_not_awaited()
        (text,) = _sent(message.answer)
        _assert_tg_html(text)
        assert "[現職] QBC・QA（2025～）" in text
        rows = message.answer.await_args.kwargs["reply_markup"].inline_keyboard
        assert [(b.text, b.callback_data) for b in rows[0]] == [
            ("在卡面隱藏", f"pp:hd:5:CO:0:{fp}:h"), ("離職", f"pp:hx:5:CO:0:{fp}:e"),
        ]
        assert [(b.text, b.callback_data) for b in rows[1]] == [("修改任職期間／說明", f"pp:pe:5:0:{fp}")]
        assert rows[2][0].callback_data == f"pp:del:5:CO:0:{fp}"

    def test_item_menu_for_a_hidden_gift(self):
        gift = {"item": "花", "direction": "given", "hidden": True}
        fp = item_fingerprint(gift)
        message, _ = _start(f"pitm-Gifts_JSON-0_{fp}-PP_1", _note(Gifts_JSON=json.dumps([gift])))
        assert "目前在卡面隱藏" in _sent(message.answer)[0]
        rows = message.answer.await_args.kwargs["reply_markup"].inline_keyboard
        assert [b.text for b in rows[0]] == ["在卡面顯示"]

    def test_item_menu_follows_a_moved_item(self):
        """序號過時（前面多了一筆）時，用指紋找到同一筆。"""
        fp = item_fingerprint(QBC)
        message, _ = _start(f"pitm-Company-0_{fp}-PP_1", _note(Company=json.dumps([SONY, QBC])))
        rows = message.answer.await_args.kwargs["reply_markup"].inline_keyboard
        assert rows[0][0].callback_data == f"pp:hd:5:CO:1:{fp}:h"

    def test_item_menu_for_a_missing_item(self):
        message, _ = _start("pitm-Company-0_zzz-PP_1", _note(Company=json.dumps([QBC])))
        assert "已經變了或不在了" in _sent(message.answer)[0]

    def test_item_menu_for_a_broken_list(self):
        """清單壞了：說格式壞了，不要叫使用者去同步。"""
        message, _ = _start("pitm-Company-0_x-PP_1", _note(Company="{壞掉"))
        text = _sent(message.answer)[0]
        assert "格式壞了" in text and "同步" not in text

    def test_item_menu_for_a_malformed_item_offers_only_delete(self):
        fp = item_fingerprint("登山")
        message, _ = _start(f"pitm-Interests-0_{fp}-PP_1", _note(Interests=json.dumps(["登山"])))
        rows = message.answer.await_args.kwargs["reply_markup"].inline_keyboard
        assert [[b.text for b in row] for row in rows] == [["刪除", "回摘要"]]

    def test_item_menu_for_a_past_job_offers_restore(self):
        fp = item_fingerprint(SONY)
        message, _ = _start(f"pitm-Company-0_{fp}-PP_1", _note(Company=json.dumps([SONY])))
        assert "[以前] Sony（2019-04～2021-03）" in _sent(message.answer)[0]
        rows = message.answer.await_args.kwargs["reply_markup"].inline_keyboard
        assert (rows[0][1].text, rows[0][1].callback_data) == ("恢復現職", f"pp:hx:5:CO:0:{fp}:o")

    def test_menu_buttons_round_trip(self):
        """選單吐出的 callback data 直接按下去，動作參數要對得上。"""
        fp = item_fingerprint(QBC)
        message, _ = _start(f"pitm-Company-0_{fp}-PP_1", _note(Company=json.dumps([QBC])))
        rows = message.answer.await_args.kwargs["reply_markup"].inline_keyboard
        note_service = _note_service()
        _run(person_profile.on_person_button(_callback(rows[0][0].callback_data), _fsm(), note_service, _card_service()))
        note_service.run_action.assert_awaited_once_with(
            5, "set_hidden", {"field": "Company", "index": 0, "fingerprint": fp, "hidden": True}
        )
        note_service = _note_service()
        _run(person_profile.on_person_button(_callback(rows[0][1].callback_data), _fsm(), note_service, _card_service()))
        note_service.run_action.assert_awaited_once_with(
            5, "set_history_status", {"field": "Company", "index": 0, "fingerprint": fp, "ended": True}
        )

    def test_hide_button_runs_the_action(self):
        state, note_service, card_service = _fsm(), _note_service(), _card_service()
        query = _callback("pp:hd:5:CO:0:abc:h")
        _run(person_profile.on_person_button(query, state, note_service, card_service))
        note_service.run_action.assert_awaited_once_with(
            5, "set_hidden", {"field": "Company", "index": 0, "fingerprint": "abc", "hidden": True}
        )
        undo = query.message.answer.await_args.kwargs["reply_markup"].inline_keyboard[0][0]
        assert undo.callback_data == "pp:hd:5:CO:0:abc:s"

    def test_field_menu(self):
        note = {**_note(Appearance="戴眼鏡"), "tags": ["人脈卡隱藏::Appearance"]}
        message, _ = _start("pfld-Appearance-none-PP_1", note)
        text = _sent(message.answer)[0]
        assert "外貌特徵" in text and "目前在卡面隱藏" in text and "戴眼鏡" in text
        index = view.EDITABLE_FIELDS.index("Appearance")
        rows = message.answer.await_args.kwargs["reply_markup"].inline_keyboard
        assert [b.callback_data for b in rows[0]] == [f"pp:ef:5:{index}", f"pp:hf:5:{index}:s"]

    def test_field_menu_only_for_text_fields(self):
        message, _ = _start("pfld-Company-none-PP_1", _note())
        assert "不支援" in _sent(message.answer)[0]

    def test_field_hide_button_adds_the_tag(self):
        state, note_service, card_service = _fsm(), _note_service(), _card_service()
        client = card_service.anki_client
        client.add_tags = mock.AsyncMock()
        client.remove_tags = mock.AsyncMock()
        client.sync = mock.AsyncMock()
        index = view.EDITABLE_FIELDS.index("Appearance")
        query = _callback(f"pp:hf:5:{index}:h")
        _run(person_profile.on_person_button(query, state, note_service, card_service))
        client.add_tags.assert_awaited_once_with([5], "人脈卡隱藏::Appearance")
        client.sync.assert_awaited()  # 同步後手機的卡面才會遮
        undo = query.message.answer.await_args.kwargs["reply_markup"].inline_keyboard[0][0]
        assert undo.callback_data == f"pp:hf:5:{index}:s"
        _run(person_profile.on_person_button(_callback(f"pp:hf:5:{index}:s"), state, note_service, card_service))
        client.remove_tags.assert_awaited_once_with([5], "人脈卡隱藏::Appearance")

    def test_leave_link_says_left(self):
        fp = item_fingerprint(QBC)
        message, note_service = _start(f"pend-Company-0_{fp}-PP_1", _note(Company=json.dumps([QBC])))
        note_service.run_action.assert_awaited_once_with(
            5, "set_history_status", {"field": "Company", "index": 0, "fingerprint": fp, "ended": True}
        )
        assert "已標成離職" in _sent(message.answer)[0]

    def test_duplicate_position_in_tg_is_not_reported_as_saved(self):
        from app.core.exceptions import InvalidNoteFieldsError

        state, card_service = _fsm(), _card_service()
        note_service = _note_service()
        note_service.run_action = mock.AsyncMock(side_effect=InvalidNoteFieldsError("已經有這份現職（QBC・QA），沒有新增。"))
        message, _ = _message()
        _run(person_profile.start_add(message, state, 5, "Company"))
        for text in ("QBC", "QA", "2025"):
            typed, _ = _message(text)
            _run(person_profile.process_add_text(typed, state))
        for _ in range(2):  # 離職、說明略過
            _run(person_profile.on_person_button(_callback("pp:skip"), state, note_service, card_service))
        query = _callback("pp:save")
        _run(person_profile.on_person_button(query, state, note_service, card_service))
        sent = _sent(query.message.answer)
        assert any("已經有這份現職" in text for text in sent)
        assert not any("已儲存" in text for text in sent)


# =============================================================================
# TG 的工作經歷：新增以前的工作、修改任職期間與說明（A1、A2、A4）
# =============================================================================


def _type(state, *texts):
    """依序輸入文字，回傳最後一則訊息（看回覆用）。"""
    typed = None
    for text in texts:
        typed, _ = _message(text)
        _run(person_profile.process_add_text(typed, state))
    return typed


def _tap(data, state, note_service=None, card_service=None):
    query = _callback(data)
    _run(person_profile.on_person_button(query, state, note_service or _note_service(), card_service or _card_service()))
    return query


class TestTgPositionAdd:
    def test_add_a_past_job_with_period_and_note(self):
        state, note_service, card_service = _fsm(), _note_service(), _card_service()
        message, _ = _message()
        _run(person_profile.start_add(message, state, 5, "Company"))
        _type(state, "Sony", "PM", "2019/4", "2021-03", "做遊戲主機")
        query = _tap("pp:save", state, note_service, card_service)
        note_service.run_action.assert_awaited_once_with(5, "add_history", {"field": "Company", "item": {
            "company": "Sony", "title": "PM", "start": "2019-04", "end": "2021-03", "note": "做遊戲主機",
        }})
        assert any("已儲存" in text for text in _sent(query.message.answer))

    def test_preview_says_past_job(self):
        state = _fsm()
        message, _ = _message()
        _run(person_profile.start_add(message, state, 5, "Company"))
        last = _type(state, "Sony", "PM", "2019", "?", "工作 7 年")
        (text,) = _sent(last.answer)
        _assert_tg_html(text)
        assert "（以前的工作）" in text and "Sony・PM（2019～？）" in text and "說明：工作 7 年" in text

    @pytest.mark.parametrize("typed", ["?", "？"])
    def test_unknown_end_is_accepted(self, typed):
        state = _fsm()
        message, _ = _message()
        _run(person_profile.start_add(message, state, 5, "Company"))
        _type(state, "台北的公司", "工程師", typed)  # 到職那一步不收 ?
        assert _run(state.get_data())["step"] == 2
        _tap("pp:skip", state)
        _type(state, typed)
        data = _run(state.get_data())
        assert (data["step"], data["item"]["end"]) == (4, "?")

    def test_end_before_start_is_asked_again(self):
        """離職早於到職：在離職那一步就擋，不必等儲存才整筆失敗。"""
        state = _fsm()
        message, _ = _message()
        _run(person_profile.start_add(message, state, 5, "Company"))
        last = _type(state, "Sony", "PM", "2022", "2021-12")
        (text,) = _sent(last.answer)
        assert "不能早於到職" in text and "寫 ?" in text and "略過" not in text and "清空" not in text
        buttons = last.answer.await_args.kwargs["reply_markup"].inline_keyboard[0]
        assert buttons[0].callback_data == "pp:back"
        assert _run(state.get_data())["step"] == 3

    def test_a_past_job_is_not_merged_into_the_current_one(self):
        """和現職同名的以前的工作（離開後又回去）另外記一筆，不把年月補進現職。"""
        anki = PersonAnki([person(1, Display_Name="田中さん", Company=json.dumps([QBC]))])
        _act(anki, 1, action="add_history", field="Company",
             item={"company": "QBC", "title": "QA", "start": "2015", "end": "2018"})
        stored = _list(anki, 1, "Company")
        assert stored[0] == QBC
        assert stored[1]["start"] == "2015" and stored[1]["end"] == "2018"

    def test_an_identical_past_job_is_an_error(self):
        from app.core.exceptions import InvalidNoteFieldsError

        anki = PersonAnki([person(1, Display_Name="田中さん", Company=json.dumps([SONY]))])
        with pytest.raises(InvalidNoteFieldsError, match="已經有這份以前的工作"):
            _act(anki, 1, action="add_history", field="Company",
                 item={"company": "Sony", "start": "2019-04", "end": "2021-03"})


class TestEditPositionAction:
    def test_updates_period_and_note_keeping_the_rest(self):
        entry = {**QBC, "hidden": True}
        anki = PersonAnki([person(1, Display_Name="田中さん", Company=json.dumps([entry]))])
        _act(anki, 1, action="edit_position", index=0, fingerprint=item_fingerprint(entry),
             start="2024/4", end="2026-10", note="測試自動化")
        assert _list(anki, 1, "Company") == [{**QBC, "hidden": True, "start": "2024-04", "end": "2026-10",
                                              "note": "測試自動化"}]

    def test_empty_values_clear(self):
        entry = {**SONY, "note": "x"}
        anki = PersonAnki([person(1, Display_Name="田中さん", Company=json.dumps([entry]))])
        _act(anki, 1, action="edit_position", index=0, fingerprint=item_fingerprint(entry), start="", end="", note="")
        assert _list(anki, 1, "Company") == [{"company": "Sony", "title": "", "added": "", "ended": ""}]

    def test_end_before_start_is_rejected(self):
        from app.core.exceptions import InvalidNoteFieldsError

        anki = PersonAnki([person(1, Display_Name="田中さん", Company=json.dumps([QBC]))])
        with pytest.raises(InvalidNoteFieldsError, match="不能早於到職"):
            _act(anki, 1, action="edit_position", index=0, fingerprint=item_fingerprint(QBC), start="2025", end="2024",
                 note="")

    def test_a_changed_item_is_not_touched(self):
        from app.core.exceptions import JsonItemChangedError

        anki = PersonAnki([person(1, Display_Name="田中さん", Company=json.dumps([QBC]))])
        with pytest.raises(JsonItemChangedError):
            _act(anki, 1, action="edit_position", index=0, fingerprint="zzz", start="2020", end="", note="")


class TestTgPositionEdit:
    @staticmethod
    def _begin(entry=QBC, items=None):
        items = items or [entry]
        state, note_service = _fsm(), _note_service()
        card_service = _card_service(_note(Company=json.dumps(items)))
        query = _tap(f"pp:pe:5:{items.index(entry)}:{item_fingerprint(entry)}", state, note_service, card_service)
        return state, note_service, card_service, query

    def test_asks_with_the_current_values_and_saves_only_period_and_note(self):
        state, note_service, card_service, query = self._begin()
        sent = _sent(query.message.answer)
        assert "修改職位" in sent[0] and "目前：2025" in sent[1] and "略過＝保留" in sent[1]
        _tap("pp:skip", state, note_service, card_service)  # 到職保留 2025
        _type(state, "2026/10", "做測試")
        _tap("pp:save", state, note_service, card_service)
        note_service.run_action.assert_awaited_once_with(5, "edit_position", {
            "index": 0, "fingerprint": item_fingerprint(QBC), "start": "2025", "end": "2026-10", "note": "做測試",
            "expected_start": "2025", "expected_end": "",
        })

    def test_preview_is_an_edit_of_a_past_job(self):
        state, note_service, card_service, _ = self._begin()
        _tap("pp:skip", state, note_service, card_service)
        last = _type(state, "2026-10", "做測試")
        (text,) = _sent(last.answer)
        _assert_tg_html(text)
        assert "<b>修改職位</b>（以前的工作）" in text and "登錄日" not in text

    def test_clear_removes_a_value(self):
        state, _, _, _ = self._begin()
        _type(state, "清空")
        assert _run(state.get_data())["item"]["start"] == ""

    def test_keeping_an_end_that_is_now_before_the_start_is_asked_again(self):
        """修改時略過＝保留原本的離職；到職改得比它晚，在離職那一步就擋。"""
        state, note_service, card_service, _ = self._begin(SONY)
        _type(state, "2022")
        query = _tap("pp:skip", state, note_service, card_service)
        assert any("不能早於到職" in text and "寫 ?" in text for text in _sent(query.message.answer))
        assert _run(state.get_data())["step"] == 1

    def test_follows_a_moved_item(self):
        """序號過時時用指紋找到同一筆，儲存用找到的序號。"""
        state, note_service = _fsm(), _note_service()
        card_service = _card_service(_note(Company=json.dumps([SONY, QBC])))
        _tap(f"pp:pe:5:0:{item_fingerprint(QBC)}", state, note_service, card_service)
        assert _run(state.get_data())["edit"]["index"] == 1

    def test_a_missing_item(self):
        state, note_service = _fsm(), _note_service()
        card_service = _card_service(_note(Company=json.dumps([QBC])))
        query = _tap("pp:pe:5:0:zzz", state, note_service, card_service)
        assert any("已經變了或不在了" in text for text in _sent(query.message.answer))
        assert _run(state.get_state()) is None


# =============================================================================
# TG 裡的隱藏開關（H1）
# =============================================================================


class TestTgHideToggles:
    def test_list_rows_have_hide_and_edit_buttons(self):
        hidden = {**SONY, "hidden": True}
        state, note_service = _fsm(), _note_service()
        card_service = _card_service(_note(Company=json.dumps([QBC, hidden])))
        query = _tap("pp:ls:5:CO", state, note_service, card_service)
        (text,) = _sent(query.message.answer)
        assert "隱藏只影響卡面" in text
        rows = query.message.answer.await_args.kwargs["reply_markup"].inline_keyboard
        fp0, fp1 = item_fingerprint(QBC), item_fingerprint(hidden)
        assert [(b.text, b.callback_data) for b in rows[0]] == [
            ("刪 1", f"pp:del:5:CO:0:{fp0}"), ("離職 1", f"pp:hx:5:CO:0:{fp0}:e"),
            ("隱藏 1", f"pp:hd:5:CO:0:{fp0}:h"), ("改 1", f"pp:pe:5:0:{fp0}"),
        ]
        assert (rows[1][2].text, rows[1][2].callback_data) == ("顯示 2", f"pp:hd:5:CO:1:{fp1}:s")

    def test_list_of_another_field_has_no_edit_button(self):
        gift = {"item": "花", "direction": "given"}
        state, note_service = _fsm(), _note_service()
        card_service = _card_service(_note(Gifts_JSON=json.dumps([gift])))
        query = _tap("pp:ls:5:G", state, note_service, card_service)
        rows = query.message.answer.await_args.kwargs["reply_markup"].inline_keyboard
        assert [b.text for b in rows[0]] == ["刪 1", "隱藏 1"]

    def test_list_hide_button_hides_the_item(self):
        state, note_service, card_service = _fsm(), _note_service(), _card_service()
        _tap("pp:hd:5:CO:0:abc:h", state, note_service, card_service)
        note_service.run_action.assert_awaited_once_with(
            5, "set_hidden", {"field": "Company", "index": 0, "fingerprint": "abc", "hidden": True}
        )

    @pytest.mark.parametrize(
        ("tags", "label", "mode"), [([], "在卡面隱藏", "h"), (["人脈卡隱藏::Hometown"], "在卡面顯示", "s")]
    )
    def test_edit_field_has_a_hide_toggle(self, tags, label, mode):
        note = _note(Hometown="新北")
        note["tags"] = tags
        state, note_service, card_service = _fsm(), _note_service(), _card_service(note)
        index = view.EDITABLE_FIELDS.index("Hometown")
        query = _tap(f"pp:ef:5:{index}", state, note_service, card_service)
        (text,) = _sent(query.message.answer)
        assert ("目前在卡面隱藏" in text) == bool(tags)
        toggle = query.message.answer.await_args.kwargs["reply_markup"].inline_keyboard[0][0]
        assert (toggle.text, toggle.callback_data) == (label, f"pp:hf:5:{index}:{mode}")
        assert _run(state.get_state()) == person_profile.PersonStates.edit_value.state


# =============================================================================
# 第二輪審查的修正（Critical 1–3、Important 1–8）
# =============================================================================


def _edit_session(items, index=0):
    """從清單的「改 N」進入修改；回傳 (state, note_service, card_service, 第一則問句的 query)。"""
    state, note_service = _fsm(), _note_service()
    card_service = _card_service(_note(Company=json.dumps(items)))
    query = _tap(f"pp:pe:5:{index}:{item_fingerprint(items[index])}", state, note_service, card_service)
    return state, note_service, card_service, query


def _last_prompt(message):
    """這則訊息最後送出的文字（按鈕的話傳 query.message）。"""
    return _sent(message.answer)[-1]


class TestStepHintsByMode:
    def test_adding_says_what_skipping_means(self):
        state = _fsm()
        message, _ = _message()
        _run(person_profile.start_add(message, state, 5, "Company"))
        last = _type(state, "Sony", "PM", "2021")
        assert "還在這裡工作就略過" in _last_prompt(last)

    def test_editing_a_past_job_does_not_say_skip_means_current(self):
        """修改時略過＝保留：不能再寫「還在這裡工作就略過」（照做會保留離職，仍是以前的工作）。"""
        state, note_service, card_service, _ = _edit_session([SONY])
        query = _tap("pp:skip", state, note_service, card_service)  # 到職保留
        prompt = _last_prompt(query.message)
        assert "還在這裡工作就略過" not in prompt
        assert "目前：2021-03" in prompt and "輸入「清空」＝改回現職" in prompt

    def test_editing_a_current_job_says_clear_clears(self):
        state, note_service, card_service, _ = _edit_session([QBC])
        prompt = _last_prompt(_tap("pp:skip", state, note_service, card_service).message)
        assert "改回現職" not in prompt and "輸入「清空」清除" in prompt

    def test_bad_period_hint_by_mode(self):
        state, _, _, _ = _edit_session([SONY])
        bad = _type(state, "去年")
        (text,) = _sent(bad.answer)
        assert "略過＝保留目前的值" in text and "不知道就略過" not in text


class TestEditPromptIsEscaped:
    def test_note_with_angle_brackets(self):
        """說明是使用者打的字：「經驗 <3 年」不轉義的話，Telegram 會拒收這則問句。"""
        entry = {**QBC, "note": "經驗 <3 年 & <b>"}
        state, note_service, card_service, _ = _edit_session([entry])
        _tap("pp:skip", state, note_service, card_service)
        prompt = _last_prompt(_tap("pp:skip", state, note_service, card_service).message)
        _assert_tg_html(prompt)
        assert "經驗 &lt;3 年 &amp; &lt;b&gt;" in prompt


class TestClearingTheEnd:
    def test_tg_left_job_becomes_current(self):
        """TG 按過「離職」的（end 與 ended 都有）：清空離職＝改回現職，失效日一起清掉。"""
        entry = {**QBC, "end": "2026-10", "ended": "2026-10-20"}
        anki = PersonAnki([person(1, Display_Name="田中さん", Company=json.dumps([entry]))])
        _act(anki, 1, action="edit_position", index=0, fingerprint=item_fingerprint(entry),
             start="2025", end="", note="")
        (stored,) = _list(anki, 1, "Company")
        assert is_active(stored) and stored["ended"] == ""

    def test_old_struck_job_without_an_end_stays_past(self):
        """只有失效日的舊資料：離職本來就空的，改說明不會讓它復活。"""
        anki = PersonAnki([person(1, Display_Name="田中さん", Company=json.dumps([OLD]))])
        _act(anki, 1, action="edit_position", index=0, fingerprint=item_fingerprint(OLD), start="", end="", note="x")
        (stored,) = _list(anki, 1, "Company")
        assert not is_active(stored) and stored["ended"] == OLD["ended"]

    def test_tg_preview_matches(self):
        entry = {**QBC, "end": "2026-10", "ended": "2026-10-20"}
        state, note_service, card_service, _ = _edit_session([entry])
        _tap("pp:skip", state, note_service, card_service)
        _type(state, "清空")
        query = _tap("pp:skip", state, note_service, card_service)  # 說明略過 → 預覽
        assert "（現職）" in _last_prompt(query.message)
        data = _run(state.get_data())
        assert data["item"]["end"] == "" and data["item"]["ended"] == ""


class TestOrderErrorWayOut:
    def test_unknown_end_after_the_error(self):
        """照提示寫 ? 之後就過了，仍是以前的工作（不會被引導成現職）。"""
        state = _fsm()
        message, _ = _message()
        _run(person_profile.start_add(message, state, 5, "Company"))
        _type(state, "Sony", "PM", "2022", "2021-12", "?")
        data = _run(state.get_data())
        assert (data["step"], data["item"]["end"]) == (4, "?")

    def test_back_to_the_start_step(self):
        state, note_service, card_service = _fsm(), _note_service(), _card_service()
        message, _ = _message()
        _run(person_profile.start_add(message, state, 5, "Company"))
        _type(state, "Sony", "PM", "2022", "2021-12")
        query = _tap("pp:back", state, note_service, card_service)
        assert _run(state.get_data())["step"] == 2
        assert "什麼時候到職" in _last_prompt(query.message)
        _type(state, "2020", "2021-12", "做遊戲")
        _tap("pp:save", state, note_service, card_service)
        assert note_service.run_action.await_args.args[2]["item"]["start"] == "2020"

    def test_back_when_the_flow_is_over(self):
        state, note_service, card_service = _fsm(), _note_service(), _card_service()
        query = _tap("pp:back", state, note_service, card_service)
        assert "已結束" in _sent(query.message.answer)[0]


class TestOldValuesOnTheCard:
    def test_period_order_normalizes_first(self):
        """卡上手改的 2021/4：直接比字串會說 2021-05 早於它。"""
        from app.schemas.person_profile import period_order_error

        assert period_order_error("2021/4", "2021-05") is None
        assert period_order_error("2021/4", "2021-03") == "離職（2021-03）不能早於到職（2021-04）"
        assert period_order_error("看不懂", "2021") is None  # 看不懂的由格式檢查報

    def test_unnormalized_start_is_tidied_when_editing(self):
        entry = {**SONY, "start": "2021/4", "end": ""}
        state, _, _, query = _edit_session([entry])
        assert "目前：2021-04" in _last_prompt(query.message)

    def test_unreadable_old_value_cannot_be_kept(self):
        entry = {**QBC, "start": "2021年春"}
        state, note_service, card_service, query = _edit_session([entry])
        assert "格式不對" in _last_prompt(query.message)
        refused = _tap("pp:skip", state, note_service, card_service)
        assert any("不能保留" in text for text in _sent(refused.message.answer))
        assert _run(state.get_data())["step"] == 0
        _type(state, "2021-04")
        assert _run(state.get_data())["step"] == 1


class TestSaveFailureKeepsAnswers:
    def _ready(self, note_service, card_service):
        state = _fsm()
        _tap(f"pp:pe:5:0:{item_fingerprint(QBC)}", state, note_service, card_service)
        _tap("pp:skip", state, note_service, card_service)
        _type(state, "2026-10", "做測試")
        return state

    def test_failed_write_stays_on_confirm(self):
        from app.core.exceptions import JsonItemChangedError

        note_service = _note_service()
        note_service.run_action = mock.AsyncMock(side_effect=JsonItemChangedError("這一筆的任職期間在別處被改過了"))
        card_service = _card_service(_note(Company=json.dumps([QBC])))
        state = self._ready(note_service, card_service)
        query = _tap("pp:save", state, note_service, card_service)
        sent = _sent(query.message.answer)
        assert any("別處被改過" in text for text in sent) and any("回答都還在" in text for text in sent)
        assert _run(state.get_state()) == person_profile.PersonStates.add_confirm.state
        retry = query.message.answer.await_args.kwargs["reply_markup"].inline_keyboard[0][0]
        assert retry.callback_data == "pp:save"

    def test_summary_failure_after_a_write_still_says_updated(self):
        from app.infrastructure.anki.client import AnkiConnectError

        note_service = _note_service()
        card_service = _card_service(_note(Company=json.dumps([QBC])))
        state = self._ready(note_service, card_service)
        card_service.get_note = mock.AsyncMock(side_effect=AnkiConnectError("連不上"))
        query = _tap("pp:save", state, note_service, card_service)
        assert any("已更新這份工作" in text and "摘要暫時讀不到" in text for text in _sent(query.message.answer))
        assert _run(state.get_state()) is None


class TestEditPositionGuards:
    def test_all_three_values_are_required(self):
        """漏送不能被當成清掉（之後別的呼叫端只送說明時會無聲清掉任職起訖）。"""
        from app.core.exceptions import InvalidNoteFieldsError

        anki = PersonAnki([person(1, Display_Name="田中さん", Company=json.dumps([SONY]))])
        with pytest.raises(InvalidNoteFieldsError, match="參數不正確"):
            _act(anki, 1, action="edit_position", index=0, fingerprint=item_fingerprint(SONY), note="x")
        assert _list(anki, 1, "Company") == [SONY]

    def test_company_cannot_be_changed_here(self):
        from app.core.exceptions import InvalidNoteFieldsError

        anki = PersonAnki([person(1, Display_Name="田中さん", Company=json.dumps([QBC]))])
        with pytest.raises(InvalidNoteFieldsError):
            _act(anki, 1, action="edit_position", index=0, fingerprint=item_fingerprint(QBC),
                 start="", end="", note="", company="X")

    def test_becoming_an_identical_past_job_is_rejected(self):
        from app.core.exceptions import InvalidNoteFieldsError

        twin = {**SONY, "start": "2018"}
        anki = PersonAnki([person(1, Display_Name="田中さん", Company=json.dumps([SONY, twin]))])
        with pytest.raises(InvalidNoteFieldsError, match="已經有這份以前的工作"):
            _act(anki, 1, action="edit_position", index=1, fingerprint=item_fingerprint(twin),
                 start="2019-04", end="2021-03", note="")

    def test_becoming_a_second_current_job_with_the_same_name_is_rejected(self):
        from app.core.exceptions import InvalidNoteFieldsError

        past = {**QBC, "start": "2015", "end": "2018"}
        anki = PersonAnki([person(1, Display_Name="田中さん", Company=json.dumps([past, QBC]))])
        with pytest.raises(InvalidNoteFieldsError, match="已經有同名的現職"):
            _act(anki, 1, action="edit_position", index=0, fingerprint=item_fingerprint(past),
                 start="2015", end="", note="")

    def test_same_name_jobs_are_told_apart_by_their_period(self):
        """同名的兩份工作指紋相同：序號過時對到另一筆時，用開始修改時的原值擋下。"""
        from app.core.exceptions import JsonItemChangedError

        past = {**QBC, "start": "2015", "end": "2018"}
        anki = PersonAnki([person(1, Display_Name="田中さん", Company=json.dumps([QBC]))])  # 以前那筆已被刪
        assert item_fingerprint(past) == item_fingerprint(QBC)
        with pytest.raises(JsonItemChangedError, match="別處被改過"):
            _act(anki, 1, action="edit_position", index=0, fingerprint=item_fingerprint(past),
                 start="2015", end="2017", note="", expected_start="2015", expected_end="2018")
        assert _list(anki, 1, "Company") == [QBC]

    def test_edit_targets_the_right_one_of_two_same_name_jobs(self):
        past = {**QBC, "start": "2015", "end": "2018"}
        state, _, _, _ = _edit_session([past, QBC], index=1)
        data = _run(state.get_data())
        assert data["edit"]["index"] == 1 and data["edit"]["end"] == ""


class TestHideToggleKeepsTheEdit:
    def test_typing_after_the_toggle_still_saves(self):
        note = _note(Hometown="新北")
        note["tags"] = []
        state, note_service, card_service = _fsm(), _note_service(), _card_service(note)
        client = card_service.anki_client
        client.add_tags = mock.AsyncMock()
        client.sync = mock.AsyncMock()
        index = view.EDITABLE_FIELDS.index("Hometown")
        _tap(f"pp:ef:5:{index}", state, note_service, card_service)
        _tap(f"pp:hf:5:{index}:h", state, note_service, card_service)
        assert _run(state.get_state()) == person_profile.PersonStates.edit_value.state
        typed, _ = _message("台北")
        _run(person_profile.process_edit_value(typed, state, note_service, card_service))
        note_service.update_fields.assert_awaited()
