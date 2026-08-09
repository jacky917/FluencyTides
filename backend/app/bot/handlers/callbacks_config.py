"""
動態設定的 Callback 處理模組。
負責處理 /setconfig 指令後展開的 Inline Keyboard 互動。

Dynamic configuration callback handler module.
Handles the inline keyboard interactions expanded from the /setconfig
command.
"""

import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from app.core.config import settings
from app.core.dynamic_config import get_modifiable_configs

logger = logging.getLogger(__name__)

router = Router(name="callbacks_config_router")


@router.callback_query(F.data.startswith("setconfig_key:"))
async def handle_setconfig_key_selection(callback: CallbackQuery) -> None:
    """處理使用者選擇要修改的設定鍵。

    Handle the user's selection of a configuration key to modify.

    Args:
        callback: 回呼查詢物件。The callback query object.
    """
    if callback.from_user is None or settings.TG_ADMIN_CHAT_ID is None:
        await callback.answer("系統未設定管理員。", show_alert=True)
        return

    if callback.from_user.id != settings.TG_ADMIN_CHAT_ID:
        await callback.answer("您沒有權限執行此操作。", show_alert=True)
        return

    # 解析鍵名
    key = callback.data.split(":", 1)[1]
    modifiable = get_modifiable_configs()

    if key not in modifiable:
        await callback.answer("此設定不允許被修改。", show_alert=True)
        return

    options = modifiable[key]
    
    # 建立按鈕
    buttons = []
    if options is None:
        # 如果沒有選項限制，提示使用者（進階實作可改用 ForceReply 讓使用者輸入，這裡先簡單提示）
        await callback.message.edit_text(
            f"設定 `{key}` 目前沒有選項限制。\n"
            f"請直接修改 .env 或設定選項清單來啟用互動按鈕。"
        )
        return
    else:
        for opt in options:
            # 使用 setcfg_val:KEY:VALUE 格式
            cb_data = f"setcfg_val:{key}:{opt}"
            if len(cb_data) > 64:
                logger.warning("Callback data 超過 64 bytes 限制: %s", cb_data)
                # 簡單截斷或略過，這裡若超過則截斷 VALUE，但通常不會
                cb_data = cb_data[:64]
            buttons.append([InlineKeyboardButton(text=opt, callback_data=cb_data)])
            
            
        # 加入返回按鈕
        buttons.append([InlineKeyboardButton(text="⬅️ 返回", callback_data="setconfig_back")])
            
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    current_val = getattr(settings, key, "未知")
    await callback.message.edit_text(
        f"您正在修改 `<b>{key}</b>`\n"
        f"目前的值：`{current_val}`\n\n"
        f"請選擇新的值：",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@router.callback_query(F.data == "setconfig_back")
async def handle_setconfig_back(callback: CallbackQuery) -> None:
    """處理返回主選單按鈕。

    Handle the back-to-main-menu button.

    Args:
        callback: 回呼查詢物件。The callback query object.
    """
    if callback.from_user is None or settings.TG_ADMIN_CHAT_ID is None:
        await callback.answer("系統未設定管理員。", show_alert=True)
        return

    if callback.from_user.id != settings.TG_ADMIN_CHAT_ID:
        await callback.answer("您沒有權限執行此操作。", show_alert=True)
        return

    modifiable = get_modifiable_configs()
    if not modifiable:
        await callback.message.edit_text("⚠️ 目前 .env 中沒有設定任何允許動態修改的變數 (MODIFY_ 開頭)。")
        return

    buttons = []
    for key in modifiable.keys():
        buttons.append([InlineKeyboardButton(text=f"⚙️ {key}", callback_data=f"setconfig_key:{key}")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text("請選擇要動態修改的設定項目（重啟後將失效）：", reply_markup=keyboard)


@router.callback_query(F.data.startswith("setcfg_val:"))
async def handle_setconfig_value_selection(callback: CallbackQuery) -> None:
    """處理使用者選擇設定的新值並動態套用。

    Handle the user's chosen new value and apply it dynamically.

    Args:
        callback: 回呼查詢物件。The callback query object.
    """
    if callback.from_user is None or settings.TG_ADMIN_CHAT_ID is None:
        await callback.answer("系統未設定管理員。", show_alert=True)
        return

    if callback.from_user.id != settings.TG_ADMIN_CHAT_ID:
        await callback.answer("您沒有權限執行此操作。", show_alert=True)
        return

    _, key, value = callback.data.split(":", 2)
    modifiable = get_modifiable_configs()

    if key not in modifiable:
        await callback.answer("此設定不允許被修改。", show_alert=True)
        return

    options = modifiable[key]
    if options is not None and value not in options:
        await callback.answer("無效的選項！", show_alert=True)
        return

    # 動態覆寫 settings (這只存在記憶體中)
    if hasattr(settings, key):
        setattr(settings, key, value)
        logger.info("動態修改設定成功: %s = %s", key, value)
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ 返回設定列表", callback_data="setconfig_back")]]
        )
        await callback.message.edit_text(
            f"✅ 成功將 `<b>{key}</b>` 暫時設為 `<b>{value}</b>`！\n\n"
            f"<i>此設定僅在本次執行期間有效，伺服器重啟後將退回 .env 的設定值。</i>",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    else:
        await callback.answer(f"找不到對應的系統設定 {key}。", show_alert=True)
