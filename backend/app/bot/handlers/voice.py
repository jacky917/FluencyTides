"""Telegram Bot voice message handler module (Workflow B).

Telegram Bot 語音訊息處理模組 (Workflow B)。

Receives user voice messages and checks the state in UserStateManager to decide whether this is the Recording flow,
then runs the full pipeline:

1. Download the .ogg voice file
2. Let AudioEvaluator transcribe and score the audio
3. Write back to Anki through the service layer's ``add_recording`` action (which finds the speaking-coach or
   trilingual card handler by note type)
4. Show progress and results
5. Clear the user state

負責接收使用者的語音訊息，根據 UserStateManager 中的狀態判斷是否為 Recording 流程，並執行以下完整管線：

1. 下載 .ogg 語音檔
2. 交由 AudioEvaluator 進行語音辨識與評分
3. 透過服務層的 ``add_recording`` 動作寫回 Anki（依筆記類型找對話卡或三語卡的 handler）
4. 顯示進度與結果
5. 清除使用者狀態

In the ``add_audio`` state (uploading an existing recording) the voice file is only stored in Anki Media, and the flow
moves on to avatar selection without scoring.

``add_audio`` 狀態（上傳現成錄音）只把音檔存進 Anki Media，不評分，接著進入選頭像流程。

Phase 9 update: the removed ``card_service.process_voice_evaluation()`` is no longer called. The write-back now goes
through ``NoteService.run_action`` instead of calling a HandlerRegistry handler directly.

Phase 9 更新：不再呼叫已移除的 ``card_service.process_voice_evaluation()``。寫回現在經由
``NoteService.run_action``，不再直接呼叫 HandlerRegistry 的 handler。

The card's handler is found by note type (``handler_registry.handler_for_note_type``), and everything about the
evaluation comes from its ``RECORDING_EVAL`` declaration: the route key that decides the strategy and models, and,
for the field being recorded, the prompt, the reference answers, the target language, and the extra parameters of
``add_recording`` (docs/wip/recording_eval_settings_FEAT_2026-11-26.md D4). A card whose handler declares nothing
cannot be recorded. The evaluator is obtained for every message from the current settings (cached per setting
combination), so a settings change (TG ``/setconfig`` or the web tool settings) takes effect on the next voice
message. Each saved recording also records when and how it was
scored (D5).

卡片的 handler 依筆記類型找到（``handler_registry.handler_for_note_type``），評分的一切都來自它的
``RECORDING_EVAL`` 宣告：決定評分策略與模型的用途鍵，以及這個欄位的提示詞、參考答案、目標語言與
``add_recording`` 要另外帶的參數（docs/wip/recording_eval_settings_FEAT_2026-11-26.md D4）。handler 沒有宣告的
卡片不能錄音評分。評分器每則語音都依目前的設定取得（依設定組合快取），改設定（TG ``/setconfig``、網頁工具設定）後下一則語音即生效。
每筆存下的錄音也記下評分的時間與方式（D5）。
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
from app.infrastructure.audio_evaluator.factory import evaluator_for
from app.infrastructure.llm.routing import ResolvedRoute
from app.schemas.speaking import RecordingEvaluationInfo, RecordingItem
from app.services.task_handlers.registry import handler_registry
from app.services.task_handlers.shared.recording_spec import RecordingTarget
from app.services.task_handlers.shared.trilingual_familiarity import familiarity_or_none
from app.services.note_service import NoteService

logger = logging.getLogger(__name__)

router = Router(name="voice_router")

# 清掉狀態的失敗之後，重新進入的方式
_RETRY_FROM_ANKI = "請回 Anki 卡片重新點按鈕。"
_NOT_RECORDABLE = "❌ 這種卡片不支援錄音評分。"


def _layer_of(resolved: ResolvedRoute) -> str:
    """Return the settings layer that took effect: the route key, the global key, or ``.env``.

    回傳生效的設定層：用途鍵、全域鍵，或 ``.env``。

    While the global's switch is on, ``（全局生效）`` is appended, so a later reader can tell "the global (or
    ``.env``) because nobody set this card type" from "because the switch overrode the card type's own setting".

    全域的開關開著時另加「（全局生效）」：事後才分得出「這種卡片沒有自己設定，所以用全域（或 ``.env``）」與
    「開關蓋過了這種卡片自己的設定」。
    """
    if resolved.source == "route":
        return resolved.route_key
    layer = resolved.global_key if resolved.source == "global" else ".env"
    return f"{layer}（全局生效）" if resolved.forced else layer


def utc_timestamp(moment: datetime) -> str:
    """Return a timezone-aware time as UTC ISO 8601 such as ``2026-11-26T06:03:12Z``.

    把帶時區的時間轉成 UTC 的 ISO 8601，例如 ``2026-11-26T06:03:12Z``。

    Args:
        moment: the time, timezone-aware
            時間，帶時區

    Returns:
        the UTC timestamp, to the second
        UTC 時間字串，精確到秒
    """
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def evaluation_metadata(plan: EvaluatorPlan) -> dict[str, object]:
    """Return what a saved recording records about how it was scored (the time is added when saving).

    回傳存下的錄音要記錄的評分資訊（時間在存檔時另外加）。

    The strategy, the model of every capability the strategy used (``(endpoint)model``, or only the endpoint when
    the capability has no model), and which settings layer took effect for the strategy itself (``audio_eval``)
    and for each of those capabilities, so scores from different settings can be told apart later
    (recording_eval_settings_FEAT_2026-11-26.md D5).

    評分策略、策略用到的各能力的模型（``(端點)模型``；能力沒有模型時只寫端點），以及策略本身（``audio_eval``）
    與這些能力各自生效的設定層，事後才分得出不同設定評出的分數（recording_eval_settings_FEAT_2026-11-26.md D5）。

    Args:
        plan: the evaluator plan the evaluator was built from
            建出評分器的計畫

    Returns:
        ``strategy``, ``models``, and ``settings``
        ``strategy``、``models`` 與 ``settings``

    Raises:
        ValidationError: if the fields do not fit the stored format (``RecordingEvaluationInfo``)
            若欄位不符合存進卡片的格式（``RecordingEvaluationInfo``）
    """
    settings_layers = {"audio_eval": _layer_of(plan.strategy)}
    models: dict[str, str] = {}
    for capability, resolved in sorted(plan.routes.items()):
        models[capability] = f"({resolved.endpoint}){resolved.model}" if resolved.model else resolved.endpoint
        settings_layers[capability] = _layer_of(resolved)
    # 這裡就以存進卡片的格式檢查：不符時在上傳音檔之前就停下（呼叫端接住並說明）
    return RecordingEvaluationInfo(
        strategy=plan.strategy_name, models=models, settings=settings_layers
    ).model_dump()


@router.message(F.voice)
async def process_voice_handler(
    message: Message,
    anki_client: AnkiClient,
    note_service: NoteService,
    user_state_manager: UserStateManager,
) -> None:
    """Handle voice messages sent by the user.

    處理使用者發送的語音訊息。

    Runs the full evaluation pipeline only when the user is in the ``recording`` state. In the ``add_audio`` state
    the voice file is uploaded to Anki Media and the user moves on to avatar selection. In any other state (or with
    no state), it prompts the user to start again from the button on the Anki card.

    僅在使用者處於 ``recording`` 狀態時執行完整的評分管線。``add_audio`` 狀態只把音檔上傳到 Anki Media，
    接著進入選頭像。其他狀態（或沒有狀態）時，提示使用者回 Anki 卡片重新點按鈕。

    A download failure or an unusable evaluator keeps the state, so the user can resend within the time limit; the
    other failures clear the state and point back to the Anki card.

    下載失敗或評分器無法使用時保留狀態，使用者可在時限內直接重傳；其他失敗會清除狀態，並請使用者回 Anki 卡片重新進入。

    Args:
        message: the Telegram voice message
            Telegram 語音訊息物件
        anki_client: the injected AnkiConnect client
            注入的 AnkiClient 實例
        note_service: the injected note service; the recording entry is written back and synced through it
            注入的筆記服務；錄音紀錄經它寫回並同步
        user_state_manager: the injected user state manager
            注入的 UserStateManager 實例
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
    except AnkiConnectError as e:
        logger.error("讀取卡片資訊失敗: %s", e)
        await status_msg.edit_text(f"❌ 讀取卡片失敗：{error_detail(e)}\n{_RETRY_FROM_ANKI}")
        user_state_manager.clear_state(chat_id)
        return

    # ── Step 2.1: 錄音評分依卡片的 handler 宣告（RECORDING_EVAL）決定怎麼評 ──
    target: RecordingTarget | None = None
    route_key = ""
    if state.action == "recording":
        state_field_name = str(state.extra.get("field_name", "") or "")
        handler = handler_registry.handler_for_note_type(note_info.modelName)
        if handler is None:
            # 卡面的錄音按鈕帶人進來，卻找不到 handler：筆記類型改了名，或 handler 沒有註冊——是設定或程式錯誤
            logger.error("卡片 %s 的筆記類型 %r 沒有對應的 handler，無法錄音評分", card_id, note_info.modelName)
            await status_msg.edit_text(
                f"❌ 後端不認得這種卡片（筆記類型 {html.escape(note_info.modelName)}），無法錄音評分。"
            )
            user_state_manager.clear_state(chat_id)
            return
        spec = handler.RECORDING_EVAL
        if spec is None:
            logger.error("handler %s 沒有宣告 RECORDING_EVAL，卡片 %s 無法錄音評分", handler.handler_name, card_id)
            await status_msg.edit_text(_NOT_RECORDABLE)
            user_state_manager.clear_state(chat_id)
            return
        try:
            target = spec.target_for(state_field_name, fields)
        except Exception:
            # target_for 是 handler 提供的回調：出錯時不能讓狀態訊息停在「讀取卡片中…」，也不要留著錄音狀態
            logger.exception(
                "handler %s 解析錄音欄位 %r 失敗（卡片 %s）", handler.handler_name, state_field_name, card_id
            )
            await status_msg.edit_text(f"❌ 讀取這張卡片的錄音設定失敗（詳見伺服器日誌）。\n{_RETRY_FROM_ANKI}")
            user_state_manager.clear_state(chat_id)
            return
        if target is None:
            logger.warning(
                "卡片 %s（%s）的欄位 %r 不能錄音評分", card_id, handler.handler_name, state_field_name
            )
            await status_msg.edit_text(f"❌ 欄位 {html.escape(state_field_name)} 不支援錄音評分。")
            user_state_manager.clear_state(chat_id)
            return
        route_key = spec.route.key
        ref_field_name = target.reference_field
        if ref_field_name not in fields:
            # 宣告的欄位不在筆記上是宣告錯誤：不能當成「沒有參考答案」照樣評分（stt_diff 會誤報、LLM 失去比對基準）
            logger.error(
                "handler %s 宣告的參考答案欄位 %r 不在卡片 %s（%s）上",
                handler.handler_name, ref_field_name, card_id, note_info.modelName,
            )
            await status_msg.edit_text("❌ 這種卡片的錄音評分設定有誤（找不到參考答案欄位），詳見伺服器日誌。")
            user_state_manager.clear_state(chat_id)
            return
        ref_json_str = str(fields[ref_field_name].get("value", "[]"))
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

    # ── Step 2.5: 錄音評分先取得評分器（在上傳音檔之前）──
    # 設定有問題時在這裡就停下：不會先把音檔存進 Anki、留下沒有紀錄引用的檔案（重傳時也不會越存越多）
    # 評分器依 handler 宣告的用途鍵建立（各卡片類型可設不同策略，語音全域的開關打開時整組用全域）
    audio_evaluator: BaseAudioEvaluator | None = None
    plan: EvaluatorPlan | None = None
    eval_meta: dict[str, object] = {}
    if state.action == "recording":
        try:
            audio_evaluator, plan = await evaluator_for(route_key)
        except AudioEvaluatorUnavailableError as e:
            # 狀態沒清（仍在錄音模式）：改好設定後可在時限內直接重傳
            logger.error("錄音評分器無法建立（%s）：%s", route_key, e.message)
            await status_msg.edit_text(
                f"❌ 語音評分服務無法使用，這次沒有評分：{html.escape(e.message)}\n"
                "改好設定後，在錄音模式內重傳語音即可。"
            )
            return
        except Exception as e:
            # 讀設定或建評分器時的非預期錯誤：不能讓狀態訊息停在「讀取卡片中…」沒有下文
            logger.exception("建立錄音評分器時發生未處理的異常（%s）: %s", route_key, e)
            await status_msg.edit_text("❌ 語音評分服務發生錯誤（詳見伺服器日誌），這次沒有評分。請稍後在錄音模式內重傳語音。")
            return
        # 評分資訊在這裡就組好：出錯時還沒上傳音檔，也不會在評分完成後被說成「寫回 Anki 失敗」。
        # 和建評分器分開接：這裡出錯是程式錯誤，每次都會重現，重傳沒用，所以清掉狀態
        try:
            eval_meta = evaluation_metadata(plan)
        except Exception:
            logger.exception("組錄音評分資訊失敗（%s，策略 %s）", route_key, plan.strategy_name)
            await status_msg.edit_text(f"❌ 錄音評分發生程式錯誤（詳見伺服器日誌），這次沒有評分。\n{_RETRY_FROM_ANKI}")
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
        await status_msg.edit_text(f"❌ 上傳音檔到 Anki 失敗：{error_detail(e)}\n{_RETRY_FROM_ANKI}")
        user_state_manager.clear_state(chat_id)
        return

    # ── Step 4: 分支處理 ──
    if state.action == "recording":
        assert audio_evaluator is not None and plan is not None and target is not None  # Step 2.1、2.5 已取得
        await status_msg.edit_text("⏳ 評分中…")

        try:
            result = await audio_evaluator.evaluate_audio(
                audio_data=audio_data,
                audio_filename=audio_filename,
                prompt_text=prompt_text,
                context_text=context_text,
                reference_answers=reference_answers,
                target_language=target.target_language,
                template_name=target.template,
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
            evaluated_at = datetime.now(tz=timezone.utc)
            # 經 RecordingItem 產生：卡片上的錄音格式只由這個模型定義（D5）
            new_recording = RecordingItem.model_validate({
                "date": evaluated_at.strftime("%Y-%m-%d"),
                "audio": audio_filename,
                "transcript": result.transcript,
                # stt_diff 提供 Anki 專用的紅綠標記版；其餘 provider 沿用 feedback
                "comment": result.feedback_anki_html or result.feedback,
                "score": result.score,
                # 這次怎麼評的（D5）：設定之後可能切換，事後才分得出不同設定評出的分數
                "evaluated_at": utc_timestamp(evaluated_at),
                **eval_meta,
            }).model_dump()
            action_params = {**target.action_params, "recording": new_recording}
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
