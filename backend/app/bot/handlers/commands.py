"""
Telegram Bot 命令處理模組。

處理所有以 '/' 開頭的指令，涵蓋三大工作流：
- Workflow A: /newcard — 無狀態卡片新增
- Workflow B: /start rec_{Card_ID} — 啟動錄音評分流程
- Workflow C: /start del_{Section}_{Index}_{Card_ID} — 刪除特定 JSON 條目
- 基礎指令: /start, /help, /sync
"""

import json
import logging
from datetime import datetime, timezone

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from app.bot.state import UserState, UserStateManager
from app.bot.utils.deep_link_parser import DeepLinkParser
from app.schemas.deep_link import (
    DeleteEntryAction,
    GenerateCardAction,
    RecordAudioAction,
)
from app.core.config import settings
from app.infrastructure.anki.client import AnkiClient
from app.schemas.anki import AnkiNote, AnkiNoteOptions
from app.schemas.speaking import NewCardPayload, ReferenceItem
from app.services.relation_service import RelationService

logger = logging.getLogger(__name__)

router = Router(name="commands_router")


@router.message(CommandStart())
async def command_start_handler(
    message: Message,
    anki_client: AnkiClient,
    user_state_manager: UserStateManager,
) -> None:
    """處理 /start 指令與 Deep Link 邏輯。

    根據 payload 前綴分派到不同工作流：
    - rec_{Card_ID}: Workflow B 啟動錄音
    - del_{Section}_{Index}_{Card_ID}: Workflow C 刪除條目
    - 無 payload: 一般歡迎訊息
    """
    args = message.text.split()[1:] if message.text else []

    if not args:
        # 一般 /start
        await message.answer(
            f"👋 歡迎使用 FluencyTides Bot！\n\n"
            f"直接傳送任何英文單字或片語給我，我將為您自動生成 Anki 卡片。\n\n"
            f"目前預設牌組：<b>{settings.TG_DEFAULT_DECK}</b>\n"
            f"目前預設模型：<b>{settings.TG_DEFAULT_MODEL_NAME}</b>\n\n"
            f"💡 <i>您可以隨時輸入 /help 了解更多。</i>"
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
        except Exception as e:
            logger.error("驗證卡片時發生錯誤: %s", e)
            await message.answer(
                "❌ 無法連線到 Anki，請確認 AnkiConnect 正在執行。"
            )
            return

        # 切換使用者狀態為 Recording
        chat_id = message.chat.id
        user_state_manager.set_state(
            chat_id,
            UserState(action="recording", card_id=action.card_id),
        )

        await message.answer(
            f"🎙️ <b>錄音模式已啟動</b>\n\n"
            f"目標卡片：<code>{action.card_id}</code>\n\n"
            f"請直接發送語音訊息，我會進行以下處理：\n"
            f"1️⃣ 語音辨識（逐字稿）\n"
            f"2️⃣ AI 評分（0-100）\n"
            f"3️⃣ 自動寫回 Anki 卡片\n\n"
            f"<i>💡 發送語音後請稍候數秒等待 AI 分析完成。</i>"
        )
        return

    if isinstance(action, DeleteEntryAction):
        await _handle_delete_entry(message, anki_client, action)
        return
        
    if isinstance(action, GenerateCardAction):
        await message.answer(f"🚧 生成卡片操作尚未實作: 目標={action.target_id}")
        return


async def _handle_delete_entry(
    message: Message,
    anki_client: AnkiClient,
    action: DeleteEntryAction,
) -> None:
    """處理 Workflow C 的刪除邏輯。

    Args:
        message: Telegram 訊息物件。
        anki_client: 注入的 AnkiClient。
        action: 刪除條目的動作模型。
    """
    field_map = {"ref": "References", "rec": "Recordings"}
    field_name = field_map.get(action.section)
    if not field_name:
        await message.answer(
            f"❌ 不支援的 section 類型: <code>{action.section}</code>。\n"
            f"僅支援: ref (References), rec (Recordings)"
        )
        return

    # 查詢 Anki 卡片
    try:
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
    except Exception as e:
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

    try:
        items_list = json.loads(raw_json)
        if not isinstance(items_list, list):
            raise ValueError("欄位內容不是 JSON 陣列")
    except (json.JSONDecodeError, ValueError) as e:
        await message.answer(
            f"❌ 無法解析 <code>{field_name}</code> 欄位的 JSON: {e}"
        )
        return

    # 驗證索引範圍
    if action.index < 0 or action.index >= len(items_list):
        await message.answer(
            f"❌ 索引 {action.index} 超出範圍。\n"
            f"<code>{field_name}</code> 目前有 {len(items_list)} 筆資料 "
            f"(索引 0~{len(items_list) - 1})。"
        )
        return

    # 執行刪除
    removed_item = items_list.pop(action.index)
    new_json = json.dumps(items_list, ensure_ascii=False)

    try:
        await anki_client.update_note_fields(
            note_id=note_id,
            fields={field_name: new_json},
        )
    except Exception as e:
        logger.error("更新 Anki 卡片欄位失敗: %s", e)
        await message.answer(f"❌ 寫回 Anki 失敗: {e}")
        return

    # 組裝成功訊息
    section_name = "參考範本" if action.section == "ref" else "歷史錄音"
    removed_preview = ""
    if isinstance(removed_item, dict):
        removed_preview = removed_item.get(
            "content", removed_item.get("date", str(removed_item))
        )
    else:
        removed_preview = str(removed_item)

    await message.answer(
        f"✅ <b>刪除成功</b>\n\n"
        f"卡片：<code>{action.card_id}</code>\n"
        f"區塊：{section_name}\n"
        f"索引：{action.index}\n"
        f"內容：{removed_preview[:100]}\n\n"
        f"<i>剩餘 {len(items_list)} 筆資料。</i>"
    )


@router.message(Command("newcard"))
async def command_newcard_handler(
    message: Message,
    anki_client: AnkiClient,
) -> None:
    """處理 Workflow A: /newcard 無狀態卡片新增。

    攔截 /newcard {JSON} 指令，解析 JSON 後
    封裝為 AnkiConnect addNote payload 並寫入 Anki。

    預期 JSON 格式:
    {"deck": "牌組名", "front": "目標句", "back": "中譯", "answers": ["範例1"]}
    """
    if not message.text:
        return

    # 去除 /newcard 前綴，取得 JSON 字串
    raw_json = message.text.replace("/newcard", "", 1).strip()

    if not raw_json:
        await message.answer(
            "❌ 請提供卡片 JSON。\n\n"
            "格式範例:\n"
            '<code>/newcard {"deck": "Default", "front": "目標句", '
            '"back": "中譯", "answers": ["範例1"]}</code>'
        )
        return

    # 解析 JSON
    try:
        payload = NewCardPayload(**json.loads(raw_json))
    except json.JSONDecodeError as e:
        await message.answer(
            f"❌ JSON 解析失敗: <code>{e}</code>\n\n"
            f"請確認 JSON 格式正確。"
        )
        return
    except Exception as e:
        await message.answer(
            f"❌ JSON 欄位驗證失敗: <code>{e}</code>\n\n"
            f"必要欄位: deck, front"
        )
        return

    status_msg = await message.answer("⏳ 正在建立卡片...")

    # 產生唯一的 Card_ID（使用時間戳）
    card_id = datetime.now(tz=timezone.utc).strftime("SC_%Y%m%d_%H%M%S")

    # 將 answers 轉為 References JSON 格式
    references: list[dict[str, object]] = []
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    for ans in payload.answers:
        ref = ReferenceItem(
            date=today,
            content=ans,
            status=1,
            audios=[],
        )
        references.append(ref.model_dump())

    # 組裝 AnkiConnect addNote payload
    # 對應 Speaking_Coach_Dark 欄位:
    # Card_ID, Prompt, Prompt_Audios, Context, Recordings, References, TG_Bot
    note = AnkiNote(
        deckName=payload.deck,
        modelName="Speaking_Coach_Dark",
        fields={
            "Card_ID": card_id,
            "Prompt": payload.front,
            "Prompt_Audios": "[]",
            "Context": payload.back,
            "Recordings": "[]",
            "References": json.dumps(references, ensure_ascii=False),
            "TG_Bot": settings.TG_BOT_USERNAME,
        },
        tags=["TelegramBot", "Speaking_Coach"],
        options=AnkiNoteOptions(
            allowDuplicate=False,
            duplicateScope="deck",
        ),
    )

    try:
        note_id = await anki_client.add_note(note)

        if note_id:
            await status_msg.edit_text(
                f"✅ <b>卡片建立成功！</b>\n\n"
                f"Card ID：<code>{card_id}</code>\n"
                f"Note ID：<code>{note_id}</code>\n"
                f"牌組：<code>{payload.deck}</code>\n"
                f"Prompt：{payload.front[:80]}\n"
                f"範本數：{len(payload.answers)} 筆\n\n"
                f"<i>趕快去 Anki 看看吧！</i>"
            )
        else:
            await status_msg.edit_text(
                "⚠️ <b>卡片建立失敗</b>\n\n"
                "可能是重複的卡片，請確認後重試。"
            )
    except Exception as e:
        logger.exception("Workflow A 卡片建立失敗: %s", e)
        await status_msg.edit_text(
            f"❌ <b>建立失敗</b>\n\n"
            f"錯誤: {str(e)[:200]}"
        )


@router.message(Command("help"))
async def command_help_handler(message: Message) -> None:
    """處理 /help 指令。"""
    help_text = (
        "📚 <b>FluencyTides 使用指南</b>\n\n"
        "1️⃣ <b>生成單字卡（TOEIC 模式）</b>\n"
        "直接傳送任何外語單字或片語，Bot 會呼叫 LLM 自動生成完整結構卡片。\n\n"
        "2️⃣ <b>新增對話卡（Speaking Coach 模式）</b>\n"
        "使用 <code>/newcard {JSON}</code> 指令新增 Speaking_Coach_Dark 卡片。\n"
        "JSON 格式: <code>{\"deck\": \"牌組\", \"front\": \"對方發言\", "
        "\"back\": \"文脈\", \"answers\": [\"範例1\"]}</code>\n\n"
        "3️⃣ <b>錄音評分</b>\n"
        "點擊 Anki 卡片上的 🎤 提交新錄音 按鈕，跳轉到此 Bot，"
        "發送語音即可獲得 AI 評分。\n\n"
        "4️⃣ <b>刪除條目</b>\n"
        "點擊 Anki 卡片上的刪除按鈕，即可遠端刪除特定的參考範本或歷史錄音。\n\n"
        "5️⃣ <b>同步清理</b>\n"
        "輸入 /sync 手動清理資料庫中已不存在於 Anki 的孤兒關聯。"
    )
    await message.answer(help_text)


@router.message(Command("sync"))
async def command_sync_handler(
    message: Message,
    anki_client: AnkiClient,
    relation_service: RelationService,
) -> None:
    """處理 /sync 指令，同步並清理孤兒關聯。"""
    status_msg = await message.answer("🔄 正在與 Anki 進行同步清理，請稍候...")

    try:
        valid_note_ids = await anki_client.find_notes("deck:*")
        deleted_count = await relation_service.sync_with_anki(valid_note_ids)

        await status_msg.edit_text(
            f"✅ <b>同步完成！</b>\n\n"
            f"已成功掃描 Anki 卡片，並從資料庫清理了 "
            f"<b>{deleted_count}</b> 筆孤兒關聯紀錄。"
        )
    except Exception as e:
        logger.exception("Telegram Bot 同步發生未預期錯誤: %s", e)
        await status_msg.edit_text(
            f"❌ <b>同步失敗</b>\n\n"
            f"無法與 Anki 完成同步。\n"
            f"詳細: {str(e)}"
        )
