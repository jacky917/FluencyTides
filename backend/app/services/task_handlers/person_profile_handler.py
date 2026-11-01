"""
人脈卡任務處理器 (Person Profile Handler) 模組。

負責 Person_Profile_Dark：網頁與 TG 建立、修改一般欄位、刪除；JSON 清單與照片經由具名動作寫入
（計畫書 ``docs/wip/person_profile_card_FEAT_2026-10-23.md`` §3.11、§3.12）。

Person profile handler module: owns Person_Profile_Dark; JSON lists and the
photo are written through named actions, not as plain text.
"""

import html
import re
import secrets
from datetime import date, datetime
from typing import TypeVar, override

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.exceptions import FieldNotEditableError, InvalidNoteFieldsError, JsonItemChangedError
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager
from app.schemas.person_profile import (
    PERSON_JSON_ITEMS,
    PERSON_NOTE_TYPE,
    PersonCreateParams,
    item_fingerprint,
)
from app.services.card_service import CardService
from app.services.relation_service import RelationService
from app.services.task_handlers.base import BaseHandler, field_text, note_field_values
from app.services.task_handlers.capabilities import (
    CreateCapability,
    HandlerCapabilities,
    JsonFieldSpec,
    Operation,
    SearchField,
)
from app.services.task_handlers.registry import register_handler

#: 系統維護、不能經 ``update_fields`` 改的欄位：TG 深連結以 ``Card_ID`` 找人，改了就斷線。
#: Fields the system owns; TG deep links find a person by ``Card_ID``.
SYSTEM_FIELDS = ("Card_ID",)

#: ``add_item`` 能新增的清單（互動紀錄與待追蹤各有自己的動作）。Lists ``add_item`` accepts.
GENERIC_LISTS = ("Family_JSON", "Gifts_JSON", "Key_Dates_JSON", "Contacts_JSON")

#: 照片檔名：Anki 媒體資料夾裡的檔名，只允許安全的字元。A safe media file name.
_MEDIA_NAME = re.compile(r"^[\w.\-]{1,120}$")

ACTIONS = ("add_interaction", "add_followup", "set_followup_status", "add_item", "remove_item", "set_photo")


def today_iso() -> str:
    """今天（伺服器時區；正式機容器設 ``TZ=Asia/Tokyo``）。Today in the server's time zone."""
    return date.today().isoformat()


def new_card_id() -> str:
    """新的 ``Card_ID``：``PP_YYYYMMDD_HHMMSS_xxxx``（同一秒建立多人也不重複）。

    A new ``Card_ID``; the random suffix keeps same-second creations apart.
    只含英數字與底線，能放進 TG 深連結（``{動作}-{欄位}-{序號}-{Card_ID}``）。
    """
    return f"PP_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(2)}"


def resolve_index(items: list[object], index: int, fingerprint: str | None) -> int:
    """找出要動的那一筆：序號上的那筆指紋相符就用它；否則在清單裡找同指紋的唯一一筆。

    Find the item to change: the one at ``index`` when its fingerprint
    matches, otherwise the only item with that fingerprint.

    Args:
        items: 目前的清單。The current list.
        index: 卡片或 TG 上的序號（可能過時）。The possibly stale index.
        fingerprint: 那一筆的指紋；None 表示只看序號。The fingerprint, or None.

    Returns:
        目前清單中的序號。The index in the current list.

    Raises:
        JsonItemChangedError: 找不到同一筆。When the item cannot be found.
    """
    if fingerprint is None:
        if 0 <= index < len(items):
            return index
        raise JsonItemChangedError("這一筆已經不在了（卡片畫面可能還沒更新），請同步後再試。")
    if 0 <= index < len(items) and item_fingerprint(items[index]) == fingerprint:
        return index
    matches = [i for i, item in enumerate(items) if item_fingerprint(item) == fingerprint]
    if len(matches) == 1:
        return matches[0]
    raise JsonItemChangedError("這一筆已經變了或不在了（卡片畫面可能還沒更新），請同步後再試。")


class _ActionParams(BaseModel):
    """具名動作的參數：多餘的鍵擋掉。Action parameters; unknown keys rejected."""

    model_config = ConfigDict(extra="forbid")
    action: str


class _AddInteraction(_ActionParams):
    item: dict[str, object]


class _AddFollowup(_ActionParams):
    item: dict[str, object]


class _SetFollowupStatus(_ActionParams):
    index: int = Field(ge=0)
    fingerprint: str | None = None
    status: str


class _AddItem(_ActionParams):
    field: str
    item: dict[str, object]


class _RemoveItem(_ActionParams):
    field: str
    index: int = Field(ge=0)
    fingerprint: str | None = None


class _SetPhoto(_ActionParams):
    filename: str = ""


@register_handler
class PersonProfileHandler(BaseHandler):
    """人脈卡任務處理器。

    Person profile card task handler.

    - 建立（網頁、TG）：參數是 ``PersonCreateParams``；自動產生 ``Card_ID``、寫入 ``TG_Bot`` 與
      ``Updated_At``、六個 JSON 欄位填 ``[]``。允許稱呼重複（不同的人可以都叫「田中さん」）。
    - 修改一般欄位：JSON、照片與 ``Card_ID`` 不能直接改；有改動時更新 ``Updated_At``。
    - 具名動作（``ACTIONS``）：追加互動紀錄（同時更新 ``Last_Contact``）、新增待追蹤、改待追蹤狀態、
      在其他清單新增一筆、刪除一筆、設定照片。項目以 §3.3 的模型驗證；刪除與改狀態以指紋確認
      要動的是同一筆。每次寫入都更新 ``Updated_At``。
    - 網頁整欄編輯 JSON 欄位（``JSON_FIELD_SPECS``）：寫入後更新 ``Updated_At``；互動紀錄有更新的日期時
      一併更新 ``Last_Contact``。
    - 刪除：標準刪除。
    """

    CAPABILITIES = HandlerCapabilities(
        create=CreateCapability(
            supported=True, channels=("web", "tg"), flow="form", deck="selected", note_type=PERSON_NOTE_TYPE
        ),
        update_fields=Operation(supported=True),
        update_json=Operation(supported=True),
        delete=Operation(supported=True),
        actions=ACTIONS,
        search=Operation(supported=True),
    )
    JSON_FIELDS = tuple(PERSON_JSON_ITEMS)
    JSON_FIELD_SPECS = {name: JsonFieldSpec(item=model, title=title) for name, (model, title) in PERSON_JSON_ITEMS.items()}
    MEDIA_FIELDS = ("Photo",)
    CREATE_TAGS = ("PersonProfile", "HandlerGenerated")
    CREATE_PARAMS = PersonCreateParams
    #: 網頁欄位搜尋的按鈕：名字合併各語言的姓名，相近的欄位放在一起。The field-search buttons.
    SEARCH_FIELDS = (
        SearchField("name", "名字", ("Display_Name", "Name_ZH", "Name_EN", "Name_JA", "Name_JA_Kana", "Name_Romaji")),
        SearchField("relationship", "關係", ("Relationship",)),
        SearchField("company", "公司", ("Company",)),
        SearchField("title", "職稱", ("Title",)),
        SearchField("work", "工作", ("Work_Detail",)),
        SearchField("interests", "興趣", ("Interests",)),
        SearchField("food", "飲食", ("Food_Drink", "Dietary")),
        SearchField("personality", "個性", ("Personality_Values", "First_Impression", "Praise", "Ask_Advice")),
        SearchField("met", "認識", ("Met_Where", "Met_Via", "Met_Date")),
        SearchField("place", "出身／現居", ("Hometown", "Residence")),
        SearchField("interactions", "互動", ("Interactions_JSON",)),
        SearchField("followups", "待追蹤", ("Followups_JSON",)),
        SearchField("family", "家人", ("Family_JSON",)),
        SearchField("gifts", "禮物", ("Gifts_JSON",)),
        SearchField("dates", "重要日期", ("Key_Dates_JSON",)),
        SearchField("contacts", "聯絡方式", ("Contacts_JSON",)),
        SearchField("notes", "備註", ("Notes",)),
    )

    @property
    @override
    def handler_name(self) -> str:
        """處理器名稱。Handler name."""
        return "person_profile"

    @property
    @override
    def title(self) -> str:
        """中文名稱。Display title."""
        return "人脈卡"

    @property
    @override
    def supported_models(self) -> list[str]:
        """支援的 Anki 模型名稱清單。Supported Anki model names."""
        return [PERSON_NOTE_TYPE]

    @override
    async def execute_create(
        self,
        card_service: CardService,
        relation_service: RelationService,
        deck_name: str,
        model_name: str,
        parameters: dict[str, object],
    ) -> list[int]:
        """建立一個人：填入使用者給的欄位與系統欄位。

        Create a person with the given fields and the system fields.

        Args:
            card_service: 卡片服務。Card service.
            relation_service: 關聯服務（不用）。Relation service (unused).
            deck_name: 目標牌組。Target deck.
            model_name: 筆記類型（服務層已確認）。The note type.
            parameters: ``PersonCreateParams`` 驗證過的值，加上服務層補的 ``tg_bot``。
                The validated parameters plus ``tg_bot``.

        Returns:
            建立的 Note ID。The created note ID.
        """
        values = dict(parameters)
        tg_bot = str(values.pop("tg_bot", "") or "")
        fields = {name: str(value) for name, value in values.items() if value not in (None, "")}
        fields.update({name: "[]" for name in self.JSON_FIELDS})
        fields.update({"Card_ID": new_card_id(), "TG_Bot": tg_bot, "Updated_At": today_iso()})
        note_id = await card_service.create_note(
            deck_name=deck_name,
            model_name=model_name,
            fields=fields,
            tags=list(self.CREATE_TAGS),
            allow_duplicate=True,
        )
        return [note_id]

    @override
    async def execute_update(
        self,
        card_service: CardService,
        relation_service: RelationService,
        note_id: int,
        parameters: dict[str, object],
    ) -> None:
        """修改一般欄位，或執行具名動作。

        Update plain fields, or run a named action.

        Args:
            card_service: 卡片服務。Card service.
            relation_service: 關聯服務。Relation service.
            note_id: 筆記 ID。The note.
            parameters: ``{"action": ..., ...}``；``update_fields`` 同基底，其他見 ``ACTIONS``。

        Raises:
            FieldNotEditableError: ``update_fields`` 改了 JSON、照片或 ``Card_ID``。A locked field.
            InvalidNoteFieldsError: 動作參數或項目內容不符格式。Invalid parameters or items.
            JsonItemChangedError: 要刪除或改狀態的那一筆已經變了。The item changed.
            HandlerOperationUnsupportedError: 不認得的動作。An unknown action.
        """
        action = parameters.get("action")
        # 每個具名動作讀一次、寫一次：清單、Last_Contact、Updated_At 在同一次寫入，不會只寫了一半
        if action == "update_fields":
            await self._update_fields(card_service, relation_service, note_id, parameters)
        elif action == "add_interaction":
            params = _parse(_AddInteraction, parameters)
            item = _validate_item("Interactions_JSON", params.item)
            values = await _read(card_service, note_id)
            items = _list(values, "Interactions_JSON") + [item]
            changes = {"Interactions_JSON": AnkiJsonFieldManager.serialize(items)}
            changes.update(_last_contact(values, str(item["date"])))
            await _write(card_service, note_id, changes)
        elif action == "add_followup":
            params = _parse(_AddFollowup, parameters)
            item = _validate_item("Followups_JSON", {"added": today_iso(), **params.item})
            items = _list(await _read(card_service, note_id), "Followups_JSON") + [item]
            await _write(card_service, note_id, {"Followups_JSON": AnkiJsonFieldManager.serialize(items)})
        elif action == "set_followup_status":
            params = _parse(_SetFollowupStatus, parameters)
            if params.status not in ("open", "done"):
                raise InvalidNoteFieldsError(f"待追蹤的狀態只能是 open 或 done：{params.status}")
            items = _list(await _read(card_service, note_id), "Followups_JSON")
            index = resolve_index(items, params.index, params.fingerprint)
            if not isinstance(items[index], dict):
                raise InvalidNoteFieldsError("這一筆待追蹤的格式不對，請在網頁或 Anki 修正。")
            items[index]["status"] = params.status
            await _write(card_service, note_id, {"Followups_JSON": AnkiJsonFieldManager.serialize(items)})
        elif action == "add_item":
            params = _parse(_AddItem, parameters)
            if params.field not in GENERIC_LISTS:
                raise InvalidNoteFieldsError(f"add_item 不能新增到 {params.field}")
            item = _validate_item(params.field, params.item)
            items = _list(await _read(card_service, note_id), params.field) + [item]
            await _write(card_service, note_id, {params.field: AnkiJsonFieldManager.serialize(items)})
        elif action == "remove_item":
            params = _parse(_RemoveItem, parameters)
            if params.field not in self.JSON_FIELDS:
                raise InvalidNoteFieldsError(f"{params.field} 不是人脈卡的清單")
            items = _list(await _read(card_service, note_id), params.field)
            index = resolve_index(items, params.index, params.fingerprint)
            items.pop(index)
            await _write(card_service, note_id, {params.field: AnkiJsonFieldManager.serialize(items)})
        elif action == "set_photo":
            params = _parse(_SetPhoto, parameters)
            if params.filename and not _MEDIA_NAME.fullmatch(params.filename):
                raise InvalidNoteFieldsError(f"照片檔名不合法：{params.filename}")
            photo = f'<img src="{html.escape(params.filename)}">' if params.filename else ""
            await _write(card_service, note_id, {"Photo": photo})
        else:
            await super().execute_update(card_service, relation_service, note_id, parameters)

    @override
    async def after_json_update(
        self, card_service: CardService, note_id: int, field_name: str, items: list[object]
    ) -> None:
        """網頁整欄寫入之後：互動紀錄的最新日期推進 ``Last_Contact``，並更新 ``Updated_At``。

        After the web writes a whole field: move ``Last_Contact`` forward and stamp ``Updated_At``.
        """
        changes: dict[str, str] = {}
        if field_name == "Interactions_JSON":
            dates = [str(item.get("date", "")) for item in items if isinstance(item, dict)]
            if dates:
                changes.update(_last_contact(await _read(card_service, note_id), max(dates)))
        await _write(card_service, note_id, changes)

    async def _update_fields(
        self,
        card_service: CardService,
        relation_service: RelationService,
        note_id: int,
        parameters: dict[str, object],
    ) -> None:
        """修改一般欄位（標準實作）：``Card_ID`` 不能改；真的有改動時更新 ``Updated_At``。

        Plain field update; ``Card_ID`` is locked; ``Updated_At`` follows changes.
        """
        fields = parameters.get("fields")
        changed = False
        if isinstance(fields, dict):
            locked = [name for name in fields if name in SYSTEM_FIELDS]
            if locked:
                raise FieldNotEditableError(f"這些欄位由系統維護，不能直接修改：{'、'.join(locked)}")
            current = note_field_values(await card_service.get_note(note_id))
            changed = any(field_text(value) != current.get(name) for name, value in fields.items())
        await super().execute_update(card_service, relation_service, note_id, parameters)
        if changed and isinstance(fields, dict) and "Updated_At" not in fields:
            await _write(card_service, note_id, {})


async def _read(card_service: CardService, note_id: int) -> dict[str, str]:
    """讀出筆記的欄位原始值。The note's raw field values."""
    return note_field_values(await card_service.get_note(note_id))


def _list(values: dict[str, str], field: str) -> list[object]:
    """解析一個 JSON 清單欄位。Parse one JSON list field.

    Raises:
        AnkiFieldCorruptedError: 欄位格式壞了（不覆寫，免得蓋掉原本的資料）。When the field is broken.
    """
    return AnkiJsonFieldManager.parse_field_string(values.get(field, ""))


def _last_contact(values: dict[str, str], contact_date: str) -> dict[str, str]:
    """互動的日期比 ``Last_Contact`` 新時要寫入的變更。The change moving ``Last_Contact`` forward."""
    return {"Last_Contact": contact_date} if contact_date > values.get("Last_Contact", "").strip() else {}


async def _write(card_service: CardService, note_id: int, changes: dict[str, str]) -> None:
    """一次寫入這些欄位，並附上今天的 ``Updated_At``。Write the changes plus today's ``Updated_At`` at once."""
    await card_service.update_note_fields(
        note_id, {**changes, "Updated_At": today_iso()}, model_name=PERSON_NOTE_TYPE
    )


_Params = TypeVar("_Params", bound=_ActionParams)


def _parse(model: type[_Params], parameters: dict[str, object]) -> _Params:
    """驗證動作參數。Validate action parameters.

    Raises:
        InvalidNoteFieldsError: 格式不對。On invalid parameters.
    """
    try:
        return model.model_validate(parameters)
    except ValidationError as e:
        raise InvalidNoteFieldsError(f"{parameters.get('action')} 的參數不正確：{_first_error(e)}") from e


def _validate_item(field: str, item: dict[str, object]) -> dict[str, object]:
    """以該清單的模型驗證一筆項目，回傳要寫入的 dict（保留多餘的鍵、去掉空值的選填鍵）。

    Validate one item with the list's model; returns the dict to store.

    Raises:
        InvalidNoteFieldsError: 格式不對。On an invalid item.
    """
    model = PERSON_JSON_ITEMS[field][0]
    try:
        validated = model.model_validate(item)
    except ValidationError as e:
        raise InvalidNoteFieldsError(f"{PERSON_JSON_ITEMS[field][1]}的內容不正確：{_first_error(e)}") from e
    data = validated.model_dump()
    # 選填的空字串、None、False 不存（卡片也不顯示），清單比較乾淨；必要的鍵與種類、狀態、方向一律保留
    keep = {name for name, info in model.model_fields.items() if info.is_required()} | {"type", "status", "direction"}
    # 用 is False 比對：0 == False，用 in 會把數值 0 當成空值丟掉
    return {key: value for key, value in data.items() if key in keep or not (value in ("", None) or value is False)}


def _first_error(error: ValidationError) -> str:
    """第一個驗證錯誤的「欄位：原因」。The first validation error as text."""
    first = error.errors()[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    return f"{location}：{first.get('msg', '')}" if location else str(first.get("msg", ""))
