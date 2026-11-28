"""Foreign-language correction Telegram Bot FSM handler.

外語糾錯 Telegram Bot FSM Handler。

This module implements the 6-step input flow plus a preview/confirm step. The full flow is:
Language -> Original Text -> Context -> Source Tag ->
Grammar Correction -> Reorganized Expression -> LLM preview -> confirm.

Design decisions:

- The LLM result is serialized into FSM state data at the preview step to avoid calling the LLM twice.
- Child cards use unified sequential numbering (grammar first, reorganized second) so the user can pick the cards
  to add with simple numbers.
- Invalid number input does not end the flow; the user is re-prompted until a valid value is entered or a button is
  pressed.

本模組實作 6 步輸入流程 + 預覽確認步驟，完整流程為：
Language → Original Text → Context → Source Tag →
Grammar Correction → Reorganized Expression → LLM 預覽 → 確認添加

設計決策：
- LLM 結果在預覽步驟序列化後存入 FSM state data，避免重複呼叫 LLM。
- 子卡片使用統一連續編號（grammar 在前、reorganized 在後），
  讓使用者可以用簡單的數字選擇想要添加的卡片。
- 非法編號輸入不會結束流程，而是持續要求重新輸入，
  直到輸入合法值或使用者點選按鈕。
"""

import html
import logging
import re
from aiogram import Router, types
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from app.infrastructure.anki.search import deck_term, field_term, note_type_term
from app.infrastructure.anki.client import AnkiConnectError
from app.bot.utils.callback_reply import answer_callback
from app.bot.utils.formatting import anki_field_to_tg_text, escape_tg
from app.bot.utils.texts import FLOW_ENDED_ALERT, NEWCARD_RESTART, error_detail, sync_suffix

from app.services.task_handlers.registry import handler_registry
from app.schemas.tg.expression import TGExpressionCorrectionRequest
from app.schemas.llm.expression import LLMExpressionCorrectionResult
from app.services.card_service import CardService
from app.services.note_service import NoteService

logger = logging.getLogger(__name__)

router = Router(name="expression")

_FLOW_EXPIRED = f"⌛ 流程已失效，{NEWCARD_RESTART}"


class ExpressionStates(StatesGroup):
    """FSM state definitions for the foreign-language correction flow.

    外語糾錯流程的 FSM 狀態定義。

    Defines the states of the full user-bot interaction: 6 data-collection steps, one duplicate-card decision step,
    and the final write confirmation.

    定義了使用者與機器人互動的完整流程狀態。
    包含 6 步資料收集、1 步重複卡片防呆決策，以及最後的寫入確認。
    """
    waiting_for_lang = State()                  # 等待輸入語言設定 (例如：中文 -> 日文)
    waiting_for_text = State()                  # 等待輸入原文 (想要糾錯的外語句子)
    waiting_for_context = State()               # 等待輸入發生情境或上下文
    waiting_for_source_tag = State()            # 等待選擇來源情境標籤
    waiting_for_grammar_correction = State()    # 等待輸入使用者自訂的文法修正版
    waiting_for_reorganization = State()        # 等待輸入使用者自訂的高階重組版
    waiting_for_duplicate_decision = State()    # 等待決策：發現重複卡片時，選擇覆蓋、允許重複或取消
    waiting_for_card_selection = State()        # 等待選擇要寫入 Anki 的子卡片編號


# Telegram 單則訊息上限 4096 字；預覽超過時分段送出（留一點餘裕）
_MESSAGE_LIMIT = 4000

# 每一步都附「取消」：這條流程的 FSM 不會過期，/start、/help 也不會清狀態，沒有出口的話使用者會卡住
_CANCEL_BUTTON = InlineKeyboardButton(text="❌ 取消", callback_data="expr_cancel")

_LANG_PROMPT = (
    "📝 <b>外語糾錯</b>（1/6）\n\n"
    "輸入「母語 目標語言」，如 <code>中文 日文</code>、<code>zh ja</code>\n"
    "/skip ＝ 中文 → 日文"
)
_CONTEXT_PROMPT = "（3/6）輸入情境或上下文，沒有就 /skip："
_SOURCE_TAG_PROMPT = "（4/6）選擇或輸入來源標籤："
_GRAMMAR_PROMPT = "（5/6）輸入您的文法修正版（保留原意與句型），不確定就跳過："
_REORG_PROMPT = "（6/6）輸入道地重組（母語人士的說法），不確定就跳過："


def _again(prompt: str, again: bool) -> str:
    """Return the prompt, marked with 「重新」 after its step number when re-entered through "back".

    回傳提示；從「返回」回到這一步時加「重新」，放在步驟編號之後（「（3/6）重新輸入…」）。
    """
    if not again:
        return prompt
    if prompt.startswith("（") and "）" in prompt:
        number, rest = prompt.split("）", 1)
        return f"{number}）重新{rest}"
    return f"重新{prompt}"


def _text_prompt(native_lang: str, target_lang: str, *, again: bool = False) -> str:
    """Return the step-2 prompt; the languages are user input, so they are escaped.

    回傳第 2 步的提示（語言是使用者輸入，要轉義）。
    """
    nl, tl = escape_tg(native_lang, 20), escape_tg(target_lang, 20)
    verb = "重新輸入" if again else "輸入"
    return f"✅ {nl} → {tl}\n\n（2/6）{verb}要糾錯的{tl}原文："


def _step_keyboard(back_data: str | None, *first_rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    """Return a step's keyboard: the extra button rows, then a last row with "back" (absent on step 1) and "cancel".

    回傳一步的鍵盤：額外的按鈕列，最後一列是「返回上一步」（第 1 步沒有）與「取消」。
    """
    last = [_CANCEL_BUTTON]
    if back_data is not None:
        last.insert(0, InlineKeyboardButton(text="🔙 返回上一步", callback_data=back_data))
    return InlineKeyboardMarkup(inline_keyboard=[*first_rows, last])


def _grammar_keyboard() -> InlineKeyboardMarkup:
    return _step_keyboard(
        "expr_back_source_tag",
        [InlineKeyboardButton(text="⏭️ 跳過（交給 AI）", callback_data="skip_grammar_correction")],
    )


def _reorg_keyboard() -> InlineKeyboardMarkup:
    return _step_keyboard(
        "expr_back_grammar",
        [InlineKeyboardButton(text="⏭️ 跳過（交給 AI）", callback_data="skip_reorganization")],
    )


async def _in_flow(state: FSMContext) -> bool:
    """Return whether the expression flow is currently active.

    回傳目前是否在外語糾錯流程中。

    Buttons on old messages stay clickable after the flow ends, so handlers check this first.

    舊訊息上的按鈕在流程結束後仍按得到，所以 handler 先檢查這一點。
    """
    current = await state.get_state()
    return current is not None and current.startswith(f"{ExpressionStates.__name__}:")


def _escape_keep_underline(text: str) -> str:
    """Escape LLM text, keeping only the ``<u>…</u>`` markers the error contrast uses on purpose (shown in bold).

    轉義 LLM 文字，只保留錯誤對比刻意使用的 ``<u>…</u>``（顯示時再加粗）。
    """
    escaped = html.escape(text)
    return escaped.replace("&lt;u&gt;", "<u><b>").replace("&lt;/u&gt;", "</b></u>")


def _split_message(text: str, limit: int = _MESSAGE_LIMIT) -> list[str]:
    """Split the text on line boundaries into chunks of at most ``limit`` characters.

    依行切成不超過 ``limit`` 字的幾段。

    Each line's tags are paired within the line, so they are never cut apart. A single line longer than ``limit``
    is cut hard.

    每一行的標籤在行內成對，不會被切斷。單一行就超過 ``limit`` 時硬切。
    """
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    # 單一行就超過上限時硬切（極少見；寧可截斷也不要整則送不出去）
    return [chunk[i:i + limit] for chunk in chunks for i in range(0, len(chunk), limit)]


def _get_source_tag_keyboard() -> InlineKeyboardMarkup:
    """Build the source-tag inline keyboard.

    產生來源情境標籤的 Inline Keyboard 鍵盤。

    Reads the tag list from settings and builds the button matrix dynamically, at most 2 buttons per row, followed
    by a "skip" row and a last row with "back" and "cancel".

    從設定檔中讀取標籤清單，並動態生成按鈕矩陣。
    每列最多 2 個按鈕，之後是「跳過」一列，最後一列是「返回」與「取消」。

    Returns:
        the keyboard containing the tag options
        包含標籤選項的鍵盤物件
    """
    from app.core.config import settings
    tags = settings.note_source_tags_list
    keyboard_buttons = []

    # 建立標籤按鈕
    row = []
    for tag in tags:
        row.append(InlineKeyboardButton(text=tag, callback_data=f"source_tag_{tag}"))
        if len(row) == 2:
            keyboard_buttons.append(row)
            row = []
    if row:
        keyboard_buttons.append(row)

    keyboard_buttons.append([InlineKeyboardButton(text="⏭️ 跳過", callback_data="source_tag_skip")])
    return _step_keyboard("expr_back_context", *keyboard_buttons)


async def start_expression_flow(message: types.Message, state: FSMContext) -> None:
    """Start the expression-correction FSM flow; the public entry point.

    啟動外語糾錯 FSM 流程的公開入口。

    Triggered by the inline keyboard callback in newcard_menu.py; clears the previous round's data, sets the FSM
    state, and prompts the user for the language pair.

    由 newcard_menu.py 的 Inline Keyboard 回調觸發，
    清掉上一輪的資料、設定 FSM 狀態並提示使用者輸入語言設定。

    Args:
        message: the Telegram message (from ``callback.message``)
            Telegram 訊息物件（來自 ``callback.message``）
        state: the aiogram FSM context
            aiogram FSM 上下文
    """
    # 清掉上一輪的資料：上一輪選了「覆蓋」但沒完成就重開時，殘留的 old_master_note_id 與
    # duplicate_decision 會讓這一輪寫入時刪掉上一輪那張舊卡（使用者完全看不到）
    await state.clear()
    await state.set_state(ExpressionStates.waiting_for_lang)
    await message.answer(_LANG_PROMPT, reply_markup=_step_keyboard(None))

@router.message(ExpressionStates.waiting_for_lang)
async def process_lang(message: types.Message, state: FSMContext) -> None:
    """Handle the language pair input (native -> target language).

    處理語言設定輸入（母語 -> 目標語言）。

    ``/skip`` means Chinese -> Japanese. A single word is taken as the target language, with Chinese as the native
    language.

    ``/skip`` 為中文 → 日文。只輸入一個詞時當作目標語言，母語為中文。

    Args:
        message: the Telegram message
            Telegram 訊息物件
        state: the aiogram FSM context
            aiogram FSM 上下文
    """
    if not message.text:
        return

    text = message.text.strip()
    if text == "/skip":
        native_lang = "中文"
        target_lang = "日文"
    else:
        parts = text.replace("->", " ").split()
        if len(parts) >= 2:
            native_lang = parts[0]
            target_lang = parts[1]
        else:
            native_lang = "中文"
            target_lang = text

    await state.update_data(native_language=native_lang, target_language=target_lang)
    await state.set_state(ExpressionStates.waiting_for_text)
    await message.answer(_text_prompt(native_lang, target_lang), reply_markup=_step_keyboard("expr_back_lang"))

@router.message(ExpressionStates.waiting_for_text)
async def process_text(message: types.Message, state: FSMContext) -> None:
    """Handle the original foreign-language text to be corrected.

    處理欲糾錯的外語原文輸入。

    Args:
        message: the Telegram message
            Telegram 訊息物件
        state: the aiogram FSM context
            aiogram FSM 上下文
    """
    if not message.text:
        return
    await state.update_data(original_text=message.text.strip())
    await state.set_state(ExpressionStates.waiting_for_context)
    await message.answer(_CONTEXT_PROMPT, reply_markup=_step_keyboard("expr_back_text"))

@router.message(ExpressionStates.waiting_for_context)
async def process_context(message: types.Message, state: FSMContext) -> None:
    """Handle the context input for the sentence; ``/skip`` leaves it empty.

    處理發生情境（上下文）輸入；``/skip`` 時留空。

    Args:
        message: the Telegram message
            Telegram 訊息物件
        state: the aiogram FSM context
            aiogram FSM 上下文
    """
    if not message.text:
        return

    context_text = "" if message.text.strip() == "/skip" else message.text.strip()
    await state.update_data(context=context_text)

    await state.set_state(ExpressionStates.waiting_for_source_tag)
    await message.answer(_SOURCE_TAG_PROMPT, reply_markup=_get_source_tag_keyboard())

@router.callback_query(lambda c: c.data and c.data.startswith("source_tag_"), ExpressionStates.waiting_for_source_tag)
async def process_source_tag(callback_query: types.CallbackQuery, state: FSMContext) -> None:
    """Handle the source tag chosen via an inline button.

    處理按鈕選擇的來源情境標籤。

    Args:
        callback_query: the Telegram callback query
            Telegram 回調查詢
        state: the aiogram FSM context
            aiogram FSM 上下文
    """
    tag = callback_query.data.replace("source_tag_", "")
    source_tag = "" if tag == "skip" else tag
    await state.update_data(source_tag=source_tag)

    if callback_query.message:
        await callback_query.message.edit_reply_markup(reply_markup=None)

    await state.set_state(ExpressionStates.waiting_for_grammar_correction)
    await callback_query.message.answer(_GRAMMAR_PROMPT, reply_markup=_grammar_keyboard())
    await answer_callback(callback_query)

@router.message(ExpressionStates.waiting_for_source_tag)
async def process_source_tag_text(message: types.Message, state: FSMContext) -> None:
    """Handle a manually typed source tag; ``/skip`` leaves it empty.

    處理手動輸入的來源情境標籤；``/skip`` 時留空。

    Args:
        message: the Telegram message
            Telegram 訊息物件
        state: the aiogram FSM context
            aiogram FSM 上下文
    """
    if not message.text:
        return

    source_tag = "" if message.text.strip() == "/skip" else message.text.strip()
    await state.update_data(source_tag=source_tag)

    await state.set_state(ExpressionStates.waiting_for_grammar_correction)
    await message.answer(_GRAMMAR_PROMPT, reply_markup=_grammar_keyboard())

@router.callback_query(lambda c: c.data == "skip_grammar_correction", ExpressionStates.waiting_for_grammar_correction)
async def process_skip_grammar_correction(callback_query: types.CallbackQuery, state: FSMContext) -> None:
    """Handle skipping the user-provided grammar correction.

    處理跳過使用者自訂文法修正。

    Args:
        callback_query: the Telegram callback query
            Telegram 回調查詢
        state: the aiogram FSM context
            aiogram FSM 上下文
    """
    await state.update_data(user_grammar_correction="")
    if callback_query.message:
        await callback_query.message.edit_reply_markup(reply_markup=None)

    await _prompt_reorganization(callback_query.message, state)
    await answer_callback(callback_query)

@router.message(ExpressionStates.waiting_for_grammar_correction)
async def process_grammar_correction(message: types.Message, state: FSMContext) -> None:
    """Handle the user-provided grammar-correction input; ``/skip`` leaves it to the AI.

    處理使用者自訂的文法修正版輸入；``/skip`` 時交給 AI。

    Args:
        message: the Telegram message
            Telegram 訊息物件
        state: the aiogram FSM context
            aiogram FSM 上下文
    """
    if not message.text:
        return

    correct_text = "" if message.text.strip() == "/skip" else message.text.strip()
    await state.update_data(user_grammar_correction=correct_text)

    await _prompt_reorganization(message, state)

async def _prompt_reorganization(message_or_query_msg: types.Message, state: FSMContext) -> None:
    """Switch to the reorganization-input state and send its prompt (shared logic).

    共用邏輯：切換到高階重組輸入狀態並發送提示。

    Args:
        message_or_query_msg: the Telegram message to reply to
            要回覆的 Telegram 訊息物件
        state: the aiogram FSM context
            aiogram FSM 上下文
    """
    await state.set_state(ExpressionStates.waiting_for_reorganization)
    await message_or_query_msg.answer(_REORG_PROMPT, reply_markup=_reorg_keyboard())

@router.callback_query(lambda c: c.data == "skip_reorganization", ExpressionStates.waiting_for_reorganization)
async def process_skip_reorganization(callback_query: types.CallbackQuery, state: FSMContext, card_service: CardService) -> None:
    """Handle skipping the user-provided reorganized expression, then check for duplicates.

    處理跳過使用者自訂高階重組，接著檢查重複卡片。

    Args:
        callback_query: the Telegram callback query
            Telegram 回調查詢
        state: the aiogram FSM context
            aiogram FSM 上下文
        card_service: the card service
            卡片服務
    """
    await state.update_data(user_reorganization="")
    if callback_query.message:
        await callback_query.message.edit_reply_markup(reply_markup=None)
    await _check_duplicate_before_generate(callback_query.message, state, card_service)
    await answer_callback(callback_query)

@router.message(ExpressionStates.waiting_for_reorganization)
async def process_reorganization(message: types.Message, state: FSMContext, card_service: CardService) -> None:
    """Handle the user-provided reorganized-expression input, then check for duplicates.

    處理使用者自訂的高階重組版輸入，接著檢查重複卡片。

    ``/skip`` leaves the reorganization to the AI.

    ``/skip`` 時交給 AI。

    Args:
        message: the Telegram message
            Telegram 訊息物件
        state: the aiogram FSM context
            aiogram FSM 上下文
        card_service: the card service
            卡片服務
    """
    if not message.text:
        return

    reorg_text = "" if message.text.strip() == "/skip" else message.text.strip()
    await state.update_data(user_reorganization=reorg_text)
    await _check_duplicate_before_generate(message, state, card_service)


# ============================================================================
# 預覽 & 確認流程
# ============================================================================

async def _check_duplicate_before_generate(
    message_or_query_msg: types.Message,
    state: FSMContext,
    card_service: CardService
) -> None:
    """Perform the Anki duplicate-card guard check before calling the LLM to generate the card content.

    在呼叫 LLM 產生卡片內容前，執行 Anki 重複卡片防呆檢查。

    Purpose:
        1. Avoid spending unnecessary LLM API tokens.
        2. Ensure the data in the Anki deck does not end up with unexpected duplicates.

    Logic:
        1. The flow state has no original text (restart, timeout): treat the flow as expired, clear the state, and
           ask the user to start over.
        2. Search for an exactly matching original text with the Anki search syntax
           ``deck:... note:... "My_Original:..."`` (the value is escaped literally).
        3. The lookup fails (``AnkiConnectError`` only): tell the user the duplicate check was skipped, then generate
           directly; other exceptions propagate as usual and the global error handler notifies the user.
        4. No duplicate: call ``_generate_and_preview`` directly to enter the LLM preview flow.
        5. Duplicate found: intercept the flow, switch the state to ``waiting_for_duplicate_decision``, and show the
           old card's information to the user with the options to overwrite, allow a duplicate, or cancel.

    目的：
        1. 避免消耗不必要的 LLM API Token 費用。
        2. 確保 Anki 牌組中的資料不會出現意外的重複。

    處理邏輯：
        1. 流程狀態裡沒有原文（重啟、逾時）：當成流程失效，清除狀態並請使用者重新開始。
        2. 使用 Anki 搜尋語法 ``deck:... note:... "My_Original:..."`` 尋找完全一致的原文（值照字面跳脫）。
        3. 查詢失敗（只限 ``AnkiConnectError``）：告訴使用者跳過了重複檢查，再直接生成；
           其他例外照常往上拋，由全域錯誤處理通知使用者。
        4. 若無重複：直接呼叫 ``_generate_and_preview`` 進入 LLM 預覽流程。
        5. 若有重複：攔截流程，將狀態切換至 ``waiting_for_duplicate_decision``，
           並將舊卡片的資訊展示給使用者，提供覆蓋、允許重複或取消的選項。

    Args:
        message_or_query_msg: the Telegram message to reply to
            要回覆的 Telegram 訊息物件
        state: the aiogram FSM context
            aiogram FSM 上下文
        card_service: the Anki card service
            Anki 卡片操作服務
    """
    data = await state.get_data()
    original_text = data.get("original_text")
    if not original_text:
        # 原文是第一步就存的；沒有代表流程狀態已遺失（重啟、逾時），用空字串去查會比對到錯的卡
        logger.warning("外語糾錯：流程狀態缺少原文，當成流程失效（state=%s）", await state.get_state())
        await message_or_query_msg.answer(_FLOW_EXPIRED)
        await state.clear()
        return

    # Anki 搜尋語法：deck:"..." note:"..." "My_Original:..."；原文照字面比對（" \ * _ : 都要跳脫，
    # 否則 * _ 會被當成萬用字元而比對到別張卡，引號寫錯則靜默地找不到）
    deck_name = "日本語::外語糾錯::母卡片"
    model_name = "Expression_Master_Dark"
    query = f"{deck_term(deck_name)} {note_type_term(model_name)} {field_term('My_Original', original_text)}"

    # 這一輪的重複決策從頭算：不沿用上一輪可能殘留的「覆蓋」與舊卡 ID
    await state.update_data(duplicate_decision=None, old_master_note_id=None)

    # 只有「查詢」失敗才退回直接生成；警告送不出去不能被當成查詢失敗而默默跳過重複檢查
    try:
        found_note_ids = await card_service.find_notes(query)
        notes_info = await card_service.get_notes_info([found_note_ids[0]]) if found_note_ids else []
    except AnkiConnectError as e:
        # 只有 Anki 查詢失敗（連不上、搜尋被拒）才退回直接生成，並明講跳過了檢查；
        # 程式錯誤照常往上拋（由 bot/handlers/errors.py 通知使用者），不被當成「查不到」而默默略過
        logger.warning("檢查重複卡片失敗，略過檢查直接生成：%s（query=%s）", e, query)
        await message_or_query_msg.answer(
            f"⚠️ 無法確認 Anki 是否已有相同原文的卡（{error_detail(e)}），將直接生成；若之後發現重複請手動刪除。"
        )
        await _generate_and_preview(message_or_query_msg, state, card_service)
        return

    if not notes_info:
        # 沒重複，直接走正常流程
        await _generate_and_preview(message_or_query_msg, state, card_service)
        return

    old_master_note_id = found_note_ids[0]
    old_fields = notes_info[0].get("fields", {})
    old_correction = anki_field_to_tg_text(str(old_fields.get("Correct_Answer", {}).get("value", "")), 300)
    old_reorg = anki_field_to_tg_text(str(old_fields.get("Reorganized_Expression", {}).get("value", "")), 300)

    await state.update_data(old_master_note_id=old_master_note_id)
    await state.set_state(ExpressionStates.waiting_for_duplicate_decision)

    preview_text = (
        f"⚠️ <b>Anki 已有相同原文的卡片</b>\n"
        f"「{escape_tg(original_text, 300)}」\n\n"
        f"舊卡：\n📝 {old_correction}\n🌟 {old_reorg}\n\n"
        f"「覆蓋」會在新卡寫入成功後刪除舊母卡與子卡。"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑️ 覆蓋舊卡", callback_data="expr_dup_overwrite")],
        [InlineKeyboardButton(text="➕ 另建新卡", callback_data="expr_dup_allow")],
        [InlineKeyboardButton(text="❌ 取消", callback_data="expr_dup_cancel")],
    ])
    await message_or_query_msg.answer(preview_text, reply_markup=keyboard, parse_mode="HTML")

@router.callback_query(lambda c: c.data == "expr_dup_overwrite", ExpressionStates.waiting_for_duplicate_decision)
async def handle_dup_overwrite(callback_query: types.CallbackQuery, state: FSMContext, card_service: CardService) -> None:
    """Handle the duplicate decision: the user chose "overwrite the old card".

    處理重複卡片決策：使用者選擇「覆蓋舊卡片」。

    Records the decision ``overwrite`` in the FSM state and continues to the LLM preview flow. At the final write to
    Anki (``_execute_card_creation``) a cascading delete removes the old master card together with its related child
    cards, so no orphan cards are left behind.

    將決策 ``overwrite`` 記錄在 FSM state 中，並繼續進入 LLM 預覽流程。
    在最終確認寫入 Anki 時（``_execute_card_creation``），會執行級聯刪除，
    將舊的母卡片與其關聯的子卡片一併刪除乾淨，避免留下落單的孤兒卡片。

    Args:
        callback_query: the Telegram callback query
            Telegram 回調查詢
        state: the aiogram FSM context
            aiogram FSM 上下文
        card_service: the Anki card service
            Anki 卡片操作服務
    """
    await state.update_data(duplicate_decision="overwrite")
    if callback_query.message:
        await callback_query.message.edit_reply_markup(reply_markup=None)
    await _generate_and_preview(callback_query.message, state, card_service)
    await answer_callback(callback_query)

@router.callback_query(lambda c: c.data == "expr_dup_allow", ExpressionStates.waiting_for_duplicate_decision)
async def handle_dup_allow(callback_query: types.CallbackQuery, state: FSMContext, card_service: CardService) -> None:
    """Handle the duplicate decision: the user chose "allow a duplicate".

    處理重複卡片決策：使用者選擇「允許重複創建」。

    Records the decision ``allow`` in the FSM state and continues to the LLM preview flow. At the final write,
    ``allow_duplicate`` is passed as ``True`` to the underlying API, bypassing Anki's duplicate-first-field check so
    the new card is created.

    將決策 ``allow`` 記錄在 FSM state 中，並繼續進入 LLM 預覽流程。
    最終寫入時會將 ``allow_duplicate`` 設為 ``True`` 傳遞給底層 API，
    從而繞過 Anki 首欄位重複的限制，成功建立新卡片。

    Args:
        callback_query: the Telegram callback query
            Telegram 回調查詢
        state: the aiogram FSM context
            aiogram FSM 上下文
        card_service: the Anki card service
            Anki 卡片操作服務
    """
    await state.update_data(duplicate_decision="allow")
    if callback_query.message:
        await callback_query.message.edit_reply_markup(reply_markup=None)
    await _generate_and_preview(callback_query.message, state, card_service)
    await answer_callback(callback_query)

@router.callback_query(lambda c: c.data == "expr_dup_cancel", ExpressionStates.waiting_for_duplicate_decision)
async def handle_dup_cancel(callback_query: types.CallbackQuery, state: FSMContext) -> None:
    """Handle the duplicate decision: the user chose "cancel the correction".

    處理重複卡片決策：使用者選擇「取消糾錯」。

    Ends the FSM flow and clears the state, avoiding unnecessary LLM token usage.

    結束 FSM 流程並清空狀態，避免不必要的 LLM Token 消耗。

    Args:
        callback_query: the Telegram callback query
            Telegram 回調查詢
        state: the aiogram FSM context
            aiogram FSM 上下文
    """
    if callback_query.message:
        await callback_query.message.edit_reply_markup(reply_markup=None)
        await callback_query.message.answer("🗑️ 已取消。")
    await state.clear()
    await answer_callback(callback_query)

async def _generate_and_preview(
    message: types.Message,
    state: FSMContext,
    card_service: CardService
) -> None:
    """Call the LLM to generate the correction result, then format it and send a preview message.

    呼叫 LLM 生成糾錯結果，格式化後發送預覽訊息。

    The LLM result is serialized into the FSM state data so the confirmation step can use it directly without
    calling the LLM again. If generation fails, the state is cleared and the user is asked to start over.

    將 LLM 結果序列化後存入 FSM state data，
    以便後續確認步驟可以直接使用，避免重複呼叫 LLM。生成失敗時清除狀態並請使用者重新開始。

    Args:
        message: the Telegram message to reply to
            要回覆的 Telegram 訊息物件
        state: the aiogram FSM context
            aiogram FSM 上下文
        card_service: the Anki card service (currently unused here)
            Anki 卡片操作服務（目前這裡沒有用到）
    """
    data = await state.get_data()
    req = TGExpressionCorrectionRequest(
        native_language=data["native_language"],
        target_language=data["target_language"],
        original_text=data["original_text"],
        context=data.get("context", ""),
        source_tag=data.get("source_tag", ""),
        user_grammar_correction=data.get("user_grammar_correction", ""),
        user_reorganization=data.get("user_reorganization", "")
    )

    processing_msg = await message.answer("⏳ AI 糾錯中，請稍候…")

    try:
        creator = handler_registry.get_creator("expression_correction")

        # 僅呼叫 LLM，不寫入 Anki
        correction_result = await creator.execute_generate(req.model_dump())

        # 序列化 LLM 結果存入 FSM data，以便後續確認步驟使用
        await state.update_data(
            llm_result=correction_result.model_dump()
        )

        # 格式化預覽訊息
        preview_text = _format_preview(correction_result)

        # 刪除「正在處理」的訊息
        try:
            await processing_msg.delete()
        except Exception:
            pass
    except Exception as e:
        logger.exception("糾錯任務發生錯誤")
        try:
            await processing_msg.delete()
        except Exception:
            pass
        await message.answer(f"❌ 生成失敗：{error_detail(e)}\n{NEWCARD_RESTART}")
        await state.clear()
        return

    # 生成已成功：之後的失敗是顯示問題，不能報成「生成失敗」、也不能把結果清掉
    # （內容已轉義並分段，正常不會發生）
    try:
        for chunk in _split_message(preview_text):
            await message.answer(chunk, parse_mode="HTML")
    except TelegramAPIError:
        # 改送純文字版：子卡編號必須看得到，下一步才能輸入編號選卡
        logger.exception("糾錯預覽 HTML 送不出去，改送純文字")
        plain = html.unescape(re.sub(r"<[^>]+>", "", preview_text))
        for chunk in _split_message(plain):
            await message.answer(chunk, parse_mode=None)

    total_count = len(correction_result.grammar_micro_points) + len(correction_result.reorganized_micro_points)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ 全部寫入", callback_data="expr_confirm_all")],
        [InlineKeyboardButton(text="🗑️ 全部放棄", callback_data="expr_abandon_all")],
        [InlineKeyboardButton(text="🔙 返回修改重組", callback_data="expr_back_reorg")]
    ])
    if total_count:
        selection_prompt = (
            f"要寫入哪些子卡？（共 {total_count} 張）\n"
            f"輸入編號如 <code>1,3</code>，或點下方按鈕。\n"
            f"<i>母卡一定會寫入（全部放棄除外）。</i>"
        )
    else:
        selection_prompt = "沒有子卡，只會寫入母卡。"
    await message.answer(selection_prompt, reply_markup=keyboard, parse_mode="HTML")
    await state.set_state(ExpressionStates.waiting_for_card_selection)


def _format_preview(result: LLMExpressionCorrectionResult) -> str:
    """Format the LLM correction result into a Telegram preview message in HTML.

    將 LLM 糾錯結果格式化為 Telegram 預覽訊息（HTML 格式）。

    Child cards use unified sequential numbering: grammar first, reorganized second. The user selects child cards
    by these numbers in the confirmation step.

    子卡片使用統一連續編號：grammar 在前，reorganized 在後。
    這個編號是使用者在確認步驟中用來選擇子卡片的依據。

    Args:
        result: the LLM's structured output
            LLM 結構化輸出結果

    Returns:
        the HTML-formatted preview text
        HTML 格式化的預覽字串
    """
    # LLM 的內容一律轉義：含 <、& 或非白名單標籤時整則預覽會送不出去（生成已成功卻看不到）。
    # 只有錯誤對比刻意用 <u> 標出錯誤處，轉義後還原它（並加粗讓錯誤處更明顯）
    lines: list[str] = []
    lines.append("🔍 <b>糾錯結果</b>\n")

    # 將 \n 替換為真正的換行，避免 Telegram 顯示為 literal \n
    error_comp_text = _escape_keep_underline(result.error_comparison.replace("\\n", "\n"))
    lines.append(f"🧐 <b>錯誤對比：</b>\n{error_comp_text}\n")

    grammar_text = html.escape(result.grammar_correction.replace("\\n", "\n"))
    lines.append(f"📝 <b>文法修正：</b>\n{grammar_text}\n")

    reorg_text = html.escape(result.reorganized_expression.replace("\\n", "\n"))
    lines.append(f"🌟 <b>道地重組：</b>\n{reorg_text}\n")

    lines.append("━━━━━━━━━━━━━━━")

    # 子卡片列表 - 統一編號
    card_number = 1

    if result.grammar_micro_points:
        lines.append("📋 <b>子卡片（文法修正）：</b>\n")
        for mp in result.grammar_micro_points:
            orig = f"{html.escape(mp.original_phrase)} → " if mp.original_phrase else ""
            lines.append(f"  <b>{card_number}.</b> {orig}{html.escape(mp.target_phrase)}")
            lines.append(f"     💡 <i>{html.escape(mp.error_hint)}</i>")
            card_number += 1
        lines.append("")

    if result.reorganized_micro_points:
        lines.append("📋 <b>子卡片（道地重組）：</b>\n")
        for mp in result.reorganized_micro_points:
            orig = f"{html.escape(mp.original_phrase)} → " if mp.original_phrase else ""
            lines.append(f"  <b>{card_number}.</b> {orig}{html.escape(mp.target_phrase)}")
            lines.append(f"     💡 <i>{html.escape(mp.error_hint)}</i>")
            card_number += 1

    return "\n".join(lines)


# ============================================================================
# 確認步驟 Handlers
# ============================================================================

@router.callback_query(lambda c: c.data == "expr_confirm_all", ExpressionStates.waiting_for_card_selection)
async def handle_confirm_all(
    callback_query: types.CallbackQuery,
    state: FSMContext,
    card_service: CardService,
    note_service: NoteService,
) -> None:
    """Handle "add all": write the master card plus all child cards to Anki.

    使用者點選「全部添加」：母卡 + 全部子卡寫入 Anki。

    Args:
        callback_query: the Telegram callback query
            Telegram 回調查詢
        state: the aiogram FSM context
            aiogram FSM 上下文
        card_service: the Anki card service
            Anki 卡片操作服務
        note_service: the note service used for the write
            寫入用的筆記服務
    """
    if callback_query.message:
        await callback_query.message.edit_reply_markup(reply_markup=None)

    await _execute_card_creation(callback_query.message, state, card_service, note_service, selected_indices=None)
    await answer_callback(callback_query)


@router.callback_query(lambda c: c.data == "expr_abandon_all", ExpressionStates.waiting_for_card_selection)
async def handle_abandon_all(
    callback_query: types.CallbackQuery,
    state: FSMContext
) -> None:
    """Handle "abandon all": nothing is written and the flow ends.

    使用者點選「全部放棄」：什麼都不寫入，流程結束。

    Args:
        callback_query: the Telegram callback query
            Telegram 回調查詢
        state: the aiogram FSM context
            aiogram FSM 上下文
    """
    if callback_query.message:
        await callback_query.message.edit_reply_markup(reply_markup=None)
        await callback_query.message.answer("🗑️ 已放棄，未寫入任何卡片。")

    await state.clear()
    await answer_callback(callback_query)


@router.message(ExpressionStates.waiting_for_card_selection)
async def handle_card_selection_text(
    message: types.Message,
    state: FSMContext,
    card_service: CardService,
    note_service: NoteService,
) -> None:
    """Handle child-card numbers typed by the user (such as 1,3,5) to select a subset.

    使用者輸入子卡片編號（如 1,3,5）進行篩選。

    Numbers may be separated by commas, spaces, or 、. Invalid input (not a number, out of range) does not end the
    flow; the user is told about the error and the bot keeps waiting until a valid value is entered or a button is
    pressed.

    編號可用逗號、空格或頓號分隔。非法輸入（非數字、超出範圍）不會結束流程，
    而是提示錯誤並繼續等待合法輸入，直到使用者
    輸入有效值或點選按鈕。

    Args:
        message: the Telegram message
            Telegram 訊息物件
        state: the aiogram FSM context
            aiogram FSM 上下文
        card_service: the Anki card service
            Anki 卡片操作服務
        note_service: the note service used for the write
            寫入用的筆記服務
    """
    if not message.text:
        return

    data = await state.get_data()
    llm_result_dict = data.get("llm_result")
    if not llm_result_dict:
        await message.answer(_FLOW_EXPIRED)
        await state.clear()
        return

    correction_result = LLMExpressionCorrectionResult.model_validate(llm_result_dict)
    total_count = len(correction_result.grammar_micro_points) + len(correction_result.reorganized_micro_points)

    # 解析使用者輸入的編號
    raw_text = message.text.strip()
    # 支援逗號、空格、頓號分隔
    parts = raw_text.replace("、", ",").replace(" ", ",").split(",")

    selected: list[int] = []
    invalid_parts: list[str] = []

    for part in parts:
        part = part.strip()
        if not part:
            continue
        try:
            num = int(part)
            if 1 <= num <= total_count:
                if num not in selected:
                    selected.append(num)
            else:
                invalid_parts.append(part)
        except ValueError:
            invalid_parts.append(part)

    # 如果有任何非法字元或無效編號，提示並重新詢問
    # 有非法編號、或完全解析不出編號（只輸入空格或逗號）：提示並維持在選子卡這一步
    if invalid_parts or not selected:
        shown = escape_tg(", ".join(invalid_parts), 50) if invalid_parts else escape_tg(raw_text, 50)
        hint = f"請輸入 1–{total_count}，以逗號分隔，或點上方按鈕。" if total_count else "沒有子卡可選，請點上方按鈕。"
        await message.answer(f"⚠️ 無效編號：{shown}\n{hint}", parse_mode="HTML")
        return

    selected.sort()
    await _execute_card_creation(message, state, card_service, note_service, selected_indices=selected)


async def _execute_card_creation(
    message: types.Message,
    state: FSMContext,
    card_service: CardService,
    note_service: NoteService,
    selected_indices: list[int] | None
) -> None:
    """Perform the final Anki card write.

    執行最終的 Anki 卡片寫入。

    Takes the previously cached LLM result and the user's parameters from the FSM state, and handles
    ``duplicate_decision``:

    - ``allow``: turn on the ``allow_duplicate`` flag to bypass the first-field duplicate check.
    - ``overwrite``: create the new cards **first** (also bypassing the duplicate check, since the old card still
      exists), and only after that succeeds cascade-delete the old master card and its children. Doing it the other
      way round (delete, then create) would lose both cards if creation failed. If the delete fails the new cards
      already exist, so the reply says plainly that Anki now has two cards and asks the user to delete the old one
      by hand.

    從 FSM state 中取出先前快取的 LLM 結果與使用者參數，依 ``duplicate_decision`` 處理：

    - ``allow``：開啟 ``allow_duplicate`` 旗標繞過首欄位重複檢查。
    - ``overwrite``：**先**建立新卡（同樣繞過重複檢查，因為舊卡還在），成功之後才級聯刪除舊的
      母卡與子卡。反過來（先刪後建）的話，建立失敗時新舊兩張都沒了。刪除失敗時新卡已在，
      明說 Anki 裡現在有兩張，請使用者手動刪舊的。

    Creation goes through the service layer (``NoteService.create``, which syncs once after writing); deleting the
    old cards on overwrite is internal to the expression flow and is still done directly, followed by another sync
    to push the deletion to AnkiWeb, and the warning comes from that sync. The FSM state is cleared whether the
    write succeeds or fails.

    建卡經服務層（``NoteService.create``，寫入後會同步一次）；覆蓋時刪舊卡是外語糾錯流程內部的操作，
    照舊直接刪除，刪完再同步一次把刪除推上 AnkiWeb，提醒取這一次的。不論寫入成敗都會清除 FSM 狀態。

    Args:
        message: the Telegram message to reply to
            Telegram 訊息物件
        state: the aiogram FSM context
            aiogram FSM 上下文
        card_service: the Anki card service, used to delete the old cards on overwrite
            Anki 卡片操作服務（覆蓋時刪舊卡用）
        note_service: the note service, used to create the cards
            筆記服務（建卡用）
        selected_indices: the 1-based numbers of the child cards to write; ``None`` means all
            要寫入的子卡片編號列表（從 1 開始），``None`` 代表全部
    """
    data = await state.get_data()
    llm_result_dict = data.get("llm_result")
    if not llm_result_dict:
        await message.answer(_FLOW_EXPIRED)
        await state.clear()
        return


    writing_msg = await message.answer("⏳ 寫入 Anki 中…")

    duplicate_decision = data.get("duplicate_decision")
    old_master_note_id = data.get("old_master_note_id")
    overwrite = duplicate_decision == "overwrite" and bool(old_master_note_id)

    try:
        bot_user = await message.bot.me() if message.bot else None

        # 牌組由 handler 宣告為固定的母卡牌組，服務層會改用它
        result = await note_service.create(
            "expression_correction",
            deck="日本語::外語糾錯::母卡片",
            note_type="Expression_Master_Dark",
            params={
                "original_text": data["original_text"],
                "context": data.get("context", ""),
                "source_tag": data.get("source_tag", ""),
                "llm_result": llm_result_dict,
                "selected_indices": selected_indices,
                # 覆蓋時舊卡在新卡建好之後才刪，建立當下兩張原文相同，也要繞過重複檢查
                "allow_duplicate": duplicate_decision == "allow" or overwrite,
            },
            tg_bot=bot_user.username if bot_user else "",
        )
        created_ids: list[int] = result["note_ids"]  # type: ignore[assignment]
    except Exception as e:
        logger.exception("糾錯任務發生錯誤")
        try:
            await writing_msg.delete()
        except Exception:
            pass
        kept = "（舊卡未變動）" if overwrite else ""
        await message.answer(f"❌ 寫入 Anki 失敗{kept}：{error_detail(e)}\n{NEWCARD_RESTART}")
        await state.clear()
        return

    replaced = ""
    if overwrite:
        replaced = await _delete_old_cards(card_service, old_master_note_id)
        # 服務層建卡後已同步過；刪舊卡之後要再同步一次，把刪除推上 AnkiWeb
        from app.bot.handlers.callbacks import _sync_with_warning
        sync_warning = await _sync_with_warning(card_service.anki_client)
    else:
        sync_warning = sync_suffix(result["sync_warning"])  # type: ignore[arg-type]

    try:
        await writing_msg.delete()
    except Exception:
        pass

    await state.clear()
    await message.answer(
        f"✅ 已寫入 Anki：母卡 1 張＋子卡 {len(created_ids) - 1} 張{replaced}{sync_warning}"
    )


async def _delete_old_cards(card_service: CardService, old_master_note_id: int) -> str:
    """Delete the old master card and its children after the new cards exist (overwrite).

    覆蓋：新卡已建立後，刪除舊的母卡與其子卡。

    If the delete fails the new cards are still there: the text says truthfully that Anki has two cards with the
    same original text and asks the user to delete the old one by hand; it must not claim the old card was deleted.

    刪除失敗時新卡仍在：如實說 Anki 裡有兩張相同原文的卡，請使用者手動刪舊的，不能寫成「已刪除」。

    Returns:
        the text to append to the success message
        要接在成功訊息後的說明
    """
    try:
        child_query = f"{note_type_term('Expression_Micro_Dark')} {field_term('Master_Note_ID', str(old_master_note_id))}"
        child_note_ids = await card_service.find_notes(child_query)
        all_to_delete = [old_master_note_id] + child_note_ids
        await card_service.anki_client.delete_notes(all_to_delete)
    except Exception:  # noqa: BLE001 - 新卡已寫入：刪不掉舊卡只能請使用者手動處理，不能報成寫入失敗
        logger.exception("覆蓋：新卡已建立，但刪除舊卡 %s 失敗", old_master_note_id)
        return "\n⚠️ 舊卡刪除失敗，Anki 裡現在有兩張相同原文的卡，請手動刪除舊的。"
    logger.info("已刪除舊母卡片及其 %d 張子卡片 (IDs: %s)", len(child_note_ids), all_to_delete)
    return "（已刪除舊卡）"

@router.callback_query(lambda c: c.data and c.data.startswith("expr_back_"))
async def process_go_back(callback_query: types.CallbackQuery, state: FSMContext) -> None:
    """Handle the go-back-one-step action.

    處理返回上一步的操作。

    Only acts while the expression flow is active; the message with the old step is deleted and the earlier step's
    prompt is sent again.

    只在外語糾錯流程中有效；刪除舊步驟的訊息，重新送出前一步的提示。

    Args:
        callback_query: the Telegram callback query
            Telegram 回調查詢
        state: the aiogram FSM context
            aiogram FSM 上下文
    """
    # 舊訊息上的返回鈕在流程結束後仍按得到：那時 data 已清空，把 FSM 拉回中間某步只會顯示
    # 預設值、往下走還會缺欄位而失敗
    if not await _in_flow(state):
        await answer_callback(callback_query, FLOW_ENDED_ALERT, show_alert=True)
        return

    action = callback_query.data.replace("expr_back_", "")

    if callback_query.message:
        try:
            await callback_query.message.delete()
        except Exception:
            pass

    if action == "lang":
        await state.set_state(ExpressionStates.waiting_for_lang)
        await callback_query.message.answer(_LANG_PROMPT, reply_markup=_step_keyboard(None))
    elif action == "text":
        data = await state.get_data()
        nl, tl = data.get('native_language', '中文'), data.get('target_language', '日文')
        await state.set_state(ExpressionStates.waiting_for_text)
        await callback_query.message.answer(
            _text_prompt(nl, tl, again=True), reply_markup=_step_keyboard("expr_back_lang")
        )
    elif action == "context":
        await state.set_state(ExpressionStates.waiting_for_context)
        await callback_query.message.answer(
            _again(_CONTEXT_PROMPT, True), reply_markup=_step_keyboard("expr_back_text")
        )
    elif action == "source_tag":
        await state.set_state(ExpressionStates.waiting_for_source_tag)
        await callback_query.message.answer(_again(_SOURCE_TAG_PROMPT, True), reply_markup=_get_source_tag_keyboard())
    elif action == "grammar":
        await state.set_state(ExpressionStates.waiting_for_grammar_correction)
        await callback_query.message.answer(_again(_GRAMMAR_PROMPT, True), reply_markup=_grammar_keyboard())
    elif action == "reorg":
        await state.set_state(ExpressionStates.waiting_for_reorganization)
        await callback_query.message.answer(_again(_REORG_PROMPT, True), reply_markup=_reorg_keyboard())

    await answer_callback(callback_query)


@router.callback_query(lambda c: c.data == "expr_cancel")
async def process_cancel(callback_query: types.CallbackQuery, state: FSMContext) -> None:
    """Cancel the expression flow; every step has this button.

    取消外語糾錯流程（每一步都有這顆鈕）。

    Only acts while the flow is active: once the flow has ended this is a button on an old message, and it must not
    clear another flow the user is in now.

    只在流程中有效：流程已結束時是舊訊息上的按鈕，不能清掉使用者現在進行中的其他流程。

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
        try:
            await callback_query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await callback_query.message.answer("🗑️ 已取消。")
    await answer_callback(callback_query)
