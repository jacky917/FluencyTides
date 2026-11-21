"""Telegram webhook API router module.

Telegram Webhook API 路由模組。
"""

import hmac
import logging
import secrets
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import APIRouter, Request, BackgroundTasks

from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Telegram Webhook"])


async def bind_webhook(bot: Bot, dp: Dispatcher, state: Any) -> str:
    """Bind the Telegram webhook with a secret token and remember the token.

    以密鑰綁定 Telegram Webhook，並記住這把密鑰。

    The token is ``TG_WEBHOOK_SECRET``, or a random one generated for this
    process when that is not set, so the webhook is never bound without
    one. ``set_webhook`` runs on every startup so a changed secret or
    update-type list takes effect. Pending updates are dropped only when
    the bound URL differs from ``tg_webhook_url``; note that a graceful
    shutdown deletes the webhook, so after a normal restart the URL is
    empty and pending updates are dropped as before. They survive only
    when the previous process ended without its shutdown step.

    密鑰為 ``TG_WEBHOOK_SECRET``，沒設定時為本行程產生的隨機密鑰，所以 Webhook 一定帶密鑰綁定。
    每次啟動都會呼叫 ``set_webhook``，改了密鑰或 update 類型才會生效。只有綁定的網址與 ``tg_webhook_url``
    不同時才丟掉排隊中的 Update；注意正常關機會刪除 Webhook，所以正常重啟後網址是空的，排隊中的 Update
    仍會像以前一樣被丟掉，只有上一個行程沒跑完關機步驟就結束時才會保留。

    Args:
        bot: the Telegram bot
            Telegram Bot
        dp: the dispatcher, used for the update types to subscribe to
            Dispatcher，用來決定要訂閱的 update 類型
        state: the application state; ``tg_webhook_secret`` is set on it
            應用程式狀態；會在上面設定 ``tg_webhook_secret``

    Returns:
        the secret token the webhook was bound with
        綁定 Webhook 所用的密鑰
    """
    secret_token = settings.TG_WEBHOOK_SECRET or secrets.token_urlsafe(32)
    if not settings.TG_WEBHOOK_SECRET:
        logger.info("🔒 未設定 TG_WEBHOOK_SECRET，已為本次啟動產生隨機 Webhook 密鑰。")
    webhook_info = await bot.get_webhook_info()
    logger.info("🔧 設定 Telegram Webhook: %s", settings.tg_webhook_url)
    await bot.set_webhook(
        url=settings.tg_webhook_url,
        allowed_updates=dp.resolve_used_update_types(),
        drop_pending_updates=webhook_info.url != settings.tg_webhook_url,
        secret_token=secret_token,
    )
    state.tg_webhook_secret = secret_token
    return secret_token


@router.post(settings.TG_WEBHOOK_PATH)
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks) -> dict[str, object]:
    """Receive webhook updates pushed from Telegram servers.

    接收 Telegram 伺服器推播的 Webhook 更新。

    The endpoint path is determined dynamically by the TG_WEBHOOK_PATH
    environment variable (defaults to /api/webhook).

    此端點路徑由環境變數 TG_WEBHOOK_PATH 動態決定（預設為 /api/webhook）。

    Every update must carry the secret token the webhook was bound with
    (``app.state.tg_webhook_secret``: ``TG_WEBHOOK_SECRET``, or a random
    one generated at startup when that is not set). The bot trusts the
    user id inside the update, so an unverified request could act as any
    allowed user; requests are therefore rejected when no secret is known.

    每個 Update 都必須帶上綁定 Webhook 時用的密鑰（``app.state.tg_webhook_secret``：``TG_WEBHOOK_SECRET``，
    沒設定時為啟動時產生的隨機密鑰）。Bot 信任 Update 裡的使用者 ID，未驗證的請求可以冒用任何白名單使用者，
    所以不知道密鑰時一律拒絕。

    Args:
        request: the incoming FastAPI request
            FastAPI 請求物件
        background_tasks: the background task queue used to process the update asynchronously
            背景任務佇列，用於非同步處理 Update

    Returns:
        a status dict, always with HTTP 200 to avoid Telegram retry storms: ``bot_disabled`` when
        the bot is not initialized, ``unauthorized`` when the secret token does not match, and
        otherwise ``ok`` (``False`` with ``error`` if the update could not be parsed)
        狀態字典，永遠回傳 200，避免 Telegram 重試風暴：Bot 未初始化時為 ``bot_disabled``、密鑰不符時為
        ``unauthorized``，其餘為 ``ok``（無法解析 Update 時為 ``False`` 並附 ``error``）
    """
    app = request.app
    bot: Bot | None = getattr(app.state, "bot", None)
    dp: Dispatcher | None = getattr(app.state, "dp", None)

    if not bot or not dp:
        # 如果尚未初始化或未啟用 Bot，忽略此請求
        logger.warning("收到 Webhook 請求，但 Bot 或 Dispatcher 尚未初始化。")
        return {"status": "bot_disabled"}

    # 驗證 Secret Token（防禦偽造的 Update）：沒有已知密鑰時一律拒絕，不再「沒設就不驗」
    expected: str | None = getattr(app.state, "tg_webhook_secret", None)
    if not expected:
        logger.error("🚫 拒絕 Webhook 請求：沒有可比對的密鑰（Webhook 尚未以密鑰綁定）。")
        return {"status": "unauthorized"}
    secret_header = request.headers.get("X-Telegram-Bot-Api-Secret-Token") or ""
    if not hmac.compare_digest(secret_header.encode(), expected.encode()):
        logger.warning("🚫 Webhook 密鑰驗證失敗（%s）。", "有帶密鑰但不符" if secret_header else "沒有帶密鑰")
        return {"status": "unauthorized"}

    try:
        update_data = await request.json()
        update = Update(**update_data)
        
        # 餵給 aiogram 的 Dispatcher 處理
        # 為了避免語音評分等耗時任務超過 Telegram 的 15 秒 webhook timeout，
        # 導致 Telegram 不斷重發相同的 Update，我們改用 BackgroundTasks 來非同步執行。
        # 注意：千萬不要使用沒有保留參照的 asyncio.create_task()，否則會在 await I/O 時被 Python GC 靜默回收導致卡死！
        background_tasks.add_task(dp.feed_update, bot=bot, update=update)
        
        return {"ok": True}
    except Exception as e:
        # 發生解析或其他不可預期錯誤時，我們依然回傳 200 OK，
        # 否則 Telegram 伺服器會認為傳送失敗而持續不斷重試發送相同訊息。
        logger.error(f"Webhook 處理失敗: {e}", exc_info=True)
        return {"ok": False, "error": "invalid update"}
