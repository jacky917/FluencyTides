"""
Telegram Bot 語音訊息處理模組 (Workflow B)。

負責接收使用者的語音訊息，根據 UserStateManager 中的狀態判斷
是否為 Recording 流程，並執行以下完整管線：
1. 下載 .ogg 語音檔
2. 交由 AudioEvaluator 進行語音辨識與評分
3. 透過 SpeakingCoachHandler.execute_update 寫回 Anki
4. 顯示進度與結果
5. 清除使用者狀態

Phase 9 更新：
- 不再呼叫已移除的 card_service.process_voice_evaluation()。
- 改為直接使用 AudioEvaluator + HandlerRegistry 的 speaking_coach handler。

Telegram Bot voice message handler module (Workflow B).

Receives user voice messages, checks the UserStateManager state to decide
whether this is the Recording flow, and runs the full pipeline:
1. Download the .ogg voice file
2. Let AudioEvaluator transcribe and score the audio
3. Write back to Anki via SpeakingCoachHandler.execute_update
4. Show progress and results
5. Clear the user state

Phase 9 update:
- No longer calls the removed card_service.process_voice_evaluation().
- Uses AudioEvaluator plus the speaking_coach handler from HandlerRegistry
  directly instead.
"""

import base64
import html
import json
import logging
from datetime import datetime, timezone
from io import BytesIO

from aiogram import F, Router
from aiogram.types import Message
from aiogram.exceptions import TelegramAPIError
from app.core.exceptions import FluencyTidesError

from app.bot.state import UserStateManager
from app.core.exceptions import AnkiFieldCorruptedError
from app.infrastructure.anki.client import AnkiClient, AnkiConnectError
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager
from app.infrastructure.audio_evaluator.base import BaseAudioEvaluator
from app.services.card_service import CardService
from app.services.relation_service import RelationService
from app.services.task_handlers.registry import HandlerRegistry

logger = logging.getLogger(__name__)

router = Router(name="voice_router")


@router.message(F.voice)
async def process_voice_handler(
    message: Message,
    anki_client: AnkiClient,
    handler_registry: HandlerRegistry,
    user_state_manager: UserStateManager,
    card_service: CardService,
    relation_service: RelationService,
    audio_evaluator: BaseAudioEvaluator | None = None,
) -> None:
    """處理使用者發送的語音訊息。

    Handle voice messages sent by the user.

    僅在使用者處於 'recording' 狀態時執行完整的評分管線。
    若使用者不在任何狀態中，則提示使用者先透過 Anki 的 Deep Link 啟動錄音。

    Runs the full evaluation pipeline only when the user is in the
    'recording' state. If the user has no active state, prompts them to
    start recording via the Anki deep link first.

    Args:
        message: Telegram 語音訊息物件。The Telegram voice message object.
        anki_client: 注入的 AnkiClient 實例。Injected AnkiClient instance.
        handler_registry: 注入的 HandlerRegistry。Injected HandlerRegistry.
        user_state_manager: 注入的 UserStateManager 實例。
            Injected UserStateManager instance.
        card_service: 注入的 CardService。Injected CardService instance.
        relation_service: 注入的 RelationService。Injected RelationService.
        audio_evaluator: 注入的 Audio Evaluator 實例（由工廠模式建立）。
            Injected audio evaluator instance (created by the factory).
    """
    if not audio_evaluator:
        logger.warning("⚠️ 語音評分服務未初始化 (API Key 缺失)。依賴評分的任務將會失敗。")

    chat_id = message.chat.id
    state = user_state_manager.get_state(chat_id)

    if not state or state.action not in ("recording", "add_audio"):
        await message.reply(
            "❓ 目前沒有進行中的錄音任務（或任務已超時過期）。\n\n"
            "請重新在 Anki 卡片上點擊按鈕，跳轉到此 Bot 後再發送語音。"
        )
        return

    card_id = state.card_id
    status_msg = await message.reply(
        "🔄 <b>處理中...</b>\n\n"
        "正在下載語音..."
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
    except TelegramAPIError as e:
        logger.error("語音下載失敗: %s", e)
        await status_msg.edit_text(f"❌ 語音下載失敗: {e}")
        return

    # ── Step 2: 查詢卡片以取得資訊 ──
    await status_msg.edit_text("🔄 <b>處理中...</b>\n\n正在讀取卡片資訊...")

    try:
        # 取前同步 (有防抖)
        await anki_client.sync(raise_errors=False)

        note_ids = await anki_client.find_notes(f"Card_ID:{card_id}")
        if not note_ids:
            await status_msg.edit_text(f"❌ 找不到 Card ID 為 <code>{card_id}</code> 的卡片。")
            user_state_manager.clear_state(chat_id)
            return

        notes_info = await anki_client.get_notes_info(notes=note_ids[:1])
        note_info = notes_info[0]
        note_id = note_info.noteId
        fields = note_info.fields

        prompt_text = str(fields.get("Prompt", {}).get("value", ""))
        context_text = str(fields.get("Context", {}).get("value", ""))

        # 三語卡分流：目標欄位帶語言後綴 (Recordings_JA 等) 時，
        # 語言/提示詞樣板/References 欄位由後綴決定 (無 Target_Language 欄位)
        from app.services.task_handlers.shared.trilingual_lang import (
            LANG_TEMPLATE_MAP,
            LANG_TO_LOCALE,
            lang_from_field,
            references_field,
        )

        state_field_name = str(state.extra.get("field_name", "") or "")
        trilingual_lang = lang_from_field(state_field_name)
        if trilingual_lang:
            target_language = LANG_TO_LOCALE[trilingual_lang]
            eval_template = f"prompts/anki/{LANG_TEMPLATE_MAP[trilingual_lang]}"
            ref_json_str = str(
                fields.get(references_field(trilingual_lang), {}).get("value", "[]")
            )
        else:
            # Speaking_Coach_Dark 既有路徑：讀 Target_Language 欄位與共用樣板
            target_language = str(fields.get("Target_Language", {}).get("value", ""))
            eval_template = "prompts/audio_evaluator.j2"
            ref_json_str = str(fields.get("References", {}).get("value", "[]"))
        # 以共用解析器讀取（S065）：References 欄位可能是未轉義的原始 JSON
        # （匯入腳本直寫），也可能是 HTML 轉義過的（經 AnkiJsonFieldManager
        # 寫入）。直接 json.loads 只對前者成立，後者會被 except 吞掉而導致
        # 參考範本靜默變成空清單——stt_diff 會誤報「此卡片沒有參考答案」，
        # LLM 評分則會失去比對基準。
        # Use the shared parser (S065): the References field may be raw JSON
        # (written directly by import scripts) or HTML-escaped (written via
        # AnkiJsonFieldManager). A bare json.loads only works for the former;
        # for the latter the except branch silently yields an empty list, so
        # stt_diff would wrongly report "no reference answers" and the LLM
        # evaluators would lose their comparison baseline.
        try:
            ref_list = AnkiJsonFieldManager.parse_field_string(ref_json_str)
            reference_answers = [
                str(r.get("content", ""))
                for r in ref_list
                if isinstance(r, dict) and r.get("status", 1) == 1
            ]
        except (AnkiFieldCorruptedError, TypeError):
            logger.warning("References 欄位解析失敗，本次評分將不帶參考範本。")
            reference_answers = []
    except AnkiConnectError as e:
        logger.error("讀取卡片資訊失敗: %s", e)
        await status_msg.edit_text(f"❌ 讀取卡片資訊失敗: {e}")
        user_state_manager.clear_state(chat_id)
        return

    # ── Step 3: 共用邏輯：上傳音檔至 Anki Media ──
    try:
        audio_base64 = base64.b64encode(audio_data).decode("utf-8")
        from app.schemas.anki import AnkiStoreMediaParams
        await anki_client.store_media_file(params=AnkiStoreMediaParams(filename=audio_filename, data=audio_base64))
        logger.info(f"✅ 已上傳 {audio_filename} 至 Anki Media")
    except AnkiConnectError as e:
        logger.error("上傳音檔失敗: %s", e)
        await status_msg.edit_text(f"❌ 上傳音檔至 Anki 失敗: {e}")
        user_state_manager.clear_state(chat_id)
        return

    # ── Step 4: 分支處理 ──
    if state.action == "recording":
        if not audio_evaluator:
            await status_msg.edit_text("⚠️ 語音評分服務目前不可用（尚未成功初始化）。\n請聯絡系統管理員檢查 API Key 設定。")
            return

        await status_msg.edit_text("🔄 <b>處理中...</b>\n\nAI 正在分析語音...")

        try:
            result = await audio_evaluator.evaluate_audio(
                audio_data=audio_data,
                audio_filename=audio_filename,
                prompt_text=prompt_text,
                context_text=context_text,
                reference_answers=reference_answers,
                target_language=target_language if target_language else None,
                template_name=eval_template,
            )
        except FluencyTidesError as e:
            logger.error("AI 評分失敗: %s", e)
            error_str = str(e)
            if "503" in error_str or "UNAVAILABLE" in error_str or "high demand" in error_str:
                user_msg = "目前 AI 伺服器忙碌中（高負載），請稍後再試。🙇‍♂️"
            elif "429" in error_str or "quota" in error_str.lower():
                user_msg = "AI 服務的請求額度已達上限，請稍後再試。"
            else:
                user_msg = "發生未預期的系統錯誤，請稍後再試或聯絡管理員。"
                
            await status_msg.edit_text(f"❌ <b>AI 評分失敗</b>\n\n{user_msg}")
            user_state_manager.clear_state(chat_id)
            return
        except Exception as e:
            logger.exception("AI 評分發生未處理的異常: %s", e)
            await status_msg.edit_text("❌ <b>AI 評分失敗</b>\n\n發生未預期的系統異常，請稍後再試或聯絡管理員。")
            user_state_manager.clear_state(chat_id)
            return

        await status_msg.edit_text("🔄 <b>處理中...</b>\n\n正在寫回 Anki...")

        try:
            new_recording = {
                "date": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"),
                "audio": audio_filename,
                "transcript": result.transcript,
                # stt_diff 提供 Anki 專用的紅綠標記版；其餘 provider 沿用 feedback
                "comment": result.feedback_anki_html or result.feedback,
                "score": result.score,
            }
            if trilingual_lang:
                speaking_handler = handler_registry.get_handler("speaking_trilingual")
                update_params = {
                    "action": "add_recording",
                    "lang": trilingual_lang,
                    "recording": new_recording,
                }
            else:
                speaking_handler = handler_registry.get_handler("speaking_coach")
                update_params = {
                    "action": "add_recording",
                    "recording": new_recording,
                }
            await speaking_handler.execute_update(
                card_service,
                relation_service,
                note_id,
                update_params,
            )
        except FluencyTidesError as e:
            logger.error("寫回 Anki 失敗: %s", e)
            await status_msg.edit_text("❌ <b>寫回 Anki 失敗</b>\n\n發生未預期的系統錯誤，請聯絡管理員。")
            user_state_manager.clear_state(chat_id)
            return
        except Exception as e:
            logger.exception("寫回 Anki 發生未處理的異常: %s", e)
            await status_msg.edit_text("❌ <b>寫回 Anki 失敗</b>\n\n發生未預期的系統異常，請聯絡管理員。")
            user_state_manager.clear_state(chat_id)
            return

        user_state_manager.clear_state(chat_id)
        
        score = result.score
        score_emoji = "🟢" if score >= 90 else ("🟡" if score >= 60 else "🔴")

        # 嘗試觸發同步
        from app.bot.handlers.callbacks import _sync_with_warning
        sync_warning = await _sync_with_warning(anki_client)

        # feedback_anki_html 存在時，feedback 為 evaluator 自行組裝的 TG 安全
        # 標記（stt_diff 差異），不可再轉義也不可截斷（避免切斷標籤）；
        # 其餘 provider 的 feedback 為 LLM 純文字，先截斷再轉義。
        safe_transcript = html.escape(result.transcript[:300])
        if result.feedback_anki_html is not None:
            safe_feedback = result.feedback
        else:
            safe_feedback = html.escape(result.feedback[:500])

        # 各 evaluator 自行回報的模式/模型標籤；缺省時退回目前設定值
        # Mode/model label self-reported by the evaluator; falls back to
        # the current setting when absent.
        from app.core.config import settings as _settings
        evaluator_label = html.escape(
            result.evaluator_label or _settings.AUDIO_EVALUATOR_PROVIDER
        )

        await status_msg.edit_text(
            f"✅ <b>錄音評分完成！</b>\n\n"
            f"🎯 卡片：<code>{card_id}</code>\n"
            f"⚙️ 模式：<code>{evaluator_label}</code>\n"
            f"{score_emoji} 分數：<b>{score}</b> / 100\n\n"
            f"📝 <b>逐字稿</b>\n"
            f"<i>{safe_transcript}</i>\n\n"
            f"💬 <b>AI 評語</b>\n"
            f"{safe_feedback}\n\n"
            f"<i>結果已自動寫入本地 Anki！🎉</i>{sync_warning}"
        )

    elif state.action == "add_audio":
        await status_msg.edit_text("🔄 <b>處理中...</b>\n\n正在上傳音檔...")
        # 音檔已經在上方的共用邏輯中傳至 Anki
        # 這裡只需更新狀態並進入頭像選擇流程
        state.extra["audio_filename"] = audio_filename
        state.action = "wait_avatar_selection"
        state.expires_at = None # 重置超時時間
        user_state_manager.set_state(chat_id, state)

        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📂 選擇系統頭像", callback_data="avatar_sys")],
            [InlineKeyboardButton(text="🖼️ 上傳自訂圖片", callback_data="avatar_upload")],
            [InlineKeyboardButton(text="⏭️ 跳過 (不使用圖片)", callback_data="avatar_skip")]
        ])

        await status_msg.edit_text(
            f"✅ <b>語音接收成功！</b>\n\n"
            f"接下來，請選擇要為這段語音搭配的頭像：",
            reply_markup=keyboard
        )
