import logging

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import APIRouter, Request, BackgroundTasks

from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Telegram Webhook"])


@router.post(settings.TG_WEBHOOK_PATH)
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks) -> dict[str, object]:
    """接收 Telegram 伺服器推播的 Webhook 更新。

    Receive webhook updates pushed from Telegram servers.

    此端點路徑由環境變數 TG_WEBHOOK_PATH 動態決定（預設為 /api/webhook）。

    The endpoint path is determined dynamically by the TG_WEBHOOK_PATH
    environment variable (defaults to /api/webhook).

    Args:
        request: FastAPI 請求物件。The incoming FastAPI request object.
        background_tasks: 背景任務佇列，用於非同步處理 Update。
            Background task queue used to process the update asynchronously.

    Returns:
        狀態字典（永遠回傳 200，避免 Telegram 重試風暴）。
        A status dict (always HTTP 200 to avoid Telegram retry storms).
    """
    app = request.app
    bot: Bot | None = getattr(app.state, "bot", None)
    dp: Dispatcher | None = getattr(app.state, "dp", None)

    if not bot or not dp:
        # 如果尚未初始化或未啟用 Bot，忽略此請求
        logger.warning("收到 Webhook 請求，但 Bot 或 Dispatcher 尚未初始化。")
        return {"status": "bot_disabled"}

    # 驗證 Secret Token (防禦惡意請求)
    if settings.TG_WEBHOOK_SECRET:
        secret_header = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if secret_header != settings.TG_WEBHOOK_SECRET:
            masked_expected = f"{settings.TG_WEBHOOK_SECRET[:3]}***{settings.TG_WEBHOOK_SECRET[-3:]}" if len(settings.TG_WEBHOOK_SECRET) > 6 else "***"
            if secret_header:
                masked_actual = f"{secret_header[:3]}***{secret_header[-3:]}" if len(secret_header) > 6 else "***"
            else:
                masked_actual = "None"
            logger.warning("🚫 Webhook 密鑰驗證失敗！期望: %s, 實際收到: %s", masked_expected, masked_actual)
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
        return {"ok": False, "error": str(e)}
