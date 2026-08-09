"""
Telegram Bot 媒體檔案處理模組。
負責處理使用者發送的照片等媒體檔案。

Telegram Bot media file handler module.
Handles media files sent by the user, such as photos.
"""

import base64
import logging
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from aiogram import F, Router
from aiogram.types import Message

from app.bot.handlers.callbacks import AVATARS_DIR
from app.bot.state import UserStateManager
from app.infrastructure.anki.client import AnkiClient, AnkiConnectError
from aiogram.exceptions import TelegramAPIError

logger = logging.getLogger(__name__)

router = Router(name="media_router")


@router.message(F.photo)
async def process_photo_handler(
    message: Message,
    user_state_manager: UserStateManager,
    anki_client: AnkiClient
) -> None:
    """處理使用者上傳的自訂頭像照片。

    Handle a custom avatar photo uploaded by the user.

    僅在狀態為 wait_avatar_upload 時處理：下載最高解析度照片、
    存到本地 assets/avatars 與 Anki Media，並推進到輸入說話者名稱狀態。

    Only processed when the state is wait_avatar_upload: downloads the
    highest-resolution photo, saves it to local assets/avatars and Anki
    media, then advances to the speaker-name input state.

    Args:
        message: Telegram 訊息物件。The Telegram message object.
        user_state_manager: 使用者狀態管理器。The UserStateManager instance.
        anki_client: AnkiClient 實例。The AnkiClient instance.
    """
    chat_id = message.chat.id
    state = user_state_manager.get_state(chat_id)

    if not state or state.action != "wait_avatar_upload":
        return

    status_msg = await message.reply("🔄 <b>處理中...</b>\n\n正在下載並處理圖片...")

    try:
        photo = message.photo[-1]  # 取得最高解析度
        bot = message.bot
        if not bot:
            await status_msg.edit_text("❌ Bot 實例不可用。")
            return

        file = await bot.get_file(photo.file_id)
        if not file or not file.file_path:
            await status_msg.edit_text("❌ 無法取得圖片檔案路徑。")
            return

        photo_buffer = BytesIO()
        await bot.download_file(file.file_path, photo_buffer)
        photo_data = photo_buffer.getvalue()

        # 產生唯一檔名
        filename = f"avatar_{chat_id}_{datetime.now(tz=timezone.utc).strftime('%Y%m%d_%H%M%S')}.jpg"
        
        # 儲存到本地 assets/avatars，供未來作為系統頭像重複使用
        if not AVATARS_DIR.exists():
            AVATARS_DIR.mkdir(parents=True, exist_ok=True)
            
        local_path = AVATARS_DIR / filename
        with open(local_path, "wb") as f:
            f.write(photo_data)

        # 儲存到 Anki Media，讓卡片可以顯示
        photo_base64 = base64.b64encode(photo_data).decode("utf-8")
        from app.schemas.anki import AnkiStoreMediaParams
        await anki_client.store_media_file(params=AnkiStoreMediaParams(filename=filename, data=photo_base64))

        # 推進狀態
        state.extra["avatar"] = filename
        state.action = "wait_speaker_name"
        state.expires_at = None
        user_state_manager.set_state(chat_id, state)

        await status_msg.edit_text(f"✅ <b>圖片上傳成功！</b>\n\n請輸入說話者的名字 (例如: 約翰, 教練)：")

    except (TelegramAPIError, AnkiConnectError, IOError) as e:
        logger.error("處理圖片失敗: %s", e)
        await status_msg.edit_text(f"❌ 處理圖片失敗: {str(e)[:200]}")
