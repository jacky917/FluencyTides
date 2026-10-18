"""
動態設定的 Callback 處理模組。
負責處理 /setconfig 指令後展開的 Inline Keyboard 互動。

Dynamic configuration callback handler module.
Handles the inline keyboard interactions expanded from the /setconfig
command.
"""

import logging
from typing import Any

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from app.bot.utils.formatting import escape_tg
from app.bot.utils.texts import NO_MODIFIABLE_CONFIGS, SETCONFIG_EXPIRED_ALERT
from app.core.config import settings
from app.core.dynamic_config import get_modifiable_configs

# Telegram 的 callback data 上限（位元組，不是字元：中文一字 3 bytes）
_CALLBACK_DATA_LIMIT = 64

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
        # 只會在舊按鈕上發生（.env 改過、或該鍵已改由 /models 管理）
        await callback.answer(SETCONFIG_EXPIRED_ALERT, show_alert=True)
        return

    await callback.answer()
    options = modifiable[key]
    if options is None:
        # 沒有選項清單時無法用按鈕選（進階實作可改用 ForceReply 讓使用者輸入）
        await callback.message.edit_text(
            f"<code>{escape_tg(key)}</code> 沒有可選的值，只能改 .env。",
            reply_markup=_back_keyboard(),
        )
        return

    buttons = []
    for opt in options:
        cb_data = f"setcfg_val:{key}:{opt}"
        if len(cb_data.encode("utf-8")) > _CALLBACK_DATA_LIMIT:
            # 截斷後的值與清單對不上，按下去必定被當成無效選項：寧可不列出
            logger.warning("/setconfig：%s 的選項 %r 超過 callback data 的 64 bytes 上限，不列出", key, opt)
            continue
        buttons.append([InlineKeyboardButton(text=opt, callback_data=cb_data)])
    buttons.append([InlineKeyboardButton(text="⬅️ 返回", callback_data="setconfig_back")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    current_val = getattr(settings, key, "未知")
    await callback.message.edit_text(
        f"⚙️ <b>{escape_tg(key)}</b>\n"
        f"目前：<code>{escape_tg(str(current_val))}</code>\n"
        f"選擇新的值：",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


def _back_keyboard() -> InlineKeyboardMarkup:
    """只有「返回設定列表」的鍵盤（避免訊息成為死路）。A keyboard with just the back button."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ 返回", callback_data="setconfig_back")]]
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

    await callback.answer()
    modifiable = get_modifiable_configs()
    if not modifiable:
        await callback.message.edit_text(NO_MODIFIABLE_CONFIGS)
        return

    buttons = []
    for key in modifiable.keys():
        buttons.append([InlineKeyboardButton(text=f"⚙️ {key}", callback_data=f"setconfig_key:{key}")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text("請選擇要動態修改的設定項目（重啟後將失效）：", reply_markup=keyboard)


@router.callback_query(F.data.startswith("setcfg_val:"))
async def handle_setconfig_value_selection(callback: CallbackQuery, app: "Any" = None) -> None:
    """處理使用者選擇設定的新值並動態套用。

    Handle the user's chosen new value and apply it dynamically.

    修改 AUDIO_EVALUATOR_PROVIDER 時會即時重建 app.state.audio_evaluator
    （STT 計畫 §2.3 解法 1）；重建失敗則回滾設定，避免「設定已改但實例
    是舊的」的不一致狀態。

    When AUDIO_EVALUATOR_PROVIDER changes, app.state.audio_evaluator is
    rebuilt immediately (STT plan §2.3, solution 1); on rebuild failure
    the setting is rolled back to avoid a stale-instance mismatch.

    Args:
        callback: 回呼查詢物件。The callback query object.
        app: dispatcher 注入的 FastAPI app 實例。The FastAPI app
            instance injected by the dispatcher.
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
        await callback.answer(SETCONFIG_EXPIRED_ALERT, show_alert=True)
        return

    options = modifiable[key]
    if options is not None and value not in options:
        # 選項清單改過（.env 變了）的舊按鈕
        await callback.answer(SETCONFIG_EXPIRED_ALERT, show_alert=True)
        return

    # 動態覆寫 settings (這只存在記憶體中)
    if hasattr(settings, key):
        old_value = getattr(settings, key)
        setattr(settings, key, value)

        # Provider 切換必須重建 Singleton，否則舊實例繼續生效（S011 同族缺口）
        # Switching providers must rebuild the singleton, or the stale
        # instance stays in effect.
        if key == "AUDIO_EVALUATOR_PROVIDER":
            if app is None:
                setattr(settings, key, old_value)
                logger.warning("/setconfig：dispatcher 沒有注入 app，無法重建 audio evaluator，%s 已回滾為 %s", key, old_value)
                await callback.answer("❌ 切換失敗，設定未變更（詳見伺服器日誌）。", show_alert=True)
                return
            try:
                from app.infrastructure.audio_evaluator.factory import (
                    create_audio_evaluator,
                )
                app.state.audio_evaluator = create_audio_evaluator()
            except Exception as e:
                setattr(settings, key, old_value)
                logger.exception("切換 Audio Evaluator 為 %s 失敗，已回滾為 %s", value, old_value)
                await callback.answer()
                await callback.message.edit_text(
                    f"❌ 切換失敗，維持 <b>{escape_tg(str(old_value))}</b>（詳見伺服器日誌）。",
                    reply_markup=_back_keyboard(),
                    parse_mode="HTML",
                )
                return

        logger.info("動態修改設定成功: %s = %s", key, value)
        await callback.answer()
        await callback.message.edit_text(
            f"✅ <b>{escape_tg(key)}</b> 已設為 <b>{escape_tg(value)}</b>（重啟後還原）。",
            reply_markup=_back_keyboard(),
            parse_mode="HTML"
        )
    else:
        await callback.answer(f"找不到對應的系統設定 {key}。"[:190], show_alert=True)
