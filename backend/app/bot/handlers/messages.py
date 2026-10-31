"""
Telegram Bot 一般文字訊息處理模組。

處理不屬於任何 FSM 流程的文字：等待說話者名稱時完成上傳語音的寫回（經服務層的 ``add_audio`` 動作），
其餘依目前的等待狀態回覆說明（建單字卡改由 /newcard 的單字卡流程處理）。

Telegram Bot plain-text message handler module.

Handles text outside any FSM flow: while waiting for a speaker name it
finishes the uploaded-audio write-back through the service layer's
``add_audio`` action; otherwise it replies according to the waiting state.
"""

import logging

from aiogram import F, Router
from aiogram.types import Message

from app.infrastructure.anki.search import field_term
from app.core.exceptions import FluencyTidesError
from app.services.note_service import NoteService

from app.bot.state import UserStateManager
from app.bot.utils.texts import CARD_NOT_FOUND, UPLOAD_RETRY, error_detail, sync_suffix, upload_done_text
from app.infrastructure.anki.client import AnkiClient, AnkiConnectError

logger = logging.getLogger(__name__)

router = Router(name="messages_router")


@router.message(F.text)
async def process_word_handler(
    message: Message,
    note_service: NoteService,
    user_state_manager: UserStateManager,
    anki_client: AnkiClient
) -> None:
    """處理使用者發送的一般文字訊息。

    Handle plain text messages sent by the user.

    如果狀態為 wait_speaker_name，則攔截作為說話者名稱，並完成上傳語音流程；
    否則依目前的等待狀態回覆說明。

    If the state is wait_speaker_name, the text is intercepted as the
    speaker's name to finish the audio-upload flow; otherwise the reply
    explains what the bot is waiting for.

    Args:
        message: Telegram 訊息物件。The Telegram message object.
        note_service: 注入的筆記服務。Injected NoteService.
        user_state_manager: 注入的使用者狀態管理器。
            Injected UserStateManager instance.
        anki_client: 注入的 AnkiClient。Injected AnkiClient instance.
    """
    word = message.text.strip() if message.text else ""
    if not word:
        return

    chat_id = message.chat.id
    state = user_state_manager.get_state(chat_id)

    # ── 攔截：互動式頭像設定完成 ──
    if state and state.action == "wait_speaker_name":
        speaker_name = word
        status_msg = await message.reply("⏳ 寫入 Anki 中…")

        try:
            card_id = state.card_id
            note_ids = await anki_client.find_notes(field_term("Card_ID", card_id))
            if not note_ids:
                await status_msg.edit_text(CARD_NOT_FOUND)
                user_state_manager.clear_state(chat_id)
                return

            notes_info = await anki_client.get_notes_info(notes=note_ids[:1])
            if not notes_info:
                await status_msg.edit_text(CARD_NOT_FOUND)
                user_state_manager.clear_state(chat_id)
                return
            note_id = notes_info[0].noteId

            field_name = state.extra.get("field_name")
            index_str = state.extra.get("index")
            audio_filename = state.extra.get("audio_filename")
            avatar_filename = state.extra.get("avatar", "")

            # 服務層依筆記類型找 handler：三語卡的自訂頭像原本寫死用對話卡的 handler，會寫錯格式
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

            await status_msg.edit_text(upload_done_text(field_name, speaker_name, sync_warning))
        except (AnkiConnectError, FluencyTidesError) as e:
            logger.error("寫回 Anki 失敗: %s", e)
            await status_msg.edit_text(f"❌ 寫回 Anki 失敗：{error_detail(e)}\n{UPLOAD_RETRY}")
            user_state_manager.clear_state(chat_id)

        return

    # ── 預設流程：兜底 ──
    # 不再提供隨打即查的服務。錄音、上傳、選頭像這些等待中的狀態收到文字也會落到這裡：
    # 依狀態說明現在在等什麼，不要說成「不在任何流程中」
    await message.reply(_fallback_reply(state.action if state else None))


def _fallback_reply(action: str | None) -> str:
    """沒有 handler 接住的文字訊息的回覆，依目前的等待狀態而定。

    The reply to unhandled text, depending on what the bot is waiting for.
    """
    if action in ("recording", "add_audio"):
        return "🎙️ 正在等語音訊息，文字不會被處理。"
    if action == "wait_avatar_selection":
        return "請點上方按鈕選擇頭像。"
    if action == "wait_avatar_upload":
        return "🖼️ 請傳一張照片。"
    return "💡 用 /newcard 新增卡片，/help 查看說明。"
