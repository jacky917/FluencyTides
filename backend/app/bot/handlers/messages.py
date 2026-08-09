"""
Telegram Bot 一般文字訊息處理模組。

負責接收使用者發送的文字（視為單字/片語），
透過 HandlerRegistry 動態分派到 VocabularyMiningHandler 進行生卡。

Phase 9 更新：
- 不再直接呼叫 CardService.generate_card()。
- 改為透過 HandlerRegistry 取得 vocabulary_mining handler 並呼叫 execute_create。
- 不再依賴硬編碼的 TG_DEFAULT_DECK / TG_DEFAULT_MODEL_NAME。

Telegram Bot plain-text message handler module.

Receives user-sent text (treated as a word/phrase) and dispatches it to
VocabularyMiningHandler via HandlerRegistry for card generation.

Phase 9 update:
- No longer calls CardService.generate_card() directly.
- Instead fetches the vocabulary_mining handler from HandlerRegistry and
  calls execute_create.
- No longer relies on hard-coded TG_DEFAULT_DECK / TG_DEFAULT_MODEL_NAME.
"""

import logging

from aiogram import F, Router
from aiogram.types import Message

from app.core.exceptions import FluencyTidesError
from app.services.card_service import CardService
from app.services.relation_service import RelationService
from app.services.task_handlers.registry import HandlerRegistry

from app.bot.state import UserStateManager
from app.infrastructure.anki.client import AnkiClient, AnkiConnectError

logger = logging.getLogger(__name__)

router = Router(name="messages_router")


@router.message(F.text)
async def process_word_handler(
    message: Message,
    handler_registry: HandlerRegistry,
    card_service: CardService,
    relation_service: RelationService,
    user_state_manager: UserStateManager,
    anki_client: AnkiClient
) -> None:
    """處理使用者發送的一般文字訊息。

    Handle plain text messages sent by the user.

    如果狀態為 wait_speaker_name，則攔截作為說話者名稱，並完成上傳語音流程。
    否則將文字視為要學習的字詞，呼叫 VocabularyMiningHandler 自動生成卡片。

    If the state is wait_speaker_name, the text is intercepted as the
    speaker's name to finish the audio-upload flow. Otherwise the text is
    treated as a word to learn and VocabularyMiningHandler generates a card.

    Args:
        message: Telegram 訊息物件。The Telegram message object.
        handler_registry: 注入的 HandlerRegistry。Injected HandlerRegistry.
        card_service: 注入的 CardService。Injected CardService instance.
        relation_service: 注入的 RelationService。Injected RelationService.
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
        status_msg = await message.reply("🔄 <b>處理中...</b>\n\n正在將資料寫回 Anki...")
        
        try:
            # 取前同步 (有防抖)
            await anki_client.sync(raise_errors=False)

            card_id = state.card_id
            note_ids = await anki_client.find_notes(f"Card_ID:{card_id}")
            if not note_ids:
                await status_msg.edit_text(f"❌ 找不到 Card ID 為 <code>{card_id}</code> 的卡片。")
                user_state_manager.clear_state(chat_id)
                return

            notes_info = await anki_client.get_notes_info(notes=note_ids[:1])
            note_id = notes_info[0].noteId

            speaking_handler = handler_registry.get_handler("speaking_coach")
            
            field_name = state.extra.get("field_name")
            index_str = state.extra.get("index")
            audio_filename = state.extra.get("audio_filename")
            avatar_filename = state.extra.get("avatar", "")
            
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
            from app.bot.handlers.callbacks import _sync_with_warning
            sync_warning = await _sync_with_warning(anki_client)

            await status_msg.edit_text(
                f"✅ <b>語音上傳完成！</b>\n\n"
                f"🎯 卡片：<code>{card_id}</code>\n"
                f"📂 目標欄位：<code>{field_name}</code>\n"
                f"👤 說話者：<code>{speaker_name}</code>\n\n"
                f"<i>音檔與頭像設定已寫入本地！🎉</i>{sync_warning}"
            )
        except (AnkiConnectError, FluencyTidesError) as e:
            logger.error("寫回 Anki 失敗: %s", e)
            await status_msg.edit_text(f"❌ 寫回 Anki 失敗: {str(e)[:200]}")
            user_state_manager.clear_state(chat_id)
            
        return

    # ── 預設流程：無狀態兜底 (防呆) ──
    # 不再提供隨打即查的服務，強制使用者使用明確的指令與狀態機流程
    await message.reply(
        "❌ <b>系統不明白您的意思。</b>\n\n"
        "您目前不在任何新增卡片的流程中。請使用以下指令開始操作：\n\n"
        "📝 /newcard - 新增各式卡片 (單字、糾錯、對話)\n"
        "📖 /help - 查看完整教學\n"
        "🔄 /sync - 同步資料庫",
        parse_mode="HTML"
    )
