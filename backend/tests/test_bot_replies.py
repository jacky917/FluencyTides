"""TG bot 回覆文字的測試（docs/archive/tg_bot_copy_review_2026-10-18.md 第 3 節與第 6 節）。

Tests for the Telegram bot replies: dynamic content is escaped so messages
are always deliverable, texts match the actual state, backticks are gone,
buttons always get an answer, and the expression flow no longer reuses
stale data from a previous round.

bot 預設 ``parse_mode=HTML``：每則回覆都以 :func:`_assert_tg_html` 檢查，只允許 Telegram 白名單
標籤，``&`` 一律是實體——不符合的話 Telegram 會拒收那則訊息。
"""

import asyncio
import re
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot.handlers import callbacks, commands, media, messages, voice
from app.bot.handlers.fsm import expression_fsm, speaking_fsm, vocabulary_fsm
from app.bot.handlers.fsm.expression_fsm import ExpressionStates
from app.bot.state import UserState, UserStateManager
from app.bot.utils import texts
from app.core.config import settings
from app.core.exceptions import FluencyTidesError, AudioEvaluatorUnavailableError
from app.infrastructure.anki.client import AnkiConnectError

CHAT_ID = 7
NASTY = "A<b>&C"  # 含 < 與 &：不轉義的話 Telegram 會拒收整則訊息

_ALLOWED_TAG = re.compile(r"</?(b|i|u|s|code|pre|blockquote|a)( [^<>]*)?>")
_ENTITY = re.compile(r"&(#\d+|#x[0-9a-fA-F]+|[a-zA-Z]+);")


def _assert_tg_html(text: str) -> None:
    """Telegram HTML 模式下送得出去：只有白名單標籤，& 都是實體，沒有字面反引號。"""
    stripped = _ALLOWED_TAG.sub("", text)
    assert "<" not in stripped and ">" not in stripped, text
    assert "&" not in _ENTITY.sub("", stripped), text
    assert "`" not in text, text


def _run(coro):
    return asyncio.run(coro)


def _message(text: str = "", user_id: int = 1):
    message = mock.MagicMock()
    message.text = text
    message.chat.id = CHAT_ID
    message.from_user.id = user_id
    status = mock.MagicMock()
    status.edit_text = mock.AsyncMock()
    status.delete = mock.AsyncMock()
    message.answer = mock.AsyncMock(return_value=status)
    message.reply = mock.AsyncMock(return_value=status)
    message.edit_reply_markup = mock.AsyncMock()
    message.bot = None
    return message, status


def _callback(data: str, user_id: int = 1):
    query = mock.MagicMock()
    query.data = data
    query.from_user.id = user_id
    query.message.chat.id = CHAT_ID
    query.message.edit_text = mock.AsyncMock()
    query.message.edit_reply_markup = mock.AsyncMock()
    query.message.delete = mock.AsyncMock()
    query.message.answer = mock.AsyncMock()
    query.answer = mock.AsyncMock()
    return query


def _fsm() -> FSMContext:
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=CHAT_ID, user_id=1))


def _sent(mock_method) -> list[str]:
    """一個 AsyncMock（answer / edit_text）送出的所有文字。"""
    return [call.args[0] for call in mock_method.await_args_list]


def _anki(**overrides):
    client = mock.MagicMock()
    client.sync = mock.AsyncMock()
    client.find_notes = mock.AsyncMock(return_value=[1])
    note = mock.MagicMock(noteId=1, fields={})
    client.get_notes_info = mock.AsyncMock(return_value=[note])
    client.store_media_file = mock.AsyncMock()
    for name, value in overrides.items():
        setattr(client, name, value)
    return client


# ----------------------------------------------------------------------
# 共用文字與同步警告
# ----------------------------------------------------------------------


def test_field_label_and_upload_done_text_are_escaped():
    """欄位名稱用中文（含三語卡的語言）；說話者名稱是使用者輸入，一律轉義。"""
    assert texts.field_label("References") == "參考範本"
    assert texts.field_label("References_JA").startswith("參考範本（")
    done = texts.upload_done_text("Recordings", NASTY, "")
    _assert_tg_html(done)
    assert "A&lt;b&gt;&amp;C" in done and "歷史錄音" in done


@pytest.mark.parametrize(
    ("error", "fragment"),
    [("偵測到同步衝突", "選「上傳」會以電腦版覆蓋 AnkiWeb"), ("timeout", "同步 AnkiWeb 失敗")],
    ids=["conflict", "other"],
)
def test_sync_warning_says_data_is_written(error, fragment):
    """同步失敗時資料其實已寫進 Anki，要說清楚；衝突時「上傳」有副作用，不能寫成「即可解決」。"""
    client = _anki(sync=mock.AsyncMock(side_effect=AnkiConnectError(error)))
    warning = _run(callbacks._sync_with_warning(client))
    assert "已寫入 Anki" in warning and fragment in warning
    assert "即可解決" not in warning


# ----------------------------------------------------------------------
# 指令與 Deep Link（commands.py）
# ----------------------------------------------------------------------


def _start(payload: str, anki=None, manager=None):
    message, status = _message(f"/start {payload}")
    _run(commands.command_start_handler(message, anki or _anki(), manager or UserStateManager(), mock.MagicMock()))
    return message, status


def test_unknown_link_does_not_suggest_typing_a_word():
    """無法辨識的連結：不叫使用者「直接傳單字」（那不會建卡），也不回顯未轉義的 payload。"""
    message, _ = _start("weird<&>payload")
    [reply] = _sent(message.answer)
    _assert_tg_html(reply)
    assert "直接傳送單字" not in reply and "weird" not in reply


def test_tts_link_gets_a_reply():
    """卡片上的 AI TTS 連結：後端還沒實作也要回一句，不能按了沒反應。"""
    message, _ = _start("ttsaudio-Prompt_Audios-0-SC_1")
    assert _sent(message.answer) == ["🚧 AI 語音生成尚未開放。"]


def test_card_not_found_does_not_blame_anki_being_closed():
    """連得上 Anki 但找不到卡：說可能已刪除或尚未同步，不說「請確認 Anki 是否正在執行」。"""
    message, _ = _start("rec-Recordings-0-SC_1", anki=_anki(find_notes=mock.AsyncMock(return_value=[])))
    assert _sent(message.answer) == [texts.CARD_NOT_FOUND]


def test_add_audio_prompt_says_only_voice_messages_work():
    """上傳模式只收語音訊息：明說不收 mp3 檔與時限，欄位用中文名。"""
    manager = UserStateManager()
    message, _ = _start("addaudio-References-0-SC_1", manager=manager)
    [reply] = _sent(message.answer)
    _assert_tg_html(reply)
    assert "不收 mp3" in reply and f"{settings.TG_STATE_EXPIRE_MINUTES} 分鐘" in reply
    assert "參考範本" in reply and "SC_1" not in reply
    assert manager.get_state(CHAT_ID).action == "add_audio"


def _delete_link(items, index="0", field="References"):
    import json

    note = mock.MagicMock(noteId=1, fields={field: {"value": json.dumps(items, ensure_ascii=False)}})
    anki = _anki(get_notes_info=mock.AsyncMock(return_value=[note]))
    message, _ = _message()
    action = commands.DeepLinkParser.parse(f"del-{field}-{index}-SC_1")
    with mock.patch.object(commands.AnkiJsonFieldManager, "update_field", mock.AsyncMock()), \
         mock.patch.object(callbacks, "_sync_with_warning", mock.AsyncMock(return_value="")):
        _run(commands._handle_delete_entry(message, anki, mock.MagicMock(), action))
    return _sent(message.answer)


def test_deleted_entry_preview_is_escaped():
    """刪除的條目內容是使用者資料：轉義後才送得出去——送不出去的話使用者再點一次會刪到下一筆。"""
    [reply] = _delete_link([{"content": NASTY}, {"content": "next"}])
    _assert_tg_html(reply)
    assert "已刪除參考範本" in reply and "A&lt;b&gt;&amp;C" in reply and "剩 1 筆" in reply


def test_stale_delete_link_explains_the_entry_is_gone():
    """索引超出範圍（卡片畫面還沒更新）：說這筆已不存在，不顯示索引與欄位英文名。"""
    [reply] = _delete_link([{"content": "only"}], index="5")
    assert reply == commands._ENTRY_GONE


def test_sync_command_reports_scanned_cards_and_escapes_errors():
    """/sync：說清理失效關聯並顯示掃描張數；錯誤訊息轉義。"""
    from app.services.relation_service import RelationSyncResult

    message, status = _message("/sync")
    result = RelationSyncResult(scanned_notes=3, orphan_notes=1, deleted=2, considered=5, ankiweb_synced=True)
    relations = mock.MagicMock(sync_with_anki=mock.AsyncMock(return_value=result))
    _run(commands.command_sync_handler(message, _anki(), relations))
    assert _sent(status.edit_text) == ["✅ 清理完成：掃描 3 張筆記，移除 2 筆失效關聯。"]

    message, status = _message("/sync")
    relations = mock.MagicMock(sync_with_anki=mock.AsyncMock(side_effect=AnkiConnectError("<Response [500]> & more")))
    _run(commands.command_sync_handler(message, _anki(), relations))
    [failed] = _sent(status.edit_text)
    _assert_tg_html(failed)
    assert failed.startswith("❌ 清理失敗（連不上 Anki，未刪除任何關聯）：")

    # Anki 有回應但回報錯誤（例如收藏未載入）：不說成連不上
    from app.infrastructure.anki.client import AnkiActionError

    message, status = _message("/sync")
    relations = mock.MagicMock(sync_with_anki=mock.AsyncMock(side_effect=AnkiActionError("collection is not available")))
    _run(commands.command_sync_handler(message, _anki(), relations))
    assert _sent(status.edit_text)[0].startswith("❌ 清理失敗（Anki 回報錯誤，未刪除任何關聯）：")


def test_sync_command_mentions_a_failed_ankiweb_sync():
    """清理前與 AnkiWeb 同步失敗：清理照做，但說明這次以本機收藏為準。"""
    from app.services.relation_service import RelationSyncResult

    message, status = _message("/sync")
    result = RelationSyncResult(scanned_notes=3, orphan_notes=0, deleted=0, considered=5, ankiweb_synced=False)
    relations = mock.MagicMock(sync_with_anki=mock.AsyncMock(return_value=result))
    _run(commands.command_sync_handler(message, _anki(), relations))
    [reply] = _sent(status.edit_text)
    assert reply.startswith("✅ 清理完成") and "AnkiWeb 同步失敗" in reply


def test_sync_command_reports_database_errors():
    """資料庫錯誤（非業務例外）也要更新狀態訊息，不能停在「⏳ 清理中」。"""
    from sqlalchemy.exc import OperationalError

    message, status = _message("/sync")
    error = OperationalError("DELETE", {}, Exception("database is locked"))
    relations = mock.MagicMock(sync_with_anki=mock.AsyncMock(side_effect=error))
    _run(commands.command_sync_handler(message, _anki(), relations))
    [failed] = _sent(status.edit_text)
    _assert_tg_html(failed)
    assert failed.startswith("❌ 清理失敗（資料庫錯誤，未刪除任何關聯）")


def test_sync_command_reports_other_business_errors():
    """拒絕以外的業務錯誤：同樣更新狀態訊息並說明沒有刪除。"""
    message, status = _message("/sync")
    relations = mock.MagicMock(sync_with_anki=mock.AsyncMock(side_effect=FluencyTidesError("<x> & y")))
    _run(commands.command_sync_handler(message, _anki(), relations))
    [failed] = _sent(status.edit_text)
    _assert_tg_html(failed)
    assert failed.startswith("❌ 清理失敗（未刪除任何關聯）：")


def test_sync_command_explains_a_refusal():
    """服務層拒絕清理（Anki 回傳 0 張筆記）：說明沒有清理與原因，不說成失敗。"""
    from app.core.exceptions import RelationSyncEmptyAnkiError

    message, status = _message("/sync")
    error = RelationSyncEmptyAnkiError("Anki 回傳 0 張筆記，為避免誤刪已停止清理。")
    relations = mock.MagicMock(sync_with_anki=mock.AsyncMock(side_effect=error))
    _run(commands.command_sync_handler(message, _anki(), relations))
    [reply] = _sent(status.edit_text)
    _assert_tg_html(reply)
    assert reply.startswith("⚠️ 沒有清理：") and "0 張筆記" in reply and "網頁" not in reply


def test_sync_command_points_to_the_web_for_a_forced_cleanup():
    """要刪超過一半而被擋：TG 不提供強制，指出到網頁確認強制清理。"""
    from app.core.exceptions import RelationSyncNeedsForceError

    message, status = _message("/sync")
    error = RelationSyncNeedsForceError("將刪除 30 / 40 筆關聯（超過一半）", to_delete=30, considered=40)
    relations = mock.MagicMock(sync_with_anki=mock.AsyncMock(side_effect=error))
    _run(commands.command_sync_handler(message, _anki(), relations))
    [reply] = _sent(status.edit_text)
    _assert_tg_html(reply)
    assert reply.startswith("⚠️ 沒有清理：") and "Knowledge Graph" in reply


# ----------------------------------------------------------------------
# 兜底文字（messages.py）與說話者名稱
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (None, "💡 用 /newcard 新增卡片，/help 查看說明。"),
        ("recording", "🎙️ 正在等語音訊息，文字不會被處理。"),
        ("add_audio", "🎙️ 正在等語音訊息，文字不會被處理。"),
        ("wait_avatar_selection", "請點上方按鈕選擇頭像。"),
        ("wait_avatar_upload", "🖼️ 請傳一張照片。"),
    ],
)
def test_fallback_reply_matches_the_waiting_state(action, expected):
    """沒被接住的文字：依目前在等什麼回覆，不說成「不在任何流程中」，也不用 ❌。"""
    manager = UserStateManager()
    if action:
        manager.set_state(CHAT_ID, UserState(action=action, card_id="SC_1"))
    message, _ = _message("hello")
    _run(messages.process_word_handler(message, mock.MagicMock(), mock.MagicMock(), mock.MagicMock(), manager, _anki()))
    assert _sent(message.reply) == [expected]


def test_speaker_name_is_escaped_in_the_confirmation():
    """說話者名稱含 < 或 & 時確認訊息仍送得出去（否則已寫入卻停在「寫入中」）。"""
    manager = UserStateManager()
    manager.set_state(CHAT_ID, UserState(action="wait_speaker_name", card_id="SC_1", extra={"field_name": "References"}))
    registry = mock.MagicMock()
    registry.get_handler.return_value.execute_update = mock.AsyncMock()
    message, status = _message(NASTY)
    with mock.patch.object(callbacks, "_sync_with_warning", mock.AsyncMock(return_value="")):
        _run(messages.process_word_handler(message, registry, mock.MagicMock(), mock.MagicMock(), manager, _anki()))
    [done] = _sent(status.edit_text)
    _assert_tg_html(done)
    assert "A&lt;b&gt;&amp;C" in done


def test_speaker_name_write_back_when_card_vanished():
    """寫回前卡片剛好被刪（notes_info 為空）：回「找不到卡片」並清狀態，不會 IndexError 卡在「寫入中」。"""
    manager = UserStateManager()
    manager.set_state(CHAT_ID, UserState(action="wait_speaker_name", card_id="SC_1", extra={"field_name": "References"}))
    message, status = _message("John")
    anki = _anki(get_notes_info=mock.AsyncMock(return_value=[]))
    _run(messages.process_word_handler(message, mock.MagicMock(), mock.MagicMock(), mock.MagicMock(), manager, anki))
    assert _sent(status.edit_text) == [texts.CARD_NOT_FOUND]
    assert manager.get_state(CHAT_ID) is None


def test_write_back_failure_says_how_to_retry():
    """寫回失敗（狀態已清）：附重新進入的入口，錯誤訊息轉義。"""
    manager = UserStateManager()
    manager.set_state(CHAT_ID, UserState(action="wait_speaker_name", card_id="SC_1", extra={"field_name": "References"}))
    registry = mock.MagicMock()
    registry.get_handler.return_value.execute_update = mock.AsyncMock(side_effect=AnkiConnectError("<bad> & worse"))
    message, status = _message("John")
    _run(messages.process_word_handler(message, registry, mock.MagicMock(), mock.MagicMock(), manager, _anki()))
    [failed] = _sent(status.edit_text)
    _assert_tg_html(failed)
    assert texts.UPLOAD_RETRY in failed


def test_error_detail_never_empty():
    """例外字串是空的（例如 TimeoutError()）時退回類別名，免得「失敗：」後面什麼都沒有。"""
    assert texts.error_detail(TimeoutError()) == "TimeoutError"


# ----------------------------------------------------------------------
# 頭像按鈕（callbacks.py）
# ----------------------------------------------------------------------


def test_expired_avatar_buttons_say_how_to_restart():
    """頭像流程已過期的按鈕：提示回 Anki 重新點「＋」，不是籠統的「請重新操作」。"""
    query = _callback("avatar_upload")
    _run(callbacks.handle_avatar_upload(query, UserStateManager()))
    query.answer.assert_awaited_once_with(texts.UPLOAD_EXPIRED_ALERT, show_alert=True)


def test_system_avatar_buttons_use_indices_within_64_bytes(tmp_path):
    """系統頭像的 callback data 用序號：中文或長檔名也不會超過 64 bytes（超過整個鍵盤會被拒收）；
    按鈕會被 answer，不會一直轉圈。"""
    (tmp_path / ("很長的中文頭像檔名" * 4 + ".png")).write_bytes(b"x")
    (tmp_path / "coach.jpg").write_bytes(b"x")
    manager = UserStateManager()
    manager.set_state(CHAT_ID, UserState(action="wait_avatar_selection", card_id="SC_1"))
    query = _callback("avatar_sys")
    with mock.patch.object(callbacks, "AVATARS_DIR", tmp_path):
        _run(callbacks.handle_avatar_sys(query, manager))

    query.answer.assert_awaited_once_with()
    markup = query.message.edit_text.await_args.kwargs["reply_markup"]
    data = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert data == ["avatar_sel_0", "avatar_sel_1", "avatar_back"]
    assert all(len(item.encode("utf-8")) <= 64 for item in data)


def test_avatar_choice_survives_directory_changes(tmp_path):
    """顯示選單後目錄多了一張排序在前面的頭像（別人剛上傳的自訂頭像也存在這裡）：
    按原本的按鈕仍選到使用者看到的那一張，不會因序號位移選錯。"""
    (tmp_path / "coach.jpg").write_bytes(b"x")
    manager = UserStateManager()
    manager.set_state(CHAT_ID, UserState(action="wait_avatar_selection", card_id="SC_1"))
    with mock.patch.object(callbacks, "AVATARS_DIR", tmp_path):
        _run(callbacks.handle_avatar_sys(_callback("avatar_sys"), manager))
        (tmp_path / "avatar_1_20261018.jpg").write_bytes(b"x")  # 排在 coach.jpg 前面
        query = _callback("avatar_sel_0")
        with mock.patch.object(callbacks, "_execute_final_update", mock.AsyncMock()) as final:
            _run(callbacks.handle_avatar_sys_select(
                query, manager, _anki(), mock.MagicMock(), mock.MagicMock(), mock.MagicMock()
            ))

    filename, speaker = final.await_args.args[4], final.await_args.args[5]
    assert (filename, speaker) == ("coach.jpg", "coach")


@pytest.mark.parametrize("data", ["avatar_sel_9", "avatar_sel_coach.jpg"], ids=["out-of-range", "old-filename-format"])
def test_stale_system_avatar_selection_is_expired(tmp_path, data):
    """頭像目錄變了、或舊格式（以檔名當 callback data）的按鈕：當成過期，不會選錯頭像。"""
    (tmp_path / "coach.jpg").write_bytes(b"x")
    manager = UserStateManager()
    manager.set_state(CHAT_ID, UserState(action="wait_avatar_selection", card_id="SC_1"))
    query = _callback(data)
    with mock.patch.object(callbacks, "AVATARS_DIR", tmp_path):
        _run(callbacks.handle_avatar_sys_select(query, manager, _anki(), mock.MagicMock(), mock.MagicMock(), mock.MagicMock()))
    query.answer.assert_awaited_once_with(texts.UPLOAD_EXPIRED_ALERT, show_alert=True)


# ----------------------------------------------------------------------
# 圖片與崩潰警報（media.py、alerts.py）
# ----------------------------------------------------------------------


def test_photo_failure_is_escaped_and_says_send_again():
    """圖片處理失敗（狀態沒推進）：錯誤訊息轉義，並說「請再傳一次」。"""
    from aiogram.exceptions import TelegramBadRequest

    manager = UserStateManager()
    manager.set_state(CHAT_ID, UserState(action="wait_avatar_upload", card_id="SC_1"))
    message, status = _message()
    message.bot = mock.MagicMock()
    message.bot.get_file = mock.AsyncMock(side_effect=TelegramBadRequest(method=mock.MagicMock(), message="<bad> & worse"))
    _run(media.process_photo_handler(message, manager, _anki()))
    [failed] = _sent(status.edit_text)
    _assert_tg_html(failed)
    assert "請再傳一次" in failed


def test_crash_alert_is_escaped_and_bounded():
    """崩潰警報：repr(exc) 常含 <…>、URL 含 &——轉義並限制長度，否則警報常被 Telegram 拒收。"""
    from app.bot import alerts

    post = mock.AsyncMock(return_value=mock.MagicMock())
    client = mock.MagicMock()
    client.__aenter__ = mock.AsyncMock(return_value=mock.MagicMock(post=post))
    client.__aexit__ = mock.AsyncMock(return_value=False)
    with mock.patch.object(settings, "TG_BOT_TOKEN", "t"), mock.patch.object(settings, "TG_ADMIN_CHAT_ID", 1), \
         mock.patch.object(alerts.httpx, "AsyncClient", return_value=client):
        _run(alerts.send_crash_alert("https://x/api?a=1&b=2", "POST", "<Response [500]>" + "x" * 5000))

    text = post.await_args.kwargs["json"]["text"]
    _assert_tg_html(text)
    assert len(text) < 4096


# ----------------------------------------------------------------------
# 語音與評分（voice.py、stt_diff）
# ----------------------------------------------------------------------


def _evaluator_patch(evaluator):
    """讓 voice 依 handler 取得的評分器是 ``evaluator``；None＝依設定建不出評分器。"""
    if evaluator is None:
        side_effect = AudioEvaluatorUnavailableError("錄音評分器無法建立：AUDIO_API_KEY 未設定")
        return mock.patch.object(voice.recording_eval, "evaluator_for_handler", mock.AsyncMock(side_effect=side_effect))
    plan = mock.MagicMock(strategy_name="listen")
    return mock.patch.object(
        voice.recording_eval, "evaluator_for_handler", mock.AsyncMock(return_value=(evaluator, plan))
    )


def _voice(manager, evaluator=None, anki=None, registry=None):
    message, status = _message()
    message.voice = mock.MagicMock(file_id="f")
    message.bot = mock.MagicMock()
    message.bot.get_file = mock.AsyncMock(return_value=mock.MagicMock(file_path="p"))
    message.bot.download_file = mock.AsyncMock()
    with _evaluator_patch(evaluator):
        _run(voice.process_voice_handler(
            message, anki or _anki(), registry or mock.MagicMock(), manager, mock.MagicMock(), mock.MagicMock()
        ))
    return message, status


def _recording_state():
    manager = UserStateManager()
    manager.set_state(CHAT_ID, UserState(action="recording", card_id="SC_1", extra={"field_name": "Recordings"}))
    return manager


def test_voice_without_task_mentions_the_time_limit():
    message, _ = _voice(UserStateManager())
    [reply] = _sent(message.reply)
    assert reply.startswith("⌛ 沒有等待中的錄音") and f"{settings.TG_STATE_EXPIRE_MINUTES} 分鐘" in reply


def test_missing_evaluator_keeps_recording_mode_and_says_so():
    """依設定建不出評分器：狀態沒清，說明原因，改好設定後可在時限內重傳；音檔還沒存進 Anki。"""
    manager = _recording_state()
    anki = _anki()
    _, status = _voice(manager, evaluator=None, anki=anki)
    anki.store_media_file.assert_not_called()
    last = _sent(status.edit_text)[-1]
    _assert_tg_html(last)
    assert "AUDIO_API_KEY 未設定" in last and "改好設定後" in last
    assert manager.get_state(CHAT_ID) is not None


def test_unexpected_error_while_building_the_evaluator_is_reported():
    """建評分器時的非預期錯誤：照實回覆（不停在「讀取卡片中…」），狀態保留可重傳。"""
    manager = _recording_state()
    message, status = _message()
    message.voice = mock.MagicMock(file_id="f")
    message.bot = mock.MagicMock()
    message.bot.get_file = mock.AsyncMock(return_value=mock.MagicMock(file_path="p"))
    message.bot.download_file = mock.AsyncMock()
    lookup = mock.AsyncMock(side_effect=RuntimeError("boom"))
    with mock.patch.object(voice.recording_eval, "evaluator_for_handler", lookup):
        _run(voice.process_voice_handler(message, _anki(), mock.MagicMock(), manager, mock.MagicMock(), mock.MagicMock()))
    assert "語音評分服務發生錯誤" in _sent(status.edit_text)[-1]
    assert manager.get_state(CHAT_ID) is not None


def test_voice_builds_the_evaluator_from_the_cards_handler():
    """三語卡與對話卡各依自己的 handler 取評分器（可以設不同策略）。"""
    for field_name, handler in (("Recordings", "speaking_coach"), ("Recordings_JA", "speaking_trilingual")):
        manager = UserStateManager()
        manager.set_state(CHAT_ID, UserState(action="recording", card_id="SC_1", extra={"field_name": field_name}))
        evaluator = mock.MagicMock()
        evaluator.evaluate_audio = mock.AsyncMock(side_effect=FluencyTidesError("boom"))
        lookup = mock.AsyncMock(return_value=(evaluator, mock.MagicMock(strategy_name="listen")))
        message, _ = _message()
        message.voice = mock.MagicMock(file_id="f")
        message.bot = mock.MagicMock()
        message.bot.get_file = mock.AsyncMock(return_value=mock.MagicMock(file_path="p"))
        message.bot.download_file = mock.AsyncMock()
        with mock.patch.object(voice.recording_eval, "evaluator_for_handler", lookup):
            _run(voice.process_voice_handler(
                message, _anki(), mock.MagicMock(), manager, mock.MagicMock(), mock.MagicMock()
            ))
        lookup.assert_awaited_once_with(handler)


def test_ai_busy_says_to_click_the_record_button_again():
    """AI 忙碌：狀態已清，所以要說「回 Anki 重新點錄音按鈕」，不能只說「稍後再試」。"""
    manager = _recording_state()
    evaluator = mock.MagicMock()
    evaluator.evaluate_audio = mock.AsyncMock(side_effect=FluencyTidesError("503 UNAVAILABLE"))
    _, status = _voice(manager, evaluator=evaluator)
    last = _sent(status.edit_text)[-1]
    assert "重新點錄音按鈕" in last and "請稍後再試。" not in last
    assert manager.get_state(CHAT_ID) is None


def test_unreadable_card_error_is_escaped():
    manager = _recording_state()
    anki = _anki(get_notes_info=mock.AsyncMock(side_effect=AnkiConnectError("<html> & stuff")))
    _, status = _voice(manager, anki=anki)
    last = _sent(status.edit_text)[-1]
    _assert_tg_html(last)
    assert "請回 Anki 卡片重新點按鈕" in last


def test_unscored_recording_is_not_written_or_reported_as_scored():
    """沒有評分（沒偵測到語音、沒有參考答案）：不寫入 0 分紀錄、不說「評分完成」，說明原因與入口。"""
    from app.infrastructure.audio_evaluator.base import AudioNotScoredError

    manager = _recording_state()
    evaluator = mock.MagicMock()
    evaluator.evaluate_audio = mock.AsyncMock(side_effect=AudioNotScoredError("沒有偵測到<語音>。"))
    registry = mock.MagicMock()
    _, status = _voice(manager, evaluator=evaluator, registry=registry)

    last = _sent(status.edit_text)[-1]
    _assert_tg_html(last)
    assert last.startswith("❌ 這次沒有評分：") and "評分完成" not in last and "/ 100" not in last
    assert "請回 Anki 卡片重新點按鈕" in last
    registry.get_handler.assert_not_called()  # 沒有寫入任何紀錄
    assert manager.get_state(CHAT_ID) is None


# ----------------------------------------------------------------------
# 外語糾錯（expression_fsm.py）
# ----------------------------------------------------------------------


def test_expression_flow_starts_from_clean_data():
    """第 6 節：上一輪選了「覆蓋」但沒完成就重開，殘留的舊卡 ID 不能帶到新一輪（會刪錯卡）。"""
    state = _fsm()
    _run(state.update_data(old_master_note_id=123, duplicate_decision="overwrite", llm_result={"x": 1}))
    message, _ = _message()
    _run(expression_fsm.start_expression_flow(message, state))

    assert _run(state.get_data()) == {}
    assert _run(state.get_state()) == ExpressionStates.waiting_for_lang.state
    [prompt] = _sent(message.answer)
    _assert_tg_html(prompt)
    assert "/skip" in prompt
    markup = message.answer.await_args.kwargs["reply_markup"]
    assert [button.callback_data for row in markup.inline_keyboard for button in row] == ["expr_cancel"]


def test_no_duplicate_resets_previous_decision():
    """這一輪沒有重複時，不沿用先前的「覆蓋」決策。"""
    state = _fsm()
    _run(state.set_state(ExpressionStates.waiting_for_reorganization))
    _run(state.update_data(original_text="x", old_master_note_id=123, duplicate_decision="overwrite"))
    service = mock.MagicMock(find_notes=mock.AsyncMock(return_value=[]))
    with mock.patch.object(expression_fsm, "_generate_and_preview", mock.AsyncMock()) as generate:
        _run(expression_fsm._check_duplicate_before_generate(_message()[0], state, service))

    generate.assert_awaited_once()
    data = _run(state.get_data())
    assert (data["duplicate_decision"], data["old_master_note_id"]) == (None, None)


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        ('He said "hi"', '"My_Original:He said \\"hi\\""'),
        ("50% off_*", '"My_Original:50% off\\_\\*"'),
        ("C:\\path", '"My_Original:C\\:\\\\path"'),
        ("第一行\n第二行", '"My_Original:第一行\n第二行"'),
    ],
    ids=["quote", "wildcards", "backslash", "newline"],
)
def test_duplicate_check_matches_the_original_literally(original, expected):
    """重複檢查：原文照字面比對——引號以 \\" 跳脫（原本的 "" 會靜默地找不到），* _ 不當萬用字元，: 不當欄位分隔。"""
    state = _fsm()
    _run(state.set_state(ExpressionStates.waiting_for_reorganization))
    _run(state.update_data(original_text=original))
    service = mock.MagicMock(find_notes=mock.AsyncMock(return_value=[]))
    with mock.patch.object(expression_fsm, "_generate_and_preview", mock.AsyncMock()):
        _run(expression_fsm._check_duplicate_before_generate(_message()[0], state, service))

    query = service.find_notes.await_args.args[0]
    assert query.endswith(" " + expected)
    assert query.startswith('deck:"日本語\\:\\:外語糾錯\\:\\:母卡片" note:"Expression\\_Master\\_Dark" ')


def test_duplicate_check_without_original_text_ends_the_flow():
    """流程狀態遺失原文（重啟、逾時）：當成流程失效，不拿空字串去查而比對到錯的卡。"""
    state = _fsm()
    _run(state.set_state(ExpressionStates.waiting_for_reorganization))
    service = mock.MagicMock(find_notes=mock.AsyncMock(return_value=[]))
    message, _ = _message()
    with mock.patch.object(expression_fsm, "_generate_and_preview", mock.AsyncMock()) as generate:
        _run(expression_fsm._check_duplicate_before_generate(message, state, service))

    generate.assert_not_awaited()
    service.find_notes.assert_not_awaited()
    assert _sent(message.answer) == [expression_fsm._FLOW_EXPIRED]
    assert _run(state.get_state()) is None


def test_duplicate_check_failure_is_announced_before_generating():
    """查不到 Anki：明講跳過了重複檢查再生成，不默默略過。"""
    state = _fsm()
    _run(state.set_state(ExpressionStates.waiting_for_reorganization))
    _run(state.update_data(original_text="x"))
    service = mock.MagicMock(find_notes=mock.AsyncMock(side_effect=AnkiConnectError("<Response [500]> & more")))
    message, _ = _message()
    with mock.patch.object(expression_fsm, "_generate_and_preview", mock.AsyncMock()) as generate:
        _run(expression_fsm._check_duplicate_before_generate(message, state, service))

    generate.assert_awaited_once()
    [warning] = _sent(message.answer)
    _assert_tg_html(warning)
    assert warning.startswith("⚠️ 無法確認 Anki 是否已有相同原文的卡")


def test_duplicate_check_programming_errors_are_not_swallowed():
    """程式錯誤不被當成「查不到」：照常拋出，不默默略過重複檢查去生成。"""
    state = _fsm()
    _run(state.set_state(ExpressionStates.waiting_for_reorganization))
    _run(state.update_data(original_text="x"))
    service = mock.MagicMock(find_notes=mock.AsyncMock(side_effect=KeyError("fields")))
    generate = mock.AsyncMock()
    with mock.patch.object(expression_fsm, "_generate_and_preview", generate), pytest.raises(KeyError):
        _run(expression_fsm._check_duplicate_before_generate(_message()[0], state, service))
    generate.assert_not_awaited()


def test_duplicate_warning_escapes_old_card_fields():
    """重複警告：原文與舊卡欄位（常帶 <div>、&nbsp;）都要轉義，否則警告送不出去、重複檢查被默默跳過。"""
    state = _fsm()
    _run(state.set_state(ExpressionStates.waiting_for_reorganization))
    _run(state.update_data(original_text=NASTY))
    fields = {"Correct_Answer": {"value": "<div>a&nbsp;b</div>"}, "Reorganized_Expression": {"value": "<b>c</b> & d"}}
    service = mock.MagicMock(
        find_notes=mock.AsyncMock(return_value=[9]),
        get_notes_info=mock.AsyncMock(return_value=[{"fields": fields}]),
    )
    message, _ = _message()
    _run(expression_fsm._check_duplicate_before_generate(message, state, service))

    [warning] = _sent(message.answer)
    _assert_tg_html(warning)
    assert "A&lt;b&gt;&amp;C" in warning and "a b" in warning
    assert _run(state.get_state()) == ExpressionStates.waiting_for_duplicate_decision.state


def test_preview_escapes_llm_text_but_keeps_underline():
    """LLM 內容一律轉義，只保留錯誤對比的 <u>（加粗）。"""
    from app.schemas.llm.expression import LLMExpressionCorrectionResult

    result = LLMExpressionCorrectionResult.model_validate({
        "error_comparison": "我<u>去</u> & <script>",
        "grammar_correction": "<div>g</div>",
        "reorganized_expression": "r & s",
        "grammar_micro_points": [{
            "original_phrase": "<a>", "target_phrase": "b&", "error_hint": "<i",
            "native_translation": "", "context_hint": "", "context_sentence": "",
        }],
        "reorganized_micro_points": [],
    })
    preview = expression_fsm._format_preview(result)
    _assert_tg_html(preview)
    assert "<u><b>去</b></u>" in preview and "&lt;script&gt;" in preview


def _creation(decision=None, *, create=None, delete=None):
    """跑一次 _execute_card_creation（選子卡後的寫入），回傳 (訊息, card_service, state)。"""
    state = _fsm()
    _run(state.set_state(ExpressionStates.waiting_for_card_selection))
    _run(state.update_data(
        original_text="x", llm_result={"k": 1}, duplicate_decision=decision,
        old_master_note_id=77 if decision == "overwrite" else None,
    ))
    service = mock.MagicMock()
    service.find_notes = mock.AsyncMock(return_value=[78, 79])
    service.anki_client.delete_notes = delete or mock.AsyncMock()
    handler = mock.MagicMock(execute_create=create or mock.AsyncMock(return_value=[1, 2, 3]))
    message, _ = _message()
    message.bot = None
    with mock.patch.object(expression_fsm.handler_registry, "get_handler", return_value=handler), \
         mock.patch.object(callbacks, "_sync_with_warning", mock.AsyncMock(return_value="")):
        _run(expression_fsm._execute_card_creation(message, state, service, selected_indices=None))
    return _sent(message.answer), service, state, handler


def test_creation_without_overwrite_never_deletes():
    """沒有選「覆蓋」時絕不刪卡（第 6 節：殘留的舊卡 ID 不能讓新一輪刪錯卡）。"""
    sent, service, state, _ = _creation(None)
    service.anki_client.delete_notes.assert_not_awaited()
    assert sent[-1] == "✅ 已寫入 Anki：母卡 1 張＋子卡 2 張"
    assert _run(state.get_state()) is None


def test_overwrite_creates_first_then_deletes_old_cards():
    """覆蓋：先建新卡（繞過重複檢查，舊卡還在），成功後才刪舊母卡與子卡。"""
    order: list[str] = []
    create = mock.AsyncMock(side_effect=lambda **kw: order.append("create") or [1, 2])
    delete = mock.AsyncMock(side_effect=lambda ids: order.append("delete"))
    sent, service, _, handler = _creation("overwrite", create=create, delete=delete)

    assert order == ["create", "delete"]
    assert handler.execute_create.await_args.kwargs["parameters"]["allow_duplicate"] is True
    service.anki_client.delete_notes.assert_awaited_once_with([77, 78, 79])
    assert service.find_notes.await_args.args[0] == 'note:"Expression\\_Micro\\_Dark" "Master_Note_ID:77"'
    assert sent[-1].endswith("（已刪除舊卡）")


def test_overwrite_keeps_old_cards_when_creation_fails():
    """覆蓋時建立新卡失敗：舊卡不動（不會兩張都沒了），訊息明說舊卡未變動，並附重新開始的方法。"""
    create = mock.AsyncMock(side_effect=AnkiConnectError("<dup> & more"))
    sent, service, state, _ = _creation("overwrite", create=create)

    service.anki_client.delete_notes.assert_not_awaited()
    _assert_tg_html(sent[-1])
    assert "舊卡未變動" in sent[-1] and "/newcard" in sent[-1]
    assert _run(state.get_state()) is None


def test_overwrite_reports_failed_deletion_honestly():
    """新卡已建、但刪舊卡失敗：不寫「已刪除舊卡」，明說現在有兩張、請手動刪舊的。"""
    delete = mock.AsyncMock(side_effect=AnkiConnectError("locked"))
    sent, _, _, _ = _creation("overwrite", delete=delete)
    assert "已刪除舊卡" not in sent[-1]
    assert "兩張相同原文的卡" in sent[-1] and sent[-1].startswith("✅ 已寫入 Anki")


def test_preview_send_failure_keeps_result_and_shows_plain_text():
    """生成成功、但 HTML 預覽送不出去：改送純文字（子卡編號看得到），保留 LLM 結果並停在選子卡這一步。"""
    from aiogram.exceptions import TelegramBadRequest

    from app.schemas.llm.expression import LLMExpressionCorrectionResult

    point = {
        "original_phrase": "a", "target_phrase": "b", "error_hint": "h",
        "native_translation": "", "context_hint": "", "context_sentence": "",
    }
    result = LLMExpressionCorrectionResult.model_validate({
        "error_comparison": "e", "grammar_correction": "g", "reorganized_expression": "r",
        "grammar_micro_points": [point], "reorganized_micro_points": [],
    })
    state = _fsm()
    _run(state.update_data(native_language="中文", target_language="日文", original_text="x"))
    handler = mock.MagicMock(execute_generate=mock.AsyncMock(return_value=result))
    message, _ = _message()

    async def answer(text, **kwargs):
        if kwargs.get("parse_mode") == "HTML" and text.startswith("🔍"):
            raise TelegramBadRequest(method=mock.MagicMock(), message="can't parse entities")
        return mock.MagicMock(delete=mock.AsyncMock())

    message.answer = mock.AsyncMock(side_effect=answer)
    with mock.patch.object(expression_fsm.handler_registry, "get_handler", return_value=handler):
        _run(expression_fsm._generate_and_preview(message, state, mock.MagicMock()))

    plain = [call for call in message.answer.await_args_list if call.kwargs.get("parse_mode") is None
             and call.args[0].startswith("🔍")]
    assert plain and "1. a → b" in plain[0].args[0]
    assert _run(state.get_state()) == ExpressionStates.waiting_for_card_selection.state
    assert _run(state.get_data())["llm_result"]


def test_reentered_step_keeps_the_step_number_first():
    """從返回回到某一步：「重新」放在步驟編號之後（「（3/6）重新輸入…」），不是「重新（3/6）」。"""
    again = expression_fsm._again(expression_fsm._CONTEXT_PROMPT, True)
    assert again.startswith("（3/6）重新輸入")
    assert speaking_fsm._again(speaking_fsm._BACK_PROMPT, True).startswith("重新輸入")


def test_long_preview_is_split_under_telegram_limit():
    chunks = expression_fsm._split_message("\n".join(["x" * 100] * 100), limit=1000)
    assert len(chunks) > 1 and all(len(chunk) <= 1000 for chunk in chunks)
    assert "\n".join(chunks) == "\n".join(["x" * 100] * 100)


@pytest.mark.parametrize("data", ["expr_back_text", "expr_cancel"])
@pytest.mark.parametrize("other_state", [speaking_fsm.SpeakingStates.waiting_for_front, None], ids=["in-other-flow", "no-flow"])
def test_expression_old_buttons_after_flow_ended_are_expired(data, other_state):
    """流程結束後點舊訊息上的返回／取消：提示已結束，不把 FSM 拉回中間某步、也不動到其他流程。"""
    state = _fsm()
    if other_state is not None:
        _run(state.set_state(other_state))
    query = _callback(data)
    handler = expression_fsm.process_go_back if data.startswith("expr_back") else expression_fsm.process_cancel
    _run(handler(query, state))

    query.answer.assert_awaited_once_with(texts.FLOW_ENDED_ALERT, show_alert=True)
    assert _run(state.get_state()) == (other_state.state if other_state is not None else None)
    query.message.delete.assert_not_awaited()


def test_expression_cancel_inside_flow_clears_state():
    state = _fsm()
    _run(state.set_state(ExpressionStates.waiting_for_context))
    query = _callback("expr_cancel")
    _run(expression_fsm.process_cancel(query, state))
    assert _run(state.get_state()) is None
    assert _sent(query.message.answer) == ["🗑️ 已取消。"]


def test_expression_language_step_escapes_input():
    state = _fsm()
    _run(state.set_state(ExpressionStates.waiting_for_lang))
    message, _ = _message(f"{NASTY} 日文")
    _run(expression_fsm.process_lang(message, state))
    [prompt] = _sent(message.answer)
    _assert_tg_html(prompt)


def test_expression_generation_failure_is_escaped_and_says_restart():
    state = _fsm()
    _run(state.update_data(native_language="中文", target_language="日文", original_text="x"))
    handler = mock.MagicMock(execute_generate=mock.AsyncMock(side_effect=ValueError("<u>bad</u> & worse")))
    message, _ = _message()
    with mock.patch.object(expression_fsm.handler_registry, "get_handler", return_value=handler):
        _run(expression_fsm._generate_and_preview(message, state, mock.MagicMock()))
    failed = _sent(message.answer)[-1]
    _assert_tg_html(failed)
    assert "/newcard" in failed


# ----------------------------------------------------------------------
# 對話卡與單字卡
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "data", ["speak_cancel", "speak_back_deck", "speak_back_lang", "speak_back_front", "speak_back_back"]
)
@pytest.mark.parametrize("other_state", [ExpressionStates.waiting_for_card_selection, None], ids=["in-other-flow", "no-flow"])
def test_speaking_old_buttons_do_not_touch_another_flow(data, other_state):
    """對話卡的取消／返回鈕（舊訊息上）：在別的流程中或沒有流程時按到，提示已結束，不清掉、也不改寫
    別條流程的狀態（例如正在選子卡的外語糾錯，那份 LLM 結果會再也用不到）。"""
    state = _fsm()
    if other_state is not None:
        _run(state.set_state(other_state))
    query = _callback(data)
    handler = speaking_fsm.process_speak_cancel if data == "speak_cancel" else speaking_fsm.process_go_back
    _run(handler(query, state))

    query.answer.assert_awaited_once_with(texts.FLOW_ENDED_ALERT, show_alert=True)
    assert _run(state.get_state()) == (other_state.state if other_state is not None else None)
    query.message.delete.assert_not_awaited()


def test_speaking_steps_have_cancel_and_escape_deck():
    """第 2 步以後也能直接取消；牌組名是使用者輸入，要轉義。"""
    state = _fsm()
    _run(state.set_state(speaking_fsm.SpeakingStates.waiting_for_deck))
    message, _ = _message(NASTY)
    _run(speaking_fsm.process_deck(message, state))
    [prompt] = _sent(message.answer)
    _assert_tg_html(prompt)
    markup = message.answer.await_args.kwargs["reply_markup"]
    assert "speak_cancel" in [button.callback_data for row in markup.inline_keyboard for button in row]


def test_speaking_back_to_front_shows_the_language():
    """從返回回到提示句：顯示的是語言（與正向進入時一致），不是牌組。"""
    state = _fsm()
    _run(state.set_state(speaking_fsm.SpeakingStates.waiting_for_back))
    _run(state.update_data(deck="Deck", target_language="ko-KR"))
    query = _callback("speak_back_front")
    _run(speaking_fsm.process_go_back(query, state))
    [prompt] = _sent(query.message.answer)
    assert "ko-KR" in prompt and "牌組" not in prompt


def test_speaking_card_created_message_is_escaped():
    """對話卡建立成功：牌組與提示句轉義（否則卡片已建、畫面卻停在「建立中」）。"""
    state = _fsm()
    _run(state.set_state(speaking_fsm.SpeakingStates.waiting_for_answers))
    _run(state.update_data(deck=NASTY, front=NASTY, target_language="en-US"))
    message, status = _message("answer one")
    anki = _anki(add_note=mock.AsyncMock(return_value=42))
    with mock.patch.object(callbacks, "_sync_with_warning", mock.AsyncMock(return_value="")):
        _run(speaking_fsm.process_answers(message, state, anki))
    [done] = _sent(status.edit_text)
    _assert_tg_html(done)
    assert done.startswith("✅ <b>對話卡已建立</b>")


def test_vocabulary_card_text_matches_what_happens():
    """單字卡：沒有呼叫 LLM，不說「查字典並生成」；單字轉義；不顯示易與 /models 混淆的「模型」。"""
    state = _fsm()
    _run(state.set_state(vocabulary_fsm.VocabularyStates.waiting_for_word))
    message, status = _message(NASTY)
    handler = mock.MagicMock(supported_models=["TOEIC_Coach_Dark"], execute_create=mock.AsyncMock(return_value=5))
    with mock.patch.object(vocabulary_fsm.handler_registry, "get_handler", return_value=handler), \
         mock.patch.object(callbacks, "_sync_with_warning", mock.AsyncMock(return_value="")):
        _run(vocabulary_fsm.process_word(message, state, mock.MagicMock(), mock.MagicMock(), _anki()))

    assert _sent(message.reply) == ["⏳ 建立中…"]
    [done] = _sent(status.edit_text)
    _assert_tg_html(done)
    assert "釋義尚未自動產生" in done and "模型" not in done
