"""
Telegram Bot 語音訊息處理模組 (Workflow B)。

負責接收使用者的語音訊息，根據 UserStateManager 中的狀態判斷
是否為 Recording 流程，並執行以下完整管線：
1. 下載 .ogg 語音檔
2. 交由 AudioEvaluator 進行語音辨識與評分
3. 透過服務層的 ``add_recording`` 動作寫回 Anki（依筆記類型找對話卡或三語卡的 handler）
4. 顯示進度與結果
5. 清除使用者狀態

Phase 9 更新：
- 不再呼叫已移除的 card_service.process_voice_evaluation()。
- 改為直接使用 AudioEvaluator + HandlerRegistry 的 speaking_coach handler。

能力路由 R2：評分器每次依卡片的 handler 從能力路由取得（``recording_eval`` 或
``recording_eval.<handler>``，見 :mod:`app.services.recording_eval`），不再注入全域的
``audio_evaluator``；改設定（TG ``/setconfig``、網頁工具設定）後下一則語音即生效。

Telegram Bot voice message handler module (Workflow B).

Receives user voice messages, checks the UserStateManager state to decide
whether this is the Recording flow, and runs the full pipeline:
1. Download the .ogg voice file
2. Let AudioEvaluator transcribe and score the audio
3. Write back to Anki through the service layer's ``add_recording`` action
4. Show progress and results
5. Clear the user state

Phase 9 update:
- No longer calls the removed card_service.process_voice_evaluation().
- Uses AudioEvaluator plus the speaking_coach handler from HandlerRegistry
  directly instead.

Capability routing R2: the evaluator is obtained per message from the
capability routes of the card's handler instead of a global singleton.
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
from app.infrastructure.anki.search import field_term
from app.core.exceptions import FluencyTidesError

from app.bot.state import UserStateManager
from app.bot.utils.texts import CARD_NOT_FOUND, error_detail, state_minutes, sync_suffix
from app.core.exceptions import AnkiFieldCorruptedError, AudioEvaluatorUnavailableError
from app.infrastructure.anki.client import AnkiClient, AnkiConnectError
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager
from app.infrastructure.audio_evaluator.base import AudioNotScoredError, BaseAudioEvaluator
from app.infrastructure.audio_evaluator.factory import EvaluatorPlan
from app.services import recording_eval
from app.services.task_handlers.shared.trilingual_familiarity import familiarity_or_none
from app.services.note_service import NoteService

logger = logging.getLogger(__name__)

router = Router(name="voice_router")

# 清掉狀態的失敗之後，重新進入的方式
_RETRY_FROM_ANKI = "請回 Anki 卡片重新點按鈕。"


@router.message(F.voice)
async def process_voice_handler(
    message: Message,
    anki_client: AnkiClient,
    note_service: NoteService,
    user_state_manager: UserStateManager,
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
        note_service: 注入的筆記服務；錄音紀錄經它寫回並同步。Injected NoteService.
        user_state_manager: 注入的 UserStateManager 實例。
            Injected UserStateManager instance.
    """
    chat_id = message.chat.id
    state = user_state_manager.get_state(chat_id)

    if not state or state.action not in ("recording", "add_audio"):
        await message.reply(
            f"⌛ 沒有等待中的錄音（或已超過 {state_minutes()} 分鐘）。請回 Anki 卡片重新點按鈕。"
        )
        return

    card_id = state.card_id
    status_msg = await message.reply("⏳ 下載語音中…")

    # ── Step 1: 下載語音檔 ──
    try:
        voice = message.voice
        # 這一步失敗時狀態沒清（仍在等語音），可以直接再傳
        if not voice:
            await status_msg.edit_text("❌ 語音下載失敗，請再傳一次。")
            return

        bot = message.bot
        if not bot:
            await status_msg.edit_text("❌ 系統錯誤，請稍後再試。")
            return

        file = await bot.get_file(voice.file_id)
        if not file or not file.file_path:
            await status_msg.edit_text("❌ 語音下載失敗，請再傳一次。")
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
        await status_msg.edit_text(f"❌ 語音下載失敗：{error_detail(e)}\n請再傳一次。")
        return

    # ── Step 2: 查詢卡片以取得資訊 ──
    await status_msg.edit_text("⏳ 讀取卡片中…")

    try:
        # 取前同步 (有防抖)
        await anki_client.sync(raise_errors=False)

        note_ids = await anki_client.find_notes(field_term("Card_ID", card_id))
        if not note_ids:
            await status_msg.edit_text(CARD_NOT_FOUND)
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
            ref_field_name = references_field(trilingual_lang)
            ref_json_str = str(fields.get(ref_field_name, {}).get("value", "[]"))
        else:
            # Speaking_Coach_Dark 既有路徑：讀 Target_Language 欄位與共用樣板
            target_language = str(fields.get("Target_Language", {}).get("value", ""))
            eval_template = "prompts/audio_evaluator.j2"
            ref_field_name = "References"
            ref_json_str = str(fields.get(ref_field_name, {}).get("value", "[]"))
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
        except AnkiFieldCorruptedError as e:
            logger.warning(
                "卡片 %s 的 %s 欄位解析失敗，本次評分不帶參考範本：%s", card_id, ref_field_name, e.message
            )
            ref_list = []
        active_refs = [r for r in ref_list if isinstance(r, dict) and r.get("status", 1) == 1]
        reference_answers = [str(r.get("content", "")) for r in active_refs]
        # 三語卡每句參考答案可帶熟識度（0–9），與文字一一對應交給評分器（LLM 類放進提示詞，stt_diff 寫進
        # feedback）；對話卡的參考答案沒有這個鍵，一律為 None。不合格的值當成未標，但記下來：匯入會擋，
        # 讀到代表有人在 Anki 手動改壞了。
        # Familiarity per reference (None for speaking-coach cards); invalid values count as unrated but are logged.
        reference_familiarity = []
        for number, ref in enumerate(active_refs, 1):
            familiarity = familiarity_or_none(ref)
            if familiarity is None and ref.get("familiarity") not in (None, ""):
                logger.warning(
                    "卡片 %s 的 %s 第 %d 句（啟用中）熟識度 %r 不合格（應為 0–9 的整數），本次評分視為未標。",
                    card_id, ref_field_name, number, ref.get("familiarity"),
                )
            reference_familiarity.append(familiarity)
    except AnkiConnectError as e:
        logger.error("讀取卡片資訊失敗: %s", e)
        await status_msg.edit_text(f"❌ 讀取卡片失敗：{error_detail(e)}\n{_RETRY_FROM_ANKI}")
        user_state_manager.clear_state(chat_id)
        return

    # ── Step 2.5: 錄音評分先取得評分器（在上傳音檔之前）──
    # 設定有問題時在這裡就停下：不會先把音檔存進 Anki、留下沒有紀錄引用的檔案（重傳時也不會越存越多）
    # 三語卡與對話卡各用自己的 handler，評分器依 handler 的用途鍵建立（可設不同策略）
    handler_name = "speaking_trilingual" if trilingual_lang else "speaking_coach"
    audio_evaluator: BaseAudioEvaluator | None = None
    plan: EvaluatorPlan | None = None
    if state.action == "recording":
        try:
            audio_evaluator, plan = await recording_eval.evaluator_for_handler(handler_name)
        except AudioEvaluatorUnavailableError as e:
            # 狀態沒清（仍在錄音模式）：改好設定後可在時限內直接重傳
            logger.error("錄音評分器無法建立（%s）：%s", handler_name, e.message)
            await status_msg.edit_text(
                f"❌ 語音評分服務無法使用，這次沒有評分：{html.escape(e.message)}\n"
                "改好設定後，在錄音模式內重傳語音即可。"
            )
            return
        except Exception as e:
            # 讀設定或建評分器時的非預期錯誤：不能讓狀態訊息停在「讀取卡片中…」沒有下文
            logger.exception("建立錄音評分器時發生未處理的異常（%s）: %s", handler_name, e)
            await status_msg.edit_text("❌ 語音評分服務發生錯誤（詳見伺服器日誌），這次沒有評分。請稍後在錄音模式內重傳語音。")
            return

    # ── Step 3: 共用邏輯：上傳音檔至 Anki Media ──
    try:
        audio_base64 = base64.b64encode(audio_data).decode("utf-8")
        from app.schemas.anki import AnkiStoreMediaParams
        await anki_client.store_media_file(params=AnkiStoreMediaParams(filename=audio_filename, data=audio_base64))
        logger.info(f"✅ 已上傳 {audio_filename} 至 Anki Media")
    except AnkiConnectError as e:
        logger.error("上傳音檔失敗: %s", e)
        await status_msg.edit_text(f"❌ 上傳音檔到 Anki 失敗：{error_detail(e)}\n{_RETRY_FROM_ANKI}")
        user_state_manager.clear_state(chat_id)
        return

    # ── Step 4: 分支處理 ──
    if state.action == "recording":
        assert audio_evaluator is not None and plan is not None  # Step 2.5 已取得
        await status_msg.edit_text("⏳ 評分中…")

        try:
            result = await audio_evaluator.evaluate_audio(
                audio_data=audio_data,
                audio_filename=audio_filename,
                prompt_text=prompt_text,
                context_text=context_text,
                reference_answers=reference_answers,
                target_language=target_language if target_language else None,
                template_name=eval_template,
                reference_familiarity=reference_familiarity,
            )
        except AudioNotScoredError as e:
            # 不是 0 分而是沒有評分：不寫入紀錄（否則卡片上多一筆 0 分），照實說明並給重新進入的入口
            logger.info("錄音未評分（%s）：%s", card_id, e.reason)
            await status_msg.edit_text(f"❌ 這次沒有評分：{html.escape(e.reason)}\n{_RETRY_FROM_ANKI}")
            user_state_manager.clear_state(chat_id)
            return
        except FluencyTidesError as e:
            logger.error("AI 評分失敗: %s", e)
            error_str = str(e)
            # 三種情況都會清掉狀態：要說「回 Anki 重新點錄音按鈕」，只說「稍後再試」的話
            # 使用者再傳語音只會收到「沒有等待中的錄音」
            if "503" in error_str or "UNAVAILABLE" in error_str or "high demand" in error_str:
                user_msg = "❌ AI 評分服務忙碌，這次沒有評分。請稍後回 Anki 重新點錄音按鈕。"
            elif "429" in error_str or "quota" in error_str.lower():
                user_msg = "❌ AI 評分額度已用完，這次沒有評分。請稍後回 Anki 重新點錄音按鈕。"
            else:
                user_msg = "❌ AI 評分失敗（詳見伺服器日誌）。請回 Anki 重新點錄音按鈕。"

            await status_msg.edit_text(user_msg)
            user_state_manager.clear_state(chat_id)
            return
        except Exception as e:
            logger.exception("AI 評分發生未處理的異常: %s", e)
            await status_msg.edit_text("❌ AI 評分失敗（詳見伺服器日誌）。請回 Anki 重新點錄音按鈕。")
            user_state_manager.clear_state(chat_id)
            return

        await status_msg.edit_text("⏳ 寫入 Anki 中…")

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
                action_params = {"lang": trilingual_lang, "recording": new_recording}
            else:
                action_params = {"recording": new_recording}
            # 服務層依筆記類型找 handler，寫入後同步並回傳提醒
            sync_warning = sync_suffix(await note_service.run_action(note_id, "add_recording", action_params))
        except FluencyTidesError as e:
            logger.error("寫回 Anki 失敗: %s", e)
            await status_msg.edit_text(f"❌ 寫回 Anki 失敗：{error_detail(e)}\n{_RETRY_FROM_ANKI}")
            user_state_manager.clear_state(chat_id)
            return
        except Exception as e:
            logger.exception("寫回 Anki 發生未處理的異常: %s", e)
            await status_msg.edit_text(f"❌ 寫回 Anki 失敗（詳見伺服器日誌）。\n{_RETRY_FROM_ANKI}")
            user_state_manager.clear_state(chat_id)
            return

        user_state_manager.clear_state(chat_id)

        score = result.score
        score_emoji = "🟢" if score >= 90 else ("🟡" if score >= 60 else "🔴")

        # feedback_anki_html 存在時，feedback 為 evaluator 自行組裝的 TG 安全
        # 標記（stt_diff 差異），不可再轉義也不可截斷（避免切斷標籤）；
        # 其餘 provider 的 feedback 為 LLM 純文字，先截斷再轉義。
        safe_transcript = html.escape(result.transcript[:300])
        if result.feedback_anki_html is not None:
            safe_feedback = result.feedback
        else:
            safe_feedback = html.escape(result.feedback[:500])

        # 各 evaluator 自行回報的模式/模型標籤；缺省時退回評分策略名
        # Mode/model label self-reported by the evaluator; falls back to
        # the strategy name when absent.
        evaluator_label = html.escape(result.evaluator_label or plan.strategy_name)

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
        # 音檔已經在上方的共用邏輯中傳至 Anki，這裡只需更新狀態並進入頭像選擇流程
        state.extra["audio_filename"] = audio_filename
        state.action = "wait_avatar_selection"
        state.expires_at = None # 重置超時時間
        user_state_manager.set_state(chat_id, state)

        from app.bot.handlers.callbacks import AVATAR_PROMPT, avatar_keyboard

        await status_msg.edit_text(AVATAR_PROMPT, reply_markup=avatar_keyboard())
