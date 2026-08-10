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
from app.bot.utils.formatting import anki_field_to_tg_text
from app.core.exceptions import AnkiFieldCorruptedError
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager
from app.schemas.tg.deep_link import (
    DeleteEntryAction,
    GenerateCardAction,
    RecordAudioAction,
    AddAudioAction,
)
from app.core.config import settings
from app.infrastructure.anki.client import AnkiClient, AnkiConnectError
from app.schemas.anki import AnkiNote, AnkiNoteOptions
from app.schemas.speaking import NewCardPayload, ReferenceItem
from app.services.relation_service import RelationService
from app.core.dynamic_config import get_modifiable_configs
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
logger = logging.getLogger(__name__)

router = Router(name="commands_router")


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
        await message.answer(
            f"👋 歡迎使用 FluencyTides Bot！\n\n"
            f"收到的 Payload: <code>{payload}</code>\n"
            f"目前無法處理此連結。請直接傳送單字或使用 /help 查看說明。"
        )
        return

    if isinstance(action, RecordAudioAction):
        # 驗證卡片是否存在於 Anki
        try:
            note_ids = await anki_client.find_notes(f"Card_ID:{action.card_id}")
            if not note_ids:
                await message.answer(
                    f"❌ 找不到 Card ID 為 <code>{action.card_id}</code> 的卡片。\n"
                    f"請確認 Anki 是否正在執行。"
                )
                return
        except AnkiConnectError as e:
            logger.error("驗證卡片時發生錯誤: %s", e)
            await message.answer(
                "❌ 無法連線到 Anki，請確認 AnkiConnect 正在執行。"
            )
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
                await message.answer(
                    f"❌ 找不到 Card ID 為 <code>{action.card_id}</code> 的卡片。\n"
                    f"請確認 Anki 是否正在執行。"
                )
                return
        except AnkiConnectError as e:
            logger.error("驗證卡片時發生錯誤: %s", e)
            await message.answer(
                "❌ 無法連線到 Anki，請確認 AnkiConnect 正在執行。"
            )
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

        await message.answer(
            f"🎙️ <b>上傳模式已啟動</b>\n\n"
            f"目標卡片：<code>{action.card_id}</code>\n"
            f"目標欄位：<code>{action.field_name}</code>\n\n"
            f"請直接發送現成的語音訊息，我會將其上傳並附加到卡片中！"
        )
        return

    if isinstance(action, DeleteEntryAction):
        await _handle_delete_entry(message, anki_client, card_service, action)
        return

    if isinstance(action, GenerateCardAction):
        await message.answer(f"🚧 生成卡片操作尚未實作: 目標={action.target_id}")
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
        await message.answer(
            f"❌ 不支援的目標欄位: <code>{field_name}</code>。\n"
            f"僅支援: {', '.join(allowed_fields)}"
        )
        return

    # 查詢 Anki 卡片
    try:
        # 取前同步 (有防抖)
        await anki_client.sync(raise_errors=False)

        note_ids = await anki_client.find_notes(f"Card_ID:{action.card_id}")
        if not note_ids:
            await message.answer(
                f"❌ 找不到 Card ID 為 <code>{action.card_id}</code> 的卡片。"
            )
            return

        notes_info = await anki_client.get_notes_info(notes=note_ids[:1])
        if not notes_info:
            await message.answer("❌ 無法取得卡片詳細資訊。")
            return

        note_info = notes_info[0]
        note_id = note_info.noteId
    except AnkiConnectError as e:
        logger.error("查詢 Anki 卡片失敗: %s", e)
        await message.answer("❌ 無法連線到 Anki。")
        return

    # 讀取 JSON 欄位
    field_data = note_info.fields.get(field_name, {})
    raw_json = str(field_data.get("value", "")).strip()

    if not raw_json:
        await message.answer(
            f"❌ 卡片的 <code>{field_name}</code> 欄位為空。"
        )
        return

    # 以共用解析器讀取：會先剝除 Anki 插入的 HTML、還原 &quot; 等實體，
    # 因此對「轉義」與「未轉義」兩種既存格式都成立（S065）。
    # The shared parser strips Anki-injected HTML and unescapes entities such
    # as &quot;, so it handles both stored formats (S065).
    try:
        items_list = AnkiJsonFieldManager.parse_field_string(raw_json)
    except AnkiFieldCorruptedError as e:
        await message.answer(
            f"❌ 無法解析 <code>{field_name}</code> 欄位的 JSON: {e}"
        )
        return

    # 驗證索引範圍
    try:
        index_val = int(action.index)
    except ValueError:
        if action.index == "last" and items_list:
            index_val = len(items_list) - 1
        else:
            await message.answer(f"❌ 無效的索引: <code>{action.index}</code>")
            return

    if index_val < 0 or index_val >= len(items_list):
        await message.answer(
            f"❌ 索引 {index_val} 超出範圍。\n"
            f"<code>{field_name}</code> 目前有 {len(items_list)} 筆資料 "
            f"(索引 0~{len(items_list) - 1})。"
        )
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
        await message.answer(f"❌ 寫回 Anki 失敗: {e}")
        return

    from app.services.task_handlers.shared.trilingual_lang import (
        LANG_DISPLAY,
        lang_from_field,
    )

    section_names = {"References": "參考範本", "Recordings": "歷史錄音", "Prompt_Audios": "正面語音"}
    _lang = lang_from_field(field_name)
    if _lang:
        _base = field_name.rsplit("_", 1)[0]
        section_name = f"{section_names.get(_base, _base)}({LANG_DISPLAY[_lang]})"
    else:
        section_name = section_names.get(field_name, field_name)
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

    await message.answer(
        f"✅ <b>刪除成功</b>\n\n"
        f"卡片：<code>{action.card_id}</code>\n"
        f"區塊：{section_name}\n"
        f"索引：{action.index}\n"
        f"內容：{removed_preview[:100]}\n\n"
        f"<i>剩餘 {len(items_list)} 筆資料。(結果已自動寫入本地 Anki)</i>{sync_warning}"
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
        "5️⃣ <b>動態設定（管理員限定）</b>\n"
        "輸入 /setconfig 開啟設定選單，不需重啟即可切換：\n"
        "  • <b>語音評分模式</b>（AUDIO_EVALUATOR_PROVIDER）：\n"
        "    ├ <code>gemini_native</code> — Gemini 多模態直接聽音檔（預設）\n"
        "    ├ <code>openai</code> — OpenAI 相容 API 聽音檔\n"
        "    ├ <code>stt_diff</code> — 本地 Whisper 轉文字＋逐字比對，零 API 費用、秒回\n"
        "    └ <code>stt_llm</code> — 本地 Whisper 轉文字＋輕量 LLM 評分，低成本\n"
        "  • <b>模型切換</b>：AUDIO_MODEL_NAME / LLM_MODEL_NAME / STT_LLM_MODEL_NAME\n"
        "<i>設定僅本次執行期間有效，重啟後回到 .env 預設值。</i>"
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
    status_msg = await message.answer("🔄 正在與 Anki 進行同步清理，請稍候...")

    try:
        valid_note_ids = await anki_client.find_notes("deck:*")
        deleted_count = await relation_service.sync_with_anki(valid_note_ids)

        await status_msg.edit_text(
            f"✅ <b>同步完成！</b>\n\n"
            f"已成功掃描 Anki 卡片，並從資料庫清理了 "
            f"<b>{deleted_count}</b> 筆孤兒關聯紀錄。"
        )
    except (AnkiConnectError, FluencyTidesError) as e:
        logger.exception("Telegram Bot 同步發生未預期錯誤: %s", e)
        await status_msg.edit_text(
            f"❌ <b>同步失敗</b>\n\n"
            f"無法與 Anki 完成同步。\n"
            f"詳細: {str(e)}"
        )


@router.message(Command("setconfig"))
async def command_setconfig_handler(message: Message) -> None:
    """處理 /setconfig 指令，顯示可動態修改的設定清單按鈕。

    Handle the /setconfig command: show buttons listing dynamically
    modifiable settings.

    Args:
        message: Telegram 訊息物件。The Telegram message object.
    """
    if message.from_user is None or settings.TG_ADMIN_CHAT_ID is None:
        await message.answer("⛔ 系統未設定管理員，此指令目前無法使用。")
        return

    if message.from_user.id != settings.TG_ADMIN_CHAT_ID:
        await message.answer("⛔ 您沒有權限使用此指令。")
        return

    modifiable = get_modifiable_configs()
    if not modifiable:
        await message.answer("⚠️ 目前 .env 中沒有設定任何允許動態修改的變數 (MODIFY_ 開頭)。")
        return

    # 建立 Inline Keyboard 顯示所有可用的設定
    buttons = []
    for key in modifiable.keys():
        buttons.append([InlineKeyboardButton(text=f"⚙️ {key}", callback_data=f"setconfig_key:{key}")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("請選擇要動態修改的設定項目（重啟後將失效）：", reply_markup=keyboard)
