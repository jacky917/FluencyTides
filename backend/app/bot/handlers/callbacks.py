"""Telegram Bot callback button (inline keyboard) handler module.

Telegram Bot 回調按鈕 (Inline Keyboard) 處理模組。

Handles the avatar-selection buttons of the audio-upload flow (system avatar, custom upload, no avatar, back) and
writes the uploaded audio back to Anki once the avatar and speaker are chosen.

處理上傳語音流程的選頭像按鈕（系統頭像、上傳自訂圖片、不用頭像、返回），選定頭像與說話者後把上傳的語音寫回 Anki。
"""

import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from app.infrastructure.anki.search import field_term
from app.bot.state import UserState, UserStateManager
from app.bot.utils.callback_reply import answer_callback
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
    """Force a sync after writing and return the warning to append to the TG reply.

    寫入後強制同步，回傳接在 TG 回覆後面的提醒。

    For write paths that do not go through the service layer yet (deletion in ``commands.py``, creating a speaking
    card in ``speaking_fsm.py``, and overwriting an old card in ``expression_fsm.py``); the warning text is the same
    as the service layer's (``note_service.sync_warning``).

    給還沒經過服務層的寫入點用（``commands.py`` 的刪除、``speaking_fsm.py`` 建對話卡、
    ``expression_fsm.py`` 覆蓋舊卡後）；提醒文字與服務層是同一份（``note_service.sync_warning``）。

    Args:
        anki_client: the AnkiConnect client
            AnkiClient 實例

    Returns:
        the warning text, starting with a newline; an empty string when the sync succeeds
        提醒文字（開頭帶換行）；同步成功時為空字串
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
    """Write the uploaded audio back to Anki after the avatar and speaker name are chosen, then sync.

    共用邏輯：完成頭像與名稱設定後，寫回 Anki 並觸發同步。

    A non-empty avatar is first copied from the local avatar directory to Anki Media. The user state is cleared on
    success, when the card is missing, and on an Anki or business error.

    頭像檔名非空時，先把本地頭像目錄的檔案存進 Anki Media。成功、找不到卡片、或發生 Anki 或業務錯誤時
    都會清除使用者狀態。

    Args:
        chat_id: the Telegram chat ID
            Telegram Chat ID
        status_msg_text: the in-progress status text shown first
            先顯示的處理中狀態訊息文字
        callback: the callback query whose message is edited
            要編輯其訊息的回調查詢
        state: the current user state
            目前的使用者狀態
        avatar_filename: the avatar file name; an empty string means no avatar
            頭像檔名；空字串表示不用頭像
        speaker_name: the speaker's name
            說話者名稱
        user_state_manager: the user state manager
            使用者狀態管理器
        anki_client: the AnkiConnect client
            AnkiClient 實例
        note_service: the note service; the write and the sync go through it
            筆記服務；寫入與同步經過它
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
    """List the built-in system avatars for the user to choose from.

    列出系統內建頭像供使用者選擇。

    The list shown is stored in the state, so the button indices refer to exactly what the user sees.

    顯示的清單會存進狀態，按鈕序號就對應使用者看到的這份清單。

    Args:
        callback: the callback query
            回調查詢物件
        user_state_manager: the user state manager
            使用者狀態管理器
    """
    chat_id = callback.message.chat.id
    state = user_state_manager.get_state(chat_id)

    if not state or state.action != "wait_avatar_selection":
        await answer_callback(callback, UPLOAD_EXPIRED_ALERT, show_alert=True)
        return

    avatar_files = _system_avatars()
    if not avatar_files:
        await answer_callback(callback, "系統目前沒有內建頭像，請選擇上傳自訂圖片。", show_alert=True)
        return

    # 把這一刻的清單存進狀態，按鈕的序號就對應使用者看到的這份清單：自訂頭像也存在同一個目錄，
    # 若之後重新列目錄，期間有人上傳頭像就會讓序號位移、選到別張
    state.extra["avatar_files"] = avatar_files
    user_state_manager.set_state(chat_id, state)

    await answer_callback(callback)
    buttons = [
        [InlineKeyboardButton(text=f"👤 {Path(filename).stem}"[:60], callback_data=f"avatar_sel_{index}")]
        for index, filename in enumerate(avatar_files)
    ]
    buttons.append([InlineKeyboardButton(text="⬅️ 返回", callback_data="avatar_back")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.edit_text("請選擇一個系統頭像：", reply_markup=keyboard)


def _system_avatars() -> list[str]:
    """Return the built-in avatar file names, sorted by name.

    回傳系統內建頭像的檔名，依名稱排序。

    Buttons refer to avatars by index, so a fixed order prevents picking the wrong one. Only .jpg, .jpeg, and .png
    files count; a missing directory gives an empty list.

    按鈕以序號對應，排序固定才不會選錯。只算 .jpg、.jpeg、.png 檔；目錄不存在時回傳空串列。
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
    """Handle a system-avatar selection and finish the write-back.

    處理系統頭像選擇並完成寫回。

    The avatar's file stem becomes the speaker name. An index that does not match the list stored in the state
    (including old buttons that carried a file name) is treated as expired.

    頭像的檔名（不含副檔名）作為說話者名稱。序號對不上狀態中存的清單時（含以檔名當 callback data 的舊按鈕）視為過期。

    Args:
        callback: the callback query
            回調查詢物件
        user_state_manager: the user state manager
            使用者狀態管理器
        anki_client: the AnkiConnect client
            AnkiClient 實例
        note_service: the note service
            筆記服務
    """
    chat_id = callback.message.chat.id
    state = user_state_manager.get_state(chat_id)

    if not state or state.action != "wait_avatar_selection":
        await answer_callback(callback, UPLOAD_EXPIRED_ALERT, show_alert=True)
        return

    # 序號對應顯示選單當下存進狀態的清單（見 handle_avatar_sys），不重新列目錄
    avatar_files = state.extra.get("avatar_files") or []
    raw_index = callback.data.replace("avatar_sel_", "", 1)
    if not (raw_index.isascii() and raw_index.isdigit()) or int(raw_index) >= len(avatar_files):
        # 舊格式（以檔名當 callback data）或沒有對應的清單：當成過期，不要選錯頭像
        await answer_callback(callback, UPLOAD_EXPIRED_ALERT, show_alert=True)
        return
    filename = avatar_files[int(raw_index)]
    speaker_name = Path(filename).stem

    await answer_callback(callback, "⏳ 寫入中…")
    await _execute_final_update(
        chat_id,
        f"⏳ 以「{escape_tg(speaker_name, 50)}」寫入 Anki 中…",
        callback, state, filename, speaker_name,
        user_state_manager, anki_client, note_service
    )


@router.callback_query(F.data == "avatar_upload")
async def handle_avatar_upload(callback: CallbackQuery, user_state_manager: UserStateManager) -> None:
    """Switch to the state waiting for the user to upload a custom avatar image.

    切換至等待使用者上傳自訂頭像圖片的狀態。

    Args:
        callback: the callback query
            回調查詢物件
        user_state_manager: the user state manager
            使用者狀態管理器
    """
    chat_id = callback.message.chat.id
    state = user_state_manager.get_state(chat_id)

    if not state or state.action != "wait_avatar_selection":
        await answer_callback(callback, UPLOAD_EXPIRED_ALERT, show_alert=True)
        return

    state.action = "wait_avatar_upload"
    state.expires_at = None
    user_state_manager.set_state(chat_id, state)

    await answer_callback(callback)
    await callback.message.edit_text("🖼️ 請傳一張照片作為頭像（不要用「檔案」傳送）。")


@router.callback_query(F.data == "avatar_skip")
async def handle_avatar_skip(
    callback: CallbackQuery,
    user_state_manager: UserStateManager,
    anki_client: AnkiClient,
    note_service: NoteService,
) -> None:
    """Handle skipping the avatar and finish the write-back, with the speaker defaulting to "User".

    處理跳過頭像設定並完成寫回（預設說話者為 User）。

    Args:
        callback: the callback query
            回調查詢物件
        user_state_manager: the user state manager
            使用者狀態管理器
        anki_client: the AnkiConnect client
            AnkiClient 實例
        note_service: the note service
            筆記服務
    """
    chat_id = callback.message.chat.id
    state = user_state_manager.get_state(chat_id)

    if not state or state.action != "wait_avatar_selection":
        await answer_callback(callback, UPLOAD_EXPIRED_ALERT, show_alert=True)
        return

    await answer_callback(callback, "⏳ 寫入中…")
    await _execute_final_update(
        chat_id,
        "⏳ 不使用頭像，寫入 Anki 中…",
        callback, state, "", "User",
        user_state_manager, anki_client, note_service
    )


@router.callback_query(F.data == "avatar_back")
async def handle_avatar_back(callback: CallbackQuery, user_state_manager: UserStateManager) -> None:
    """Return to the avatar-selection main menu.

    返回頭像選擇主選單。

    Args:
        callback: the callback query
            回調查詢物件
        user_state_manager: the user state manager
            使用者狀態管理器
    """
    chat_id = callback.message.chat.id
    state = user_state_manager.get_state(chat_id)

    if not state or state.action != "wait_avatar_selection":
        await answer_callback(callback, UPLOAD_EXPIRED_ALERT, show_alert=True)
        return

    await answer_callback(callback)
    await callback.message.edit_text(AVATAR_PROMPT, reply_markup=avatar_keyboard())


# 收到上傳的語音後，請使用者選頭像（voice.py 與「返回」共用）
AVATAR_PROMPT = "✅ 已收到語音。選擇搭配的頭像："


def avatar_keyboard() -> InlineKeyboardMarkup:
    """Return the avatar-choice keyboard: system avatar, custom upload, or no avatar.

    回傳選頭像的按鈕：系統頭像、上傳自訂圖片或不用頭像。
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📂 選擇系統頭像", callback_data="avatar_sys")],
        [InlineKeyboardButton(text="🖼️ 上傳自訂圖片", callback_data="avatar_upload")],
        [InlineKeyboardButton(text="⏭️ 不用頭像", callback_data="avatar_skip")],
    ])
