"""Unified /newcard command entry module.

統一 /newcard 指令入口模組。

When the user types /newcard, the bot shows an inline keyboard of card types for the user to choose from. Adding a
new note type in the future only requires an entry in the ``CARD_TYPES`` list plus a dispatch branch in
``handle_newcard_select`` — no new commands to memorize.

當使用者輸入 /newcard 時，Bot 會彈出 Inline Keyboard 按鈕列表，
讓使用者選擇要新增的卡片類型。未來新增筆記類型時，只需在
``CARD_TYPES`` 清單中加一筆，並在 ``handle_newcard_select`` 加上對應的分派即可，無需記憶新指令。

Design principles:

- This module belongs to the bot controller layer and must contain no business logic.
- Pagination keeps each page to at most 4 buttons to avoid crowding on mobile screens.
- The actual flow of each card type is delegated to its handler module.

設計原則：

- 此模組屬於 Bot Controller 層，嚴禁包含任何業務邏輯。
- 分頁邏輯確保每頁最多 4 個按鈕，避免行動裝置畫面擁擠。
- 各卡片類型的實際處理流程委派給對應的 handler 模組。
"""

import logging
import math

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from app.bot.utils.callback_reply import answer_callback, is_not_modified
from app.services.card_service import CardService
from app.infrastructure.anki.client import AnkiClient

logger = logging.getLogger(__name__)

router = Router(name="newcard_menu_router")

# ============================================================================
# 卡片類型註冊表 (Card Type Registry)
# ============================================================================
# 未來新增筆記類型時，在這裡多加一筆，並在 handle_newcard_select 加上對應的分派。
# - key: 唯一識別碼，用於 callback_data 的路由。
# - label: 顯示在 Inline Keyboard 按鈕上的文字。
# - description: 簡短的功能描述（目前沒有程式讀取；/help 的文字寫在 commands.py）。

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
    {
        "key": "person",
        "label": "👤 人脈卡 (Person Profile)",
        "description": "記住一個人的名字、喜好與上次聊過的事；之後用 /p 名字 找人。",
    },
]

# 每頁最多顯示的卡片類型按鈕數量
ITEMS_PER_PAGE = 4


# ============================================================================
# 分頁鍵盤構建
# ============================================================================

def build_newcard_keyboard(page: int = 0) -> InlineKeyboardMarkup:
    """Build the paginated card-type selection inline keyboard.

    構建帶分頁的卡片類型選擇 Inline Keyboard。

    Pagination rules:

    - Each page holds at most ``ITEMS_PER_PAGE`` card-type buttons.
    - If the total is at most ``ITEMS_PER_PAGE``, no paging buttons are shown.
    - When previous or next pages exist, a navigation row is appended at the bottom.

    分頁規則：

    - 每頁最多 ``ITEMS_PER_PAGE`` 個卡片類型按鈕。
    - 若總數 ≤ ``ITEMS_PER_PAGE``：不顯示翻頁按鈕。
    - 若有上/下頁：在底部加入翻頁導航列。

    Args:
        page: the current page number, 0-based; out-of-range values are clamped into range
            目前的頁碼（從 0 開始）；越界時夾回有效範圍

    Returns:
        the assembled keyboard
        組裝好的 InlineKeyboardMarkup
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
    """Handle the unified /newcard command entry point.

    統一的 /newcard 指令入口。

    Shows the inline keyboard menu of all available card types; tapping one enters the corresponding creation flow.

    顯示所有可用卡片類型的 Inline Keyboard 選單，
    讓使用者點選後進入對應的新增流程。

    Args:
        message: the Telegram message
            Telegram 訊息物件
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
    """Handle the callbacks of the pagination buttons.

    處理分頁翻頁按鈕的回調。

    Tapping the button for the page already shown is not an error.

    連點同一顆換頁按鈕（已經是這一頁）不算錯誤。

    Args:
        callback: the callback query
            回調查詢物件
    """
    page_str = callback.data.replace("newcard_page:", "", 1)
    try:
        page = int(page_str)
    except ValueError:
        await answer_callback(callback, "無效的頁碼。", show_alert=True)
        return

    keyboard = build_newcard_keyboard(page=page)
    try:
        await callback.message.edit_reply_markup(reply_markup=keyboard)
    except TelegramBadRequest as e:
        # 連點同一顆換頁：已經是這一頁了（message is not modified），不是錯誤
        if not is_not_modified(e):
            raise
    await answer_callback(callback)


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
    """Handle the user's card-type selection and dispatch to the matching flow.

    處理使用者選擇的卡片類型，分派到對應的處理流程。

    Acts strictly as a dispatcher with no business logic; each card type's actual flow is delegated to the public
    function of its module. An unknown key gets a "not implemented" message.

    此處嚴格作為分派器（Dispatcher），不包含任何業務邏輯。
    各卡片類型的實際處理流程委派給對應模組的公開函式。未知的類型會回覆「尚未實作」。

    Args:
        callback: the callback query
            回調查詢物件
        state: the aiogram FSM context
            aiogram FSM 上下文
        card_service: the injected card service (currently unused here)
            注入的 CardService（目前這裡沒有用到）
        anki_client: the injected AnkiConnect client (currently unused here)
            注入的 AnkiClient（目前這裡沒有用到）
    """
    card_type_key = callback.data.replace("newcard:", "", 1)
    await answer_callback(callback)

    if card_type_key == "expression":
        from app.bot.handlers.fsm.expression_fsm import start_expression_flow
        await start_expression_flow(callback.message, state)

    elif card_type_key == "speaking":
        from app.bot.handlers.fsm.speaking_fsm import start_speaking_fsm_flow
        await start_speaking_fsm_flow(callback.message, state)
        
    elif card_type_key == "vocab":
        from app.bot.handlers.fsm.vocabulary_fsm import start_vocabulary_fsm_flow
        await start_vocabulary_fsm_flow(callback.message, state)

    elif card_type_key == "person":
        from app.bot.handlers.person_profile import start_person_create
        await start_person_create(callback.message, state)

    else:
        await callback.message.edit_text(
            f"🚧 卡片類型 <code>{card_type_key}</code> 尚未實作。"
        )
