"""Telegram 變慢、使用者連點按鈕時的處理（2026-11-16 正式機的 4 筆錯誤）。

Tests for slow Telegram and repeated button taps.

- 同一個人的 update 排隊（dispatcher 的事件隔離），並行時會互相清掉 FSM 狀態。
- 回應按鈕失敗（``query is too old``）不中斷 handler：取消照樣執行。
- 連點兩下的第二下（或舊訊息上的按鈕）不能拿來回答目前這一步：不會多略過一步。
- 改按鈕時 ``message is not modified`` 代表已處理過：直接結束，不冒成未預期錯誤。
"""

import json
from unittest import mock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.fsm.storage.memory import SimpleEventIsolation

from app.bot.dispatcher import setup_dispatcher
from app.bot.handlers import newcard_menu, person_profile
from app.bot.handlers.person_profile import PersonStates
from app.bot.utils.callback_reply import answer_callback
from app.infrastructure.anki.client import AnkiConnectError
from app.schemas.person_profile import item_fingerprint
from tests.test_bot_replies import MESSAGE_ID, _callback, _fsm, _message, _run, _sent
from tests.test_person_career import QBC
from tests.test_person_profile_bot import _card_service, _note, _note_service
from tests.test_person_profile_ingest import _ingested

OLD_ID, NEW_ID = MESSAGE_ID, MESSAGE_ID + 1


def _bad_request(text: str) -> TelegramBadRequest:
    return TelegramBadRequest(method=mock.MagicMock(), message=f"Bad Request: {text}")


NOT_MODIFIED = "message is not modified: specified new message content and reply markup are exactly the same"


def _press(data: str, state, *, message_id: int = OLD_ID, next_id: int = NEW_ID, note_service=None, card_service=None):
    """在 ``message_id`` 那則訊息上按 ``data``；這次送出的新訊息 ID 是 ``next_id``。"""
    query = _callback(data)
    query.message.message_id = message_id
    query.message.answer.return_value.message_id = next_id
    _run(person_profile.on_person_button(query, state, note_service or _note_service(), card_service or _card_service()))
    return query


def _type(state, text: str, *, next_id: int):
    """打字回答目前這一步；bot 回的訊息 ID 是 ``next_id``。"""
    typed, _ = _message(text)
    typed.answer.return_value.message_id = next_id
    _run(person_profile.process_add_text(typed, state))
    return typed


def _skip_to_confirm(state) -> int:
    """在每一步的提問訊息上按「略過」，直到預覽；回傳預覽的訊息 ID。"""
    for _ in range(10):
        if _run(state.get_state()) == PersonStates.add_confirm.state:
            return _run(state.get_data())["prompt_id"]
        prompt_id = _run(state.get_data())["prompt_id"]
        _press("pp:skip", state, message_id=prompt_id, next_id=prompt_id + 1)
    raise AssertionError("略過十次還沒到預覽")


def _start_company(state):
    """開始新增職位（每一步都能略過）；第一步的提問訊息 ID 是 ``OLD_ID``。"""
    message, _ = _message()
    _run(person_profile.start_add(message, state, 5, "Company"))
    data = _run(state.get_data())
    assert data["step"] == 0 and data["prompt_id"] == OLD_ID


class TestEventIsolation:
    def test_dispatcher_queues_updates_of_the_same_user(self):
        """沒有事件隔離時，同一個人的 update 會並行，一個 handler 清掉另一個寫到一半的狀態（KeyError: 'field'）。

        aiogram 的 router 只能掛一次，測試後解除掛載，避免影響之後的 setup。"""
        dp = setup_dispatcher()
        try:
            assert isinstance(dp.fsm.events_isolation, SimpleEventIsolation)
        finally:
            for sub_router in dp.sub_routers:
                sub_router._parent_router = None


class TestCallbackAnswerFailure:
    def test_cancel_still_runs_when_the_query_is_too_old(self):
        state = _fsm()
        _start_company(state)
        query = _callback("pp:cancel")
        query.answer.side_effect = _bad_request("query is too old and response timeout expired or query ID is invalid")
        _run(person_profile.on_person_button(query, state, _note_service(), _card_service()))
        assert _run(state.get_state()) is None
        query.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
        assert _sent(query.message.answer) == ["已取消。"]

    def test_other_bad_requests_are_not_swallowed(self):
        """只容忍「太晚回應」：其他 400（例如提示文字太長）是程式問題，照常往外拋。"""
        query = _callback("x")
        query.answer.side_effect = _bad_request("MESSAGE_TOO_LONG")
        with pytest.raises(TelegramBadRequest):
            _run(answer_callback(query, "x" * 300))

    def test_expired_alert_answer_does_not_raise(self):
        query = _callback("x")
        query.answer.side_effect = _bad_request("query is too old and response timeout expired or query ID is invalid")
        _run(answer_callback(query, "⌛ 流程已結束", show_alert=True))
        query.answer.assert_awaited_once_with("⌛ 流程已結束", show_alert=True)

    def test_network_errors_are_not_swallowed(self):
        """網路逾時照舊往外拋（交給全域錯誤處理），這次不改。"""
        query = _callback("x")
        query.answer.side_effect = TelegramNetworkError(method=mock.MagicMock(), message="Request timeout error")
        with pytest.raises(TelegramNetworkError):
            _run(answer_callback(query))


class TestRepeatedTaps:
    def test_double_skip_skips_one_step_only(self):
        """「略過」連點兩下：第二下排隊到第一下跑完才執行，那時按鈕所在的訊息已經不是目前的提問，直接忽略。"""
        state = _fsm()
        _start_company(state)
        first = _press("pp:skip", state)
        assert _run(state.get_data())["step"] == 1 and _run(state.get_data())["prompt_id"] == NEW_ID
        first.message.answer.assert_awaited_once()  # 問下一步

        second = _press("pp:skip", state, next_id=NEW_ID + 1)
        assert _run(state.get_data())["step"] == 1
        second.message.answer.assert_not_awaited()
        # 只回應一次，用短提示說明（不送聊天訊息，連點也不洗版）
        second.answer.assert_awaited_once()
        assert "不能用" in second.answer.await_args.args[0]

    def test_button_on_an_old_prompt_does_not_answer_the_current_step(self):
        """打字回答後，上一步提問訊息上的「略過」還在；按了不能略過目前這一步。"""
        state = _fsm()
        _start_company(state)
        typed, _ = _message("Sony")
        typed.answer.return_value.message_id = NEW_ID
        _run(person_profile.process_add_text(typed, state))
        assert _run(state.get_data())["step"] == 1

        query = _press("pp:skip", state, message_id=OLD_ID)
        assert _run(state.get_data())["step"] == 1
        assert _run(state.get_data())["item"] == {"company": "Sony"}
        query.message.answer.assert_not_awaited()
        # 過時的按鈕拿掉，讓使用者看得出它不能用
        query.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)

    def test_double_save_writes_once(self):
        state, note_service = _fsm(), _note_service()
        _start_company(state)
        preview_id = _skip_to_confirm(state)
        _press("pp:save", state, message_id=preview_id, note_service=note_service)
        second = _press("pp:save", state, message_id=preview_id, note_service=note_service)
        note_service.run_action.assert_awaited_once()
        second.message.answer.assert_not_awaited()


    def test_back_button_on_the_order_error_message(self):
        """離職早於到職：只認錯誤訊息上的「重新輸入到職」；原提問上的「略過」（職位等於現職）不再有效。"""
        state = _fsm()
        _start_company(state)
        _type(state, "Sony", next_id=OLD_ID + 1)
        _type(state, "PM", next_id=OLD_ID + 2)
        _type(state, "2022", next_id=OLD_ID + 3)  # 問離職的提問
        _type(state, "2021-12", next_id=OLD_ID + 4)  # 早於到職：錯誤訊息
        assert _run(state.get_data())["step"] == 3

        _press("pp:skip", state, message_id=OLD_ID + 3)
        assert _run(state.get_data())["step"] == 3 and "end" not in _run(state.get_data())["item"]

        _press("pp:back", state, message_id=OLD_ID + 4, next_id=OLD_ID + 5)
        assert _run(state.get_data())["step"] == 2 and _run(state.get_data())["prompt_id"] == OLD_ID + 5

    def test_retry_save_after_a_failed_edit(self):
        """修改職位寫入失敗：新送的「儲存」可以重試，原預覽上的「儲存」忽略。"""
        note_service = _note_service()
        card_service = _card_service(_note(Company=json.dumps([QBC])))
        state = _fsm()
        _press(f"pp:pe:5:0:{item_fingerprint(QBC)}", state, card_service=card_service)
        preview_id = _skip_to_confirm(state)

        note_service.run_action.side_effect = AnkiConnectError("Anki 沒有回應")
        _press("pp:save", state, message_id=preview_id, next_id=preview_id + 1,
               note_service=note_service, card_service=card_service)
        assert _run(state.get_state()) == PersonStates.add_confirm.state
        assert _run(state.get_data())["prompt_id"] == preview_id + 1

        note_service.run_action.side_effect = None
        note_service.run_action.reset_mock()
        _press("pp:save", state, message_id=preview_id, note_service=note_service, card_service=card_service)
        note_service.run_action.assert_not_awaited()
        _press("pp:save", state, message_id=preview_id + 1, note_service=note_service, card_service=card_service)
        note_service.run_action.assert_awaited_once()
        assert _run(state.get_state()) is None

    def test_double_skip_while_creating_creates_once(self):
        """建立新的人時「略過」認識場合連點：只建立一張。"""
        state, note_service = _fsm(), _note_service()
        message, _ = _message()
        message.answer.return_value.message_id = OLD_ID
        _run(person_profile.start_person_create(message, state, name="田中"))
        _press("pp:skip", state, note_service=note_service)
        _press("pp:skip", state, note_service=note_service)
        note_service.create.assert_awaited_once()
        assert note_service.create.await_args.kwargs["params"]["Met_Where"] == ""

    def test_double_append_writes_once(self):
        """改資料選「接在後面」連點：內容只接一次。"""
        state, note_service = _fsm(), _note_service()
        card_service = _card_service(_note(Notes="第一次見面"))
        _run(person_profile._start_edit(_message()[0], state, card_service, 5, "Notes"))
        typed, _ = _message("很健談")
        typed.answer.return_value.message_id = OLD_ID
        _run(person_profile.process_edit_value(typed, state, note_service, card_service))
        _press("pp:em:a", state, note_service=note_service, card_service=card_service)
        _press("pp:em:a", state, note_service=note_service, card_service=card_service)
        note_service.update_fields.assert_awaited_once()

    def test_double_ingest_save_writes_once(self):
        state, note_service, card_service = _fsm(), _note_service(), _card_service()
        _ingested(state, card_service)
        _press("pp:igsave", state, note_service=note_service, card_service=card_service)
        writes = note_service.run_action.await_count
        second = _press("pp:igsave", state, note_service=note_service, card_service=card_service)
        assert note_service.run_action.await_count == writes
        note_service.update_fields.assert_awaited_once()
        second.message.answer.assert_not_awaited()


class TestIngestSaveMessages:
    def test_saved_result_is_reported_even_if_buttons_cannot_be_removed(self):
        """已經寫入了：拿不掉按鈕也要回覆「已記下」，不然使用者以為沒存而重做（重複寫入）。"""
        state, note_service, card_service = _fsm(), _note_service(), _card_service()
        _ingested(state, card_service)
        query = _callback("pp:igsave")
        query.message.edit_reply_markup.side_effect = _bad_request("message can't be edited")
        _run(person_profile.on_person_button(query, state, note_service, card_service))
        assert _run(state.get_state()) is None
        assert "已記下" in _sent(query.message.answer)[-1]

    @pytest.mark.parametrize("edit_error", [NOT_MODIFIED, "message can't be edited"])
    def test_failed_write_is_reported_even_if_the_preview_cannot_change(self, edit_error):
        """第一項就寫入失敗時預覽沒變（not modified），或預覽改不了：照樣告訴使用者還沒存、可以再按。"""
        state, note_service, card_service = _fsm(), _note_service(), _card_service()
        note_service.run_action.side_effect = AnkiConnectError("Anki 沒有回應")
        _ingested(state, card_service)
        query = _callback("pp:igsave")
        query.message.edit_text.side_effect = _bad_request(edit_error)
        _run(person_profile.on_person_button(query, state, note_service, card_service))
        assert _run(state.get_state()) == PersonStates.ingest_confirm.state
        assert not _run(state.get_data())["saving"]
        assert "只會補存" in _sent(query.message.answer)[-1]


class TestNotModified:
    def test_not_modified_on_the_current_prompt_ends_quietly(self):
        """按鈕已經拿掉（已處理過）：不往下執行，也不冒成未預期錯誤。"""
        state = _fsm()
        _start_company(state)
        query = _callback("pp:skip")
        query.message.message_id = OLD_ID
        query.message.edit_reply_markup.side_effect = _bad_request(NOT_MODIFIED)
        _run(person_profile.on_person_button(query, state, _note_service(), _card_service()))
        assert _run(state.get_data())["step"] == 0
        query.message.answer.assert_not_awaited()

    def test_double_cancel_replies_once(self):
        state = _fsm()
        _start_company(state)
        _press("pp:cancel", state)
        second = _callback("pp:cancel")
        second.message.edit_reply_markup.side_effect = _bad_request(NOT_MODIFIED)
        _run(person_profile.on_person_button(second, state, _note_service(), _card_service()))
        second.message.answer.assert_not_awaited()

    def test_double_confirm_delete_removes_once(self):
        """確認刪除連點：第二下在拿按鈕時就結束（同內容的兩筆指紋相同，再刪一次會刪到另一筆）。"""
        state, note_service = _fsm(), _note_service()
        query = _callback("pp:delok:5:IN:0:abc")
        query.message.edit_reply_markup.side_effect = _bad_request(NOT_MODIFIED)
        _run(person_profile.on_person_button(query, state, note_service, _card_service()))
        note_service.run_action.assert_not_awaited()
        query.message.answer.assert_not_awaited()

    def test_failed_delete_offers_the_list_again(self):
        """先拿掉確認按鈕再刪：刪除失敗時附上「打開清單」，不必回 /p 重找。"""
        note_service = _note_service()
        note_service.run_action.side_effect = AnkiConnectError("Anki 沒有回應")
        query = _callback("pp:delok:5:IN:0:abc")
        _run(person_profile.on_person_button(query, _fsm(), note_service, _card_service()))
        sent = _sent(query.message.answer)
        assert "還沒刪除" in sent[-1]
        buttons = query.message.answer.await_args.kwargs["reply_markup"].inline_keyboard[0]
        assert buttons[0].callback_data == "pp:ls:5:IN"

    def test_double_newcard_page_is_not_an_error(self):
        query = _callback("newcard_page:1")
        query.message.edit_reply_markup.side_effect = _bad_request(NOT_MODIFIED)
        _run(newcard_menu.handle_newcard_page(query))
        query.answer.assert_awaited_once_with()

    @pytest.mark.parametrize("data", ["pp:gc:5:0:abc:d:0", "pp:mask:5:off"])
    def test_double_tap_on_menu_buttons_ends_quietly(self, data):
        """目標達成日、內文遮名字切換連點：第二下按鈕已是處理後的樣子，不再寫入、不再回覆。"""
        state, note_service, card_service = _fsm(), _note_service(), _card_service()
        for name in ("add_tags", "remove_tags", "sync"):
            setattr(card_service.anki_client, name, mock.AsyncMock())
        query = _callback(data)
        query.message.edit_reply_markup.side_effect = _bad_request(NOT_MODIFIED)
        _run(person_profile.on_person_button(query, state, note_service, card_service))
        note_service.run_action.assert_not_awaited()
        query.message.answer.assert_not_awaited()

    def test_stale_button_that_cannot_be_edited_still_answers(self):
        """過時按鈕拿不掉（訊息太舊不能改）：不算錯誤，照樣回應短提示。"""
        state = _fsm()
        _start_company(state)
        query = _callback("pp:skip")
        query.message.message_id = OLD_ID - 1
        query.message.edit_reply_markup.side_effect = _bad_request("message can't be edited")
        _run(person_profile.on_person_button(query, state, _note_service(), _card_service()))
        assert "不能用" in query.answer.await_args.args[0]

    def test_other_bad_requests_still_raise(self):
        state = _fsm()
        _start_company(state)
        query = _callback("pp:skip")
        query.message.message_id = OLD_ID
        query.message.edit_reply_markup.side_effect = _bad_request("message to edit not found")
        with pytest.raises(TelegramBadRequest):
            _run(person_profile.on_person_button(query, state, _note_service(), _card_service()))
