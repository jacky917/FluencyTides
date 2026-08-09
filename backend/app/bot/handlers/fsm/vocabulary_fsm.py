"""單字卡生成 Telegram Bot FSM Handler。

本模組實作單字/片語卡的互動式新增流程，取代原本無狀態的兜底生成邏輯。
流程：
啟動單字卡流程 -> 等待輸入單字 -> 呼叫 LLM 查字典並生成 Anki 卡片 -> 結束。

Vocabulary-card creation Telegram Bot FSM handler.

This module implements the interactive creation flow for word/phrase cards,
replacing the previous stateless fallback generation. Flow:
start the vocabulary flow -> wait for a word -> call the LLM to look it up
and generate the Anki card -> finish.
"""

import logging

from aiogram import Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from app.core.exceptions import FluencyTidesError
from app.services.card_service import CardService
from app.services.relation_service import RelationService
from app.services.task_handlers.registry import handler_registry
from app.infrastructure.anki.client import AnkiClient, AnkiConnectError

logger = logging.getLogger(__name__)

router = Router(name="vocabulary_fsm")


class VocabularyStates(StatesGroup):
    """單字卡生成流程的 FSM 狀態定義。

    FSM state definitions for the vocabulary-card creation flow.

    定義了使用者與機器人互動的單字查詢狀態。

    Defines the word-lookup interaction state.
    """
    waiting_for_word = State()


async def start_vocabulary_fsm_flow(message: types.Message, state: FSMContext) -> None:
    """啟動單字卡新增 FSM 流程的公開入口。

    Public entry point that starts the vocabulary-card creation FSM flow.

    由 newcard_menu.py 的 Inline Keyboard 回呼觸發，
    設定 FSM 狀態並提示使用者輸入單字。

    Triggered by the inline keyboard callback in newcard_menu.py; sets the
    FSM state and prompts the user for a word.

    Args:
        message: Telegram 訊息物件（來自 callback.message）。
            The Telegram message object (from callback.message).
        state: aiogram FSM 上下文。The aiogram FSM context.

    Returns:
        None
    """
    await state.set_state(VocabularyStates.waiting_for_word)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ 取消", callback_data="vocab_cancel")]
    ])
    
    await message.answer(
        "📚 <b>新增單字卡 (Vocabulary Mining)</b>\n\n"
        "請輸入您想學習的外語單字或片語：",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(lambda c: c.data == "vocab_cancel", VocabularyStates.waiting_for_word)
async def process_vocab_cancel(callback_query: types.CallbackQuery, state: FSMContext) -> None:
    """處理取消操作。

    Handle the cancel action.

    Args:
        callback_query: Telegram 回呼查詢。The Telegram callback query.
        state: aiogram FSM 上下文。The aiogram FSM context.

    Returns:
        None
    """
    if callback_query.message:
        await callback_query.message.edit_reply_markup(reply_markup=None)
        await callback_query.message.answer("🗑️ 已取消單字卡新增。")
    await state.clear()
    await callback_query.answer()


@router.message(VocabularyStates.waiting_for_word)
async def process_word(
    message: types.Message,
    state: FSMContext,
    card_service: CardService,
    relation_service: RelationService,
    anki_client: AnkiClient
) -> None:
    """處理使用者輸入的單字並呼叫生成邏輯。

    Handle the user-entered word and invoke the generation logic.

    Args:
        message: 包含單字文字的訊息。The message containing the word text.
        state: aiogram FSM 上下文。The aiogram FSM context.
        card_service: 卡片服務。The CardService instance.
        relation_service: 關聯服務。The RelationService instance.
        anki_client: Anki 客戶端。The AnkiClient instance.

    Returns:
        None
    """
    if not message.text:
        return

    word = message.text.strip()
    
    # 回覆處理中訊息，提升使用者體驗
    status_msg = await message.reply("⏳ 正在為您查字典並生成卡片，請稍候...")

    try:
        handler = handler_registry.get_handler("vocabulary_mining")

        # 使用 Handler 的第一個 supported_model 作為預設模型 (通常為 TOEIC_Coach_Dark)
        default_model = handler.supported_models[0]
        default_deck = "Default"

        # 取前同步 (有防抖)
        await anki_client.sync(raise_errors=False)

        note_id = await handler.execute_create(
            card_service,
            relation_service,
            deck_name=default_deck,
            model_name=default_model,
            parameters={"word": word},
        )

        # 建立完成後觸發同步
        from app.bot.handlers.callbacks import _sync_with_warning
        sync_warning = await _sync_with_warning(anki_client)

        # 生成成功，修改回覆訊息
        await status_msg.edit_text(
            f"✅ <b>卡片生成完成！</b>\n\n"
            f"🎯 學習字詞：<b>{word}</b>\n"
            f"📦 目標牌組：<code>{default_deck}</code>\n"
            f"🔖 使用模型：<code>{default_model}</code>\n"
            f"🆔 Note ID：<code>{note_id}</code>\n\n"
            f"<i>可以到 Anki 中查看結果，已寫入本地！🎉</i>{sync_warning}",
            parse_mode="HTML"
        )
    except FluencyTidesError as e:
        logger.warning("Telegram Bot 卡片生成失敗 (業務異常): %s", e.message)
        error_icon = "⚠️"
        if e.error_code == "DUPLICATE_CARD":
            error_icon = "🔁"
        elif e.error_code == "FIELD_MISMATCH":
            error_icon = "📋"

        await status_msg.edit_text(
            f"{error_icon} <b>生成失敗</b>\n\n"
            f"錯誤碼：<code>{e.error_code}</code>\n"
            f"原因：{e.message}",
            parse_mode="HTML"
        )
    except AnkiConnectError as e:
        logger.exception("Telegram Bot 卡片生成發生未預期錯誤: %s", e)
        await status_msg.edit_text(
            f"❌ <b>系統發生異常</b>\n\n"
            f"無法完成生成卡片，請檢查後端日誌或確認 AnkiConnect 是否正常運作。\n"
            f"詳細: {str(e)[:200]}",
            parse_mode="HTML"
        )
    finally:
        await state.clear()
