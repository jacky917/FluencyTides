"""外語糾錯 Telegram Bot FSM Handler。

本模組實作 6 步輸入流程 + 預覽確認步驟，完整流程為：
Language → Original Text → Context → Source Tag →
Grammar Correction → Reorganized Expression → LLM 預覽 → 確認添加

設計決策：
- LLM 結果在預覽步驟序列化後存入 FSM state data，避免重複呼叫 LLM。
- 子卡片使用統一連續編號（grammar 在前、reorganized 在後），
  讓使用者可以用簡單的數字選擇想要添加的卡片。
- 非法編號輸入不會結束流程，而是持續要求重新輸入，
  直到輸入合法值或使用者點選按鈕。

Foreign-language correction Telegram Bot FSM handler.

This module implements the 6-step input flow plus a preview/confirm step:
Language -> Original Text -> Context -> Source Tag ->
Grammar Correction -> Reorganized Expression -> LLM preview -> confirm.

Design decisions:
- The LLM result is serialized into FSM state data at the preview step to
  avoid calling the LLM twice.
- Child cards use unified sequential numbering (grammar first, reorganized
  second) so the user can pick cards with simple numbers.
- Invalid number input does not end the flow; the user is re-prompted until
  a valid value is entered or a button is pressed.
"""

import logging
from aiogram import Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from app.services.task_handlers.registry import handler_registry
from app.schemas.tg.expression import TGExpressionCorrectionRequest
from app.schemas.llm.expression import LLMExpressionCorrectionResult
from app.services.card_service import CardService

logger = logging.getLogger(__name__)

router = Router(name="expression")


class ExpressionStates(StatesGroup):
    """外語糾錯流程的 FSM 狀態定義。

    FSM state definitions for the foreign-language correction flow.

    定義了使用者與機器人互動的完整流程狀態。
    包含 6 步資料收集、1 步重複卡片防呆決策，以及最後的寫入確認。

    Defines the full interaction states: 6 data-collection steps, one
    duplicate-card decision step, and the final write confirmation.
    """
    waiting_for_lang = State()                  # 等待輸入語言設定 (例如：中文 -> 日文)
    waiting_for_text = State()                  # 等待輸入原文 (想要糾錯的外語句子)
    waiting_for_context = State()               # 等待輸入發生情境或上下文
    waiting_for_source_tag = State()            # 等待選擇來源情境標籤
    waiting_for_grammar_correction = State()    # 等待輸入使用者自訂的文法修正版
    waiting_for_reorganization = State()        # 等待輸入使用者自訂的高階重組版
    waiting_for_duplicate_decision = State()    # 等待決策：發現重複卡片時，選擇覆蓋、允許重複或取消
    waiting_for_card_selection = State()        # 等待選擇要寫入 Anki 的子卡片編號


def _get_source_tag_keyboard() -> InlineKeyboardMarkup:
    """產生來源情境標籤的 Inline Keyboard 鍵盤。

    Build the source-tag inline keyboard.

    從設定檔中讀取標籤清單，並動態生成按鈕矩陣。
    每列最多 2 個按鈕，並在最後附上「跳過」與「返回」按鈕。

    Reads the tag list from settings and builds the button matrix
    dynamically, at most 2 buttons per row, ending with "skip" and
    "back" buttons.

    Returns:
        InlineKeyboardMarkup: 包含標籤選項的鍵盤物件。
            The keyboard object containing the tag options.
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
        
    # 加入跳過按鈕
    keyboard_buttons.append([InlineKeyboardButton(text="⏭️ 跳過，不加入標籤", callback_data="source_tag_skip")])
    keyboard_buttons.append([InlineKeyboardButton(text="🔙 返回上一步", callback_data="expr_back_context")])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)


async def start_expression_flow(message: types.Message, state: FSMContext) -> None:
    """啟動外語糾錯 FSM 流程的公開入口。

    Public entry point that starts the expression-correction FSM flow.

    由 newcard_menu.py 的 Inline Keyboard 回呼觸發，
    設定 FSM 狀態並提示使用者輸入語言設定。

    Triggered by the inline keyboard callback in newcard_menu.py; sets the
    FSM state and prompts the user for the language pair.

    Args:
        message: Telegram 訊息物件（來自 callback.message）。
            The Telegram message object (from callback.message).
        state: aiogram FSM 上下文。The aiogram FSM context.

    Returns:
        None
    """
    await state.set_state(ExpressionStates.waiting_for_lang)
    await message.answer(
        "📝 開始外語糾錯任務\n\n"
        "請輸入「您的母語 -> 目標語言」，例如：`中文 -> 日文` 或 `zh -> ja`\n"
        "若直接輸入 `/skip` 則預設為 `中文 -> 日文`。"
    )

@router.message(ExpressionStates.waiting_for_lang)
async def process_lang(message: types.Message, state: FSMContext) -> None:
    """處理語言設定輸入（母語 -> 目標語言）。

    Handle the language pair input (native -> target language).

    Args:
        message: Telegram 訊息物件。The Telegram message object.
        state: aiogram FSM 上下文。The aiogram FSM context.
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
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 返回上一步", callback_data="expr_back_lang")]])
    await message.answer(f"✅ 語言設定：{native_lang} -> {target_lang}\n\n請輸入您想糾錯的「外語原文」：", reply_markup=keyboard)

@router.message(ExpressionStates.waiting_for_text)
async def process_text(message: types.Message, state: FSMContext) -> None:
    """處理欲糾錯的外語原文輸入。

    Handle the original foreign-language text to be corrected.

    Args:
        message: Telegram 訊息物件。The Telegram message object.
        state: aiogram FSM 上下文。The aiogram FSM context.
    """
    if not message.text:
        return
    await state.update_data(original_text=message.text.strip())
    await state.set_state(ExpressionStates.waiting_for_context)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 返回上一步", callback_data="expr_back_text")]])
    await message.answer("請輸入這段話的「發生情境或上下文」：\n\n(若無請輸入 `/skip`)", reply_markup=keyboard)

@router.message(ExpressionStates.waiting_for_context)
async def process_context(message: types.Message, state: FSMContext) -> None:
    """處理發生情境（上下文）輸入。

    Handle the context input for the sentence.

    Args:
        message: Telegram 訊息物件。The Telegram message object.
        state: aiogram FSM 上下文。The aiogram FSM context.
    """
    if not message.text:
        return
        
    context_text = "" if message.text.strip() == "/skip" else message.text.strip()
    await state.update_data(context=context_text)
    
    await state.set_state(ExpressionStates.waiting_for_source_tag)
    await message.answer("請選擇這個句子的「來源情境標籤」：\n\n(若無請點擊跳過)", reply_markup=_get_source_tag_keyboard())

@router.callback_query(lambda c: c.data and c.data.startswith("source_tag_"), ExpressionStates.waiting_for_source_tag)
async def process_source_tag(callback_query: types.CallbackQuery, state: FSMContext) -> None:
    """處理按鈕選擇的來源情境標籤。

    Handle the source tag chosen via an inline button.

    Args:
        callback_query: Telegram 回呼查詢。The Telegram callback query.
        state: aiogram FSM 上下文。The aiogram FSM context.
    """
    tag = callback_query.data.replace("source_tag_", "")
    source_tag = "" if tag == "skip" else tag
    await state.update_data(source_tag=source_tag)
    
    if callback_query.message:
        await callback_query.message.edit_reply_markup(reply_markup=None)
        
    await state.set_state(ExpressionStates.waiting_for_grammar_correction)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭️ 跳過，由 AI 生成文法修正", callback_data="skip_grammar_correction")],
        [InlineKeyboardButton(text="🔙 返回上一步", callback_data="expr_back_source_tag")]
    ])
    
    await callback_query.message.answer("請輸入這段話的「文法修正版」(維持原意與架構)：\n\n(若您不知道，可以點擊下方跳過)", reply_markup=keyboard)
    await callback_query.answer()

@router.message(ExpressionStates.waiting_for_source_tag)
async def process_source_tag_text(message: types.Message, state: FSMContext) -> None:
    """處理手動輸入的來源情境標籤。

    Handle a manually typed source tag.

    Args:
        message: Telegram 訊息物件。The Telegram message object.
        state: aiogram FSM 上下文。The aiogram FSM context.
    """
    if not message.text:
        return
        
    source_tag = "" if message.text.strip() == "/skip" else message.text.strip()
    await state.update_data(source_tag=source_tag)
    
    await state.set_state(ExpressionStates.waiting_for_grammar_correction)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭️ 跳過，由 AI 生成文法修正", callback_data="skip_grammar_correction")],
        [InlineKeyboardButton(text="🔙 返回上一步", callback_data="expr_back_source_tag")]
    ])
    
    await message.answer("請輸入這段話的「文法修正版」(維持原意與架構)：\n\n(若您不知道，可以點擊下方跳過)", reply_markup=keyboard)

@router.callback_query(lambda c: c.data == "skip_grammar_correction", ExpressionStates.waiting_for_grammar_correction)
async def process_skip_grammar_correction(callback_query: types.CallbackQuery, state: FSMContext) -> None:
    """處理跳過使用者自訂文法修正。

    Handle skipping the user-provided grammar correction.

    Args:
        callback_query: Telegram 回呼查詢。The Telegram callback query.
        state: aiogram FSM 上下文。The aiogram FSM context.
    """
    await state.update_data(user_grammar_correction="")
    if callback_query.message:
        await callback_query.message.edit_reply_markup(reply_markup=None)
    
    await _prompt_reorganization(callback_query.message, state)
    await callback_query.answer()

@router.message(ExpressionStates.waiting_for_grammar_correction)
async def process_grammar_correction(message: types.Message, state: FSMContext) -> None:
    """處理使用者自訂的文法修正版輸入。

    Handle the user-provided grammar-correction input.

    Args:
        message: Telegram 訊息物件。The Telegram message object.
        state: aiogram FSM 上下文。The aiogram FSM context.
    """
    if not message.text:
        return
        
    correct_text = "" if message.text.strip() == "/skip" else message.text.strip()
    await state.update_data(user_grammar_correction=correct_text)
    
    await _prompt_reorganization(message, state)

async def _prompt_reorganization(message_or_query_msg: types.Message, state: FSMContext) -> None:
    """共用邏輯：切換到高階重組輸入狀態並發送提示。

    Shared logic: switch to the reorganization-input state and prompt.

    Args:
        message_or_query_msg: Telegram 訊息物件。The Telegram message object.
        state: aiogram FSM 上下文。The aiogram FSM context.
    """
    await state.set_state(ExpressionStates.waiting_for_reorganization)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭️ 跳過，由 AI 生成高階重組", callback_data="skip_reorganization")],
        [InlineKeyboardButton(text="🔙 返回上一步", callback_data="expr_back_grammar")]
    ])
    
    await message_or_query_msg.answer("請輸入這段話的「重新組織過的高階表達」(母語人士說法)：\n\n(若您不知道，可以點擊下方跳過)", reply_markup=keyboard)

@router.callback_query(lambda c: c.data == "skip_reorganization", ExpressionStates.waiting_for_reorganization)
async def process_skip_reorganization(callback_query: types.CallbackQuery, state: FSMContext, card_service: CardService) -> None:
    """處理跳過使用者自訂高階重組。

    Handle skipping the user-provided reorganized expression.

    Args:
        callback_query: Telegram 回呼查詢。The Telegram callback query.
        state: aiogram FSM 上下文。The aiogram FSM context.
        card_service: 卡片服務。The CardService instance.
    """
    await state.update_data(user_reorganization="")
    if callback_query.message:
        await callback_query.message.edit_reply_markup(reply_markup=None)
    await _check_duplicate_before_generate(callback_query.message, state, card_service)
    await callback_query.answer()

@router.message(ExpressionStates.waiting_for_reorganization)
async def process_reorganization(message: types.Message, state: FSMContext, card_service: CardService) -> None:
    """處理使用者自訂的高階重組版輸入。

    Handle the user-provided reorganized-expression input.

    Args:
        message: Telegram 訊息物件。The Telegram message object.
        state: aiogram FSM 上下文。The aiogram FSM context.
        card_service: 卡片服務。The CardService instance.
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
    """在呼叫 LLM 產生卡片內容前，執行 Anki 重複卡片防呆檢查。

    Perform the Anki duplicate-card guard check before calling the LLM.

    目的：
        1. 避免消耗不必要的 LLM API Token 費用。
        2. 確保 Anki 牌組中的資料不會出現意外的重複。
        
    處理邏輯：
        1. 使用 Anki 搜尋語法 `deck:... note:... "My_Original:..."` 尋找完全一致的原文。
        2. 若無重複：直接呼叫 `_generate_and_preview` 進入 LLM 預覽流程。
        3. 若有重複：攔截流程，將狀態切換至 `waiting_for_duplicate_decision`，
           並將舊卡片的資訊展示給使用者，提供覆蓋、允許重複或取消的選項。
           
    Args:
        message_or_query_msg: Telegram 訊息物件。
        state: aiogram FSM 上下文。
        card_service: Anki 卡片操作服務。
        
    Returns:
        None
    """
    data = await state.get_data()
    original_text = data.get("original_text", "").replace('"', '""')
    
    # Anki 搜尋語法：deck:"..." note:"..." "My_Original:..."
    deck_name = "日本語::外語糾錯::母卡片"
    model_name = "Expression_Master_Dark"
    query = f'deck:"{deck_name}" note:"{model_name}" "My_Original:{original_text}"'
    
    try:
        found_note_ids = await card_service.find_notes(query)
        if not found_note_ids:
            # 沒重複，直接走正常流程
            await _generate_and_preview(message_or_query_msg, state, card_service)
            return
            
        # 有重複！取出第一張的資訊展示給使用者看
        old_master_note_id = found_note_ids[0]
        notes_info = await card_service.get_notes_info([old_master_note_id])
        if not notes_info:
            await _generate_and_preview(message_or_query_msg, state, card_service)
            return
            
        old_info = notes_info[0]
        old_fields = old_info.get("fields", {})
        old_correction = old_fields.get("Correct_Answer", {}).get("value", "")
        old_reorg = old_fields.get("Reorganized_Expression", {}).get("value", "")
        
        await state.update_data(old_master_note_id=old_master_note_id)
        await state.set_state(ExpressionStates.waiting_for_duplicate_decision)
        
        preview_text = (
            f"⚠️ <b>發現重複的卡片！</b>\n\n"
            f"在牌組 <code>{deck_name}</code> 中，已經有一張完全相同的原文：\n"
            f"「{data.get('original_text', '')}」\n\n"
            f"<b>舊卡片目前內容：</b>\n"
            f"📝 修正：\n{old_correction}\n"
            f"🌟 重組：\n{old_reorg}\n\n"
            f"請選擇您要如何處理這筆請求？"
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑️ 覆蓋舊卡片 (刪除舊母/子卡，重新生成)", callback_data="expr_dup_overwrite")],
            [InlineKeyboardButton(text="➕ 允許重複 (保留舊卡，建立新卡)", callback_data="expr_dup_allow")],
            [InlineKeyboardButton(text="❌ 取消糾錯", callback_data="expr_dup_cancel")]
        ])
        
        await message_or_query_msg.answer(preview_text, reply_markup=keyboard, parse_mode="HTML")
        
    except Exception as e:
        logger.exception("檢查重複卡片時發生錯誤")
        # 發生錯誤時退回保守策略：繼續原本的生成，讓它在 create 時自然失敗或成功
        await _generate_and_preview(message_or_query_msg, state, card_service)

@router.callback_query(lambda c: c.data == "expr_dup_overwrite", ExpressionStates.waiting_for_duplicate_decision)
async def handle_dup_overwrite(callback_query: types.CallbackQuery, state: FSMContext, card_service: CardService) -> None:
    """處理重複卡片決策：使用者選擇「覆蓋舊卡片」。

    Handle the duplicate decision: the user chose "overwrite old card".

    將決策 `overwrite` 記錄在 FSM state 中，並繼續進入 LLM 預覽流程。
    在最終確認寫入 Anki 時（_execute_card_creation），會執行級聯刪除，
    將舊的母卡片與其關聯的子卡片一併刪除乾淨，避免留下落單的孤兒卡片。
    
    Args:
        callback_query: Telegram 回呼查詢。
        state: aiogram FSM 上下文。
        card_service: Anki 卡片操作服務。
        
    Returns:
        None
    """
    await state.update_data(duplicate_decision="overwrite")
    if callback_query.message:
        await callback_query.message.edit_reply_markup(reply_markup=None)
    await _generate_and_preview(callback_query.message, state, card_service)
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "expr_dup_allow", ExpressionStates.waiting_for_duplicate_decision)
async def handle_dup_allow(callback_query: types.CallbackQuery, state: FSMContext, card_service: CardService) -> None:
    """處理重複卡片決策：使用者選擇「允許重複創建」。

    Handle the duplicate decision: the user chose "allow duplicate".

    將決策 `allow` 記錄在 FSM state 中，並繼續進入 LLM 預覽流程。
    最終寫入時會將 `allow_duplicate` 設為 True 傳遞給底層 API，
    從而繞過 Anki 首欄位重複的限制，成功建立新卡片。
    
    Args:
        callback_query: Telegram 回呼查詢。
        state: aiogram FSM 上下文。
        card_service: Anki 卡片操作服務。
        
    Returns:
        None
    """
    await state.update_data(duplicate_decision="allow")
    if callback_query.message:
        await callback_query.message.edit_reply_markup(reply_markup=None)
    await _generate_and_preview(callback_query.message, state, card_service)
    await callback_query.answer()

@router.callback_query(lambda c: c.data == "expr_dup_cancel", ExpressionStates.waiting_for_duplicate_decision)
async def handle_dup_cancel(callback_query: types.CallbackQuery, state: FSMContext) -> None:
    """處理重複卡片決策：使用者選擇「取消糾錯」。

    Handle the duplicate decision: the user chose "cancel correction".

    結束 FSM 流程並清空狀態，避免不必要的 LLM Token 消耗。
    
    Args:
        callback_query: Telegram 回呼查詢。
        state: aiogram FSM 上下文。
        
    Returns:
        None
    """
    if callback_query.message:
        await callback_query.message.edit_reply_markup(reply_markup=None)
        await callback_query.message.answer("🗑️ 已取消本次糾錯任務。")
    await state.clear()
    await callback_query.answer()

async def _generate_and_preview(
    message: types.Message,
    state: FSMContext,
    card_service: CardService
) -> None:
    """呼叫 LLM 生成糾錯結果，格式化後發送預覽訊息。

    Call the LLM to generate the correction result, then format and send
    a preview message.

    此函式將 LLM 結果序列化後存入 FSM state data，
    以便後續確認步驟可以直接使用，避免重複呼叫 LLM。

    Args:
        message: Telegram 訊息物件。
        state: aiogram FSM 上下文。
        card_service: Anki 卡片操作服務。
        
    Returns:
        None
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

    processing_msg = await message.answer("⏳ 正在進行深度糾錯分析... (可能需要數秒)")

    try:
        handler = handler_registry.get_handler("expression_correction")

        # 僅呼叫 LLM，不寫入 Anki
        correction_result = await handler.execute_generate(req.model_dump())

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

        # 發送預覽
        await message.answer(preview_text, parse_mode="HTML")

        # 發送操作提示 + 按鈕
        total_count = len(correction_result.grammar_micro_points) + len(correction_result.reorganized_micro_points)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ 全部添加", callback_data="expr_confirm_all")],
            [InlineKeyboardButton(text="❌ 全部放棄", callback_data="expr_abandon_all")],
            [InlineKeyboardButton(text="🔙 返回修改重組", callback_data="expr_back_reorg")]
        ])

        await message.answer(
            f"請選擇要添加的子卡片（共 {total_count} 張）：\n"
            f"• 輸入編號（如 <code>1,3,4</code>）選擇特定子卡片\n"
            f"• 或點選下方按鈕\n\n"
            f"<i>母卡片除「全部放棄」外必定添加。</i>",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

        await state.set_state(ExpressionStates.waiting_for_card_selection)

    except Exception as e:
        logger.exception("糾錯任務發生錯誤")
        try:
            await processing_msg.delete()
        except Exception:
            pass
        await message.answer(f"❌ 生成失敗：{e}")
        await state.clear()


def _format_preview(result: LLMExpressionCorrectionResult) -> str:
    """將 LLM 糾錯結果格式化為 Telegram 預覽訊息（HTML 格式）。

    Format the LLM correction result into a Telegram preview message
    (HTML format).

    子卡片使用統一連續編號：grammar 在前，reorganized 在後。
    這個編號是使用者在確認步驟中用來選擇子卡片的依據。

    Args:
        result: LLM 結構化輸出結果。

    Returns:
        HTML 格式化的預覽字串。
    """
    lines: list[str] = []
    lines.append("🔍 <b>LLM 糾錯結果預覽</b>\n")
    
    error_comp_text = result.error_comparison.replace("\\n", "\n")
    # 將 <u> 轉換為 <u><b> 讓錯誤處在 Telegram 中更明顯
    error_comp_text = error_comp_text.replace("<u>", "<u><b>").replace("</u>", "</b></u>")
    lines.append(f"🧐 <b>錯誤對比：</b>\n{error_comp_text}\n")

    # 文法修正
    # 將 \n 替換為真正的換行，避免 Telegram 顯示為 literal \n
    grammar_text = result.grammar_correction.replace("\\n", "\n")
    lines.append(f"📝 <b>文法修正：</b>\n{grammar_text}\n")

    # 道地重組
    reorg_text = result.reorganized_expression.replace("\\n", "\n")
    lines.append(f"🌟 <b>道地重組：</b>\n{reorg_text}\n")

    lines.append("━━━━━━━━━━━━━━━")

    # 子卡片列表 - 統一編號
    card_number = 1

    if result.grammar_micro_points:
        lines.append("📋 <b>子卡片（文法修正）：</b>\n")
        for mp in result.grammar_micro_points:
            orig = f"{mp.original_phrase} → " if mp.original_phrase else ""
            lines.append(f"  <b>{card_number}.</b> {orig}{mp.target_phrase}")
            lines.append(f"     💡 <i>{mp.error_hint}</i>")
            card_number += 1
        lines.append("")

    if result.reorganized_micro_points:
        lines.append("📋 <b>子卡片（道地重組）：</b>\n")
        for mp in result.reorganized_micro_points:
            orig = f"{mp.original_phrase} → " if mp.original_phrase else ""
            lines.append(f"  <b>{card_number}.</b> {orig}{mp.target_phrase}")
            lines.append(f"     💡 <i>{mp.error_hint}</i>")
            card_number += 1

    return "\n".join(lines)


# ============================================================================
# 確認步驟 Handlers
# ============================================================================

@router.callback_query(lambda c: c.data == "expr_confirm_all", ExpressionStates.waiting_for_card_selection)
async def handle_confirm_all(
    callback_query: types.CallbackQuery,
    state: FSMContext,
    card_service: CardService
) -> None:
    """使用者點選「全部添加」：母卡 + 全部子卡寫入 Anki。

    The user pressed "add all": write the master card plus all child cards
    to Anki.

    Args:
        callback_query: Telegram 回呼查詢。
        state: aiogram FSM 上下文。
        card_service: Anki 卡片操作服務。
        
    Returns:
        None
    """
    if callback_query.message:
        await callback_query.message.edit_reply_markup(reply_markup=None)

    await _execute_card_creation(callback_query.message, state, card_service, selected_indices=None)
    await callback_query.answer()


@router.callback_query(lambda c: c.data == "expr_abandon_all", ExpressionStates.waiting_for_card_selection)
async def handle_abandon_all(
    callback_query: types.CallbackQuery,
    state: FSMContext
) -> None:
    """使用者點選「全部放棄」：什麼都不寫入，流程結束。

    The user pressed "abandon all": nothing is written and the flow ends.

    Args:
        callback_query: Telegram 回呼查詢。
        state: aiogram FSM 上下文。
        
    Returns:
        None
    """
    if callback_query.message:
        await callback_query.message.edit_reply_markup(reply_markup=None)
        await callback_query.message.answer("🗑️ 已放棄全部卡片，流程結束。")

    await state.clear()
    await callback_query.answer()


@router.message(ExpressionStates.waiting_for_card_selection)
async def handle_card_selection_text(
    message: types.Message,
    state: FSMContext,
    card_service: CardService
) -> None:
    """使用者輸入子卡片編號（如 1,3,5）進行篩選。

    The user typed child-card numbers (e.g. 1,3,5) to select a subset.

    非法輸入（非數字、超出範圍）不會結束流程，
    而是提示錯誤並繼續等待合法輸入，直到使用者
    輸入有效值或點選按鈕。
    
    Args:
        message: Telegram 訊息物件。
        state: aiogram FSM 上下文。
        card_service: Anki 卡片操作服務。
        
    Returns:
        None
    """
    if not message.text:
        return

    data = await state.get_data()
    llm_result_dict = data.get("llm_result")
    if not llm_result_dict:
        await message.answer("❌ 狀態已失效，請重新發起糾錯流程。")
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
    if invalid_parts:
        await message.answer(
            f"⚠️ 以下輸入無效：{', '.join(invalid_parts)}\n\n"
            f"請重新輸入 <b>1~{total_count}</b> 的數字，用逗號分隔（如 <code>1,3,5</code>）。\n"
            f"或點選上方按鈕操作。",
            parse_mode="HTML"
        )
        return

    # 完全無法解析出任何合法編號 (例如只輸入空格或逗號)
    if not selected:
        await message.answer(
            f"⚠️ 無法辨識有效的子卡片編號。\n\n"
            f"請輸入 <b>1~{total_count}</b> 的數字，用逗號分隔（如 <code>1,3,5</code>）。\n"
            f"或點選上方按鈕操作。",
            parse_mode="HTML"
        )
        # 維持在 waiting_for_card_selection 狀態，不退出
        return

    selected.sort()
    await _execute_card_creation(message, state, card_service, selected_indices=selected)


async def _execute_card_creation(
    message: types.Message,
    state: FSMContext,
    card_service: CardService,
    selected_indices: list[int] | None
) -> None:
    """執行最終的 Anki 卡片寫入。

    Perform the final Anki card write.

    從 FSM state 中取出先前快取的 LLM 結果與使用者參數。
    在寫入前，會根據前置的 `duplicate_decision` 執行對應邏輯：
    - 若選擇 `overwrite`：執行級聯刪除，將舊的母卡片及附屬子卡片徹底刪除乾淨。
    - 若選擇 `allow`：開啟 `allow_duplicate` 旗標繞過檢查。
    
    最後呼叫 ExpressionCorrectionHandler.execute_create 完成新卡片寫入。

    Args:
        message: Telegram 訊息物件。
        state: aiogram FSM 上下文。
        card_service: Anki 卡片操作服務。
        selected_indices: 要寫入的子卡片編號列表，None 代表全部。
        
    Returns:
        None
    """
    data = await state.get_data()
    llm_result_dict = data.get("llm_result")
    if not llm_result_dict:
        await message.answer("❌ 狀態已失效，請重新發起糾錯流程。")
        await state.clear()
        return

    # 計算將要寫入的數量（用於顯示）
    correction_result = LLMExpressionCorrectionResult.model_validate(llm_result_dict)
    total_micro = len(correction_result.grammar_micro_points) + len(correction_result.reorganized_micro_points)
    micro_count = len(selected_indices) if selected_indices else total_micro

    if selected_indices:
        selection_desc = f"（已選擇 {micro_count}/{total_micro} 張子卡片）"
    else:
        selection_desc = f"（全部 {total_micro} 張子卡片）"

    writing_msg = await message.answer(f"✍️ 正在寫入 Anki... {selection_desc}")

    try:
        duplicate_decision = data.get("duplicate_decision")
        old_master_note_id = data.get("old_master_note_id")
        allow_duplicate = False
        
        if duplicate_decision == "allow":
            allow_duplicate = True
        elif duplicate_decision == "overwrite" and old_master_note_id:
            # 刪除舊的母卡片以及附屬的所有子卡片
            try:
                child_query = f'note:"Expression_Micro_Dark" "Master_Note_ID:{old_master_note_id}"'
                child_note_ids = await card_service.find_notes(child_query)
                all_to_delete = [old_master_note_id] + child_note_ids
                await card_service.anki_client.delete_notes(all_to_delete)
                logger.info("已刪除舊母卡片及其 %d 張子卡片 (IDs: %s)", len(child_note_ids), all_to_delete)
            except Exception as e:
                logger.error("刪除舊卡片失敗: %s", e)
                await message.answer(f"⚠️ 刪除舊卡片時發生錯誤：{e}\n將嘗試繼續建立新卡片...")

        handler = handler_registry.get_handler("expression_correction")
        bot_user = await message.bot.me() if message.bot else None

        created_ids = await handler.execute_create(
            card_service=card_service,
            relation_service=None,  # type: ignore
            deck_name="日本語::外語糾錯::母卡片",
            model_name="Expression_Master_Dark",
            parameters={
                "original_text": data["original_text"],
                "context": data.get("context", ""),
                "source_tag": data.get("source_tag", ""),
                "tg_bot": bot_user.username if bot_user else "",
                "llm_result": llm_result_dict,
                "selected_indices": selected_indices,
                "allow_duplicate": allow_duplicate
            }
        )

        # 強制同步，確保新增的卡片立即推送到 AnkiWeb
        from app.bot.handlers.callbacks import _sync_with_warning
        sync_warning = await _sync_with_warning(card_service.anki_client)

        try:
            await writing_msg.delete()
        except Exception:
            pass

        await message.answer(
            f"🎉 糾錯完成！{selection_desc}\n"
            f"共生成了 {len(created_ids)} 張卡片（1 張母卡 + {len(created_ids) - 1} 張子卡）。\n"
            f"請至 Anki 複習。{sync_warning}"
        )
    except Exception as e:
        logger.exception("糾錯任務發生錯誤")
        try:
            await writing_msg.delete()
        except Exception:
            pass
        await message.answer(f"❌ 寫入 Anki 失敗：{e}")
    finally:
        await state.clear()

@router.callback_query(lambda c: c.data and c.data.startswith("expr_back_"))
async def process_go_back(callback_query: types.CallbackQuery, state: FSMContext) -> None:
    """處理返回上一步的操作。

    Handle the go-back-one-step action.

    Args:
        callback_query: Telegram 回呼查詢。The Telegram callback query.
        state: aiogram FSM 上下文。The aiogram FSM context.
    """
    action = callback_query.data.replace("expr_back_", "")
    
    if callback_query.message:
        try:
            await callback_query.message.delete()
        except Exception:
            pass
        
    if action == "lang":
        await state.set_state(ExpressionStates.waiting_for_lang)
        await callback_query.message.answer(
            "📝 開始外語糾錯任務\n\n"
            "請輸入「您的母語 -> 目標語言」，例如：`中文 -> 日文` 或 `zh -> ja`\n"
            "若直接輸入 `/skip` 則預設為 `中文 -> 日文`。"
        )
    elif action == "text":
        data = await state.get_data()
        nl, tl = data.get('native_language', '中文'), data.get('target_language', '日文')
        await state.set_state(ExpressionStates.waiting_for_text)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 返回上一步", callback_data="expr_back_lang")]])
        await callback_query.message.answer(f"✅ 語言設定：{nl} -> {tl}\n\n請重新輸入您想糾錯的「外語原文」：", reply_markup=keyboard)
    elif action == "context":
        await state.set_state(ExpressionStates.waiting_for_context)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 返回上一步", callback_data="expr_back_text")]])
        await callback_query.message.answer("請重新輸入這段話的「發生情境或上下文」：\n\n(若無請輸入 `/skip`)", reply_markup=keyboard)
    elif action == "source_tag":
        await state.set_state(ExpressionStates.waiting_for_source_tag)
        await callback_query.message.answer("請重新選擇這個句子的「來源情境標籤」：\n\n(若無請點擊跳過)", reply_markup=_get_source_tag_keyboard())
    elif action == "grammar":
        await state.set_state(ExpressionStates.waiting_for_grammar_correction)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏭️ 跳過，由 AI 生成文法修正", callback_data="skip_grammar_correction")],
            [InlineKeyboardButton(text="🔙 返回上一步", callback_data="expr_back_source_tag")]
        ])
        await callback_query.message.answer("請重新輸入這段話的「文法修正版」(維持原意與架構)：\n\n(若您不知道，可以點擊下方跳過)", reply_markup=keyboard)
    elif action == "reorg":
        await state.set_state(ExpressionStates.waiting_for_reorganization)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏭️ 跳過，由 AI 生成高階重組", callback_data="skip_reorganization")],
            [InlineKeyboardButton(text="🔙 返回上一步", callback_data="expr_back_grammar")]
        ])
        await callback_query.message.answer("請重新輸入這段話的「重新組織過的高階表達」(母語人士說法)：\n\n(若您不知道，可以點擊下方跳過)", reply_markup=keyboard)
        
    await callback_query.answer()
