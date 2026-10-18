"""對話卡生成 Telegram Bot FSM Handler。

本模組實作 Speaking_Coach_Dark 的有狀態(互動式)新增流程。
依序收集：牌組(deck) -> 目標句(front) -> 情境(back) -> 參考回答(answers)。
收集完畢後，直接組裝為 AnkiNote 並寫入 Anki。

Speaking-card creation Telegram Bot FSM handler.

This module implements the stateful (interactive) creation flow for
Speaking_Coach_Dark. It collects, in order: deck -> front (target
sentence) -> back (context) -> reference answers. Once collected, an
AnkiNote is assembled directly and written to Anki.
"""

import json
import logging
import re
from datetime import datetime, timezone

from aiogram import Router, html, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from app.bot.utils.formatting import escape_tg
from app.bot.utils.texts import FLOW_ENDED_ALERT, NEWCARD_RESTART, error_detail
from app.core.config import settings
from app.infrastructure.anki.client import AnkiClient, AnkiConnectError
from app.schemas.anki import AnkiNote, AnkiNoteOptions
from app.schemas.language import TargetLanguage
from app.schemas.speaking import ReferenceItem

logger = logging.getLogger(__name__)

#: BCP-47 基本形式：2-3 字母語言碼，可帶 2 字母地區碼（如 en、ja-JP、zh-TW）。
_LANG_CODE_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z]{2})?$")


def _normalize_language_code(raw: str) -> str | None:
    """驗證並正規化使用者輸入的語言代碼。

    Validate and normalize a user-provided language code.

    優先比對 :class:`TargetLanguage` 列舉的已知值（不分大小寫）；
    其餘輸入以 BCP-47 基本形式驗證後正規化大小寫
    （語言碼小寫、地區碼大寫，如 ``ja-jp`` → ``ja-JP``）。

    Known :class:`TargetLanguage` enum values are matched first
    (case-insensitively); other inputs are validated against the basic
    BCP-47 form and case-normalized (lowercase language, uppercase region,
    e.g. ``ja-jp`` → ``ja-JP``).

    Args:
        raw: 使用者輸入的原始字串。The raw user input string.

    Returns:
        正規化後的語言代碼；輸入不合法時回傳 ``None``
        （呼叫端應提示重新輸入，而非把任意文字寫進卡片）。
        The normalized language code, or ``None`` for invalid input
        (callers should re-prompt instead of writing arbitrary text
        into the card).
    """
    candidate = raw.strip()
    if not candidate:
        return None
    # 已知列舉值：直接採用其標準寫法（'other' 除外——它是內部
    # 佔位語意，寫進卡片對 LLM 提示無意義，不接受手動輸入）
    for lang in TargetLanguage:
        if lang is TargetLanguage.OTHER:
            continue
        if candidate.lower() == lang.value.lower():
            return lang.value
    if not _LANG_CODE_RE.match(candidate):
        return None
    if "-" in candidate:
        lang_part, region_part = candidate.split("-", 1)
        return f"{lang_part.lower()}-{region_part.upper()}"
    return candidate.lower()

router = Router(name="speaking_fsm")


_CANCEL_BUTTON = InlineKeyboardButton(text="❌ 取消", callback_data="speak_cancel")

_DECK_PROMPT = (
    "💬 <b>對話卡</b>\n\n"
    "輸入牌組名稱，如 <code>English::Speaking</code>，或點「跳過」用 Default："
)
_BACK_PROMPT = "輸入情境或背景，沒有就跳過："
_ANSWERS_PROMPT = "輸入參考回答，多則請換行分隔："


def _again(prompt: str, again: bool) -> str:
    """從「返回」回到這一步時，在提示前加「重新」。Prefix the prompt when re-entering a step."""
    return f"重新{prompt}" if again else prompt


def _deck_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭️ 跳過（Default）", callback_data="speak_skip_deck")],
        [_CANCEL_BUTTON],
    ])


def _step_keyboard(back_data: str, *first_rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    """一步的鍵盤：額外的按鈕列，最後一列是「返回上一步」與「取消」（每一步都能直接取消）。

    A step's keyboard: extra rows, then back and cancel on every step.
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
    """選語言這一步的提示（牌組名是使用者輸入，要轉義）。The language step; the deck is user input."""
    verb = "重新選擇" if again else "選擇"
    return f"✅ 牌組：<code>{escape_tg(deck, 100)}</code>\n\n{verb}目標語言，或輸入代碼（如 <code>ko-KR</code>）："


def _front_prompt(lang_code: str, *, again: bool = False) -> str:
    """輸入提示句這一步的提示。The front-text step."""
    verb = "重新輸入" if again else "輸入"
    return f"✅ 語言：<code>{escape_tg(lang_code, 20)}</code>\n\n{verb}提示句（如對方說的話，或要翻譯的句子）："


async def _in_flow(state: FSMContext) -> bool:
    """目前是否在對話卡流程中（舊訊息上的按鈕在流程結束後仍按得到）。

    Whether the speaking flow is active; old buttons stay clickable after it ends.
    """
    current = await state.get_state()
    return current is not None and current.startswith(f"{SpeakingStates.__name__}:")


class SpeakingStates(StatesGroup):
    """對話卡生成流程的 FSM 狀態定義。

    FSM state definitions for the speaking-card creation flow.

    定義了使用者與機器人互動的每一步驟。

    Defines each step of the user-bot interaction.
    """
    waiting_for_deck = State()
    waiting_for_language = State()
    waiting_for_front = State()
    waiting_for_back = State()
    waiting_for_answers = State()


async def start_speaking_fsm_flow(message: types.Message, state: FSMContext) -> None:
    """啟動對話卡新增 FSM 流程的公開入口。

    Public entry point that starts the speaking-card creation FSM flow.

    由 newcard_menu.py 的 Inline Keyboard 回呼觸發，
    設定 FSM 狀態並提示使用者輸入目標牌組。

    Triggered by the inline keyboard callback in newcard_menu.py; sets the
    FSM state and prompts the user for the target deck.

    Args:
        message: Telegram 訊息物件（來自 callback.message）。
            The Telegram message object (from callback.message).
        state: aiogram FSM 上下文。The aiogram FSM context.

    Returns:
        None
    """
    await state.clear()
    await state.set_state(SpeakingStates.waiting_for_deck)
    await message.answer(_DECK_PROMPT, reply_markup=_deck_keyboard(), parse_mode="HTML")


@router.callback_query(lambda c: c.data == "speak_cancel")
async def process_speak_cancel(callback_query: types.CallbackQuery, state: FSMContext) -> None:
    """處理取消操作。

    Handle the cancel action.

    只在對話卡流程中有效：流程已結束時這是舊訊息上的按鈕，不能清掉使用者現在進行中的其他流程。

    Only acts while the speaking flow is active, so an old button never
    clears another flow the user is in now.

    Args:
        callback_query: Telegram 回呼查詢。The Telegram callback query.
        state: aiogram FSM 上下文。The aiogram FSM context.

    Returns:
        None
    """
    if not await _in_flow(state):
        await callback_query.answer(FLOW_ENDED_ALERT, show_alert=True)
        return
    await state.clear()
    if callback_query.message:
        await callback_query.message.edit_reply_markup(reply_markup=None)
        await callback_query.message.answer("🗑️ 已取消。")
    await callback_query.answer()


@router.callback_query(lambda c: c.data == "speak_skip_deck", SpeakingStates.waiting_for_deck)
async def process_skip_deck(callback_query: types.CallbackQuery, state: FSMContext) -> None:
    """處理跳過牌組設定。

    Handle skipping the deck setting.

    Args:
        callback_query: Telegram 回呼查詢。The Telegram callback query.
        state: aiogram FSM 上下文。The aiogram FSM context.

    Returns:
        None
    """
    await state.update_data(deck="Default")
    if callback_query.message:
        await callback_query.message.edit_reply_markup(reply_markup=None)

    await state.set_state(SpeakingStates.waiting_for_language)
    await callback_query.message.answer(
        _language_prompt("Default"), reply_markup=_language_keyboard(), parse_mode="HTML"
    )
    await callback_query.answer()


@router.message(SpeakingStates.waiting_for_deck)
async def process_deck(message: types.Message, state: FSMContext) -> None:
    """處理牌組輸入。

    Handle the deck name input.

    Args:
        message: 包含牌組名稱的訊息。The message containing the deck name.
        state: aiogram FSM 上下文。The aiogram FSM context.

    Returns:
        None
    """
    if not message.text:
        return

    deck = "Default" if message.text.strip() == "/skip" else message.text.strip()
    await state.update_data(deck=deck)

    await state.set_state(SpeakingStates.waiting_for_language)
    await message.answer(_language_prompt(deck), reply_markup=_language_keyboard(), parse_mode="HTML")

@router.callback_query(lambda c: c.data and c.data.startswith("speak_lang_"), SpeakingStates.waiting_for_language)
async def process_lang_callback(callback_query: types.CallbackQuery, state: FSMContext) -> None:
    """處理按鈕選擇的語言代碼。

    Handle the language code chosen via an inline button.

    Args:
        callback_query: Telegram 回呼查詢。The Telegram callback query.
        state: aiogram FSM 上下文。The aiogram FSM context.
    """
    lang_code = callback_query.data.replace("speak_lang_", "")
    await _handle_language_selection(lang_code, callback_query.message, state)
    await callback_query.answer()

@router.message(SpeakingStates.waiting_for_language)
async def process_lang_message(message: types.Message, state: FSMContext) -> None:
    """處理手動輸入的語言代碼（含格式驗證，H1 修復）。

    Handle a manually typed language code (with format validation, H1 fix).

    不合法的輸入（任意文字、誤觸長句）不再直接寫進卡片與 LLM 提示詞，
    而是提示使用者重新輸入並停留在本狀態。

    Invalid input (arbitrary text, accidental long sentences) is no longer
    written into the card or LLM prompt; instead the user is re-prompted
    while staying in this state.

    Args:
        message: 包含語言代碼的訊息。The message containing the language code.
        state: aiogram FSM 上下文。The aiogram FSM context.
    """
    if not message.text:
        return
    normalized = _normalize_language_code(message.text)
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
    """共用邏輯：記錄語言代碼並推進到目標句輸入狀態。

    Shared logic: store the language code and advance to the front-input
    state.

    Args:
        lang_code: 正規化後的語言代碼。The normalized language code.
        message: Telegram 訊息物件（可為 None）。
            The Telegram message object (may be None).
        state: aiogram FSM 上下文。The aiogram FSM context.
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
    """處理目標句輸入。

    Handle the front (target sentence) input.

    Args:
        message: 包含目標句的訊息。The message containing the target sentence.
        state: aiogram FSM 上下文。The aiogram FSM context.

    Returns:
        None
    """
    if not message.text:
        return

    front = message.text.strip()
    await state.update_data(front=front)

    await state.set_state(SpeakingStates.waiting_for_back)
    await message.answer(_BACK_PROMPT, reply_markup=_back_keyboard(), parse_mode="HTML")


@router.callback_query(lambda c: c.data == "speak_skip_back", SpeakingStates.waiting_for_back)
async def process_skip_back(callback_query: types.CallbackQuery, state: FSMContext) -> None:
    """處理跳過情境設定。

    Handle skipping the context setting.

    Args:
        callback_query: Telegram 回呼查詢。The Telegram callback query.
        state: aiogram FSM 上下文。The aiogram FSM context.

    Returns:
        None
    """
    await state.update_data(back="")
    if callback_query.message:
        await callback_query.message.edit_reply_markup(reply_markup=None)

    await state.set_state(SpeakingStates.waiting_for_answers)
    await callback_query.message.answer(
        _ANSWERS_PROMPT, reply_markup=_step_keyboard("speak_back_back"), parse_mode="HTML"
    )
    await callback_query.answer()


@router.message(SpeakingStates.waiting_for_back)
async def process_back(message: types.Message, state: FSMContext) -> None:
    """處理情境輸入。

    Handle the context input.

    Args:
        message: 包含情境的訊息。The message containing the context.
        state: aiogram FSM 上下文。The aiogram FSM context.

    Returns:
        None
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
    """處理參考回答輸入，並寫入 Anki。

    Handle the reference answers input and write the card to Anki.

    Args:
        message: 包含參考回答的訊息。
            The message containing the reference answers.
        state: aiogram FSM 上下文。The aiogram FSM context.
        anki_client: Anki 客戶端。The AnkiClient instance.

    Returns:
        None
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
    """處理返回上一步的操作。

    Handle the go-back-one-step action.

    Args:
        callback_query: Telegram 回呼查詢。The Telegram callback query.
        state: aiogram FSM 上下文。The aiogram FSM context.

    Returns:
        None
    """
    # 舊訊息上的返回鈕在流程結束後仍按得到：那時 data 已清空，不能把 FSM 拉回中間某步
    if not await _in_flow(state):
        await callback_query.answer(FLOW_ENDED_ALERT, show_alert=True)
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

    await callback_query.answer()
