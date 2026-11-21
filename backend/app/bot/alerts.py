"""Telegram crash alert module.

Telegram 崩潰警報模組。
"""

import logging
import httpx

from app.bot.utils.formatting import escape_tg
from app.core.config import settings

logger = logging.getLogger(__name__)

async def send_crash_alert(url: str, method: str, error_msg: str) -> None:
    """Send a Telegram crash alert to the administrator.

    發送 Telegram 崩潰警報給管理員。

    Called when an unexpected error (HTTP 500) occurs. Uses a standalone
    httpx client to avoid depending on the possibly-crashed aiogram Bot.
    Does nothing when ``TG_BOT_TOKEN`` or ``TG_ADMIN_CHAT_ID`` is not set; a
    failure to send is logged and never raised.

    當系統發生未預期錯誤 (HTTP 500) 時呼叫。
    使用獨立的 httpx 客戶端，避免依賴可能已經崩潰的 aiogram Bot 實例。
    ``TG_BOT_TOKEN`` 或 ``TG_ADMIN_CHAT_ID`` 未設定時不做任何事；發送失敗只記錄、不往上拋。

    Args:
        url: the URL of the failing request
            發生錯誤的請求 URL
        method: the HTTP method (GET, POST, and so on)
            HTTP 方法 (GET, POST 等)
        error_msg: a short error summary or type
            簡短的錯誤摘要或類型
    """
    bot_token = settings.TG_BOT_TOKEN
    chat_id = settings.TG_ADMIN_CHAT_ID

    if not bot_token or not chat_id:
        logger.debug("TG_BOT_TOKEN 或 TG_ADMIN_CHAT_ID 未設定，略過發送 Telegram 崩潰警報。")
        return

    # error_msg 多半是 repr(exc)（常含 <Response [500]> 這類 <…>）、URL 的查詢字串含 &：
    # 不轉義、不限長度的話，警報常在最需要的時候被 Telegram 拒收（上限 4096 字）
    message = (
        "🚨 <b>HTTP 500</b>\n"
        f"{escape_tg(method, 10)} <code>{escape_tg(url, 300)}</code>\n"
        f"<code>{escape_tg(error_msg, 1000)}</code>\n"
        "詳見伺服器日誌。"
    )

    api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML"
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(api_url, json=payload)
            response.raise_for_status()
            logger.info("已成功發送 Telegram 崩潰警報至管理員 %s", chat_id)
    except Exception as e:
        # 警報機制本身不應導致系統二次崩潰
        logger.error("發送 Telegram 崩潰警報失敗: %s", e)
