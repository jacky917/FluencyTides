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
from app.services.task_handlers.capabilities import CreateCapability, HandlerCapabilities, JsonFieldSpec, Operation
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
    )
    JSON_FIELDS = tuple(PERSON_JSON_ITEMS)
    JSON_FIELD_SPECS = {name: JsonFieldSpec(item=model, title=title) for name, (model, title) in PERSON_JSON_ITEMS.items()}
    MEDIA_FIELDS = ("Photo",)
    CREATE_TAGS = ("PersonProfile", "HandlerGenerated")
    CREATE_PARAMS = PersonCreateParams

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
        if action == "update_fields":
            await self._update_fields(card_service, relation_service, note_id, parameters)
        elif action == "add_interaction":
            params = _parse(_AddInteraction, parameters)
            item = _validate_item("Interactions_JSON", params.item)
            await AnkiJsonFieldManager.append_to_list(card_service, note_id, "Interactions_JSON", item)
            await self._bump_last_contact(card_service, note_id, str(item["date"]))
            await self._touch(card_service, note_id)
        elif action == "add_followup":
            params = _parse(_AddFollowup, parameters)
            item = _validate_item("Followups_JSON", {"added": today_iso(), **params.item})
            await AnkiJsonFieldManager.append_to_list(card_service, note_id, "Followups_JSON", item)
            await self._touch(card_service, note_id)
        elif action == "set_followup_status":
            params = _parse(_SetFollowupStatus, parameters)
            if params.status not in ("open", "done"):
                raise InvalidNoteFieldsError(f"待追蹤的狀態只能是 open 或 done：{params.status}")
            items = await AnkiJsonFieldManager.safe_read_list(card_service, note_id, "Followups_JSON")
            index = resolve_index(items, params.index, params.fingerprint)
            if not isinstance(items[index], dict):
                raise InvalidNoteFieldsError("這一筆待追蹤的格式不對，請在網頁或 Anki 修正。")
            items[index]["status"] = params.status
            await AnkiJsonFieldManager.update_field(card_service, note_id, "Followups_JSON", items)
            await self._touch(card_service, note_id)
        elif action == "add_item":
            params = _parse(_AddItem, parameters)
            if params.field not in GENERIC_LISTS:
                raise InvalidNoteFieldsError(f"add_item 不能新增到 {params.field}")
            item = _validate_item(params.field, params.item)
            await AnkiJsonFieldManager.append_to_list(card_service, note_id, params.field, item)
            await self._touch(card_service, note_id)
        elif action == "remove_item":
            params = _parse(_RemoveItem, parameters)
            if params.field not in self.JSON_FIELDS:
                raise InvalidNoteFieldsError(f"{params.field} 不是人脈卡的清單")
            items = await AnkiJsonFieldManager.safe_read_list(card_service, note_id, params.field)
            index = resolve_index(items, params.index, params.fingerprint)
            items.pop(index)
            await AnkiJsonFieldManager.update_field(card_service, note_id, params.field, items)
            await self._touch(card_service, note_id)
        elif action == "set_photo":
            params = _parse(_SetPhoto, parameters)
            if params.filename and not _MEDIA_NAME.fullmatch(params.filename):
                raise InvalidNoteFieldsError(f"照片檔名不合法：{params.filename}")
            photo = f'<img src="{html.escape(params.filename)}">' if params.filename else ""
            await card_service.update_note_fields(note_id, {"Photo": photo}, model_name=PERSON_NOTE_TYPE)
            await self._touch(card_service, note_id)
        else:
            await super().execute_update(card_service, relation_service, note_id, parameters)

    @override
    async def after_json_update(
        self, card_service: CardService, note_id: int, field_name: str, items: list[object]
    ) -> None:
        """網頁整欄寫入之後：互動紀錄的最新日期推進 ``Last_Contact``，並更新 ``Updated_At``。

        After the web writes a whole field: move ``Last_Contact`` forward and stamp ``Updated_At``.
        """
        if field_name == "Interactions_JSON":
            dates = [str(item.get("date", "")) for item in items if isinstance(item, dict)]
            if dates:
                await self._bump_last_contact(card_service, note_id, max(dates))
        await self._touch(card_service, note_id)

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
            await self._touch(card_service, note_id)

    async def _bump_last_contact(self, card_service: CardService, note_id: int, contact_date: str) -> None:
        """互動的日期比 ``Last_Contact`` 新時更新它。Move ``Last_Contact`` forward."""
        current = note_field_values(await card_service.get_note(note_id)).get("Last_Contact", "").strip()
        if contact_date > current:
            await card_service.update_note_fields(
                note_id, {"Last_Contact": contact_date}, model_name=PERSON_NOTE_TYPE
            )

    async def _touch(self, card_service: CardService, note_id: int) -> None:
        """寫入今天的 ``Updated_At``。Stamp today's date into ``Updated_At``."""
        await card_service.update_note_fields(note_id, {"Updated_At": today_iso()}, model_name=PERSON_NOTE_TYPE)


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
    return {key: value for key, value in data.items() if key in keep or value not in ("", None, False)}


def _first_error(error: ValidationError) -> str:
    """第一個驗證錯誤的「欄位：原因」。The first validation error as text."""
    first = error.errors()[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    return f"{location}：{first.get('msg', '')}" if location else str(first.get("msg", ""))
