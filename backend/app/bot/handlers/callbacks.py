"""
Telegram Bot 回呼按鈕 (Inline Keyboard) 處理模組。

Telegram Bot callback button (inline keyboard) handler module.
"""

import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from app.infrastructure.anki.search import field_term
from app.bot.state import UserState, UserStateManager
from app.bot.utils.formatting import escape_tg
from app.bot.utils.texts import (
    CARD_NOT_FOUND,
    UPLOAD_EXPIRED_ALERT,
    UPLOAD_RETRY,
    error_detail,
    sync_suffix,
    upload_done_text,
)
from app.infrastructure.anki.client import AnkiClient, AnkiConnectError
from app.core.exceptions import FluencyTidesError
from app.services.note_service import NoteService, sync_warning

logger = logging.getLogger(__name__)

router = Router(name="callbacks_router")

AVATARS_DIR = Path("app/assets/avatars")


async def _sync_with_warning(anki_client: AnkiClient) -> str:
    """寫入後強制同步，回傳接在 TG 回覆後面的提醒（同步成功時為空字串）。

    Force a sync after writing and return the warning suffix for a reply.

    給還沒經過服務層的寫入點用（``commands.py`` 的刪除、``speaking_fsm.py`` 建對話卡、
    ``expression_fsm.py`` 覆蓋舊卡後）；提醒文字與服務層是同一份（``note_service.sync_warning``）。

    For write paths that do not go through the service layer yet; the
    warning text is the service layer's.

    Args:
        anki_client: AnkiClient 實例。The AnkiClient instance.

    Returns:
        提醒文字（開頭帶換行）；同步成功時為空字串。The warning suffix, or "".
    """
    return sync_suffix(await sync_warning(anki_client))


async def _execute_final_update(
    chat_id: int,
    status_msg_text: str,
    callback: CallbackQuery,
    state: UserState,
    avatar_filename: str,
    speaker_name: str,
    user_state_manager: UserStateManager,
    anki_client: AnkiClient,
    note_service: NoteService,
) -> None:
    """共用邏輯：完成頭像與名稱設定後，寫回 Anki 並觸發同步。

    Shared logic: after avatar and speaker-name selection, write back to
    Anki and trigger a sync.

    Args:
        chat_id: Telegram Chat ID。The Telegram chat ID.
        status_msg_text: 處理中狀態訊息文字。The in-progress status text.
        callback: 回呼查詢物件。The callback query object.
        state: 目前的使用者狀態。The current UserState.
        avatar_filename: 頭像檔名（可為空字串）。Avatar file name (may be empty).
        speaker_name: 說話者名稱。The speaker's name.
        user_state_manager: 使用者狀態管理器。The UserStateManager instance.
        anki_client: AnkiClient 實例。The AnkiClient instance.
        note_service: 筆記服務；寫入與同步經過它。The NoteService instance.
    """
    await callback.message.edit_text(status_msg_text)

    try:
        # 取前同步 (有防抖)
        await anki_client.sync(raise_errors=False)

        card_id = state.card_id
        note_ids = await anki_client.find_notes(field_term("Card_ID", card_id))
        if not note_ids:
            await callback.message.edit_text(CARD_NOT_FOUND)
            user_state_manager.clear_state(chat_id)
            return

        notes_info = await anki_client.get_notes_info(notes=note_ids[:1])
        if not notes_info:
            await callback.message.edit_text(CARD_NOT_FOUND)
            user_state_manager.clear_state(chat_id)
            return
        note_id = notes_info[0].noteId

        field_name = state.extra.get("field_name")
        index_str = state.extra.get("index")
        audio_filename = state.extra.get("audio_filename")

        import base64
        from app.schemas.anki import AnkiStoreMediaParams

        if avatar_filename:
            local_avatar_path = AVATARS_DIR / avatar_filename
            if local_avatar_path.exists():
                with open(local_avatar_path, "rb") as f:
                    avatar_b64 = base64.b64encode(f.read()).decode("utf-8")
                await anki_client.store_media_file(
                    params=AnkiStoreMediaParams(filename=avatar_filename, data=avatar_b64)
                )

        # 服務層依筆記類型找 handler（對話卡或三語卡），寫入後同步並回傳提醒
        warning = await note_service.run_action(
            note_id,
            "add_audio",
            {
                "field_name": field_name,
                "index": index_str,
                "audio": audio_filename,
                "avatar": avatar_filename,
                "speaker": speaker_name
            },
        )
        sync_warning = sync_suffix(warning)

        user_state_manager.clear_state(chat_id)

        await callback.message.edit_text(upload_done_text(field_name, speaker_name, sync_warning))
    except (AnkiConnectError, FluencyTidesError) as e:
        logger.error("寫回 Anki 失敗: %s", e)
        # 狀態已清：要說重新進入的入口（音檔已上傳到 Anki Media，重新上傳即可）
        await callback.message.edit_text(f"❌ 寫回 Anki 失敗：{error_detail(e)}\n{UPLOAD_RETRY}")
        user_state_manager.clear_state(chat_id)


@router.callback_query(F.data == "avatar_sys")
async def handle_avatar_sys(callback: CallbackQuery, user_state_manager: UserStateManager) -> None:
    """列出系統內建頭像供使用者選擇。

    List the built-in system avatars for the user to choose from.

    Args:
        callback: 回呼查詢物件。The callback query object.
        user_state_manager: 使用者狀態管理器。The UserStateManager instance.
    """
    chat_id = callback.message.chat.id
    state = user_state_manager.get_state(chat_id)

    if not state or state.action != "wait_avatar_selection":
        await callback.answer(UPLOAD_EXPIRED_ALERT, show_alert=True)
        return

    avatar_files = _system_avatars()
    if not avatar_files:
        await callback.answer("系統目前沒有內建頭像，請選擇上傳自訂圖片。", show_alert=True)
        return

    # 把這一刻的清單存進狀態，按鈕的序號就對應使用者看到的這份清單：自訂頭像也存在同一個目錄，
    # 若之後重新列目錄，期間有人上傳頭像就會讓序號位移、選到別張
    state.extra["avatar_files"] = avatar_files
    user_state_manager.set_state(chat_id, state)

    await callback.answer()
    buttons = [
        [InlineKeyboardButton(text=f"👤 {Path(filename).stem}"[:60], callback_data=f"avatar_sel_{index}")]
        for index, filename in enumerate(avatar_files)
    ]
    buttons.append([InlineKeyboardButton(text="⬅️ 返回", callback_data="avatar_back")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.edit_text("請選擇一個系統頭像：", reply_markup=keyboard)


def _system_avatars() -> list[str]:
    """系統內建頭像的檔名，依名稱排序（按鈕以序號對應，排序固定才不會選錯）。

    Built-in avatar file names, sorted so button indices stay stable.
    """
    if not AVATARS_DIR.exists():
        return []
    return sorted(
        file.name
        for file in AVATARS_DIR.iterdir()
        if file.is_file() and file.suffix.lower() in (".jpg", ".png", ".jpeg")
    )


@router.callback_query(F.data.startswith("avatar_sel_"))
async def handle_avatar_sys_select(
    callback: CallbackQuery,
    user_state_manager: UserStateManager,
    anki_client: AnkiClient,
    note_service: NoteService,
) -> None:
    """處理系統頭像選擇並完成寫回。

    Handle a system-avatar selection and finish the write-back.

    Args:
        callback: 回呼查詢物件。The callback query object.
        user_state_manager: 使用者狀態管理器。The UserStateManager instance.
        anki_client: AnkiClient 實例。The AnkiClient instance.
        note_service: 筆記服務。The NoteService instance.
    """
    chat_id = callback.message.chat.id
    state = user_state_manager.get_state(chat_id)

    if not state or state.action != "wait_avatar_selection":
        await callback.answer(UPLOAD_EXPIRED_ALERT, show_alert=True)
        return

    # 序號對應顯示選單當下存進狀態的清單（見 handle_avatar_sys），不重新列目錄
    avatar_files = state.extra.get("avatar_files") or []
    raw_index = callback.data.replace("avatar_sel_", "", 1)
    if not (raw_index.isascii() and raw_index.isdigit()) or int(raw_index) >= len(avatar_files):
        # 舊格式（以檔名當 callback data）或沒有對應的清單：當成過期，不要選錯頭像
        await callback.answer(UPLOAD_EXPIRED_ALERT, show_alert=True)
        return
    filename = avatar_files[int(raw_index)]
    speaker_name = Path(filename).stem

    await callback.answer("⏳ 寫入中…")
    await _execute_final_update(
        chat_id,
        f"⏳ 以「{escape_tg(speaker_name, 50)}」寫入 Anki 中…",
        callback, state, filename, speaker_name,
        user_state_manager, anki_client, note_service
    )


@router.callback_query(F.data == "avatar_upload")
async def handle_avatar_upload(callback: CallbackQuery, user_state_manager: UserStateManager) -> None:
    """切換至等待使用者上傳自訂頭像圖片的狀態。

    Switch to the state waiting for the user to upload a custom avatar image.

    Args:
        callback: 回呼查詢物件。The callback query object.
        user_state_manager: 使用者狀態管理器。The UserStateManager instance.
    """
    chat_id = callback.message.chat.id
    state = user_state_manager.get_state(chat_id)

    if not state or state.action != "wait_avatar_selection":
        await callback.answer(UPLOAD_EXPIRED_ALERT, show_alert=True)
        return

    state.action = "wait_avatar_upload"
    state.expires_at = None
    user_state_manager.set_state(chat_id, state)

    await callback.answer()
    await callback.message.edit_text("🖼️ 請傳一張照片作為頭像（不要用「檔案」傳送）。")


@router.callback_query(F.data == "avatar_skip")
async def handle_avatar_skip(
    callback: CallbackQuery,
    user_state_manager: UserStateManager,
    anki_client: AnkiClient,
    note_service: NoteService,
) -> None:
    """處理跳過頭像設定並完成寫回（預設說話者為 User）。

    Handle skipping avatar selection and finish the write-back
    (defaults the speaker to "User").

    Args:
        callback: 回呼查詢物件。The callback query object.
        user_state_manager: 使用者狀態管理器。The UserStateManager instance.
        anki_client: AnkiClient 實例。The AnkiClient instance.
        note_service: 筆記服務。The NoteService instance.
    """
    chat_id = callback.message.chat.id
    state = user_state_manager.get_state(chat_id)

    if not state or state.action != "wait_avatar_selection":
        await callback.answer(UPLOAD_EXPIRED_ALERT, show_alert=True)
        return

    await callback.answer("⏳ 寫入中…")
    await _execute_final_update(
        chat_id,
        "⏳ 不使用頭像，寫入 Anki 中…",
        callback, state, "", "User",
        user_state_manager, anki_client, note_service
    )


@router.callback_query(F.data == "avatar_back")
async def handle_avatar_back(callback: CallbackQuery, user_state_manager: UserStateManager) -> None:
    """返回頭像選擇主選單。

    Return to the avatar-selection main menu.

    Args:
        callback: 回呼查詢物件。The callback query object.
        user_state_manager: 使用者狀態管理器。The UserStateManager instance.
    """
    chat_id = callback.message.chat.id
    state = user_state_manager.get_state(chat_id)

    if not state or state.action != "wait_avatar_selection":
        await callback.answer(UPLOAD_EXPIRED_ALERT, show_alert=True)
        return

    await callback.answer()
    await callback.message.edit_text(AVATAR_PROMPT, reply_markup=avatar_keyboard())


# 收到上傳的語音後，請使用者選頭像（voice.py 與「返回」共用）
AVATAR_PROMPT = "✅ 已收到語音。選擇搭配的頭像："


def avatar_keyboard() -> InlineKeyboardMarkup:
    """選頭像的按鈕。The avatar-choice keyboard."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📂 選擇系統頭像", callback_data="avatar_sys")],
        [InlineKeyboardButton(text="🖼️ 上傳自訂圖片", callback_data="avatar_upload")],
        [InlineKeyboardButton(text="⏭️ 不用頭像", callback_data="avatar_skip")],
    ])
