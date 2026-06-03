"""
Telegram Bot 語音訊息處理模組 (Workflow B)。

負責接收使用者的語音訊息，根據 UserStateManager 中的狀態判斷
是否為 Recording 流程，並執行以下完整管線：
1. 下載 .ogg 語音檔
2. 交由 CardService 執行評估與寫回 Anki
3. 顯示進度與結果
4. 清除使用者狀態

設計決策：
- 使用進度訊息 (status_msg) 實時更新處理狀態，提升使用者體驗。
- Controller (此模組) 僅負責 Telegram 介面與進度顯示，真正的業務邏輯（與 LLM、Anki 互動）由 CardService 處理。
"""

import logging
from datetime import datetime, timezone
from io import BytesIO

from aiogram import F, Router
from aiogram.types import Message

from app.bot.state import UserStateManager
from app.infrastructure.audio_evaluator.base import BaseAudioEvaluator
from app.services.card_service import CardService

logger = logging.getLogger(__name__)

router = Router(name="voice_router")


@router.message(F.voice)
async def process_voice_handler(
    message: Message,
    card_service: CardService,
    user_state_manager: UserStateManager,
    audio_evaluator: BaseAudioEvaluator,
) -> None:
    """處理使用者發送的語音訊息。

    僅在使用者處於 'recording' 狀態時執行完整的評分管線。
    若使用者不在任何狀態中，則提示使用者先透過 Anki 的 Deep Link 啟動錄音。

    Args:
        message: Telegram 語音訊息物件。
        card_service: 注入的 CardService 實例。
        user_state_manager: 注入的 UserStateManager 實例。
        audio_evaluator: 注入的 Audio Evaluator 實例（由工廠模式建立）。
    """
    chat_id = message.chat.id
    state = user_state_manager.get_state(chat_id)

    if not state or state.action != "recording":
        await message.reply(
            "❓ 目前沒有進行中的錄音任務（或任務已超時過期）。\n\n"
            "請重新在 Anki 卡片上點擊 🎤 <b>提交新錄音</b> 按鈕，"
            "跳轉到此 Bot 後再發送語音。"
        )
        return

    card_id = state.card_id
    status_msg = await message.reply(
        f"🔄 <b>處理中...</b>\n\n"
        f"步驟 1/4: 正在下載語音..."
    )

    # ── Step 1: 下載語音檔 ──
    try:
        voice = message.voice
        if not voice:
            await status_msg.edit_text("❌ 無法讀取語音訊息。")
            return

        bot = message.bot
        if not bot:
            await status_msg.edit_text("❌ Bot 實例不可用。")
            return

        file = await bot.get_file(voice.file_id)
        if not file or not file.file_path:
            await status_msg.edit_text("❌ 無法取得語音檔案路徑。")
            return

        audio_buffer = BytesIO()
        await bot.download_file(file.file_path, audio_buffer)
        audio_data = audio_buffer.getvalue()
        audio_filename = f"rec_{card_id}_{datetime.now(tz=timezone.utc).strftime('%Y%m%d_%H%M%S')}.ogg"

        logger.info(
            "語音下載完成: %s, 大小: %d bytes",
            audio_filename,
            len(audio_data),
        )
    except Exception as e:
        logger.error("語音下載失敗: %s", e)
        await status_msg.edit_text(f"❌ 語音下載失敗: {e}")
        return

    # ── Step 2-4: 交由 CardService 處理 ──
    async def update_progress(msg: str) -> None:
        await status_msg.edit_text(f"🔄 <b>處理中...</b>\n\n{msg}")

    try:
        result = await card_service.process_voice_evaluation(
            card_id=card_id,
            audio_data=audio_data,
            audio_filename=audio_filename,
            audio_evaluator=audio_evaluator,
            progress_callback=update_progress,
        )
    except Exception as e:
        logger.error("AI 評分或寫回 Anki 失敗: %s", e)
        await status_msg.edit_text(
            f"❌ <b>處理失敗</b>\n\n"
            f"錯誤: {str(e)[:200]}\n"
        )
        user_state_manager.clear_state(chat_id)
        return

    # ── 完成：發送結果 ──
    user_state_manager.clear_state(chat_id)

    score = int(result["score"])
    status_code = int(result["status_code"])
    transcript = str(result["transcript"])
    feedback = str(result["feedback"])

    score_emoji = "🟢" if score >= 90 else ("🟡" if score >= 60 else "🔴")
    status_labels = {0: "✨ 完美吻合", 1: "⚠️ 有待改善", 2: "💙 獨特表達"}
    status_label = status_labels.get(status_code, "")

    await status_msg.edit_text(
        f"✅ <b>錄音評分完成！</b>\n\n"
        f"🎯 卡片：<code>{card_id}</code>\n"
        f"{score_emoji} 分數：<b>{score}</b> / 100\n"
        f"📊 狀態：{status_label}\n\n"
        f"📝 <b>逐字稿</b>\n"
        f"<i>{transcript[:300]}</i>\n\n"
        f"💬 <b>AI 評語</b>\n"
        f"{feedback[:500]}\n\n"
        f"<i>結果已自動寫回 Anki！🎉</i>"
    )
