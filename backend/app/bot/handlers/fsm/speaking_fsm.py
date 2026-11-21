"""Speaking-card creation Telegram Bot FSM handler.

對話卡生成 Telegram Bot FSM Handler。

This module implements the stateful (interactive) creation flow for Speaking_Coach_Dark. It collects, in order:
deck -> target language -> front (target sentence) -> back (context) -> reference answers. Once collected, an
AnkiNote is assembled directly and written to Anki. Every step after the deck has a "back" button, and every step
can be cancelled.

本模組實作 Speaking_Coach_Dark 的有狀態(互動式)新增流程。
依序收集：牌組(deck) -> 目標語言(language) -> 目標句(front) -> 情境(back) -> 參考回答(answers)。
收集完畢後，直接組裝為 AnkiNote 並寫入 Anki。牌組之後的每一步都有「返回上一步」，每一步都能取消。
"""

import json
import logging
import re
from datetime import datetime, timezone

from aiogram import Router, html, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from app.bot.utils.callback_reply import answer_callback
from app.bot.utils.formatting import escape_tg
from app.bot.utils.texts import FLOW_ENDED_ALERT, NEWCARD_RESTART, error_detail
from app.core.config import settings
from app.infrastructure.anki.client import AnkiClient, AnkiConnectError
from app.schemas.anki import AnkiNote, AnkiNoteOptions
from app.schemas.language import TargetLanguage, normalize_language_code
from app.schemas.speaking import ReferenceItem

logger = logging.getLogger(__name__)

router = Router(name="speaking_fsm")


_CANCEL_BUTTON = InlineKeyboardButton(text="❌ 取消", callback_data="speak_cancel")

_DECK_PROMPT = (
    "💬 <b>對話卡</b>\n\n"
    "輸入牌組名稱，如 <code>English::Speaking</code>，或點「跳過」用 Default："
)
_BACK_PROMPT = "輸入情境或背景，沒有就跳過："
_ANSWERS_PROMPT = "輸入參考回答，多則請換行分隔："


def _again(prompt: str, again: bool) -> str:
    """Return the prompt, prefixed with 「重新」 when the step is re-entered through "back".

    回傳提示；從「返回」回到這一步時，在提示前加「重新」。
    """
    return f"重新{prompt}" if again else prompt


def _deck_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭️ 跳過（Default）", callback_data="speak_skip_deck")],
        [_CANCEL_BUTTON],
    ])


def _step_keyboard(back_data: str, *first_rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    """Return a step's keyboard: the extra button rows, then a last row with "back" and "cancel".

    回傳一步的鍵盤：額外的按鈕列，最後一列是「返回上一步」與「取消」（每一步都能直接取消）。
    """
    back = InlineKeyboardButton(text="🔙 返回上一步", callback_data=back_data)
    return InlineKeyboardMarkup(inline_keyboard=[*first_rows, [back, _CANCEL_BUTTON]])


def _language_keyboard() -> InlineKeyboardMarkup:
    return _step_keyboard(
        "speak_back_deck",
        [InlineKeyboardButton(text="🇺🇸 英文 (en-US)", callback_data="speak_lang_en-US")],
        [InlineKeyboardButton(text="🇯🇵 日文 (ja-JP)", callback_data="speak_lang_ja-JP")],
        [InlineKeyboardButton(text="🇹🇼 繁體中文 (zh-TW)", callback_data="speak_lang_zh-TW")],
    )


def _back_keyboard() -> InlineKeyboardMarkup:
    return _step_keyboard(
        "speak_back_front",
        [InlineKeyboardButton(text="⏭️ 跳過", callback_data="speak_skip_back")],
    )


def _language_prompt(deck: str, *, again: bool = False) -> str:
    """Return the prompt of the language step; the deck name is user input, so it is escaped.

    回傳選語言這一步的提示（牌組名是使用者輸入，要轉義）。
    """
    verb = "重新選擇" if again else "選擇"
    return f"✅ 牌組：<code>{escape_tg(deck, 100)}</code>\n\n{verb}目標語言，或輸入代碼（如 <code>ko-KR</code>）："


def _front_prompt(lang_code: str, *, again: bool = False) -> str:
    """Return the prompt of the front-text step.

    回傳輸入提示句這一步的提示。
    """
    verb = "重新輸入" if again else "輸入"
    return f"✅ 語言：<code>{escape_tg(lang_code, 20)}</code>\n\n{verb}提示句（如對方說的話，或要翻譯的句子）："


async def _in_flow(state: FSMContext) -> bool:
    """Return whether the speaking flow is currently active.

    回傳目前是否在對話卡流程中。

    Buttons on old messages stay clickable after the flow ends, so handlers check this first.

    舊訊息上的按鈕在流程結束後仍按得到，所以 handler 先檢查這一點。
    """
    current = await state.get_state()
    return current is not None and current.startswith(f"{SpeakingStates.__name__}:")


class SpeakingStates(StatesGroup):
    """FSM state definitions for the speaking-card creation flow.

    對話卡生成流程的 FSM 狀態定義。

    Defines each step of the user-bot interaction.

    定義了使用者與機器人互動的每一步驟。
    """
    waiting_for_deck = State()
    waiting_for_language = State()
    waiting_for_front = State()
    waiting_for_back = State()
    waiting_for_answers = State()


async def start_speaking_fsm_flow(message: types.Message, state: FSMContext) -> None:
    """Start the speaking-card creation FSM flow; the public entry point.

    啟動對話卡新增 FSM 流程的公開入口。

    Triggered by the inline keyboard callback in newcard_menu.py; clears any previous FSM state, sets the new one,
    and prompts the user for the target deck.

    由 newcard_menu.py 的 Inline Keyboard 回調觸發，
    清除先前的 FSM 狀態、設定新狀態並提示使用者輸入目標牌組。

    Args:
        message: the Telegram message (from ``callback.message``)
            Telegram 訊息物件（來自 ``callback.message``）
        state: the aiogram FSM context
            aiogram FSM 上下文
    """
    await state.clear()
    await state.set_state(SpeakingStates.waiting_for_deck)
    await message.answer(_DECK_PROMPT, reply_markup=_deck_keyboard(), parse_mode="HTML")


@router.callback_query(lambda c: c.data == "speak_cancel")
async def process_speak_cancel(callback_query: types.CallbackQuery, state: FSMContext) -> None:
    """Handle the cancel action.

    處理取消操作。

    Only acts while the speaking flow is active: once the flow has ended this is a button on an old message, and it
    must not clear another flow the user is in now.

    只在對話卡流程中有效：流程已結束時這是舊訊息上的按鈕，不能清掉使用者現在進行中的其他流程。

    Args:
        callback_query: the Telegram callback query
            Telegram 回調查詢
        state: the aiogram FSM context
            aiogram FSM 上下文
    """
    if not await _in_flow(state):
        await answer_callback(callback_query, FLOW_ENDED_ALERT, show_alert=True)
        return
    await state.clear()
    if callback_query.message:
        await callback_query.message.edit_reply_markup(reply_markup=None)
        await callback_query.message.answer("🗑️ 已取消。")
    await answer_callback(callback_query)


@router.callback_query(lambda c: c.data == "speak_skip_deck", SpeakingStates.waiting_for_deck)
async def process_skip_deck(callback_query: types.CallbackQuery, state: FSMContext) -> None:
    """Handle skipping the deck setting, which uses the Default deck.

    處理跳過牌組設定（使用 Default 牌組）。

    Args:
        callback_query: the Telegram callback query
            Telegram 回調查詢
        state: the aiogram FSM context
            aiogram FSM 上下文
    """
    await state.update_data(deck="Default")
    if callback_query.message:
        await callback_query.message.edit_reply_markup(reply_markup=None)

    await state.set_state(SpeakingStates.waiting_for_language)
    await callback_query.message.answer(
        _language_prompt("Default"), reply_markup=_language_keyboard(), parse_mode="HTML"
    )
    await answer_callback(callback_query)


@router.message(SpeakingStates.waiting_for_deck)
async def process_deck(message: types.Message, state: FSMContext) -> None:
    """Handle the deck name input; ``/skip`` uses the Default deck.

    處理牌組輸入；``/skip`` 使用 Default 牌組。

    Args:
        message: the message containing the deck name
            包含牌組名稱的訊息
        state: the aiogram FSM context
            aiogram FSM 上下文
    """
    if not message.text:
        return

    deck = "Default" if message.text.strip() == "/skip" else message.text.strip()
    await state.update_data(deck=deck)

    await state.set_state(SpeakingStates.waiting_for_language)
    await message.answer(_language_prompt(deck), reply_markup=_language_keyboard(), parse_mode="HTML")

@router.callback_query(lambda c: c.data and c.data.startswith("speak_lang_"), SpeakingStates.waiting_for_language)
async def process_lang_callback(callback_query: types.CallbackQuery, state: FSMContext) -> None:
    """Handle the language code chosen via an inline button.

    處理按鈕選擇的語言代碼。

    Args:
        callback_query: the Telegram callback query
            Telegram 回調查詢
        state: the aiogram FSM context
            aiogram FSM 上下文
    """
    lang_code = callback_query.data.replace("speak_lang_", "")
    await _handle_language_selection(lang_code, callback_query.message, state)
    await answer_callback(callback_query)

@router.message(SpeakingStates.waiting_for_language)
async def process_lang_message(message: types.Message, state: FSMContext) -> None:
    """Handle a manually typed language code, with format validation (H1 fix).

    處理手動輸入的語言代碼（含格式驗證，H1 修復）。

    Invalid input (arbitrary text, accidental long sentences) is no longer written into the card or the LLM prompt;
    instead the user is re-prompted while staying in this state.

    不合法的輸入（任意文字、誤觸長句）不再直接寫進卡片與 LLM 提示詞，
    而是提示使用者重新輸入並停留在本狀態。

    Args:
        message: the message containing the language code
            包含語言代碼的訊息
        state: the aiogram FSM context
            aiogram FSM 上下文
    """
    if not message.text:
        return
    normalized = normalize_language_code(message.text)
    if normalized is None:
        await message.answer(
            "⚠️ 無法辨識：<code>{}</code>\n"
            "請輸入如 <code>en-US</code>、<code>ko-KR</code> 的代碼，或點上方按鈕。".format(
                html.quote(message.text.strip()[:50])
            ),
            parse_mode="HTML",
        )
        return
    await _handle_language_selection(normalized, message, state)

async def _handle_language_selection(lang_code: str, message: types.Message | None, state: FSMContext) -> None:
    """Store the language code and advance to the front-input state (shared logic).

    共用邏輯：記錄語言代碼並推進到目標句輸入狀態。

    Args:
        lang_code: the normalized language code
            正規化後的語言代碼
        message: the Telegram message to reply to; when ``None`` the state still advances but no prompt is sent
            要回覆的 Telegram 訊息；為 ``None`` 時仍推進狀態，但不送出提示
        state: the aiogram FSM context
            aiogram FSM 上下文
    """
    await state.update_data(target_language=lang_code)
    await state.set_state(SpeakingStates.waiting_for_front)

    if message:
        try:
            await message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await message.answer(
            _front_prompt(lang_code), reply_markup=_step_keyboard("speak_back_lang"), parse_mode="HTML"
        )


@router.message(SpeakingStates.waiting_for_front)
async def process_front(message: types.Message, state: FSMContext) -> None:
    """Handle the front (target sentence) input.

    處理目標句輸入。

    Args:
        message: the message containing the target sentence
            包含目標句的訊息
        state: the aiogram FSM context
            aiogram FSM 上下文
    """
    if not message.text:
        return

    front = message.text.strip()
    await state.update_data(front=front)

    await state.set_state(SpeakingStates.waiting_for_back)
    await message.answer(_BACK_PROMPT, reply_markup=_back_keyboard(), parse_mode="HTML")


@router.callback_query(lambda c: c.data == "speak_skip_back", SpeakingStates.waiting_for_back)
async def process_skip_back(callback_query: types.CallbackQuery, state: FSMContext) -> None:
    """Handle skipping the context setting.

    處理跳過情境設定。

    Args:
        callback_query: the Telegram callback query
            Telegram 回調查詢
        state: the aiogram FSM context
            aiogram FSM 上下文
    """
    await state.update_data(back="")
    if callback_query.message:
        await callback_query.message.edit_reply_markup(reply_markup=None)

    await state.set_state(SpeakingStates.waiting_for_answers)
    await callback_query.message.answer(
        _ANSWERS_PROMPT, reply_markup=_step_keyboard("speak_back_back"), parse_mode="HTML"
    )
    await answer_callback(callback_query)


@router.message(SpeakingStates.waiting_for_back)
async def process_back(message: types.Message, state: FSMContext) -> None:
    """Handle the context input; ``/skip`` leaves the context empty.

    處理情境輸入；``/skip`` 時情境留空。

    Args:
        message: the message containing the context
            包含情境的訊息
        state: the aiogram FSM context
            aiogram FSM 上下文
    """
    if not message.text:
        return

    back = "" if message.text.strip() == "/skip" else message.text.strip()
    await state.update_data(back=back)

    await state.set_state(SpeakingStates.waiting_for_answers)
    await message.answer(_ANSWERS_PROMPT, reply_markup=_step_keyboard("speak_back_back"), parse_mode="HTML")


@router.message(SpeakingStates.waiting_for_answers)
async def process_answers(
    message: types.Message,
    state: FSMContext,
    anki_client: AnkiClient
) -> None:
    """Handle the reference answers input and write the card to Anki.

    處理參考回答輸入，並寫入 Anki。

    Each non-blank line becomes one reference answer; if there is none, the user is asked again and the flow stays on
    this step. Once the write is attempted, the FSM state is cleared whether it succeeds or fails.

    每一個非空白行是一則參考回答；一則都沒有時請使用者重新輸入，停留在這一步。一旦嘗試寫入，不論成敗都會清除 FSM 狀態。

    Args:
        message: the message containing the reference answers
            包含參考回答的訊息
        state: the aiogram FSM context
            aiogram FSM 上下文
        anki_client: the AnkiConnect client
            Anki 客戶端
    """
    if not message.text:
        return

    raw_text = message.text.strip()

    # 支援多系統不同的換行符號 (Windows \r\n, Linux/Mac \n, 舊版 Mac \r)
    raw_answers = re.split(r'\r\n|\n|\r', raw_text)
    answers = [ans.strip() for ans in raw_answers if ans.strip()]

    if not answers:
        await message.answer("⚠️ 參考回答不能為空，請重新輸入：")
        return

    data = await state.get_data()
    deck = data.get("deck", "Default")
    target_language = data.get("target_language", "en-US")
    front = data.get("front", "")
    back = data.get("back", "")

    status_msg = await message.answer("⏳ 建立中…")

    # 產生唯一的 Card_ID（使用時間戳）
    card_id = datetime.now(tz=timezone.utc).strftime("SC_%Y%m%d_%H%M%S")

    # 將 answers 轉為 References JSON 格式
    references: list[dict[str, object]] = []
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    for ans in answers:
        ref = ReferenceItem(
            date=today,
            content=ans,
            status=1,
            audios=[],
        )
        references.append(ref.model_dump())

    # 組裝 AnkiConnect addNote payload
    note = AnkiNote(
        deckName=deck,
        modelName="Speaking_Coach_Dark",
        fields={
            "Card_ID": card_id,
            "Prompt": front,
            "Prompt_Audios": "[]",
            "Context": back,
            "Recordings": "[]",
            "References": json.dumps(references, ensure_ascii=False),
            "Target_Language": target_language,
            "TG_Bot": settings.TG_BOT_USERNAME,
        },
        tags=["TelegramBot", "Speaking_Coach"],
        options=AnkiNoteOptions(
            allowDuplicate=False,
            duplicateScope="deck",
        ),
    )

    try:
        note_id = await anki_client.add_note(note)

        if note_id:
            # 建立完成後觸發同步
            from app.bot.handlers.callbacks import _sync_with_warning
            sync_warning = await _sync_with_warning(anki_client)

            # 牌組與提示句是使用者輸入：不轉義的話含 < 或 & 時這則送不出去，卡片已建、畫面卻停在「建立中」
            await status_msg.edit_text(
                f"✅ <b>對話卡已建立</b>\n"
                f"牌組：<code>{escape_tg(deck, 100)}</code>\n"
                f"提示句：{escape_tg(front, 80)}\n"
                f"參考回答：{len(answers)} 則{sync_warning}",
                parse_mode="HTML"
            )
        else:
            # 狀態會在 finally 清掉：不能只說「重試」，使用者再輸入只會落到兜底
            await status_msg.edit_text(f"❌ 建立失敗：可能是重複卡片。\n{NEWCARD_RESTART}")
    except AnkiConnectError as e:
        logger.exception("Workflow A 卡片建立失敗: %s", e)
        await status_msg.edit_text(f"❌ 建立失敗：{error_detail(e)}\n{NEWCARD_RESTART}")
    finally:
        await state.clear()


# ============================================================================
# 返回上一步邏輯
# ============================================================================

@router.callback_query(lambda c: c.data and c.data.startswith("speak_back_"))
async def process_go_back(callback_query: types.CallbackQuery, state: FSMContext) -> None:
    """Handle the go-back-one-step action.

    處理返回上一步的操作。

    Only acts while the speaking flow is active; the message with the old step is deleted and the earlier step's
    prompt is sent again.

    只在對話卡流程中有效；刪除舊步驟的訊息，重新送出前一步的提示。

    Args:
        callback_query: the Telegram callback query
            Telegram 回調查詢
        state: the aiogram FSM context
            aiogram FSM 上下文
    """
    # 舊訊息上的返回鈕在流程結束後仍按得到：那時 data 已清空，不能把 FSM 拉回中間某步
    if not await _in_flow(state):
        await answer_callback(callback_query, FLOW_ENDED_ALERT, show_alert=True)
        return

    action = callback_query.data.replace("speak_back_", "")

    if callback_query.message:
        try:
            await callback_query.message.delete()
        except Exception:
            pass

    data = await state.get_data()
    if action == "deck":
        await state.set_state(SpeakingStates.waiting_for_deck)
        await callback_query.message.answer(_DECK_PROMPT, reply_markup=_deck_keyboard(), parse_mode="HTML")
    elif action == "lang":
        await state.set_state(SpeakingStates.waiting_for_language)
        await callback_query.message.answer(
            _language_prompt(data.get("deck", "Default"), again=True),
            reply_markup=_language_keyboard(),
            parse_mode="HTML",
        )
    elif action == "front":
        # 回到提示句這一步：顯示的是上一步選的語言（與正向進入時一致）
        await state.set_state(SpeakingStates.waiting_for_front)
        await callback_query.message.answer(
            _front_prompt(data.get("target_language", ""), again=True),
            reply_markup=_step_keyboard("speak_back_lang"),
            parse_mode="HTML",
        )
    elif action == "back":
        await state.set_state(SpeakingStates.waiting_for_back)
        await callback_query.message.answer(_again(_BACK_PROMPT, True), reply_markup=_back_keyboard(), parse_mode="HTML")

    await answer_callback(callback_query)
