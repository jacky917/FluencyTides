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
    await state.set_state(SpeakingStates.waiting_for_deck)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭️ 跳過，使用預設牌組 (Default)", callback_data="speak_skip_deck")],
        [InlineKeyboardButton(text="❌ 取消", callback_data="speak_cancel")]
    ])
    
    await message.answer(
        "🎙️ <b>新增對話卡 (Speaking Coach)</b>\n\n"
        "請輸入要加入的「目標牌組名稱」（例如 `English::Speaking`）：\n\n"
        "(若無特定牌組，請點擊跳過)",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(lambda c: c.data == "speak_cancel")
async def process_speak_cancel(callback_query: types.CallbackQuery, state: FSMContext) -> None:
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
        await callback_query.message.answer("🗑️ 已取消對話卡新增。")
    await state.clear()
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
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇺🇸 英文 (en-US)", callback_data="speak_lang_en-US")],
        [InlineKeyboardButton(text="🇯🇵 日文 (ja-JP)", callback_data="speak_lang_ja-JP")],
        [InlineKeyboardButton(text="🇹🇼 繁體中文 (zh-TW)", callback_data="speak_lang_zh-TW")],
        [InlineKeyboardButton(text="🔙 返回上一步", callback_data="speak_back_deck")]
    ])
    await callback_query.message.answer(
        "✅ 牌組：<code>Default</code>\n\n"
        "請選擇「目標語言 (Target Language)」(或直接手打語言代碼)：",
        reply_markup=keyboard,
        parse_mode="HTML"
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
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇺🇸 英文 (en-US)", callback_data="speak_lang_en-US")],
        [InlineKeyboardButton(text="🇯🇵 日文 (ja-JP)", callback_data="speak_lang_ja-JP")],
        [InlineKeyboardButton(text="🇹🇼 繁體中文 (zh-TW)", callback_data="speak_lang_zh-TW")],
        [InlineKeyboardButton(text="🔙 返回上一步", callback_data="speak_back_deck")]
    ])
    await message.answer(
        f"✅ 牌組：<code>{deck}</code>\n\n"
        f"請選擇「目標語言 (Target Language)」(或直接手打語言代碼)：",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

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
            "⚠️ 語言代碼格式不正確：<code>{}</code>\n\n"
            "請輸入 BCP-47 形式的代碼（如 <code>en-US</code>、"
            "<code>ja-JP</code>、<code>zh-TW</code>、<code>ko-KR</code>），"
            "或點選上方按鈕。".format(html.quote(message.text.strip()[:50])),
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
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 返回上一步", callback_data="speak_back_lang")]
    ])
    
    data = await state.get_data()
    deck = data.get("deck", "Default")
    
    if message:
        try:
            await message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await message.answer(
            f"✅ 目標語言：<code>{lang_code}</code>\n\n"
            f"請輸入「提示/目標句 (Front/Prompt)」(例如對方的話或指定翻譯)：",
            reply_markup=keyboard,
            parse_mode="HTML"
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
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭️ 跳過，不加入情境", callback_data="speak_skip_back")],
        [InlineKeyboardButton(text="🔙 返回上一步", callback_data="speak_back_front")]
    ])
    await message.answer(
        "請輸入這句話的「發生情境或背景 (Back/Context)」：\n\n"
        "(若無請點擊跳過)",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


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
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 返回上一步", callback_data="speak_back_back")]
    ])
    await callback_query.message.answer(
        "請輸入「參考回答 (Answers)」：\n\n"
        "💡 <i>您可以輸入多個回答，請以「換行」分隔即可。</i>",
        reply_markup=keyboard,
        parse_mode="HTML"
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
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 返回上一步", callback_data="speak_back_back")]
    ])
    await message.answer(
        "請輸入「參考回答 (Answers)」：\n\n"
        "💡 <i>您可以輸入多個回答，請以「換行」分隔即可。</i>",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


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
    
    status_msg = await message.answer("⏳ 正在建立對話卡...")
    
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

            await status_msg.edit_text(
                f"✅ <b>卡片建立成功！</b>\n\n"
                f"Card ID：<code>{card_id}</code>\n"
                f"Note ID：<code>{note_id}</code>\n"
                f"牌組：<code>{deck}</code>\n"
                f"Prompt：{front[:80]}\n"
                f"範本數：{len(answers)} 筆\n\n"
                f"<i>已寫入本地 Anki！🎉</i>{sync_warning}",
                parse_mode="HTML"
            )
        else:
            await status_msg.edit_text(
                "⚠️ <b>卡片建立失敗</b>\n\n"
                "可能是重複的卡片，請確認後重試。"
            )
    except AnkiConnectError as e:
        logger.exception("Workflow A 卡片建立失敗: %s", e)
        await status_msg.edit_text(
            f"❌ <b>建立失敗</b>\n\n"
            f"錯誤: {str(e)[:200]}"
        )
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
    action = callback_query.data.replace("speak_back_", "")
    
    if callback_query.message:
        try:
            await callback_query.message.delete()
        except Exception:
            pass
            
    if action == "deck":
        await state.set_state(SpeakingStates.waiting_for_deck)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏭️ 跳過，使用預設牌組 (Default)", callback_data="speak_skip_deck")],
            [InlineKeyboardButton(text="❌ 取消", callback_data="speak_cancel")]
        ])
        await callback_query.message.answer(
            "🎙️ <b>新增對話卡 (Speaking Coach)</b>\n\n"
            "請重新輸入要加入的「目標牌組名稱」：\n\n"
            "(若無特定牌組，請點擊跳過)",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    elif action == "lang":
        await state.set_state(SpeakingStates.waiting_for_language)
        data = await state.get_data()
        deck = data.get("deck", "Default")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🇺🇸 英文 (en-US)", callback_data="speak_lang_en-US")],
            [InlineKeyboardButton(text="🇯🇵 日文 (ja-JP)", callback_data="speak_lang_ja-JP")],
            [InlineKeyboardButton(text="🇹🇼 繁體中文 (zh-TW)", callback_data="speak_lang_zh-TW")],
            [InlineKeyboardButton(text="🔙 返回上一步", callback_data="speak_back_deck")]
        ])
        await callback_query.message.answer(
            f"✅ 牌組：<code>{deck}</code>\n\n"
            f"請重新選擇「目標語言 (Target Language)」(或直接手打語言代碼)：",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    elif action == "front":
        await state.set_state(SpeakingStates.waiting_for_front)
        data = await state.get_data()
        deck = data.get("deck", "Default")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 返回上一步", callback_data="speak_back_lang")]
        ])
        await callback_query.message.answer(
            f"✅ 牌組：<code>{deck}</code>\n\n"
            f"請重新輸入「提示/目標句 (Front/Prompt)」(例如對方的話或指定翻譯)：",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    elif action == "back":
        await state.set_state(SpeakingStates.waiting_for_back)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏭️ 跳過，不加入情境", callback_data="speak_skip_back")],
            [InlineKeyboardButton(text="🔙 返回上一步", callback_data="speak_back_front")]
        ])
        await callback_query.message.answer(
            "請重新輸入這句話的「發生情境或背景 (Back/Context)」：\n\n"
            "(若無請點擊跳過)",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        
    await callback_query.answer()
