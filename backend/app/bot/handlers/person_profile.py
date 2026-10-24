"""
人脈卡的 Telegram 流程（人脈卡計畫書 §3.12、§3.13）。

Telegram flows of the person profile card.

- ``/p 名字``：找人，回覆「見面前必看」摘要與操作按鈕。
- 摘要的按鈕：記一筆互動、待追蹤（標成完成、刪除）、改資料、各清單（新增、刪除）、照片、全部資料。
- 卡面的深連結（``pview``、``padd``、``pdel``、``pdone``、``pphoto``）由 ``commands.py`` 的 ``/start``
  轉給 :func:`handle_person_link`。
- ``/newcard`` 選「人脈卡」時由 :func:`start_person_create` 建立新的人。

寫入一律經 ``NoteService``（具名動作或 ``update_fields``），由 handler 驗證並在寫入後同步。
callback data 以 ``pp:`` 開頭；用筆記 ID 指人（卡面深連結才用 ``Card_ID``）。

All writes go through ``NoteService``; callback data starts with ``pp:``.
"""

from __future__ import annotations

import base64
import logging
from datetime import date, datetime
from io import BytesIO

from aiogram import F, Router, types
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.utils import person_profile_view as view
from pydantic import ValidationError

from app.bot.utils.formatting import anki_field_to_tg_text, escape_tg
from app.bot.utils.texts import ANKI_UNREACHABLE, CARD_NOT_FOUND, error_detail, sync_suffix
from app.core.config import settings
from app.core.exceptions import AnkiFieldCorruptedError, FluencyTidesError
from app.infrastructure.anki.client import AnkiClient, AnkiConnectError
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager
from app.schemas.anki import AnkiStoreMediaParams
from app.schemas.person_profile import PERSON_JSON_ITEMS, PERSON_NOTE_TYPE, PersonCreateParams, item_fingerprint
from app.schemas.tg.deep_link import PersonLinkAction
from app.services.card_service import CardService
from app.services.note_service import NoteService
from app.services.task_handlers.base import note_field_values
from app.services.task_handlers.person_profile_handler import new_card_id

logger = logging.getLogger(__name__)

router = Router(name="person_profile")

HANDLER_NAME = "person_profile"
#: TG 建立的人放在這個牌組。The deck people created on Telegram go into.
PERSON_DECK = "人脈"


class PersonStates(StatesGroup):
    """人脈卡流程的狀態。States of the person flows."""

    waiting_search = State()
    create_name = State()
    create_context = State()
    add_step = State()
    add_confirm = State()
    edit_value = State()
    edit_mode = State()
    waiting_photo = State()


def _button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


CANCEL_ROW = [_button("❌ 取消", "pp:cancel")]


def _today() -> date:
    """今天（伺服器時區）。Today in the server's time zone."""
    return date.today()


# =============================================================================
# 讀取與顯示
# =============================================================================


async def _load(card_service: CardService, note_id: int) -> tuple[dict[str, str], dict[str, list[object]]] | None:
    """讀出一個人：一般欄位與解析好的清單；不是人脈卡時為 None。格式壞掉的清單當成空的。

    Load a person's fields and parsed lists; None when not a person card.
    """
    note = await card_service.get_note(note_id)
    if str(note.get("modelName")) != PERSON_NOTE_TYPE:
        return None
    fields = note_field_values(note)
    lists: dict[str, list[object]] = {}
    for name in PERSON_JSON_ITEMS:
        try:
            lists[name] = AnkiJsonFieldManager.parse_field_string(fields.get(name, ""))
        except AnkiFieldCorruptedError:
            lists[name] = []
    return fields, lists


async def _ensure_ids(card_service: CardService, note_id: int, fields: dict[str, str]) -> None:
    """Anki app 建立的人沒有 ``Card_ID``、``TG_Bot``：第一次在 TG 找到時補上（R7）。

    Backfill ``Card_ID`` and ``TG_Bot`` for people created in the Anki app.
    """
    changes: dict[str, str] = {}
    if not fields.get("Card_ID", "").strip():
        changes["Card_ID"] = new_card_id()
    if not fields.get("TG_Bot", "").strip() and settings.TG_BOT_USERNAME:
        changes["TG_Bot"] = settings.TG_BOT_USERNAME.lstrip("@")
    if changes:
        await card_service.update_note_fields(note_id, changes, model_name=PERSON_NOTE_TYPE)
        fields.update(changes)


def summary_keyboard(note_id: int) -> InlineKeyboardMarkup:
    """摘要下方的操作按鈕。The buttons under a summary."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [_button("➕ 記一筆互動", f"pp:add:{note_id}:I"), _button("✅ 待追蹤", f"pp:ls:{note_id}:F")],
        [_button("✏️ 改資料", f"pp:edit:{note_id}"), _button("📋 清單", f"pp:lists:{note_id}")],
        [_button("📷 照片", f"pp:photo:{note_id}"), _button("📇 全部資料", f"pp:full:{note_id}")],
    ])


async def send_summary(
    target: types.Message, card_service: CardService, note_id: int, *, prefix: str = "", edit: bool = False
) -> None:
    """送出（或改寫成）某人的摘要。Send, or edit into, a person's summary.

    Args:
        target: 回覆的訊息；``edit`` 時改寫它。The message to answer or edit.
        card_service: 卡片服務。Card service.
        note_id: 人脈卡的筆記 ID。The person note.
        prefix: 放在摘要前面的一行（例如「✅ 已儲存」）。A line before the summary.
        edit: 改寫 ``target`` 而不是送新訊息。Edit instead of sending.
    """
    loaded = await _load(card_service, note_id)
    if loaded is None:
        await target.answer(CARD_NOT_FOUND)
        return
    fields, lists = loaded
    await _ensure_ids(card_service, note_id, fields)
    text = (prefix + "\n\n" if prefix else "") + view.summary_text(fields, lists, _today())
    if edit:
        await target.edit_text(view.truncate(text), reply_markup=summary_keyboard(note_id), parse_mode="HTML")
    else:
        await target.answer(view.truncate(text), reply_markup=summary_keyboard(note_id), parse_mode="HTML")


async def _reply_error(target: types.Message, error: Exception) -> None:
    """服務層或 Anki 的錯誤轉成回覆。Reply with a service or Anki error."""
    if isinstance(error, FluencyTidesError):
        await target.answer(f"❌ {error_detail(error.message)}", parse_mode="HTML")
    else:
        logger.error("人脈卡操作失敗: %s", error)
        await target.answer(ANKI_UNREACHABLE)


# =============================================================================
# 找人：/p 名字
# =============================================================================


@router.message(Command("p"))
async def command_person(
    message: types.Message,
    command: CommandObject,
    state: FSMContext,
    anki_client: AnkiClient,
    card_service: CardService,
) -> None:
    """``/p 名字``：找人；沒給名字時請使用者輸入。``/p name``: find a person."""
    await state.clear()
    if not command.args or not command.args.strip():
        await state.set_state(PersonStates.waiting_search)
        await message.answer("👤 要找誰？輸入名字（稱呼、中文、日文、讀音、英文都可以）：",
                             reply_markup=InlineKeyboardMarkup(inline_keyboard=[CANCEL_ROW]))
        return
    await _search(message, state, anki_client, card_service, command.args.strip())


@router.message(PersonStates.waiting_search, F.text)
async def process_search(
    message: types.Message, state: FSMContext, anki_client: AnkiClient, card_service: CardService
) -> None:
    """處理輸入的名字。Handle the typed name."""
    await state.clear()
    await _search(message, state, anki_client, card_service, (message.text or "").strip())


async def _search(
    message: types.Message, state: FSMContext, anki_client: AnkiClient, card_service: CardService, name: str
) -> None:
    """依名字找人：一位直接回摘要；多位列按鈕；找不到時提供建立。Search by name."""
    if not name:
        await message.answer("請輸入名字。")
        return
    try:
        await anki_client.sync(raise_errors=False)
        note_ids = await anki_client.find_notes(view.search_query(name))
        if not note_ids:
            await state.update_data(new_name=name)
            await message.answer(
                f"🔍 找不到「{escape_tg(name, 40)}」。",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[_button("➕ 建立這個人", "pp:new")]]),
                parse_mode="HTML",
            )
            return
        if len(note_ids) == 1:
            await send_summary(message, card_service, note_ids[0])
            return
        if len(note_ids) > view.MAX_SEARCH_RESULTS:
            await message.answer(f"🔍 找到 {len(note_ids)} 位，太多了，請打更完整的名字。")
            return
        notes = await anki_client.get_notes_info(notes=note_ids)
        rows = []
        for note in notes:
            fields = {name: str(v.get("value", "")) for name, v in note.fields.items()}
            label = " ".join(filter(None, [fields.get("Display_Name", ""), fields.get("Company", "")]))
            rows.append([_button(escape_tg(label, 40) or str(note.noteId), f"pp:s:{note.noteId}")])
        await message.answer(f"🔍 找到 {len(notes)} 位，要看哪一位？", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    except (FluencyTidesError, AnkiConnectError) as e:
        await _reply_error(message, e)


# =============================================================================
# 卡面深連結
# =============================================================================


async def handle_person_link(
    message: types.Message,
    state: FSMContext,
    note_service: NoteService,
    card_service: CardService,
    anki_client: AnkiClient,
    action: PersonLinkAction,
) -> None:
    """卡面深連結：先同步，以 ``Card_ID`` 找到人，再依動作處理。Handle a card link."""
    await state.clear()
    try:
        await anki_client.sync(raise_errors=False)
        note_ids = await anki_client.find_notes(view.card_id_query(action.card_id))
    except AnkiConnectError as e:
        await _reply_error(message, e)
        return
    if not note_ids:
        await message.answer(CARD_NOT_FOUND)
        return
    note_id = note_ids[0]
    try:
        if action.verb == "view":
            await send_summary(message, card_service, note_id)
        elif action.verb == "add":
            if action.field_name not in view.CODE_OF_LIST:
                await message.answer("❌ 這個連結不支援，請回 Anki 重新點。")
                return
            await start_add(message, state, note_id, action.field_name)
        elif action.verb in ("del", "done"):
            index, _, fingerprint = action.index.partition("_")
            known = view.CODE_OF_LIST if action.verb == "del" else ("Followups_JSON",)
            if action.field_name not in known or not index.isdigit() or not fingerprint:
                await message.answer("❌ 這個連結不支援，請回 Anki 重新點。")
                return
            if action.verb == "del":
                await _confirm_delete(message, card_service, note_id, action.field_name, int(index), fingerprint)
            else:
                await _set_followup_status(message, note_service, card_service, note_id, int(index), fingerprint, "done")
        elif action.verb == "photo":
            await _start_photo(message, state, note_id)
    except (FluencyTidesError, AnkiConnectError) as e:
        await _reply_error(message, e)


# =============================================================================
# 按鈕
# =============================================================================


@router.callback_query(F.data.startswith("pp:"))
async def on_person_button(
    callback: types.CallbackQuery,
    state: FSMContext,
    note_service: NoteService,
    card_service: CardService,
) -> None:
    """``pp:`` 按鈕的分派。Dispatch the ``pp:`` buttons."""
    await callback.answer()
    message = callback.message
    # 太舊的訊息（超過 48 小時）拿不到內容，無法回覆或改寫
    if message is None or isinstance(message, types.InaccessibleMessage):
        return
    parts = (callback.data or "").split(":")
    verb = parts[1] if len(parts) > 1 else ""
    try:
        if verb == "cancel":
            await state.clear()
            await message.edit_reply_markup(reply_markup=None)
            await message.answer("🗑️ 已取消。")
        elif verb == "s":
            await state.clear()
            await send_summary(message, card_service, int(parts[2]))
        elif verb == "full":
            await _send_full(message, card_service, int(parts[2]))
        elif verb == "lists":
            await _send_lists_menu(message, card_service, int(parts[2]))
        elif verb == "ls":
            await _send_list(message, card_service, int(parts[2]), view.LIST_CODES[parts[3]])
        elif verb == "add":
            await start_add(message, state, int(parts[2]), view.LIST_CODES[parts[3]])
        elif verb == "del":
            await _confirm_delete(message, card_service, int(parts[2]), view.LIST_CODES[parts[3]], int(parts[4]), parts[5])
        elif verb == "delok":
            note_id, field_name = int(parts[2]), view.LIST_CODES[parts[3]]
            warning = await note_service.run_action(
                note_id, "remove_item", {"field": field_name, "index": int(parts[4]), "fingerprint": parts[5]}
            )
            await message.edit_reply_markup(reply_markup=None)
            await send_summary(message, card_service, note_id, prefix="🗑 已刪除。" + sync_suffix(warning))
        elif verb == "st":
            status = "done" if parts[5] == "d" else "open"
            await _set_followup_status(message, note_service, card_service, int(parts[2]), int(parts[3]), parts[4], status)
        elif verb == "edit":
            await _send_edit_groups(message, int(parts[2]))
        elif verb == "eg":
            await _send_edit_fields(message, int(parts[2]), int(parts[3]))
        elif verb == "ef":
            await _start_edit(message, state, card_service, int(parts[2]), view.EDITABLE_FIELDS[int(parts[3])])
        elif verb == "em":
            await _finish_edit(message, state, note_service, card_service, append=parts[2] == "a")
        elif verb == "photo":
            await _start_photo(message, state, int(parts[2]))
        elif verb == "phrm":
            await state.clear()
            note_id = int(parts[2])
            warning = await note_service.run_action(note_id, "set_photo", {"filename": ""})
            await send_summary(message, card_service, note_id, prefix="🗑 已移除照片。" + sync_suffix(warning))
        elif verb == "new":
            data = await state.get_data()
            await start_person_create(message, state, name=str(data.get("new_name") or "") or None)
        elif verb in ("ch", "skip"):
            await _answer_step_button(message, state, note_service, card_service, parts)
        elif verb == "save":
            await _save_item(message, state, note_service, card_service)
    except (FluencyTidesError, AnkiConnectError) as e:
        await _reply_error(message, e)
    except (IndexError, KeyError, ValueError):
        await message.answer("❌ 這個按鈕已失效，請用 /p 重新找人。")


async def _send_full(message: types.Message, card_service: CardService, note_id: int) -> None:
    loaded = await _load(card_service, note_id)
    if loaded is None:
        await message.answer(CARD_NOT_FOUND)
        return
    await message.answer(
        view.full_text(*loaded),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[_button("⬅️ 摘要", f"pp:s:{note_id}")]]),
        parse_mode="HTML",
    )


async def _send_lists_menu(message: types.Message, card_service: CardService, note_id: int) -> None:
    loaded = await _load(card_service, note_id)
    if loaded is None:
        await message.answer(CARD_NOT_FOUND)
        return
    _, lists = loaded
    rows = [
        [_button(f"{PERSON_JSON_ITEMS[name][1]}（{len(lists.get(name, []))}）", f"pp:ls:{note_id}:{code}")]
        for code, name in view.LIST_CODES.items()
    ]
    rows.append([_button("⬅️ 摘要", f"pp:s:{note_id}")])
    await message.answer("📋 要看哪一個清單？", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def _send_list(message: types.Message, card_service: CardService, note_id: int, field_name: str) -> None:
    """一個清單的每一筆，附刪除（與待追蹤的完成）按鈕。One list with its item buttons."""
    loaded = await _load(card_service, note_id)
    if loaded is None:
        await message.answer(CARD_NOT_FOUND)
        return
    items = loaded[1].get(field_name, [])
    code = view.CODE_OF_LIST[field_name]
    title = PERSON_JSON_ITEMS[field_name][1]
    lines = [f"📋 <b>{title}</b>"]
    rows: list[list[InlineKeyboardButton]] = []
    shown = list(enumerate(items))[-20:]
    for index, item in shown:
        lines.append(f"{index + 1}. {view.item_line(field_name, item)}")
        fp = item_fingerprint(item)
        row = [_button(f"🗑 {index + 1}", f"pp:del:{note_id}:{code}:{index}:{fp}")]
        if field_name == "Followups_JSON" and isinstance(item, dict):
            done = item.get("status") == "done"
            row.append(_button(f"{'↩️' if done else '✅'} {index + 1}", f"pp:st:{note_id}:{index}:{fp}:{'o' if done else 'd'}"))
        rows.append(row)
    if not items:
        lines.append("（還沒有）")
    elif len(items) > len(shown):
        lines.insert(1, f"<i>只列最近 {len(shown)} 筆</i>")
    rows.append([_button("➕ 新增", f"pp:add:{note_id}:{code}"), _button("⬅️ 摘要", f"pp:s:{note_id}")])
    await message.answer(view.truncate("\n".join(lines)), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
                         parse_mode="HTML")


async def _confirm_delete(
    message: types.Message, card_service: CardService, note_id: int, field_name: str, index: int, fingerprint: str
) -> None:
    """刪除前顯示那一筆並請使用者確認；指紋對不上時找同一筆，找不到就說明。Confirm a deletion."""
    loaded = await _load(card_service, note_id)
    if loaded is None:
        await message.answer(CARD_NOT_FOUND)
        return
    items = loaded[1].get(field_name, [])
    matches = [i for i, item in enumerate(items) if item_fingerprint(item) == fingerprint]
    if not (0 <= index < len(items) and item_fingerprint(items[index]) == fingerprint):
        if len(matches) != 1:
            await message.answer("❌ 這一筆已經變了或不在了（卡片畫面可能還沒更新），請在 Anki 同步後再看。")
            return
        index = matches[0]
    code = view.CODE_OF_LIST[field_name]
    await message.answer(
        f"🗑 要刪除這一筆{PERSON_JSON_ITEMS[field_name][1]}嗎？\n\n{view.item_line(field_name, items[index])}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            _button("🗑 確認刪除", f"pp:delok:{note_id}:{code}:{index}:{fingerprint}"),
            _button("取消", f"pp:s:{note_id}"),
        ]]),
        parse_mode="HTML",
    )


async def _set_followup_status(
    message: types.Message,
    note_service: NoteService,
    card_service: CardService,
    note_id: int,
    index: int,
    fingerprint: str,
    status: str,
) -> None:
    """標成完成（或復原），附反向的按鈕。Mark a follow-up done or open again."""
    warning = await note_service.run_action(
        note_id, "set_followup_status", {"index": index, "fingerprint": fingerprint, "status": status}
    )
    undo = "o" if status == "done" else "d"
    await message.answer(
        ("✅ 已標成完成。" if status == "done" else "↩️ 已改回未完成。") + sync_suffix(warning),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            _button("↩️ 復原" if status == "done" else "✅ 標成完成", f"pp:st:{note_id}:{index}:{fingerprint}:{undo}"),
            _button("⬅️ 摘要", f"pp:s:{note_id}"),
        ]]),
    )


# =============================================================================
# 新增一筆（各清單的問答）
# =============================================================================


async def start_add(message: types.Message, state: FSMContext, note_id: int, field_name: str) -> None:
    """開始新增一筆：依清單的步驟逐一詢問。Start adding an item to a list."""
    await state.clear()
    await state.set_state(PersonStates.add_step)
    await state.update_data(nid=note_id, field=field_name, step=0, item={})
    await message.answer(f"➕ <b>新增{PERSON_JSON_ITEMS[field_name][1]}</b>", parse_mode="HTML")
    await _ask_step(message, state)


async def _ask_step(message: types.Message, state: FSMContext) -> None:
    """問目前這一步；全部問完時顯示預覽。Ask the current step, or preview when done."""
    data = await state.get_data()
    steps = view.ADD_STEPS[data["field"]]
    if data["step"] >= len(steps):
        await state.set_state(PersonStates.add_confirm)
        await message.answer(
            view.preview_item(data["field"], data["item"]),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[_button("✅ 儲存", "pp:save"), *CANCEL_ROW]]),
            parse_mode="HTML",
        )
        return
    step = steps[data["step"]]
    rows: list[list[InlineKeyboardButton]] = []
    if step.choices:
        buttons = [_button(label, f"pp:ch:{i}") for i, (_, label) in enumerate(step.choices)]
        rows.extend(buttons[i:i + 3] for i in range(0, len(buttons), 3))
    if step.default_today:
        rows.append([_button("今天", "pp:ch:t0"), _button("昨天", "pp:ch:t1")])
    last = [_button("略過", "pp:skip")] if not step.required else []
    rows.append(last + CANCEL_ROW)
    await message.answer(step.prompt, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def _store_answer(message: types.Message, state: FSMContext, raw: str | None) -> None:
    """記下這一步的回答（None 為略過）並問下一步；格式不對時重問。Store an answer and move on."""
    data = await state.get_data()
    step = view.ADD_STEPS[data["field"]][data["step"]]
    item = dict(data["item"])
    if raw is not None:
        if step.kind == "date":
            value = view.parse_date_input(raw, _today())
            if value is None:
                await message.answer("❌ 日期看不懂，請用 YYYY-MM-DD 或 MM-DD，例如 2026-10-01。")
                return
            item[step.key] = value
        elif step.kind == "lines":
            item[step.key] = [line.strip() for line in raw.splitlines() if line.strip()]
        else:
            try:
                item[step.key] = view.coerce_answer(step, raw)
            except ValueError as e:
                await message.answer(f"❌ {escape_tg(str(e) or '格式不對', 80)}，請重新輸入。", parse_mode="HTML")
                return
            if step.required and item[step.key] in ("", None):
                await message.answer("這一項必填，請輸入內容。")
                return
    await state.update_data(item=item, step=data["step"] + 1)
    await _ask_step(message, state)


@router.message(PersonStates.add_step, F.text)
async def process_add_text(message: types.Message, state: FSMContext) -> None:
    """新增一筆時輸入的文字。Typed text while adding an item."""
    data = await state.get_data()
    step = view.ADD_STEPS[data["field"]][data["step"]]
    text = (message.text or "").strip()
    if step.kind == "choice":
        # 選擇題也接受打字：比對值或按鈕文字
        for value, label in step.choices:
            if text in (value, label) or text in label:
                await _store_answer(message, state, value)
                return
        await message.answer("請點上面的按鈕選一個。")
        return
    await _store_answer(message, state, text)


async def _answer_step_button(
    message: types.Message, state: FSMContext, note_service: NoteService, card_service: CardService, parts: list[str]
) -> None:
    """新增一筆時的按鈕：選項、今天／昨天、略過；建立新的人時的「略過」。Step buttons."""
    current = await state.get_state()
    if current == PersonStates.create_context.state and parts[1] == "skip":
        await message.edit_reply_markup(reply_markup=None)
        await _create_person(message, state, note_service, card_service, context="")
        return
    if current != PersonStates.add_step.state:
        await message.answer("⌛ 這個步驟已結束，請重新開始。")
        return
    data = await state.get_data()
    step = view.ADD_STEPS[data["field"]][data["step"]]
    await message.edit_reply_markup(reply_markup=None)
    if parts[1] == "skip":
        await _store_answer(message, state, None)
    elif parts[2].startswith("t"):
        await _store_answer(message, state, view.today_choice(_today(), int(parts[2][1:])))
    else:
        await _store_answer(message, state, step.choices[int(parts[2])][0])


async def _save_item(
    message: types.Message, state: FSMContext, note_service: NoteService, card_service: CardService
) -> None:
    """儲存新增的一筆（互動紀錄附帶的「下次要問」各存成一筆待追蹤）。Save the new item."""
    if await state.get_state() != PersonStates.add_confirm.state:
        await message.answer("⌛ 這個步驟已結束，請重新開始。")
        return
    data = await state.get_data()
    await state.clear()
    note_id, field_name = int(data["nid"]), str(data["field"])
    item = {k: v for k, v in dict(data["item"]).items() if not k.startswith("_")}
    await message.edit_reply_markup(reply_markup=None)
    if field_name == "Interactions_JSON":
        warning = await note_service.run_action(note_id, "add_interaction", {"item": item})
        for line in data["item"].get("_followups") or []:
            warning = await note_service.run_action(note_id, "add_followup", {"item": {"item": line, "type": "ask"}})
    elif field_name == "Followups_JSON":
        warning = await note_service.run_action(note_id, "add_followup", {"item": item})
    else:
        warning = await note_service.run_action(note_id, "add_item", {"field": field_name, "item": item})
    await send_summary(message, card_service, note_id, prefix="✅ 已儲存。" + sync_suffix(warning))


# =============================================================================
# 改資料（一般欄位）
# =============================================================================


async def _send_edit_groups(message: types.Message, note_id: int) -> None:
    rows = [[_button(name, f"pp:eg:{note_id}:{i}")] for i, (name, _) in enumerate(view.FIELD_GROUPS)]
    rows.append([_button("⬅️ 摘要", f"pp:s:{note_id}")])
    await message.answer("✏️ 要改哪一類？", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def _send_edit_fields(message: types.Message, note_id: int, group_index: int) -> None:
    _, names = view.FIELD_GROUPS[group_index]
    buttons = [_button(view.field_title(name), f"pp:ef:{note_id}:{view.EDITABLE_FIELDS.index(name)}") for name in names]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([_button("⬅️ 上一層", f"pp:edit:{note_id}")])
    await message.answer("要改哪一個欄位？", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def _start_edit(
    message: types.Message, state: FSMContext, card_service: CardService, note_id: int, field_name: str
) -> None:
    loaded = await _load(card_service, note_id)
    if loaded is None:
        await message.answer(CARD_NOT_FOUND)
        return
    current = loaded[0].get(field_name, "")
    await state.clear()
    await state.set_state(PersonStates.edit_value)
    await state.update_data(nid=note_id, field=field_name)
    info = PersonCreateParams.model_fields[field_name]
    hint = f"\n<i>{escape_tg(info.description, 120)}</i>" if info.description else ""
    shown = anki_field_to_tg_text(current, 600) if current else "（空白）"
    await message.answer(
        f"✏️ <b>{escape_tg(view.field_title(field_name))}</b>{hint}\n\n目前：{shown}\n\n"
        "輸入新的內容（輸入「清空」清除）：",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[CANCEL_ROW]),
        parse_mode="HTML",
    )


@router.message(PersonStates.edit_value, F.text)
async def process_edit_value(
    message: types.Message, state: FSMContext, note_service: NoteService, card_service: CardService
) -> None:
    """改資料時輸入的內容：文字區段且原本有內容時，問要取代還是接在後面。Typed new value."""
    data = await state.get_data()
    field_name = str(data["field"])
    text = (message.text or "").strip()
    value = "" if text == "清空" else text
    if value and not view.is_multiline(field_name):
        try:
            # 格式檢查與建立時相同（日期、親密度等）；稱呼以外的欄位搭配假的稱呼
            PersonCreateParams.model_validate({"Display_Name": "x", field_name: value})
        except ValidationError as e:
            detail = str(e.errors()[0].get("msg", ""))
            await message.answer(f"❌ 格式不對：{escape_tg(detail, 120)}，請重新輸入。", parse_mode="HTML")
            return
    if field_name == "Display_Name" and not value:
        await message.answer("稱呼不能是空的，請重新輸入。")
        return
    loaded = await _load(card_service, int(data["nid"]))
    current = loaded[0].get(field_name, "") if loaded else ""
    await state.update_data(value=value)
    if value and view.is_multiline(field_name) and current.strip():
        await state.set_state(PersonStates.edit_mode)
        await message.answer(
            "要取代原本的內容，還是接在後面？",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [_button("🔁 取代", "pp:em:r"), _button("➕ 接在後面", "pp:em:a")], CANCEL_ROW,
            ]),
        )
        return
    await _finish_edit(message, state, note_service, card_service, append=False)


async def _finish_edit(
    message: types.Message, state: FSMContext, note_service: NoteService, card_service: CardService, *, append: bool
) -> None:
    """寫入改好的欄位。Write the edited field."""
    current_state = await state.get_state()
    if current_state not in (PersonStates.edit_value.state, PersonStates.edit_mode.state):
        await message.answer("⌛ 這個步驟已結束，請重新開始。")
        return
    data = await state.get_data()
    await state.clear()
    note_id, field_name, value = int(data["nid"]), str(data["field"]), str(data.get("value", ""))
    new_value = view.text_to_field_html(value)
    if append:
        loaded = await _load(card_service, note_id)
        current = loaded[0].get(field_name, "") if loaded else ""
        new_value = f"{current}<br>{new_value}" if current.strip() else new_value
    result = await note_service.update_fields(HANDLER_NAME, note_id, {field_name: new_value})
    await send_summary(message, card_service, note_id,
                       prefix=f"✅ 已更新「{escape_tg(view.field_title(field_name))}」。" +
                       sync_suffix(result.get("sync_warning")))  # type: ignore[arg-type]


# =============================================================================
# 照片
# =============================================================================


async def _start_photo(message: types.Message, state: FSMContext, note_id: int) -> None:
    await state.clear()
    await state.set_state(PersonStates.waiting_photo)
    await state.update_data(nid=note_id)
    await message.answer(
        "📷 傳一張照片（會放在「認人」卡的正面與背面頭像）：",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[_button("🗑 移除照片", f"pp:phrm:{note_id}"), *CANCEL_ROW]]),
    )


@router.message(PersonStates.waiting_photo, F.photo)
async def process_photo(
    message: types.Message,
    state: FSMContext,
    note_service: NoteService,
    card_service: CardService,
    anki_client: AnkiClient,
) -> None:
    """收到照片：存進 Anki 媒體，再設成這個人的照片。Store the photo and set it."""
    data = await state.get_data()
    note_id = int(data["nid"])
    status = await message.reply("⏳ 處理照片中…")
    try:
        bot = message.bot
        photo = message.photo[-1] if message.photo else None
        file = await bot.get_file(photo.file_id) if bot and photo else None
        if not bot or not file or not file.file_path:
            await status.edit_text("❌ 照片下載失敗，請再傳一次。")
            return
        buffer = BytesIO()
        await bot.download_file(file.file_path, buffer)
        loaded = await _load(card_service, note_id)
        card_id = (loaded[0].get("Card_ID") if loaded else "") or str(note_id)
        filename = f"person_{card_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        await anki_client.store_media_file(
            params=AnkiStoreMediaParams(filename=filename, data=base64.b64encode(buffer.getvalue()).decode("ascii"))
        )
        warning = await note_service.run_action(note_id, "set_photo", {"filename": filename})
        await state.clear()
        await status.delete()
        await send_summary(message, card_service, note_id, prefix="✅ 已更新照片。" + sync_suffix(warning))
    except (TelegramAPIError, IOError) as e:
        logger.error("人脈卡照片處理失敗: %s", e)
        await status.edit_text(f"❌ 照片處理失敗：{error_detail(e)}\n請再傳一次。", parse_mode="HTML")
    except (FluencyTidesError, AnkiConnectError) as e:
        await state.clear()
        await _reply_error(message, e)


# =============================================================================
# 建立新的人（/newcard → 人脈卡、/p 找不到時）
# =============================================================================


async def start_person_create(message: types.Message, state: FSMContext, *, name: str | None = None) -> None:
    """開始建立一個人：問稱呼（已知時跳過）與認識場合。Start creating a person."""
    await state.clear()
    if name:
        await state.set_state(PersonStates.create_context)
        await state.update_data(name=name)
        await _ask_context(message, name)
        return
    await state.set_state(PersonStates.create_name)
    await message.answer(
        "👤 <b>人脈卡</b>\n\n他的稱呼是？（例如「田中さん」）",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[CANCEL_ROW]),
        parse_mode="HTML",
    )


async def _ask_context(message: types.Message, name: str) -> None:
    await message.answer(
        f"在哪裡認識「{escape_tg(name, 40)}」的？（例如「Geekly 面談」）",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[_button("略過", "pp:skip"), *CANCEL_ROW]]),
        parse_mode="HTML",
    )


@router.message(PersonStates.create_name, F.text)
async def process_create_name(message: types.Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name:
        await message.answer("請輸入稱呼。")
        return
    await state.set_state(PersonStates.create_context)
    await state.update_data(name=name)
    await _ask_context(message, name)


@router.message(PersonStates.create_context, F.text)
async def process_create_context(
    message: types.Message, state: FSMContext, note_service: NoteService, card_service: CardService
) -> None:
    try:
        await _create_person(message, state, note_service, card_service, context=(message.text or "").strip())
    except (FluencyTidesError, AnkiConnectError) as e:
        await _reply_error(message, e)


async def _create_person(
    message: types.Message,
    state: FSMContext,
    note_service: NoteService,
    card_service: CardService,
    *,
    context: str,
) -> None:
    """建立並回覆摘要；有認識場合時認識日期記為今天。Create the person and reply with the summary."""
    data = await state.get_data()
    await state.clear()
    params = {"Display_Name": str(data.get("name", "")), "Met_Where": context}
    if context:
        params["Met_Date"] = _today().isoformat()
    result = await note_service.create(HANDLER_NAME, deck=PERSON_DECK, note_type=PERSON_NOTE_TYPE, params=params)
    note_id = int(result["note_ids"][0])  # type: ignore[index]
    await send_summary(
        message, card_service, note_id,
        prefix=f"✅ 已建立，放在 <code>{PERSON_DECK}</code> 牌組。" + sync_suffix(result.get("sync_warning")),  # type: ignore[arg-type]
    )
