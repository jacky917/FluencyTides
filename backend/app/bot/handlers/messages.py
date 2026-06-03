"""
Telegram Bot 一般文字訊息處理模組。

負責接收使用者發送的文字（視為單字/片語），組裝成 CardGenerateRequest，
並委託由 Middleware 注入的 CardService 進行核心生成邏輯。
"""

import logging

from aiogram import F, Router
from aiogram.types import Message

from app.core.config import settings
from app.core.exceptions import FluencyTidesError
from app.schemas.card import CardGenerateRequest
from app.services.card_service import CardService

logger = logging.getLogger(__name__)

router = Router(name="messages_router")


@router.message(F.text)
async def process_word_handler(
    message: Message,
    card_service: CardService,
) -> None:
    """處理使用者發送的一般文字訊息。

    將使用者發送的文字視為要學習的字詞，呼叫 CardService 自動生成卡片。

    Args:
        message: Telegram 訊息物件。
        card_service: 由 ServiceInjectionMiddleware 注入的 CardService 實例。
    """
    word = message.text.strip()
    if not word:
        return

    # 回覆處理中訊息，提升使用者體驗
    status_msg = await message.reply("⏳ 正在為您生成卡片，請稍候...")

    # 使用環境變數設定的預設牌組與模型
    deck_name = settings.TG_DEFAULT_DECK
    model_name = settings.TG_DEFAULT_MODEL_NAME
    model_file_name = settings.TG_DEFAULT_MODEL_FILE

    # 建立請求物件
    request = CardGenerateRequest(
        user_input=word,
        deck_name=deck_name,
        model_name=model_name,
        model_file_name=model_file_name,
        primary_field_name="Expression",
        tags=["TelegramBot"],
    )

    try:
        # 呼叫與 Web API 完全共用的業務邏輯層
        response = await card_service.generate_card(request)
        
        await status_msg.edit_text(
            f"✅ <b>建立成功！</b>\n\n"
            f"字詞：「<b>{word}</b>」\n"
            f"牌組：<code>{deck_name}</code>\n"
            f"模型：<code>{model_name}</code>\n"
            f"筆記 ID：<code>{response.note_id}</code>\n\n"
            f"<i>趕快去 Anki 看看生成的內容吧！</i>"
        )
    except FluencyTidesError as e:
        # 捕捉已定義的業務錯誤，回報給使用者
        logger.warning("Telegram Bot 卡片生成失敗 (業務異常): %s", e.message)
        
        error_icon = "⚠️"
        if e.error_code == "DUPLICATE_CARD":
            error_icon = "🔁"
        elif e.error_code == "DECK_NOT_FOUND":
            error_icon = "📂"
            
        await status_msg.edit_text(
            f"{error_icon} <b>生成失敗</b>\n\n"
            f"錯誤碼：<code>{e.error_code}</code>\n"
            f"原因：{e.message}"
        )
    except Exception as e:
        # 捕捉預期外的系統錯誤
        logger.exception("Telegram Bot 卡片生成發生未預期錯誤: %s", e)
        await status_msg.edit_text(
            f"❌ <b>系統發生異常</b>\n\n"
            f"無法完成生成卡片，請檢查後端日誌或確認 AnkiConnect 是否正常運作。\n"
            f"詳細: {str(e)}"
        )
