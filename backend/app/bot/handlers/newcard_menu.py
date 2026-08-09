"""
統一 /newcard 指令入口模組。

當使用者輸入 /newcard 時，Bot 會彈出 Inline Keyboard 按鈕列表，
讓使用者選擇要新增的卡片類型。未來新增筆記類型時，只需在
CARD_TYPES 清單中加一筆即可，無需記憶新指令。

設計原則：
- 此模組屬於 Bot Controller 層，嚴禁包含任何業務邏輯。
- 分頁邏輯確保每頁最多 4 個按鈕，避免行動裝置畫面擁擠。
- 各卡片類型的實際處理流程委派給對應的 handler 模組。

Unified /newcard command entry module.

When the user types /newcard, the bot shows an inline keyboard of card
types. Adding a new note type in the future only requires appending an
entry to CARD_TYPES — no new commands to memorize.

Design principles:
- This module belongs to the bot controller layer and must contain no
  business logic.
- Pagination keeps each page to at most 4 buttons to avoid crowding on
  mobile screens.
- The actual flow of each card type is delegated to its handler module.
"""

import logging
import math

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from app.services.card_service import CardService
from app.infrastructure.anki.client import AnkiClient

logger = logging.getLogger(__name__)

router = Router(name="newcard_menu_router")

# ============================================================================
# 卡片類型註冊表 (Card Type Registry)
# ============================================================================
# 未來新增筆記類型時，只需在這裡多加一筆即可。
# - key: 唯一識別碼，用於 callback_data 的路由。
# - label: 顯示在 Inline Keyboard 按鈕上的文字。
# - description: 簡短的功能描述，供 /help 使用。

CARD_TYPES: list[dict[str, str]] = [
    {
        "key": "expression",
        "label": "📝 外語糾錯 (Expression Correction)",
        "description": "提交一段有錯誤的外語，AI 會糾錯並拆解成原子化知識點。",
    },
    {
        "key": "speaking",
        "label": "🎙️ 對話卡 (Speaking Coach)",
        "description": "透過互動式問答新增 Speaking_Coach_Dark 對話練習卡片。",
    },
    {
        "key": "vocab",
        "label": "📚 單字卡 (Vocabulary Mining)",
        "description": "輸入單字或片語，AI 自動生成完整結構的單字學習卡。",
    },
]

# 每頁最多顯示的卡片類型按鈕數量
ITEMS_PER_PAGE = 4


# ============================================================================
# 分頁鍵盤構建
# ============================================================================

def build_newcard_keyboard(page: int = 0) -> InlineKeyboardMarkup:
    """構建帶分頁的卡片類型選擇 Inline Keyboard。

    Build the paginated card-type selection inline keyboard.

    分頁規則：
    - 每頁最多 ITEMS_PER_PAGE 個卡片類型按鈕。
    - 若總數 ≤ ITEMS_PER_PAGE：不顯示翻頁按鈕。
    - 若有上/下頁：在底部加入翻頁導航列。

    Pagination rules:
    - Each page holds at most ITEMS_PER_PAGE card-type buttons.
    - If the total is <= ITEMS_PER_PAGE, no paging buttons are shown.
    - When previous/next pages exist, a navigation row is appended.

    Args:
        page: 目前的頁碼（從 0 開始）。Current page number (0-based).

    Returns:
        組裝好的 InlineKeyboardMarkup。The assembled InlineKeyboardMarkup.
    """
    total = len(CARD_TYPES)
    total_pages = max(1, math.ceil(total / ITEMS_PER_PAGE))

    # 防止越界
    page = max(0, min(page, total_pages - 1))

    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    page_items = CARD_TYPES[start_idx:end_idx]

    # 卡片類型按鈕（每個獨佔一行）
    buttons: list[list[InlineKeyboardButton]] = []
    for item in page_items:
        buttons.append([
            InlineKeyboardButton(
                text=item["label"],
                callback_data=f"newcard:{item['key']}"
            )
        ])

    # 翻頁導航列
    if total_pages > 1:
        nav_row: list[InlineKeyboardButton] = []
        if page > 0:
            nav_row.append(
                InlineKeyboardButton(text="◀ 上一頁", callback_data=f"newcard_page:{page - 1}")
            )
        if page < total_pages - 1:
            nav_row.append(
                InlineKeyboardButton(text="下一頁 ▶", callback_data=f"newcard_page:{page + 1}")
            )
        if nav_row:
            buttons.append(nav_row)

    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ============================================================================
# /newcard 指令入口
# ============================================================================

@router.message(Command("newcard"))
async def cmd_newcard(message: Message) -> None:
    """統一的 /newcard 指令入口。

    Unified /newcard command entry point.

    顯示所有可用卡片類型的 Inline Keyboard 選單，
    讓使用者點選後進入對應的新增流程。

    Shows the inline keyboard menu of all available card types so the user
    can enter the corresponding creation flow.

    Args:
        message: Telegram 訊息物件。The Telegram message object.
    """
    keyboard = build_newcard_keyboard(page=0)
    await message.answer(
        "📋 <b>請選擇要新增的卡片類型：</b>",
        reply_markup=keyboard,
    )


# ============================================================================
# 翻頁回呼
# ============================================================================

@router.callback_query(F.data.startswith("newcard_page:"))
async def handle_newcard_page(callback: CallbackQuery) -> None:
    """處理分頁翻頁按鈕的回呼。

    Handle pagination button callbacks.

    Args:
        callback: 回呼查詢物件。The callback query object.
    """
    page_str = callback.data.replace("newcard_page:", "", 1)
    try:
        page = int(page_str)
    except ValueError:
        await callback.answer("無效的頁碼。", show_alert=True)
        return

    keyboard = build_newcard_keyboard(page=page)
    await callback.message.edit_reply_markup(reply_markup=keyboard)
    await callback.answer()


# ============================================================================
# 卡片類型選擇回呼（分派到各 handler）
# ============================================================================

@router.callback_query(F.data.startswith("newcard:"))
async def handle_newcard_select(
    callback: CallbackQuery,
    state: FSMContext,
    card_service: CardService,
    anki_client: AnkiClient,
) -> None:
    """處理使用者選擇的卡片類型，分派到對應的處理流程。

    Handle the user's card-type selection and dispatch to the matching flow.

    此處嚴格作為分派器（Dispatcher），不包含任何業務邏輯。
    各卡片類型的實際處理流程委派給對應模組的公開函式。

    Acts strictly as a dispatcher with no business logic; each card type's
    actual flow is delegated to the public function of its module.

    Args:
        callback: 回呼查詢物件。The callback query object.
        state: aiogram FSM 上下文。The aiogram FSM context.
        card_service: 注入的 CardService。Injected CardService instance.
        anki_client: 注入的 AnkiClient。Injected AnkiClient instance.
    """
    card_type_key = callback.data.replace("newcard:", "", 1)
    await callback.answer()

    if card_type_key == "expression":
        from app.bot.handlers.fsm.expression_fsm import start_expression_flow
        await start_expression_flow(callback.message, state)

    elif card_type_key == "speaking":
        from app.bot.handlers.fsm.speaking_fsm import start_speaking_fsm_flow
        await start_speaking_fsm_flow(callback.message, state)
        
    elif card_type_key == "vocab":
        from app.bot.handlers.fsm.vocabulary_fsm import start_vocabulary_fsm_flow
        await start_vocabulary_fsm_flow(callback.message, state)

    else:
        await callback.message.edit_text(
            f"🚧 卡片類型 <code>{card_type_key}</code> 尚未實作。"
        )
