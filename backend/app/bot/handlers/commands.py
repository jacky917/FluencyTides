"""
Telegram Bot 命令處理模組。

處理所有以 '/' 開頭的指令，涵蓋三大工作流：
- Workflow A: /newcard — 無狀態卡片新增
- Workflow B: /start rec_{Card_ID} — 啟動錄音評分流程
- Workflow C: /start del_{Section}_{Index}_{Card_ID} — 刪除特定 JSON 條目
- 基礎指令: /start, /help, /sync

Telegram Bot command handler module.

Handles all commands starting with '/', covering three main workflows:
- Workflow A: /newcard — stateless card creation
- Workflow B: /start rec_{Card_ID} — start the recording evaluation flow
- Workflow C: /start del_{Section}_{Index}_{Card_ID} — delete a JSON entry
- Basic commands: /start, /help, /sync
"""

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING
import pydantic
from app.core.exceptions import FluencyTidesError

if TYPE_CHECKING:
    from app.services.card_service import CardService

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from app.bot.state import UserState, UserStateManager
from app.bot.utils.deep_link_parser import DeepLinkParser
from app.bot.utils.formatting import anki_field_to_tg_text, escape_tg
from app.bot.utils.texts import (
    ANKI_UNREACHABLE,
    CARD_NOT_FOUND,
    error_detail,
    field_label,
    state_minutes,
)
from app.core.exceptions import AnkiFieldCorruptedError
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager
from app.schemas.tg.deep_link import (
    DeleteEntryAction,
    GenerateCardAction,
    RecordAudioAction,
    AddAudioAction,
    TTSAudioAction,
)
from app.core.config import settings
from app.infrastructure.anki.client import AnkiClient, AnkiConnectError
from app.schemas.anki import AnkiNote, AnkiNoteOptions
from app.schemas.speaking import NewCardPayload, ReferenceItem
from app.services.relation_service import RelationService
logger = logging.getLogger(__name__)

router = Router(name="commands_router")

# 刪除連結指向的那一筆已不存在（多半是卡片畫面還沒更新）
_ENTRY_GONE = "❌ 這筆資料已不存在（卡片畫面可能還沒更新），請在 Anki 同步後再看。"


@router.message(CommandStart())
async def command_start_handler(
    message: Message,
    anki_client: AnkiClient,
    user_state_manager: UserStateManager,
    card_service: "CardService",
) -> None:
    """處理 /start 指令與 Deep Link 邏輯。

    Handle the /start command and deep link logic.

    根據 payload 前綴分派到不同工作流：
    - rec-{FieldName}-{Index}-{Card_ID}: Workflow B 啟動錄音
    - del-{FieldName}-{Index}-{Card_ID}: Workflow C 刪除條目
    - addaudio-{FieldName}-{Index}-{Card_ID}: 上傳現成音檔
    - 無 payload: 一般歡迎訊息

    Dispatches to different workflows by payload prefix:
    - rec-{FieldName}-{Index}-{Card_ID}: Workflow B, start recording
    - del-{FieldName}-{Index}-{Card_ID}: Workflow C, delete an entry
    - addaudio-{FieldName}-{Index}-{Card_ID}: upload an existing audio file
    - No payload: generic welcome message

    Args:
        message: Telegram 訊息物件。The Telegram message object.
        anki_client: 注入的 AnkiClient。Injected AnkiClient instance.
        user_state_manager: 注入的使用者狀態管理器。
            Injected UserStateManager instance.
    """
    args = message.text.split()[1:] if message.text else []

    if not args:
        # 一般 /start
        await message.answer(
            f"👋 歡迎使用 FluencyTides Bot！\n\n"
            f"請使用 /newcard 指令開啟選單，"
            f"來新增單字、對話或外語糾錯卡片。\n\n"
            f"💡 <i>您可以隨時輸入 /help 了解更多。</i>",
            parse_mode="HTML"
        )
        return

    payload = args[0]
    logger.info(
        "使用者 %d 透過 Deep Link 啟動，Payload: %s",
        message.from_user.id,
        payload,
    )

    action = DeepLinkParser.parse(payload)
    if not action:
        await message.answer("❌ 無法辨識這個連結，請回 Anki 重新點按鈕，或用 /help 查看說明。")
        return

    if isinstance(action, RecordAudioAction):
        # 驗證卡片是否存在於 Anki
        try:
            note_ids = await anki_client.find_notes(f"Card_ID:{action.card_id}")
            if not note_ids:
                await message.answer(CARD_NOT_FOUND)
                return
        except AnkiConnectError as e:
            logger.error("驗證卡片時發生錯誤: %s", e)
            await message.answer(ANKI_UNREACHABLE)
            return

        chat_id = message.chat.id

        # 抓取卡片內容以顯示題目給使用者（取第一個欄位）
        try:
            notes_info = await anki_client.get_notes_info(notes=note_ids[:1])
            if notes_info:
                note_info = notes_info[0]
                fields = note_info.fields
                # 泛用性處理：依據 order 排序欄位，取 order 最小的（通常是第一個欄位）
                sorted_fields = sorted(fields.items(), key=lambda item: int(item[1].get("order", 999)))
                if sorted_fields:
                    display_text = str(sorted_fields[0][1].get("value", ""))
                else:
                    display_text = action.card_id
            else:
                display_text = action.card_id
        except Exception as e:
            logger.error("取得卡片內容時發生錯誤: %s", e)
            display_text = action.card_id

        # S010 修復：Anki 欄位本身含 HTML（<div>/<ruby>/<span>），未經處理直接
        # 插入 Telegram HTML 訊息會拋 TelegramBadRequest。統一以工具函數
        # 去標籤 → 截斷 → 轉義。
        # S010 fix: Anki fields contain HTML (<div>/<ruby>/<span>); embedding
        # them raw in a Telegram HTML message raises TelegramBadRequest. The
        # helper strips tags, truncates, then escapes.
        display_text = anki_field_to_tg_text(display_text, limit=300)

        # S010 修復：狀態改為「提示訊息成功送出後」才設定。原本先設狀態再發訊息，
        # 一旦發送失敗，使用者已進入錄音模式卻收不到任何提示，體感是「按了沒反應」。
        # S010 fix: the state is set only after the prompt is delivered.
        # Previously the state was set first, so a send failure left the user
        # silently in recording mode — it felt like the button did nothing.
        try:
            await message.answer(
                f"🎙️ <b>錄音模式已啟動</b>\n\n"
                f"目標題目：\n<blockquote>{display_text}</blockquote>\n\n"
                f"請直接發送語音訊息，我會進行以下處理：\n"
                f"1️⃣ 語音辨識（逐字稿）\n"
                f"2️⃣ AI 評分（0-100）\n"
                f"3️⃣ 自動寫回 Anki 卡片\n\n"
                f"<i>💡 發送語音後請稍候數秒等待 AI 分析完成。</i>"
            )
        except TelegramAPIError as e:
            # 降級為純文字重試，確保使用者至少知道錄音模式已就緒
            logger.error("錄音提示訊息發送失敗，改以純文字重送: %s", e)
            await message.answer(
                "🎙️ 錄音模式已啟動，請直接發送語音訊息。",
                parse_mode=None,
            )

        # 切換使用者狀態為 Recording
        user_state_manager.set_state(
            chat_id,
            UserState(
                action="recording",
                card_id=action.card_id,
                extra={"field_name": action.field_name, "index": action.index}
            ),
        )
        return

    if isinstance(action, AddAudioAction):
        # 驗證卡片是否存在於 Anki
        try:
            note_ids = await anki_client.find_notes(f"Card_ID:{action.card_id}")
            if not note_ids:
                await message.answer(CARD_NOT_FOUND)
                return
        except AnkiConnectError as e:
            logger.error("驗證卡片時發生錯誤: %s", e)
            await message.answer(ANKI_UNREACHABLE)
            return

        # 切換使用者狀態為 add_audio
        chat_id = message.chat.id
        user_state_manager.set_state(
            chat_id,
            UserState(
                action="add_audio",
                card_id=action.card_id,
                extra={"field_name": action.field_name, "index": action.index}
            ),
        )

        # 只處理 Telegram 的「語音訊息」（F.voice）；以檔案傳送的 mp3 等不會被處理，所以明說
        await message.answer(
            f"📤 <b>上傳到{field_label(action.field_name)}</b>\n"
            f"請傳一則語音訊息（不收 mp3 等檔案，{state_minutes()} 分鐘內有效）。"
        )
        return

    if isinstance(action, DeleteEntryAction):
        await _handle_delete_entry(message, anki_client, card_service, action)
        return

    if isinstance(action, GenerateCardAction):
        await message.answer("🚧 這個連結的功能尚未開放。")
        return

    if isinstance(action, TTSAudioAction):
        # 卡片模板已有「透過 AI TTS 生成語音」的連結，但後端還沒實作；不回覆的話使用者以為按了沒反應
        await message.answer("🚧 AI 語音生成尚未開放。")
        return


async def _handle_delete_entry(
    message: Message,
    anki_client: AnkiClient,
    card_service: "CardService",
    action: DeleteEntryAction,
) -> None:
    """處理 Workflow C 的刪除邏輯。

    Handle the Workflow C deletion logic.

    JSON 欄位的讀寫一律經由 `AnkiJsonFieldManager`（S065）：這些欄位的儲存
    格式並不一致——經語音流程寫入的 `Recordings_*` 是 HTML 轉義過的
    （`&quot;`），而匯入腳本直寫的 `References_*` 是未轉義的原始 JSON。
    直接 `json.loads` 只在後者碰巧成立，刪除錄音時必定失敗。

    All JSON field reads and writes go through `AnkiJsonFieldManager` (S065):
    the stored format is not uniform — `Recordings_*` written by the voice
    flow are HTML-escaped (`&quot;`), while `References_*` written directly by
    import scripts are raw JSON. A bare `json.loads` only happens to work for
    the latter and always fails when deleting a recording.

    Args:
        message: Telegram 訊息物件。The Telegram message object.
        anki_client: 注入的 AnkiClient。Injected AnkiClient instance.
        card_service: 注入的 CardService，供 JSON 欄位安全寫回。Injected
            CardService used for safe JSON field write-back.
        action: 刪除條目的動作模型。The delete-entry action model.
    """
    from app.services.task_handlers.shared.trilingual_lang import LANG_CODES

    field_name = action.field_name
    # 既有卡片（無後綴）+ 三語卡（Recordings_ZH/JA/EN、References_ZH/JA/EN）
    allowed_fields = ("References", "Recordings", "Prompt_Audios") + tuple(
        f"{base}_{lang}"
        for base in ("References", "Recordings")
        for lang in LANG_CODES
    )
    if field_name not in allowed_fields:
        await message.answer("❌ 這個刪除連結不支援，請回 Anki 重新點。")
        return

    # 查詢 Anki 卡片
    try:
        # 取前同步 (有防抖)
        await anki_client.sync(raise_errors=False)

        note_ids = await anki_client.find_notes(f"Card_ID:{action.card_id}")
        if not note_ids:
            await message.answer(CARD_NOT_FOUND)
            return

        notes_info = await anki_client.get_notes_info(notes=note_ids[:1])
        if not notes_info:
            await message.answer(CARD_NOT_FOUND)
            return

        note_info = notes_info[0]
        note_id = note_info.noteId
    except AnkiConnectError as e:
        logger.error("查詢 Anki 卡片失敗: %s", e)
        await message.answer(ANKI_UNREACHABLE)
        return

    # 讀取 JSON 欄位
    field_data = note_info.fields.get(field_name, {})
    raw_json = str(field_data.get("value", "")).strip()

    section_name = field_label(field_name)
    if not raw_json:
        await message.answer(f"❌ {section_name}已經沒有資料。")
        return

    # 以共用解析器讀取：會先剝除 Anki 插入的 HTML、還原 &quot; 等實體，
    # 因此對「轉義」與「未轉義」兩種既存格式都成立（S065）。
    # The shared parser strips Anki-injected HTML and unescapes entities such
    # as &quot;, so it handles both stored formats (S065).
    try:
        items_list = AnkiJsonFieldManager.parse_field_string(raw_json)
    except AnkiFieldCorruptedError as e:
        await message.answer(f"❌ {section_name}的資料無法解析：{error_detail(e)}")
        return

    # 驗證索引範圍
    try:
        index_val = int(action.index)
    except ValueError:
        if action.index == "last" and items_list:
            index_val = len(items_list) - 1
        elif action.index == "last":
            await message.answer(f"❌ {section_name}已經沒有資料。")
            return
        else:
            await message.answer(_ENTRY_GONE)
            return

    if index_val < 0 or index_val >= len(items_list):
        # 最常見的原因是卡片畫面還沒更新（剛刪過一筆，索引已位移）
        await message.answer(_ENTRY_GONE)
        return

    # 執行刪除
    removed_item = items_list.pop(index_val)

    # 以共用寫入器回寫：內部會 html.escape，避免 Anki 富文本編輯器把值內含的
    # HTML（如評語中的 <span style="color:red">）當成標籤解析而損毀 JSON。
    # 直接寫入未轉義的 json.dumps 會讓欄位格式與語音流程不一致（S065）。
    # The shared writer html-escapes the payload so Anki's rich-text editor
    # cannot reinterpret embedded HTML (e.g. <span style="color:red"> inside a
    # comment) as markup and corrupt the JSON. Writing raw json.dumps here
    # would desync the field format from the voice flow (S065).
    try:
        await AnkiJsonFieldManager.update_field(
            card_service, note_id, field_name, items_list
        )
    except (AnkiConnectError, FluencyTidesError) as e:
        logger.error("更新 Anki 卡片欄位失敗: %s", e)
        # 寫回失敗代表沒有刪到、索引也沒位移：可以直接再點一次同一個刪除按鈕
        await message.answer(f"❌ 寫回 Anki 失敗：{error_detail(e)}\n可回 Anki 再點一次刪除。")
        return

    removed_preview = ""
    if isinstance(removed_item, dict):
        removed_preview = removed_item.get(
            "content", removed_item.get("date", str(removed_item))
        )
    else:
        removed_preview = str(removed_item)

    # 嘗試觸發同步
    from app.bot.handlers.callbacks import _sync_with_warning
    sync_warning = await _sync_with_warning(anki_client)

    # 內容是使用者資料（References 已被 parse_field_string 還原過實體）：不轉義的話含 < 或 & 時
    # 這則確認送不出去，使用者以為沒刪而再點一次，就會刪到下一筆
    await message.answer(
        f"✅ 已刪除{section_name}：{escape_tg(str(removed_preview), 100)}\n"
        f"剩 {len(items_list)} 筆。{sync_warning}"
    )





@router.message(Command("help"))
async def command_help_handler(message: Message) -> None:
    """處理 /help 指令。

    Handle the /help command.

    Args:
        message: Telegram 訊息物件。The Telegram message object.
    """
    help_text = (
        "📚 <b>FluencyTides 使用指南</b>\n\n"
        "1️⃣ <b>新增卡片（統一入口）</b>\n"
        "輸入 /newcard ，Bot 會彈出選單讓您選擇要新增的卡片類型：\n"
        "  • 📚 單字卡 — 輸入單字自動查字典並建立卡片\n"
        "  • 🎙️ 對話卡 — 以問答方式建立口說練習情境卡片\n"
        "  • 📝 外語糾錯 — AI 糾錯並拆解成原子化知識點\n\n"
        "2️⃣ <b>錄音評分</b>\n"
        "點擊 Anki 卡片上的 🎤 提交新錄音 按鈕，跳轉到此 Bot，"
        "發送語音即可獲得 AI 評分。\n\n"
        "3️⃣ <b>刪除條目</b>\n"
        "點擊 Anki 卡片上的刪除按鈕，即可遠端刪除特定的參考範本或歷史錄音。\n\n"
        "4️⃣ <b>同步清理</b>\n"
        "輸入 /sync 手動清理資料庫中已不存在於 Anki 的孤兒關聯。\n\n"
        "5️⃣ <b>工具設定（管理員限定）</b>\n"
        "輸入 /setconfig 為每個用途（例如自他動詞子卡、錄音評分）或全域指定要用的工具：\n"
        "  • <b>文字 LLM</b>：端點、模型與思考深度\n"
        "  • <b>評分策略</b>（錄音評分）：\n"
        "    ├ <code>listen</code> — 多模態模型直接聽音檔\n"
        "    ├ <code>stt_diff</code> — 本地 Whisper 轉文字＋逐字比對，零 API 費用、秒回\n"
        "    └ <code>stt_llm</code> — 本地 Whisper 轉文字＋輕量 LLM 評分，低成本\n"
        "  • <b>聽音檔的模型</b>、<b>語音轉文字</b>：評分策略用到的模型\n"
        "三語卡另有自己的錄音評分設定，沒設時沿用錄音評分的。"
        "選項來自 .env 的白名單；<b>設定存在資料庫，重啟後仍有效</b>。恢復預設後改用上一層"
        "（三語卡 → 錄音評分 → 全域），都沒設定時用 .env 預設。"
    )
    await message.answer(help_text)


@router.message(Command("sync"))
async def command_sync_handler(
    message: Message,
    anki_client: AnkiClient,
    relation_service: RelationService,
) -> None:
    """處理 /sync 指令，同步並清理孤兒關聯。

    Handle the /sync command: sync with Anki and prune orphan relations.

    Args:
        message: Telegram 訊息物件。The Telegram message object.
        anki_client: 注入的 AnkiClient。Injected AnkiClient instance.
        relation_service: 注入的 RelationService。Injected RelationService.
    """
    # 這個指令不會觸發 AnkiWeb 同步，只是掃描 Anki 現有卡片、清掉指向已刪卡片的關聯
    status_msg = await message.answer("⏳ 清理失效關聯中…")

    try:
        valid_note_ids = await anki_client.find_notes("deck:*")
        deleted_count = await relation_service.sync_with_anki(valid_note_ids)

        # 顯示掃描張數：Anki 回 0 張卻刪了一堆關聯時，使用者看得出不對勁
        await status_msg.edit_text(
            f"✅ 清理完成：掃描 {len(valid_note_ids)} 張卡片，移除 {deleted_count} 筆失效關聯。"
        )
    except (AnkiConnectError, FluencyTidesError) as e:
        logger.exception("Telegram Bot 同步發生未預期錯誤: %s", e)
        await status_msg.edit_text(f"❌ 清理失敗：{error_detail(e)}")
