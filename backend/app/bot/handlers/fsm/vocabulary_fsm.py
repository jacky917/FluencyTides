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

from app.bot.utils.formatting import escape_tg
from app.bot.utils.texts import NEWCARD_RESTART, error_detail
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
    await state.clear()
    await state.set_state(VocabularyStates.waiting_for_word)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ 取消", callback_data="vocab_cancel")]
    ])

    await message.answer(
        "📚 <b>單字卡</b>\n\n"
        "輸入外語單字或片語：",
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
        await callback_query.message.answer("🗑️ 已取消。")
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

    # 目前的 handler 不呼叫 LLM（只建立 Meaning 為佔位文字的卡片，見 vocabulary_mining_handler），
    # 不要說成「查字典並生成」
    status_msg = await message.reply("⏳ 建立中…")

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

        # 單字是使用者輸入，要轉義（否則含 < 或 & 時卡片已建、畫面卻停在「建立中」）；
        # 「模型」指 Anki 筆記類型，容易與 /models 的 LLM 模型混淆，不顯示
        await status_msg.edit_text(
            f"✅ <b>單字卡已建立</b>（釋義尚未自動產生）\n"
            f"單字：<b>{escape_tg(word, 100)}</b>\n"
            f"牌組：<code>{default_deck}</code>{sync_warning}",
            parse_mode="HTML"
        )
    except FluencyTidesError as e:
        logger.warning("Telegram Bot 卡片生成失敗 (業務異常): %s", e.message)
        if e.error_code == "DUPLICATE_CARD":
            text = "🔁 已有相同單字的卡片，未建立。"
        else:
            text = f"❌ 建立失敗：{error_detail(e.message)}\n{NEWCARD_RESTART}"
        await status_msg.edit_text(text, parse_mode="HTML")
    except AnkiConnectError as e:
        logger.exception("Telegram Bot 卡片生成發生未預期錯誤: %s", e)
        await status_msg.edit_text(
            f"❌ 建立失敗：{error_detail(e)}\n請確認電腦版 Anki 已開啟。",
            parse_mode="HTML"
        )
    finally:
        await state.clear()
