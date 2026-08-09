"""
Telegram Bot 回呼按鈕 (Inline Keyboard) 處理模組。

Telegram Bot callback button (inline keyboard) handler module.
"""

import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from app.bot.state import UserState, UserStateManager
from app.infrastructure.anki.client import AnkiClient, AnkiConnectError
from app.services.card_service import CardService
from app.core.exceptions import FluencyTidesError
from app.services.relation_service import RelationService
from app.services.task_handlers.registry import HandlerRegistry

logger = logging.getLogger(__name__)

router = Router(name="callbacks_router")

AVATARS_DIR = Path("app/assets/avatars")


async def _sync_with_warning(anki_client: AnkiClient, force: bool = True) -> str:
    """嘗試觸發 Anki 同步，回傳警告文字（若同步正常則回傳空字串）。

    Try to trigger an Anki sync and return warning text (empty string when
    the sync succeeds).

    將同步邏輯集中於此，避免在每個 handler 中重複撰寫相同的 try/except 區塊。

    Centralizes the sync logic so each handler does not repeat the same
    try/except block.

    Args:
        anki_client: AnkiClient 實例。The AnkiClient instance.
        force: 是否忽略防抖機制，強制同步（通常用於寫入資料後）。
            Whether to bypass debouncing and force the sync (usually after
            writing data).

    Returns:
        空字串代表同步成功，否則回傳包含警告 HTML 的字串。
        An empty string when the sync succeeds, otherwise a string
        containing the warning HTML.
    """
    try:
        await anki_client.sync(raise_errors=True, force=force)
    except AnkiConnectError as e:
        logger.warning("Anki 同步失敗: %s", e)
        if "衝突" in str(e) or "背景同步" in str(e):
            return (
                "\n\n⚠️ <b>Anki 同步衝突</b>\n"
                "您的 Anki 電腦版遇到了同步衝突，請打開電腦版 Anki，"
                "點擊「同步」並選擇「上傳到 AnkiWeb」即可解決！"
            )
        return "\n\n⚠️ <b>Anki 同步失敗</b>\n請確認 Anki 電腦版狀態。"
    return ""


async def _execute_final_update(
    chat_id: int,
    status_msg_text: str,
    callback: CallbackQuery,
    state: UserState,
    avatar_filename: str,
    speaker_name: str,
    user_state_manager: UserStateManager,
    anki_client: AnkiClient,
    handler_registry: HandlerRegistry,
    card_service: CardService,
    relation_service: RelationService,
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
        handler_registry: 任務處理器註冊表。The HandlerRegistry instance.
        card_service: 卡片服務。The CardService instance.
        relation_service: 關聯服務。The RelationService instance.
    """
    await callback.message.edit_text(status_msg_text)
    
    try:
        # 取前同步 (有防抖)
        await anki_client.sync(raise_errors=False)

        card_id = state.card_id
        note_ids = await anki_client.find_notes(f"Card_ID:{card_id}")
        if not note_ids:
            await callback.message.edit_text(f"❌ 找不到 Card ID 為 <code>{card_id}</code> 的卡片。")
            user_state_manager.clear_state(chat_id)
            return

        notes_info = await anki_client.get_notes_info(notes=note_ids[:1])
        note_id = notes_info[0].noteId

        field_name = state.extra.get("field_name")
        index_str = state.extra.get("index")

        # 三語卡分流：欄位帶語言後綴 (References_JA 等)，
        # 或 Prompt_Audios 的 index 槽位為語言碼 (ZH/JA/EN)
        from app.services.task_handlers.shared.trilingual_lang import (
            is_lang_code,
            lang_from_field,
        )

        is_trilingual = bool(lang_from_field(str(field_name or ""))) or (
            field_name == "Prompt_Audios" and is_lang_code(str(index_str or ""))
        )
        speaking_handler = handler_registry.get_handler(
            "speaking_trilingual" if is_trilingual else "speaking_coach"
        )
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

        await speaking_handler.execute_update(
            card_service,
            relation_service,
            note_id,
            {
                "action": "add_audio", 
                "field_name": field_name,
                "index": index_str,
                "audio": audio_filename,
                "avatar": avatar_filename,
                "speaker": speaker_name
            },
        )
        
        user_state_manager.clear_state(chat_id)
        
        # 寫入完成後觸發同步
        sync_warning = await _sync_with_warning(anki_client)

        await callback.message.edit_text(
            f"✅ <b>語音上傳完成！</b>\n\n"
            f"🎯 卡片：<code>{card_id}</code>\n"
            f"📂 目標欄位：<code>{field_name}</code>\n"
            f"👤 說話者：<code>{speaker_name}</code>\n\n"
            f"<i>音檔與頭像設定已寫入本地！🎉</i>{sync_warning}"
        )
    except (AnkiConnectError, FluencyTidesError) as e:
        logger.error("寫回 Anki 失敗: %s", e)
        await callback.message.edit_text(f"❌ 寫回 Anki 失敗: {str(e)[:200]}")
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
        await callback.answer("狀態已失效，請重新操作。", show_alert=True)
        return

    avatar_files = []
    if AVATARS_DIR.exists():
        for file in AVATARS_DIR.iterdir():
            if file.is_file() and file.suffix.lower() in (".jpg", ".png", ".jpeg"):
                avatar_files.append(file.name)

    if not avatar_files:
        await callback.answer("系統目前沒有內建頭像，請選擇上傳自訂圖片。", show_alert=True)
        return

    buttons = []
    for filename in avatar_files:
        display_name = Path(filename).stem
        buttons.append([InlineKeyboardButton(text=f"👤 {display_name}", callback_data=f"avatar_sel_{filename}")])
    
    buttons.append([InlineKeyboardButton(text="⬅️ 返回", callback_data="avatar_back")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.edit_text("請選擇一個系統頭像：", reply_markup=keyboard)


@router.callback_query(F.data.startswith("avatar_sel_"))
async def handle_avatar_sys_select(
    callback: CallbackQuery, 
    user_state_manager: UserStateManager,
    anki_client: AnkiClient,
    handler_registry: HandlerRegistry,
    card_service: CardService,
    relation_service: RelationService,
) -> None:
    """處理系統頭像選擇並完成寫回。

    Handle a system-avatar selection and finish the write-back.

    Args:
        callback: 回呼查詢物件。The callback query object.
        user_state_manager: 使用者狀態管理器。The UserStateManager instance.
        anki_client: AnkiClient 實例。The AnkiClient instance.
        handler_registry: 任務處理器註冊表。The HandlerRegistry instance.
        card_service: 卡片服務。The CardService instance.
        relation_service: 關聯服務。The RelationService instance.
    """
    chat_id = callback.message.chat.id
    state = user_state_manager.get_state(chat_id)

    if not state or state.action != "wait_avatar_selection":
        await callback.answer("狀態已失效，請重新操作。", show_alert=True)
        return

    filename = callback.data.replace("avatar_sel_", "", 1)
    speaker_name = Path(filename).stem
    
    await _execute_final_update(
        chat_id, 
        f"🔄 <b>處理中...</b>\n\n已選擇系統頭像「{speaker_name}」，正在寫回 Anki...",
        callback, state, filename, speaker_name,
        user_state_manager, anki_client, handler_registry, card_service, relation_service
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
        await callback.answer("狀態已失效，請重新操作。", show_alert=True)
        return

    state.action = "wait_avatar_upload"
    state.expires_at = None
    user_state_manager.set_state(chat_id, state)

    await callback.message.edit_text("🖼️ 請傳送一張圖片 (將作為頭像使用)：")


@router.callback_query(F.data == "avatar_skip")
async def handle_avatar_skip(
    callback: CallbackQuery, 
    user_state_manager: UserStateManager,
    anki_client: AnkiClient,
    handler_registry: HandlerRegistry,
    card_service: CardService,
    relation_service: RelationService,
) -> None:
    """處理跳過頭像設定並完成寫回（預設說話者為 User）。

    Handle skipping avatar selection and finish the write-back
    (defaults the speaker to "User").

    Args:
        callback: 回呼查詢物件。The callback query object.
        user_state_manager: 使用者狀態管理器。The UserStateManager instance.
        anki_client: AnkiClient 實例。The AnkiClient instance.
        handler_registry: 任務處理器註冊表。The HandlerRegistry instance.
        card_service: 卡片服務。The CardService instance.
        relation_service: 關聯服務。The RelationService instance.
    """
    chat_id = callback.message.chat.id
    state = user_state_manager.get_state(chat_id)

    if not state or state.action != "wait_avatar_selection":
        await callback.answer("狀態已失效，請重新操作。", show_alert=True)
        return

    await _execute_final_update(
        chat_id, 
        "🔄 <b>處理中...</b>\n\n已跳過頭像設定 (預設為 User)，正在寫回 Anki...",
        callback, state, "", "User",
        user_state_manager, anki_client, handler_registry, card_service, relation_service
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
        await callback.answer("狀態已失效，請重新操作。", show_alert=True)
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📂 選擇系統頭像", callback_data="avatar_sys")],
        [InlineKeyboardButton(text="🖼️ 上傳自訂圖片", callback_data="avatar_upload")],
        [InlineKeyboardButton(text="⏭️ 跳過 (不使用圖片)", callback_data="avatar_skip")]
    ])

    await callback.message.edit_text(
        f"✅ <b>語音接收成功！</b>\n\n接下來，請選擇要為這段語音搭配的頭像：",
        reply_markup=keyboard
    )
