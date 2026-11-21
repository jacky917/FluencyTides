"""Person profile handler module.

人脈卡任務處理器（Person Profile Handler）模組。

Owns Person_Profile_Dark: creation from the web and TG, plain field updates, and deletion; JSON lists and the
photo are written through named actions (plan ``docs/archive/person_profile_card_FEAT_2026-10-23.md`` §3.11,
§3.12). Fields that change over time (interests, positions, residence, and so on; ``PERSON_HISTORY_FIELDS``)
are JSON lists too, each entry with a recorded date and an end date
(``docs/wip/person_field_history_FEAT_2026-11-07.md``).

負責 Person_Profile_Dark：網頁與 TG 建立、修改一般欄位、刪除；JSON 清單與照片經由具名動作寫入
（計畫書 ``docs/archive/person_profile_card_FEAT_2026-10-23.md`` §3.11、§3.12）。會變的欄位
（興趣、職位、現居地等，``PERSON_HISTORY_FIELDS``）也是 JSON 清單，每筆附登錄日與失效日
（``docs/wip/person_field_history_FEAT_2026-11-07.md``）。
"""

import html
import logging
import re
import secrets
from datetime import date
from typing import Literal, TypeVar, override

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.core import clock
from app.core.exceptions import (
    ActionContractError,
    AnkiFieldCorruptedError,
    FieldNotEditableError,
    InvalidNoteFieldsError,
    JsonItemChangedError,
)
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager
from app.schemas.person_profile import (
    HISTORY_PERIOD_KEYS,
    ITEM_EDIT_KEYS,
    PERSON_HISTORY_FIELDS,
    PERSON_JSON_ITEMS,
    PERSON_NOTE_TYPE,
    RETIRED_FIELDS,
    NOTE_FIELDS,
    PersonCreateParams,
    entry_detail,
    entry_key,
    entry_text,
    history_from_input,
    history_from_text,
    is_active,
    item_fingerprint,
    merged_note,
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

logger = logging.getLogger(__name__)

#: Fields the system maintains, which ``update_fields`` cannot change: TG deep links find a person by
#: ``Card_ID``, so changing it breaks them.
#: 系統維護、不能經 ``update_fields`` 改的欄位：TG 深連結以 ``Card_ID`` 找人，改了就斷線。
SYSTEM_FIELDS = ("Card_ID",)

#: The lists ``add_item`` can add to (interactions and follow-ups each have their own action).
#: ``add_item`` 能新增的清單（互動紀錄與待追蹤各有自己的動作）。
GENERIC_LISTS = ("Family_JSON", "Gifts_JSON", "Key_Dates_JSON", "Contacts_JSON", "Goals_JSON")

#: The photo file name: a file name in the Anki media folder, safe characters only.
#: 照片檔名：Anki 媒體資料夾裡的檔名，只允許安全的字元。
_MEDIA_NAME = re.compile(r"^[\w.\-]{1,120}$")

#: The history field names (a tuple in fixed order, appended after ``JSON_FIELDS``).
#: 會變的欄位名（固定順序的 tuple，接在 ``JSON_FIELDS`` 後面用）。
HISTORY_FIELDS = tuple(PERSON_HISTORY_FIELDS)

ACTIONS = (
    "add_interaction",
    "add_followup",
    "set_followup_status",
    "add_item",
    "set_goal_status",
    "add_history",
    "set_history_status",
    "edit_position",
    "edit_history_period",
    "edit_item",
    "set_hidden",
    "remove_item",
    "set_photo",
)


def today_iso() -> str:
    """Return today's date in ``APP_TIMEZONE`` (see :mod:`app.core.clock`).

    今天（``APP_TIMEZONE``，見 ``app.core.clock``）。

    Returns:
        today as an ISO date string
        今天的 ISO 日期字串
    """
    return clock.today_iso()


def new_card_id() -> str:
    """Return a new ``Card_ID``: ``PP_YYYYMMDD_HHMMSS_xxxx``, unique even for people created in the same second.

    新的 ``Card_ID``：``PP_YYYYMMDD_HHMMSS_xxxx``（同一秒建立多人也不重複）。

    It contains only letters, digits, and underscores, so it fits in a TG deep link
    (``{action}-{field}-{index}-{Card_ID}``).

    只含英數字與底線，能放進 TG 深連結（``{動作}-{欄位}-{序號}-{Card_ID}``）。

    Returns:
        the new ``Card_ID``
        新的 ``Card_ID``
    """
    return f"PP_{clock.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(2)}"


def resolve_index(items: list[object], index: int, fingerprint: str | None) -> int:
    """Find the item to change in the current list.

    找出要動的那一筆。

    The item at ``index`` is used when its fingerprint matches; otherwise the only item in the list with that
    fingerprint.

    序號上的那筆指紋相符就用它；否則在清單裡找同指紋的唯一一筆。

    Args:
        items: the current list
            目前的清單
        index: the index on the card or in TG (possibly stale)
            卡片或 TG 上的序號（可能過時）
        fingerprint: that item's fingerprint; ``None`` means only the index is checked
            那一筆的指紋；``None`` 表示只看序號

    Returns:
        the index in the current list
        目前清單中的序號

    Raises:
        JsonItemChangedError: if the same item cannot be found (out of range, or zero or several items with the
            fingerprint)
            若找不到同一筆（超出範圍，或同指紋的有零筆或多筆）
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
    """The parameters of a named action; unknown keys are rejected.

    具名動作的參數：多餘的鍵擋掉。
    """

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


class _SetGoalStatus(_ActionParams):
    """The parameters for setting a goal's status.

    改目標的狀態。

    ``closed`` is the day the goal was reached or dropped (TG asks when the button is pressed; today when
    omitted); it must be a real date and not in the future. It is ignored when the goal is set back to open.

    ``closed`` 是達成或放棄的那天（TG 按的時候問；沒給就是今天），要是真的日期、不能在未來；
    改回進行中時不看它。
    """

    index: int = Field(ge=0)
    fingerprint: str | None = None
    status: Literal["open", "done", "dropped"]
    closed: date | None = None

    @model_validator(mode="after")
    def _closed_not_in_future(self) -> "_SetGoalStatus":
        if self.closed is not None and self.closed.isoformat() > today_iso():
            raise ValueError(f"結束日 {self.closed.isoformat()} 還沒到")
        return self


class _AddHistory(_ActionParams):
    field: str
    item: dict[str, object]


class _SetHistoryStatus(_ActionParams):
    field: str
    index: int = Field(ge=0)
    fingerprint: str | None = None
    ended: bool


class _EditPosition(_ActionParams):
    """The parameters for editing a job's start, end, and note (legacy action, kept for old callers).

    改一份工作的任職起訖與說明（舊動作：TG 已改用 ``edit_item``，§16；保留給舊的呼叫端）。

    TG has switched to ``edit_item`` (§16). All three values are required (they replace the set as a whole; an
    empty string clears), so a value left out is not taken as a clear. ``expected_start`` and ``expected_end``
    are the original values seen when editing began: two jobs with the same name have the same fingerprint, so
    a stale index may match the other one, and if the original values do not match, the item counts as
    changed.

    三個值都必填（整組取代，空字串＝清掉），漏送不會被當成清掉。``expected_start``、``expected_end`` 是開始
    修改時看到的原值：同名的兩份工作指紋相同，序號過時可能對到另一筆，原值對不上就當成這一筆變了。
    """

    index: int = Field(ge=0)
    fingerprint: str | None = None
    start: str
    end: str
    note: str
    expected_start: str | None = None
    expected_end: str | None = None


class _EditHistoryPeriod(_ActionParams):
    """The parameters for editing one entry's event times.

    改一筆的事件時間。

    For fields other than positions that have :data:`HISTORY_PERIOD_KEYS`; positions use ``edit_position``.
    ``values`` must be exactly that field's time keys (replacing the set as a whole; an empty string clears),
    so a key left out is not taken as a clear. ``expected`` is the original values seen when editing began:
    two entries with the same content have the same fingerprint, so a stale index may match the other one, and
    if the original values do not match, the entry counts as changed.

    職位以外有 ``HISTORY_PERIOD_KEYS`` 的欄位；職位走 ``edit_position``。
    ``values`` 要剛好是那個欄位的時間鍵（整組取代，空字串＝清掉），漏送不會被當成清掉。``expected`` 是開始修改時
    看到的原值：同內容的兩筆指紋相同，序號過時可能對到另一筆，原值對不上就當成這一筆變了。
    """

    field: str
    index: int = Field(ge=0)
    fingerprint: str | None = None
    values: dict[str, str]
    expected: dict[str, str] | None = None


class _EditItem(_ActionParams):
    """The parameters for editing one item (work history plan §16).

    改一筆（工作經歷計畫書 §16）。

    - ``values``: must be exactly the list's editable keys (:data:`ITEM_EDIT_KEYS`), replacing them as a set.
      Clearing text sends an empty string; ``None`` removes the key and leaves it to the model's default (for
      clearing a number or yes/no, or skipping a key that did not exist).
    - Other keys (recorded date, hidden, status) stay as they are. The end date also stays, except that it is
      cleared when a position's original end month is cleared (back to the current job).
    - ``expected``: the original values seen when editing began (``None``, empty string, and ``False`` all
      count as empty). Two items with the same content have the same fingerprint, so a stale index may match
      the other one, and if the original values do not match, the item counts as changed. But when that item
      already looks like ``values`` (the save succeeded, the response was lost, and the button was pressed
      again), it counts as already edited.

    - ``values``：要剛好是那個清單能改的鍵（:data:`ITEM_EDIT_KEYS`），整組取代。文字清掉送空字串；None＝拿掉
      那個鍵、交給模型的預設值（數字、是／否清掉，或原本就沒有這個鍵又略過時）。
    - 其他鍵（登錄日、隱藏、狀態）照舊。劃掉日也照舊，只有職位把原本的離職年月清掉時一併清掉（改回現職）。
    - ``expected``：開始修改時看到的原值（None、空字串、False 都算空）。同內容的兩筆指紋相同，序號過時可能對到
      另一筆，原值對不上就當成這一筆變了。但那一筆已經是 ``values`` 的樣子時（存檔成功、回應沒送到又重按），
      當成已經改好。
    """

    field: str
    index: int = Field(ge=0)
    fingerprint: str | None = None
    values: dict[str, object]
    expected: dict[str, object] | None = None


class _SetHidden(_ActionParams):
    field: str
    index: int = Field(ge=0)
    fingerprint: str | None = None
    hidden: bool


class _RemoveItem(_ActionParams):
    field: str
    index: int = Field(ge=0)
    fingerprint: str | None = None


class _SetPhoto(_ActionParams):
    filename: str = ""


@register_handler
class PersonProfileHandler(BaseHandler):
    """Person profile card task handler.

    人脈卡任務處理器。

    - Create (web, TG): the parameters are ``PersonCreateParams``; ``Card_ID`` is generated, ``TG_Bot`` and
      ``Updated_At`` are written, and the six JSON fields are filled with ``[]`` (history fields left unfilled
      stay empty, see below). Duplicate names are allowed (different people can all be called 「田中さん」).
    - Plain field updates: JSON fields and ``Card_ID`` cannot be changed directly, and retired fields
      (:data:`RETIRED_FIELDS`: title and work content, both merged into ``Company``) cannot be written with a
      non-empty value (clearing is allowed); the photo accepts only a file name obtained from a web upload
      (media_upload plan D3); ``Updated_At`` is updated when something changes.
    - Named actions (``ACTIONS``): append an interaction (also updating ``Last_Contact``), add a follow-up,
      change a follow-up's status, add an item to another list (goals always start open, recorded today),
      change a goal's status (``set_goal_status``: reached or dropped records an end date, which may be given
      and defaults to today), add an entry to a history field (``add_history``) or strike it out or restore
      it (``set_history_status``; striking out a position means leaving, and the end month is added
      separately), edit one item of any list (``edit_item``: content, times, and note), hide or show an item
      on the card (``set_hidden``), delete an item, and set the photo. Items are validated with the models of
      §3.3; delete, status changes, and hiding confirm by fingerprint that they touch the same item. Every
      write updates ``Updated_At``. The legacy actions ``edit_position`` and ``edit_history_period`` (times
      and note only) are kept for old callers; TG has switched to ``edit_item``.
    - History fields: on create, text is turned into a list (recorded date = today); the system fills in
      today as the recorded date when adding and as the end date when striking out; for single-value fields
      (residence, time zone, relationship, closeness), adding strikes out the entry that was active. When a
      list becomes empty, the field is written as an empty string (the same as leaving it unfilled at
      creation; the "who is this" card used to check whether ``Company`` had a value, and ``[]`` counted as a
      value).
    - Web whole-field JSON editing (``JSON_FIELD_SPECS``): ``Updated_At`` is updated after writing; when
      interactions get a later date, ``Last_Contact`` is updated too.
    - Delete: the standard delete.

    - 建立（網頁、TG）：參數是 ``PersonCreateParams``；自動產生 ``Card_ID``、寫入 ``TG_Bot`` 與
      ``Updated_At``、六個 JSON 欄位填 ``[]``（會變的欄位沒填時留空，見下）。允許稱呼重複（不同的人
      可以都叫「田中さん」）。
    - 修改一般欄位：JSON 與 ``Card_ID`` 不能直接改，停用的欄位（:data:`RETIRED_FIELDS`：職稱、工作內容，都已
      併進 ``Company``）也不能寫入非空的值（清空可以）；照片只能填網頁上傳取得的檔名（media_upload 計畫書 D3）；有改動時更新 ``Updated_At``。
    - 具名動作（``ACTIONS``）：追加互動紀錄（同時更新 ``Last_Contact``）、新增待追蹤、改待追蹤狀態、
      在其他清單新增一筆（目標一律進行中、登錄日今天）、改目標的狀態（``set_goal_status``：達成、放棄記下結束日，
      可以指定那天，預設今天）、
      在會變的欄位新增一筆（``add_history``）或劃掉／取消劃掉
      （``set_history_status``；職位的劃掉＝離職，另外補上離職年月）、修改任何清單的一筆
      （``edit_item``：內容、時間與說明）、在卡面隱藏或顯示一筆
      （``set_hidden``）、刪除一筆、設定照片。項目以 §3.3 的模型驗證；刪除、改狀態與隱藏以指紋確認
      要動的是同一筆。每次寫入都更新 ``Updated_At``。舊動作 ``edit_position``、``edit_history_period``（只改
      時間與說明）保留給舊的呼叫端，TG 已改用 ``edit_item``。
    - 會變的欄位：建立時把文字轉成清單（登錄日＝今天）；新增時登錄日、劃掉時失效日都由系統填今天；
      單值欄位（現居地、時區、關係、親密度）新增時把原本有效的那筆劃掉。清單空了時欄位寫空字串
      （和建卡時沒填的一樣；以前「認人」卡看 ``Company`` 有沒有值，``[]`` 也算有值）。
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
    JSON_FIELDS = (*PERSON_JSON_ITEMS, *HISTORY_FIELDS)
    JSON_FIELD_SPECS = {
        **{name: JsonFieldSpec(item=model, title=title) for name, (model, title) in PERSON_JSON_ITEMS.items()},
        **{name: JsonFieldSpec(item=spec.model, title=spec.title) for name, spec in PERSON_HISTORY_FIELDS.items()},
    }
    MEDIA_FIELDS = {"Photo": "image"}
    CREATE_TAGS = ("PersonProfile", "HandlerGenerated")
    CREATE_PARAMS = PersonCreateParams
    #: The web field-search buttons: "name" combines the names in each language, and related fields are grouped.
    #: 網頁欄位搜尋的按鈕：名字合併各語言的姓名，相近的欄位放在一起。
    SEARCH_FIELDS = (
        SearchField("name", "名字", ("Display_Name", "Name_ZH", "Name_EN", "Name_JA", "Name_JA_Kana", "Name_Romaji")),
        SearchField("relationship", "關係", ("Relationship",)),
        SearchField("company", "工作經歷", ("Company",)),
        # 鍵沿用 work（網頁記住的搜尋條件用鍵），工作內容停用後只剩學歷（§18）
        SearchField("work", "學歷", ("Education",)),
        SearchField("interests", "興趣・經歷", ("Interests", "Experiences")),
        SearchField("food", "飲食", ("Food_Drink", "Dietary")),
        SearchField("personality", "個性", ("Personality_Values", "First_Impression", "Praise", "Ask_Advice")),
        SearchField("languages", "語言", ("Languages",)),
        SearchField("met", "認識", ("Met_Where", "Met_Via", "Met_Date")),
        SearchField("place", "出身／現居", ("Hometown", "Residence")),
        SearchField("interactions", "互動", ("Interactions_JSON",)),
        SearchField("followups", "待追蹤", ("Followups_JSON",)),
        SearchField("family", "家人", ("Family_JSON",)),
        SearchField("gifts", "禮物", ("Gifts_JSON",)),
        SearchField("dates", "重要日期", ("Key_Dates_JSON",)),
        SearchField("contacts", "聯絡方式", ("Contacts_JSON", "Comm_Prefs")),
        SearchField("notes", "備註", ("Notes",)),
    )

    @property
    @override
    def handler_name(self) -> str:
        """The handler name used in URLs.

        處理器名稱，出現在網址裡。
        """
        return "person_profile"

    @property
    @override
    def title(self) -> str:
        """The Chinese display title.

        中文名稱。
        """
        return "人脈卡"

    @property
    @override
    def supported_models(self) -> list[str]:
        """The Anki note types this handler owns.

        這個處理器負責的 Anki 筆記類型。
        """
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
        """Create a person, filling in the fields the user gave and the system fields.

        建立一個人：填入使用者給的欄位與系統欄位。

        Duplicates are allowed.

        允許重複。

        Args:
            card_service: the card service
                卡片服務
            relation_service: the relation service (unused)
                關聯服務（不用）
            deck_name: the target deck
                目標牌組
            model_name: the note type (confirmed by the service layer)
                筆記類型（服務層已確認）
            parameters: the values validated by ``PersonCreateParams``, plus the ``tg_bot`` the service layer
                adds. History fields are turned into lists here (text is split by the rules, a list gives one
                entry per row; a position row is company plus title).
                ``PersonCreateParams`` 驗證過的值，加上服務層補的 ``tg_bot``。會變的欄位在這裡轉成清單
                （文字照規則切、清單每列一筆；職位一列是公司＋職稱）。

        Returns:
            the created note ID, as a one-element list
            建立的 Note ID（單元素陣列）

        Raises:
            InvalidNoteFieldsError: if a history entry does not match its model
                若會變的欄位有一筆不符模型
        """
        values = dict(parameters)
        tg_bot = str(values.pop("tg_bot", "") or "")
        today = today_iso()
        fields = {
            name: str(value)
            for name, value in values.items()
            if value not in (None, "") and name not in PERSON_HISTORY_FIELDS
        }
        for name in HISTORY_FIELDS:
            items = history_from_input(name, values.get(name), today)
            if items:
                fields[name] = AnkiJsonFieldManager.serialize([_validate_item(name, item) for item in items])
        fields.update({name: "[]" for name in PERSON_JSON_ITEMS})
        fields.update({"Card_ID": new_card_id(), "TG_Bot": tg_bot, "Updated_At": today})
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
        """Update plain fields, or run a named action.

        修改一般欄位，或執行具名動作。

        Each named action reads once and writes once.

        每個具名動作讀一次、寫一次。

        Args:
            card_service: the card service
                卡片服務
            relation_service: the relation service
                關聯服務
            note_id: the note ID
                筆記 ID
            parameters: ``{"action": ..., ...}``; ``update_fields`` is as in the base class, the others are
                listed in ``ACTIONS``
                ``{"action": ..., ...}``；``update_fields`` 同基底，其他見 ``ACTIONS``

        Raises:
            AnkiFieldCorruptedError: if the list to change is malformed (it is not overwritten)
                若要改的清單格式壞了（不覆寫）
            FieldNotEditableError: if ``update_fields`` changes a JSON field or ``Card_ID``, or writes a
                non-empty value to a retired field (:data:`RETIRED_FIELDS`)
                若 ``update_fields`` 改了 JSON、``Card_ID``，或在停用的欄位（``RETIRED_FIELDS``）寫入非空的值
            HandlerOperationUnsupportedError: if the action is unknown
                若是不認得的動作
            InvalidNoteFieldsError: if the action parameters or item contents do not match the format
                (including ``ActionContractError``)
                若動作參數或項目內容不符格式（含 ``ActionContractError``）
            JsonItemChangedError: if the item to change has changed or is gone
                若要動的那一筆已經變了或不在了
            MediaFieldInvalidError: if the photo in ``update_fields`` is not an image file name in the
                ``POST /media`` naming format
                若 ``update_fields`` 的照片不是 ``POST /media`` 命名格式的圖片檔名
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
            raw = dict(params.item)
            if params.field == "Goals_JSON":
                # 新的目標一律進行中，登錄日由系統填（同會變的欄位）
                raw.update({"added": today_iso(), "status": "open", "closed": ""})
            item = _validate_item(params.field, raw)
            items = _list(await _read(card_service, note_id), params.field) + [item]
            await _write(card_service, note_id, {params.field: AnkiJsonFieldManager.serialize(items)})
        elif action == "set_goal_status":
            params = _parse(_SetGoalStatus, parameters)
            items = _list(await _read(card_service, note_id), "Goals_JSON")
            index = resolve_index(items, params.index, params.fingerprint)
            entry = items[index]
            if not isinstance(entry, dict):
                raise InvalidNoteFieldsError("這一筆目標的格式不對，請在網頁修正。")
            entry["status"] = params.status
            # 達成、放棄記下那天（沒給就是今天）；改回進行中拿掉（選填的空值不存，和 add_item 存下來的形狀一致）
            if params.status == "open":
                entry.pop("closed", None)
            else:
                entry["closed"] = params.closed.isoformat() if params.closed else today_iso()
            await _write(card_service, note_id, {"Goals_JSON": AnkiJsonFieldManager.serialize(items)})
        elif action == "add_history":
            params = _parse(_AddHistory, parameters)
            _require_history(params.field)
            items = _list(await _read(card_service, note_id), params.field)
            items = add_history(params.field, items, params.item)
            await _write(card_service, note_id, {params.field: _serialize(params.field, items)})
        elif action == "set_history_status":
            params = _parse(_SetHistoryStatus, parameters)
            _require_history(params.field)
            items = _list(await _read(card_service, note_id), params.field)
            index = resolve_index(items, params.index, params.fingerprint)
            if not isinstance(items[index], dict):
                raise InvalidNoteFieldsError(f"這一筆{PERSON_HISTORY_FIELDS[params.field].title}的格式不對，請在網頁修正。")
            set_history_status(params.field, items[index], ended=params.ended)
            if not params.ended:
                _check_restored(params.field, items[index])
            await _write(card_service, note_id, {params.field: _serialize(params.field, items)})
        elif action == "edit_position":
            params = _parse(_EditPosition, parameters)
            items = _list(await _read(card_service, note_id), "Company")
            index = resolve_index(items, params.index, params.fingerprint)
            items[index] = edit_position(items, index, params)
            await _write(card_service, note_id, {"Company": _serialize("Company", items)})
        elif action == "edit_item":
            params = _parse(_EditItem, parameters)
            if params.field not in ITEM_EDIT_KEYS:
                raise ActionContractError(f"{params.field} 不是人脈卡的清單")
            current = await _read(card_service, note_id)
            items = _list(current, params.field)
            try:
                index = resolve_index(items, params.index, params.fingerprint)
            except JsonItemChangedError:
                # 改了內容指紋就變：存檔成功、回應沒送到又重按時，序號上那一筆已經是改好的樣子，不必再寫
                if 0 <= params.index < len(items) and _already_applied(params.field, items[params.index], params.values):
                    logger.info("人脈卡 note %s：%s #%s 已經是要改成的樣子（重送），不再寫入", note_id, params.field,
                                params.index + 1)
                    return
                raise
            old = items[index]
            items[index] = edit_item(params.field, items, index, params.values, params.expected)
            changes = {params.field: _serialize(params.field, items)}
            if params.field == "Interactions_JSON":
                changes.update(_contact_after_edit(current, items, old, items[index]))
            await _write(card_service, note_id, changes)
        elif action == "edit_history_period":
            params = _parse(_EditHistoryPeriod, parameters)
            if params.field == "Company" or params.field not in HISTORY_PERIOD_KEYS:
                spec = PERSON_HISTORY_FIELDS.get(params.field)
                what = f"「{spec.title}」" if spec else params.field
                raise InvalidNoteFieldsError(f"{what}沒有可以用這個動作改的時間（職位用 edit_position）")
            items = _list(await _read(card_service, note_id), params.field)
            index = resolve_index(items, params.index, params.fingerprint)
            items[index] = edit_history_period(params.field, items, index, params.values, params.expected)
            await _write(card_service, note_id, {params.field: _serialize(params.field, items)})
        elif action == "set_hidden":
            params = _parse(_SetHidden, parameters)
            if params.field not in self.JSON_FIELDS:
                raise InvalidNoteFieldsError(f"{params.field} 不是人脈卡的清單")
            items = _list(await _read(card_service, note_id), params.field)
            index = resolve_index(items, params.index, params.fingerprint)
            entry = items[index]
            if not isinstance(entry, dict):
                raise InvalidNoteFieldsError(f"這一筆{self.JSON_FIELD_SPECS[params.field].title}的格式不對，請在網頁修正。")
            # 不隱藏時拿掉鍵、不留 hidden: false，和 model_dump 的輸出形狀一致。指紋不含 hidden，
            # 所以隱藏、顯示都不會讓卡片上的舊連結失效
            if params.hidden:
                entry["hidden"] = True
            else:
                entry.pop("hidden", None)
            await _write(card_service, note_id, {params.field: _serialize(params.field, items)})
        elif action == "remove_item":
            params = _parse(_RemoveItem, parameters)
            if params.field not in self.JSON_FIELDS:
                raise InvalidNoteFieldsError(f"{params.field} 不是人脈卡的清單")
            items = _list(await _read(card_service, note_id), params.field)
            index = resolve_index(items, params.index, params.fingerprint)
            items.pop(index)
            await _write(card_service, note_id, {params.field: _serialize(params.field, items)})
        elif action == "set_photo":
            # TG 的頭像流程用：照片檔名是 TG 產生的 avatar_…，所以這裡只擋不安全的字元，不套用網頁
            # update_fields 的「只收 POST /media 命名格式」（media_names.media_field_text）
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
        """Maintain related fields after the web writes a whole JSON field.

        網頁整欄寫入之後維護相關欄位。

        The latest interaction date moves ``Last_Contact`` forward; an emptied history field is written as an
        empty string (the same as leaving it unfilled at creation); and ``Updated_At`` is updated.

        互動紀錄的最新日期推進 ``Last_Contact``；會變的欄位清空時寫空字串（和建卡時沒填的一樣）；
        並更新 ``Updated_At``。

        Args:
            card_service: the card service
                卡片服務
            note_id: the note ID
                筆記 ID
            field_name: the JSON field written
                寫入的 JSON 欄位
            items: the list written
                寫入的清單
        """
        changes: dict[str, str] = {}
        if field_name in PERSON_HISTORY_FIELDS and not items:
            changes[field_name] = ""
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
        """Update plain fields through the standard implementation, with this card's extra checks.

        修改一般欄位（標準實作）。

        ``Card_ID`` cannot be changed, and retired fields (:data:`RETIRED_FIELDS`) cannot be written with a
        non-empty value; ``Updated_At`` is updated only when something actually changes.

        ``Card_ID`` 不能改、停用的欄位（``RETIRED_FIELDS``）不能寫入非空的值；真的有改動時更新 ``Updated_At``。
        """
        fields = parameters.get("fields")
        changed = False
        if isinstance(fields, dict):
            locked = [name for name in fields if name in SYSTEM_FIELDS]
            if locked:
                raise FieldNotEditableError(f"這些欄位由系統維護，不能直接修改：{'、'.join(locked)}")
            retired = [name for name in fields if name in RETIRED_FIELDS and field_text(fields[name]).strip()]
            if retired:
                raise FieldNotEditableError("".join(RETIRED_FIELDS[name] for name in retired))
            current = note_field_values(await card_service.get_note(note_id))
            changed = any(field_text(value) != current.get(name) for name, value in fields.items())
        await super().execute_update(card_service, relation_service, note_id, parameters)
        if changed and isinstance(fields, dict) and "Updated_At" not in fields:
            await _write(card_service, note_id, {})


async def _read(card_service: CardService, note_id: int) -> dict[str, str]:
    """Read the note's raw field values.

    讀出筆記的欄位原始值。
    """
    return note_field_values(await card_service.get_note(note_id))


def _list(values: dict[str, str], field: str) -> list[object]:
    """Parse one JSON list field.

    解析一個 JSON 清單欄位。

    Raises:
        AnkiFieldCorruptedError: if the field is malformed (it is not overwritten, so the original data is not
            lost); the message names the field, and for history fields also hints that it may be old text not
            yet migrated
            若欄位格式壞了（不覆寫，免得蓋掉原本的資料）；訊息帶欄位名稱，會變的欄位另外提示可能是還沒轉換的舊文字
    """
    try:
        return AnkiJsonFieldManager.parse_field_string(values.get(field, ""))
    except AnkiFieldCorruptedError as e:
        if field in PERSON_HISTORY_FIELDS:
            title = PERSON_HISTORY_FIELDS[field].title
            hint = "可能是還沒轉換的舊文字，請先執行 migrate_history_fields.py，或在網頁修正。"
        else:
            title = PERSON_JSON_ITEMS[field][1] if field in PERSON_JSON_ITEMS else field
            hint = "請在網頁或 Anki 修正。"
        raise AnkiFieldCorruptedError(f"「{title}」的資料格式壞了，為避免覆寫已中止：{hint}") from e


def _require_history(field: str) -> None:
    """Check that the field is a history field.

    確認是會變的欄位。

    Raises:
        InvalidNoteFieldsError: if it is not
            若不是
    """
    if field not in PERSON_HISTORY_FIELDS:
        raise InvalidNoteFieldsError(f"{field} 不是人脈卡會變的欄位")


def add_history(field: str, items: list[object], item: dict[str, object], today: str | None = None) -> list[object]:
    """Add entries to a history field.

    在會變的欄位新增。

    The recorded date is today and the end date is blank; the content of splittable fields is split into
    several entries at commas, enumeration commas, and line breaks; entries identical to a currently active one
    are skipped; a single-value field strikes out the previously active entry only when a new entry is
    actually added (recorded date plan D5, D6).

    When an entry is the same as an active one (:func:`entry_key`) but this time carries an event time that
    entry still lacks (:data:`HISTORY_PERIOD_KEYS`, for example adding the year an interest started) or a new
    note (:data:`NOTE_FIELDS`, joined per :func:`merged_note`), it is filled into that entry instead of being
    added; when the given time differs from the one the entry already has, :class:`InvalidNoteFieldsError` is
    raised (so the time the user just typed is not silently dropped). When nothing is added or filled (a full
    duplicate), an error is raised too, so the user does not think it was saved.

    Positions are the exception: one with the same name as a current job is not added; instead its start month
    (when that entry's is empty) and note (per :func:`merged_note`) are filled into that entry; a start that
    differs from the entry's existing one does not change it. When nothing is filled, it is a duplicate. One
    with an end month (a past job) is not merged into the current job but appended; it counts as a duplicate
    only when :func:`entry_key` and both start and end match an entry.

    Only the keys the field can edit (:data:`ITEM_EDIT_KEYS`, plus the date keys that are overwritten) are
    accepted: keys of other fields (for example a note on an interest) would be stored but never shown or
    editable.

    登錄日填今天、失效日空白；可切的欄位內容以逗號、頓號、換行切成多筆；與目前有效的
    某筆相同的略過；單值欄位真的新增了一筆時才把原本有效的劃掉（登錄日計畫書 D5、D6）。

    和某筆有效的相同（:func:`entry_key`）、但這次帶了那一筆還空著的事件時間（:data:`HISTORY_PERIOD_KEYS`，例如
    補上興趣開始的年份）或新的說明（:data:`NOTE_FIELDS`，照 :func:`merged_note` 接上）時，補進那一筆，不另外
    新增；帶的時間和那一筆已有的不同時丟 :class:`InvalidNoteFieldsError`（不默默丟掉使用者剛輸入的時間）。
    什麼都沒新增也沒補到（完全重複）時也丟錯，免得使用者以為存了。

    職位例外：和某份現職同名時不另外新增，而是把到職年月（那一筆原本空著時）與說明（照 :func:`merged_note`）
    補進那一筆；到職和那一筆已有的不同時不改到職。什麼都沒補到就是重複。有離職年月的（以前的工作）不併進
    現職，直接接在後面；:func:`entry_key` 與起訖都和某一筆相同時才算重複。

    只收那個欄位能改的鍵（:data:`ITEM_EDIT_KEYS`，加上會被蓋掉的日期鍵）：別的欄位的鍵（例如興趣帶了說明）
    存了也顯示不出來、改不到。

    Args:
        field: the history field
            會變的欄位
        items: the current list (modified in place)
            目前的清單（會被修改）
        item: ``{"value": ...}``; for positions ``{"company", "title"}``. Date keys are overwritten with the
            system's values.
            ``{"value": ...}``；職位為 ``{"company", "title"}``。日期鍵會被系統的值蓋掉。
        today: today (defaults to :func:`today_iso`)
            今天（預設 ``today_iso``）

    Returns:
        the new list (``items`` itself)
        新的清單（就是 ``items``）

    Raises:
        InvalidNoteFieldsError: if the content does not match the format (for example empty, or a closeness
            outside 1–5), a key is not editable for the field (``ActionContractError``), a given time
            conflicts with an existing entry, or the entry is a full duplicate
            若內容不符格式（例如空的、親密度不是 1–5）、帶了那個欄位不能改的鍵（``ActionContractError``）、
            帶的時間和已有的一筆不同，或完全重複
    """
    today = today or today_iso()
    data = {key: value for key, value in item.items() if key not in ("added", "ended")}
    unknown = sorted(set(data) - set(ITEM_EDIT_KEYS[field]))
    if unknown:
        raise ActionContractError(
            f"{PERSON_HISTORY_FIELDS[field].title}沒有這些鍵：{'、'.join(unknown)}（能用的是 {'、'.join(ITEM_EDIT_KEYS[field])}）"
        )
    original = [dict(entry) if isinstance(entry, dict) else entry for entry in items]
    if field == "Company":
        candidates = [{**data, "added": today, "ended": ""}]
    else:
        value = str(data.pop("value", "") or "")
        texts = history_from_text(field, value, today) or [{"value": value, "added": today, "ended": ""}]
        candidates = [{**data, **text} for text in texts]
    new = [_validate_item(field, candidate) for candidate in candidates]
    candidates_validated = list(new)
    if field == "Company" and not all(is_active(entry) for entry in new):
        # 以前的工作（有離職年月）：可以和現職同名（離開後又回去），只擋起訖也完全相同的重複
        _reject_duplicate_past(items, new)
        items.extend(new)
        return items
    current = {entry_key(field, entry): entry for entry in items if is_active(entry)}
    if field == "Company" and new and all(entry_key(field, entry) in current for entry in new):
        _merge_into_current(new, current)
        return items
    conflicts = []
    for entry in new:
        if entry_key(field, entry) in current:
            conflicts += _fill_periods(field, entry, current[entry_key(field, entry)])
    if conflicts:
        raise InvalidNoteFieldsError(
            f"已經有「{'、'.join(conflicts)}」，時間和這次輸入的不同，沒有修改。"
            "要改時間請長按卡片上那一筆，或在 TG 的清單按「改」。"
        )
    new = [entry for entry in new if entry_key(field, entry) not in current]
    if not new and items == original:
        names = "、".join(entry_detail(field, current[entry_key(field, entry)]) for entry in candidates_validated)
        raise InvalidNoteFieldsError(f"已經有「{names}」，沒有新增。")
    if new and PERSON_HISTORY_FIELDS[field].single:
        for entry in items:
            if is_active(entry):
                entry["ended"] = today  # type: ignore[index]
    items.extend(new)
    return items


def _check_restored(field: str, entry: dict[str, object]) -> None:
    """Check that an entry is still valid after being restored (back to a current job).

    取消劃掉（恢復現職）之後整筆要仍然合法。

    A job with only an end time (§17) has nothing left once the end is removed.

    只有離職時間的工作（§17）拿掉離職就什麼都沒有了。

    Raises:
        InvalidNoteFieldsError: if the restored entry is invalid (nothing is written)
            若恢復後不合法（不寫入）
    """
    try:
        _validate_item(field, entry)
    except InvalidNoteFieldsError as e:
        if field == "Company":
            raise InvalidNoteFieldsError(
                "這份工作只有離職時間，恢復成現職後就沒有內容了：請先按「修改」補上公司、職稱或說明，再恢復現職。"
            ) from e
        raise


def set_history_status(field: str, entry: dict[str, object], *, ended: bool, today: str | None = None) -> None:
    """Strike out or restore one history entry, modifying ``entry`` in place.

    劃掉或取消劃掉一筆（會修改 ``entry``）。

    - Strike out: the end date is set to today; striking out an entry that is already struck out keeps its end
      date. Striking out a position means leaving the job: when there is no end month, this month is added.
    - Restore: the end date is cleared and the end month removed (so a position becomes the current job again;
      for other fields, an ``end`` left by manual editing is removed too, symmetric with :func:`is_active`,
      which does not distinguish fields). Other active entries are not struck out (D5 covers only adding).

    - 劃掉：失效日填今天；已經劃掉的再劃一次不改失效日。職位的劃掉是「離職」：沒有離職年月時補上這個月。
    - 取消劃掉：清空失效日，並拿掉離職年月（職位因此恢復成現職；其他欄位若因手改留下 ``end``，也一併拿掉，
      和 :func:`is_active` 不分欄位的判斷對稱）。不會把其他有效的劃掉（D5 只管新增）。

    Args:
        field: the history field
            會變的欄位
        entry: the entry
            那一筆
        ended: ``True`` to strike it out, ``False`` to restore it
            劃掉為 ``True``，取消劃掉為 ``False``
        today: today (defaults to :func:`today_iso`)
            今天（預設 ``today_iso``）
    """
    today = today or today_iso()
    if ended:
        if not str(entry.get("ended") or "").strip():
            entry["ended"] = today
        if field == "Company" and not str(entry.get("end") or "").strip():
            entry["end"] = today[:7]
    else:
        entry["ended"] = ""
        entry.pop("end", None)


def _merge_into_current(new: list[dict[str, object]], current: dict[str, object]) -> None:
    """Fill a current job with the same name from the new position.

    同名的現職：用新的那筆補進現職。

    The new start month fills that entry's empty start (when it already has a different start, it is not
    changed and no error is raised: editing is the way to change it); the note is filled per
    :func:`merged_note` (filled when empty, otherwise new content appended on a new line, §18).

    新的到職年月補進那一筆空著的到職（已經有不同的到職時不改，也不報錯：要改請用修改）；說明照
    ``merged_note`` 補（空的就填，新的內容換行接在後面，§18）。

    Raises:
        InvalidNoteFieldsError: if there is nothing to fill (a full duplicate, or the start already has a
            different value)
            若沒有任何可以補的（完全重複，或到職已經有不同的值）
    """
    filled = False
    for entry in new:
        target = current[entry_key("Company", entry)]
        assert isinstance(target, dict)  # is_active 只對 dict 成立
        if entry.get("start") and not str(target.get("start") or "").strip():
            target["start"] = entry["start"]
            filled = True
        note = merged_note(str(target.get("note") or ""), str(entry.get("note") or ""))
        if note is not None:
            target["note"] = note
            filled = True
    if not filled:
        names = "、".join(entry_text("Company", entry) for entry in new)
        raise InvalidNoteFieldsError(
            f"已經有這份現職（{names}），沒有新增。要改到職年月或說明，請長按卡片上那一筆，或在 TG 的工作經歷清單按「改」。"
        )


def edit_position(items: list[object], index: int, params: _EditPosition) -> dict[str, object]:
    """Return the edited job, without modifying ``items``.

    改好的那一份工作（不改 ``items``）。

    The start, end, and note are replaced with the new ones, other keys stay as they are, and the whole entry
    is validated with the model again.

    - If the original values (``expected_*``) do not match, :class:`JsonItemChangedError` is raised (two jobs
      with the same name have the same fingerprint).
    - If an existing end month is cleared, the job becomes current again and the end date is cleared too (the
      same as "restore to current job"). Old data with only an end date and no end month already has an empty
      end, so it is not revived by this.
    - If the result duplicates another entry (a past job with identical start and end, or two current jobs with
      the same name), :class:`InvalidNoteFieldsError` is raised, the same as :func:`add_history` blocks.

    任職起訖與說明換成新的，其他鍵照舊，整筆再經模型驗證。

    - 原值（``expected_*``）對不上：丟 :class:`JsonItemChangedError`（同名的兩份工作指紋相同）。
    - 原本有離職年月、改成清空：改回現職，失效日一起清掉（和「恢復現職」相同）。只有失效日、沒有離職年月的
      舊資料，離職本來就是空的，不會因此復活。
    - 改完和另一筆重複（起訖完全相同的以前的工作、同名的兩份現職）：丟 :class:`InvalidNoteFieldsError`，
      和 ``add_history`` 擋的一樣。

    Args:
        items: the current list of jobs
            目前的工作經歷清單
        index: the index of the job to edit
            要改的那一份工作的序號
        params: the action parameters
            動作參數

    Returns:
        the edited job
        改好的那一份工作

    Raises:
        InvalidNoteFieldsError: if the format is wrong, the end is earlier than the start, or the result
            duplicates another entry
            若格式不對、離職早於到職，或改完和另一筆重複
        JsonItemChangedError: if the item was changed elsewhere
            若這一筆在別處被改過
    """
    entry = items[index]
    if not isinstance(entry, dict):
        raise InvalidNoteFieldsError("這一筆工作經歷的格式不對，請在網頁修正。")
    old_start, old_end = str(entry.get("start") or ""), str(entry.get("end") or "")
    if (params.expected_start is not None and params.expected_start != old_start) or (
        params.expected_end is not None and params.expected_end != old_end
    ):
        raise JsonItemChangedError("這一筆的任職期間在別處被改過了，請重新打開工作經歷清單再改。")
    changed = {**entry, "start": params.start, "end": params.end, "note": params.note}
    if old_end.strip() and not params.end.strip():
        changed["ended"] = ""
    # 其他鍵（公司、職稱、登錄日、隱藏）照舊；整筆再經模型驗證（年月整理、離職不早於到職）
    edited = _validate_item("Company", changed)
    _reject_position_duplicate(items, index, edited)
    return edited


def _reject_position_duplicate(items: list[object], index: int, edited: dict[str, object]) -> None:
    """Reject an edited position that duplicates another entry.

    改好的職位和另一筆重複時丟 ``InvalidNoteFieldsError``。

    Duplicates are two current jobs with the same name, or past jobs with identical start and end (the same
    as :func:`add_history` blocks).

    重複指同名的兩份現職、或起訖完全相同的以前的工作（和 ``add_history`` 擋的一樣）。

    Raises:
        InvalidNoteFieldsError: if it duplicates another entry
            若和另一筆重複
    """
    others = [item for i, item in enumerate(items) if i != index]
    if is_active(edited):
        key, text = entry_key("Company", edited), entry_text("Company", edited)
        if any(is_active(item) and entry_key("Company", item) == key for item in others):
            raise InvalidNoteFieldsError(f"已經有同名的現職（{text}），沒有修改。")
    else:
        _reject_duplicate_past(others, [edited])


def _same(a: object, b: object) -> bool:
    """Compare an original value, treating a missing key (``None``), ``""``, and ``False`` all as empty.

    原值比對：沒有這個鍵（None）、空字串與 False 在比對時都算空。

    JSON lists store none of these three (see :func:`_validate_item`); some keys of history fields are stored
    as empty strings.

    JSON 清單存檔時這三種都不寫，見 ``_validate_item``；會變的欄位有的鍵會存成空字串。
    """
    def empty(value: object) -> bool:
        # 用 is False：0 == False，數值 0 不算空
        return value is None or value == "" or value is False

    return (empty(a) and empty(b)) or a == b


def _already_applied(field: str, entry: object, values: dict[str, object]) -> bool:
    """Check whether the item's editable keys already look like ``values`` (the same edit sent again).

    這一筆能改的鍵已經是 ``values`` 的樣子（同一次修改重送）。
    """
    return isinstance(entry, dict) and set(values) == set(ITEM_EDIT_KEYS[field]) and all(
        _same(value, entry.get(key)) for key, value in values.items()
    )


def _contact_after_edit(
    values: dict[str, str], items: list[object], old: object, new: dict[str, object]
) -> dict[str, str]:
    """Return the ``Last_Contact`` change after editing an interaction.

    改了一筆互動之後 ``Last_Contact`` 的變更。

    A date moved later advances it by the same rule as adding; when the previous last contact was this item
    and its date is corrected earlier, it becomes the latest date in the list (otherwise a date that no longer
    exists would remain). When the last contact came from another source (filled in by hand), it is not moved
    back.

    日期往後改照新增的規則推進；原本的上次聯絡就是這一筆、日期又往前更正時，改成清單裡最新的日期
    （不然會留著一個已經不存在的日期）。上次聯絡是別的來源時（手動填的）不往回改。

    Args:
        values: the card's current field values (``Last_Contact`` is read)
            卡片目前的欄位值（讀 ``Last_Contact``）
        items: the interactions after the edit
            改好之後的互動紀錄
        old: the item before the edit
            改之前的那一筆
        new: the edited item
            改好的那一筆

    Returns:
        the fields to write; empty when ``Last_Contact`` does not change
        要寫入的欄位；``Last_Contact`` 不變時為空
    """
    old_date = str(old.get("date") or "") if isinstance(old, dict) else ""
    new_date = str(new.get("date") or "")
    if old_date and new_date < old_date and old_date == values.get("Last_Contact", "").strip():
        return {"Last_Contact": max(str(item.get("date") or "") for item in items if isinstance(item, dict))}
    return _last_contact(values, new_date)


def edit_item(
    field: str, items: list[object], index: int, values: dict[str, object], expected: dict[str, object] | None
) -> dict[str, object]:
    """Return the edited item, without modifying ``items`` (work history plan §16).

    改好的那一筆（不改 ``items``；工作經歷計畫書 §16）。

    The editable keys (:data:`ITEM_EDIT_KEYS`) are replaced with the new ones, other keys stay as they are,
    and the whole item is validated with the model again.

    - If the original values (``expected``) do not match but the item already looks like ``values``, it is the
      same edit sent again, and the item is returned as is (no error).
    - Positions: clearing an existing end month makes the job current again (the end date is cleared too, as in
      :func:`edit_position`); a past job whose end month is not cleared keeps its end date. A result that
      duplicates another entry is blocked.
    - Other history fields: when the content of a splittable field changes, it can become only one value (old
      data whose content is unchanged may contain enumeration commas and have only its times edited); a result
      equal to another active entry is blocked.
    - Other lists: only validated with the model (the status and end date of goals and follow-ups stay as they
      are).

    能改的鍵（``ITEM_EDIT_KEYS``）換成新的，其他鍵照舊，整筆再經模型驗證。

    - 原值（``expected``）對不上、但這一筆已經是 ``values`` 的樣子：同一次修改重送，照樣回傳（不丟錯）。
    - 職位：清掉原本有的離職年月時改回現職（劃掉日一併清掉，同 :func:`edit_position`）；離職年月沒清掉的以前的
      工作，劃掉日照舊。改完和另一筆重複就擋。
    - 其他會變的欄位：可切的欄位改了內容時只能改成一個值（沒改內容的舊資料可以含頓號，只改時間）；改完和另一筆
      有效的相同就擋。
    - 其他清單：只經模型驗證（目標、待追蹤的狀態與結束日照舊）。

    Args:
        field: the list field
            清單欄位
        items: the current list
            目前的清單
        index: the index of the item to edit
            要改的那一筆的序號
        values: the new values of the editable keys
            能改的鍵的新值
        expected: the original values seen when editing began, or ``None`` to skip the check
            開始修改時看到的原值；``None`` 表示不檢查

    Returns:
        the edited item
        改好的那一筆

    Raises:
        InvalidNoteFieldsError: if the keys are wrong (``ActionContractError``), the format is wrong, or the
            result duplicates another entry
            若鍵不對（``ActionContractError``）、格式不對，或改完和另一筆重複
        JsonItemChangedError: if the original values (``expected``) do not match: the item was changed elsewhere
            若原值（``expected``）對不上：這一筆在別處被改過
    """
    keys = ITEM_EDIT_KEYS[field]
    title = PERSON_HISTORY_FIELDS[field].title if field in PERSON_HISTORY_FIELDS else PERSON_JSON_ITEMS[field][1]
    if set(values) != set(keys):
        raise ActionContractError(f"{title}能改的是 {'、'.join(keys)}，收到 {'、'.join(values) or '（沒有）'}")
    if expected is not None and set(expected) != set(keys):
        raise ActionContractError(f"{title}的原值要送 {'、'.join(keys)}，收到 {'、'.join(expected) or '（沒有）'}")
    entry = items[index]
    if not isinstance(entry, dict):
        raise InvalidNoteFieldsError(f"這一筆{title}的格式不對，請在網頁修正。")
    if expected is not None and not all(_same(expected[key], entry.get(key)) for key in keys):
        if _already_applied(field, entry, values):
            logger.info("%s #%s 已經是要改成的樣子（重送），照原樣回傳", field, index + 1)
            return dict(entry)
        raise JsonItemChangedError(f"這一筆{title}在別處被改過了，請重新打開清單再改。")
    # None＝這一筆原本沒有這個鍵（或清掉了數字）：拿掉，交給模型的預設值（字串欄位是空字串）
    changed = {key: value for key, value in {**entry, **values}.items() if not (key in keys and value is None)}
    if field == "Company":
        if str(entry.get("end") or "").strip() and not str(values.get("end") or "").strip():
            changed["ended"] = ""
        edited = _validate_item(field, changed)
        _reject_position_duplicate(items, index, edited)
        return edited
    if field in PERSON_HISTORY_FIELDS:
        value = str(values.get("value") or "")
        if PERSON_HISTORY_FIELDS[field].split and not _same(value, entry.get("value")) \
                and len(history_from_text(field, value, "")) > 1:
            raise InvalidNoteFieldsError(f"修改時只能改成一個{title}；要新增好幾個請用「新增」。")
        edited = _validate_item(field, changed)
        text = entry_text(field, edited)
        if is_active(edited) and any(
            i != index and is_active(item) and entry_key(field, item) == entry_key(field, edited)
            for i, item in enumerate(items)
        ):
            raise InvalidNoteFieldsError(f"已經有「{text}」這一筆{title}，沒有修改。")
        return edited
    return _validate_item(field, changed)


def _fill_periods(field: str, new: dict[str, object], target: object) -> list[str]:
    """Fill the duplicate entry's (``target``) empty event times from the new one, modifying ``target``.

    重複的那筆（``target``）還空著的事件時間用新的補上。

    For fields with a note (:data:`NOTE_FIELDS`), the note is joined per :func:`merged_note` (positions
    excepted; see :func:`_merge_into_current`). The filled entry is validated with the model again (for
    example, education that had only graduation 2015 and is then given enrollment 2018); if it is invalid,
    nothing is filled and an error is raised.

    有說明的欄位（``NOTE_FIELDS``）說明照 ``merged_note`` 接上（職位以外；職位見 ``_merge_into_current``）。
    補完整筆再經模型驗證（例如學歷原本只有畢業 2015，又補上入學 2018），不合法就不補、丟錯。

    Returns:
        the text of the entry (with its existing times) when a given time differs from the one it already has,
        for the caller to report; otherwise empty
        帶了時間、但和那一筆已有的時間不同時，該項目的文字（含已有的時間），給呼叫端報錯；否則為空

    Raises:
        InvalidNoteFieldsError: if the filled entry is invalid
            若補完的那一筆不合法
    """
    if field == "Company" or not isinstance(target, dict):
        return []
    filled = dict(target)
    conflict = False
    for key in HISTORY_PERIOD_KEYS.get(field, ()):
        value = str(new.get(key) or "")
        existing = str(target.get(key) or "").strip()
        if value and not existing:
            filled[key] = value
        elif value and value != existing:
            conflict = True
    if conflict:
        return [entry_detail(field, target)]
    if field in NOTE_FIELDS:
        note = merged_note(str(target.get("note") or ""), str(new.get("note") or ""))
        if note is not None:
            filled["note"] = note
    if filled != target:
        try:
            validated = _validate_item(field, filled)
        except InvalidNoteFieldsError as e:
            raise InvalidNoteFieldsError(
                f"已經有「{entry_detail(field, target)}」，補上這次的內容會不合理（{e}）。請長按那一筆修改。"
            ) from e
        target.update(validated)
    return []


def edit_history_period(
    field: str, items: list[object], index: int, values: dict[str, str], expected: dict[str, str] | None
) -> dict[str, object]:
    """Return the entry with its event times replaced, without modifying ``items``.

    改好的那一筆（不改 ``items``）。

    The event times are replaced with the new ones, other keys stay as they are, and the whole entry is
    validated with the model again.

    事件時間換成新的，其他鍵照舊，整筆再經模型驗證。

    Args:
        field: the history field
            會變的欄位
        items: the current list
            目前的清單
        index: the index of the entry to edit
            要改的那一筆的序號
        values: the new event times
            新的事件時間
        expected: the original values seen when editing began, or ``None`` to skip the check
            開始修改時看到的原值；``None`` 表示不檢查

    Returns:
        the edited entry
        改好的那一筆

    Raises:
        InvalidNoteFieldsError: if the keys are wrong, the format is wrong, or graduation is earlier than
            enrollment
            若鍵不對、格式不對或畢業早於入學
        JsonItemChangedError: if the original values (``expected``) do not match: the entry was changed elsewhere
            若原值（``expected``）對不上：這一筆在別處被改過
    """
    keys = HISTORY_PERIOD_KEYS[field]
    title = PERSON_HISTORY_FIELDS[field].title
    if set(values) != set(keys):
        raise InvalidNoteFieldsError(f"{title}的時間要送 {'、'.join(keys)}，收到 {'、'.join(values) or '（沒有）'}")
    if expected is not None and set(expected) != set(keys):
        raise InvalidNoteFieldsError(f"{title}的原值要送 {'、'.join(keys)}，收到 {'、'.join(expected) or '（沒有）'}")
    entry = items[index]
    if not isinstance(entry, dict):
        raise InvalidNoteFieldsError(f"這一筆{title}的格式不對，請在網頁修正。")
    if expected is not None and any(expected.get(key, "") != str(entry.get(key) or "") for key in keys):
        raise JsonItemChangedError(f"這一筆{title}的時間在別處被改過了，請重新打開清單再改。")
    return _validate_item(field, {**entry, **values})


def _reject_duplicate_past(items: list[object], new: list[dict[str, object]]) -> None:
    """Reject past jobs that are already recorded.

    擋掉已經記錄過的以前的工作。

    A past job is a duplicate when its :func:`entry_key` (company and title; for unnamed ones, the note and
    start) and its start and end all match an existing entry.

    以前的工作：``entry_key``（公司・職稱；沒名字的看說明與到職）與任職起訖都和某一筆相同就是重複。

    Raises:
        InvalidNoteFieldsError: if any is a duplicate
            若重複
    """
    def key(entry: object) -> tuple[str, str, str]:
        values = entry if isinstance(entry, dict) else {}
        return (entry_key("Company", entry), str(values.get("start") or ""), str(values.get("end") or ""))

    existing = {key(entry) for entry in items if isinstance(entry, dict)}
    duplicates = [entry for entry in new if key(entry) in existing]
    if duplicates:
        names = "、".join(entry_detail("Company", entry) for entry in duplicates)
        raise InvalidNoteFieldsError(f"已經有這份以前的工作（{names}），沒有新增。")


def _serialize(field: str, items: list[object]) -> str:
    """Return the field string a list is written back as.

    清單寫回欄位的字串。

    An emptied history field becomes an empty string (the same as leaving it unfilled at creation).

    會變的欄位清空時寫空字串（和建卡時沒填的一樣）。
    """
    if field in PERSON_HISTORY_FIELDS and not items:
        return ""
    return AnkiJsonFieldManager.serialize(items)


def _last_contact(values: dict[str, str], contact_date: str) -> dict[str, str]:
    """Return the change that moves ``Last_Contact`` forward when the interaction date is newer.

    互動的日期比 ``Last_Contact`` 新時要寫入的變更；不比較新時為空。
    """
    return {"Last_Contact": contact_date} if contact_date > values.get("Last_Contact", "").strip() else {}


async def _write(card_service: CardService, note_id: int, changes: dict[str, str]) -> None:
    """Write these fields in one go, together with today's ``Updated_At``.

    一次寫入這些欄位，並附上今天的 ``Updated_At``。
    """
    await card_service.update_note_fields(
        note_id, {**changes, "Updated_At": today_iso()}, model_name=PERSON_NOTE_TYPE
    )


_Params = TypeVar("_Params", bound=_ActionParams)


def _parse(model: type[_Params], parameters: dict[str, object]) -> _Params:
    """Validate action parameters with the given model.

    驗證動作參數。

    Raises:
        InvalidNoteFieldsError: if the parameters are invalid
            若格式不對
    """
    try:
        return model.model_validate(parameters)
    except ValidationError as e:
        raise InvalidNoteFieldsError(f"{parameters.get('action')} 的參數不正確：{_first_error(e)}") from e


def _validate_item(field: str, item: dict[str, object]) -> dict[str, object]:
    """Validate one item with the list's model and return the dict to store.

    以該清單的模型驗證一筆項目，回傳要寫入的 dict（保留多餘的鍵、去掉空值的選填鍵）。

    Extra keys are kept and optional keys with empty values are dropped. History fields keep their fixed-shape
    keys (value or company/title, added, ended; a blank ``ended`` means active); optional keys added later
    (hidden, plus each model's own such as a position's note and event times) are dropped by the model's own
    ``model_dump`` when empty (``_Item._OMIT_WHEN_EMPTY``).

    會變的欄位保留形狀固定的鍵（value 或 company／title、added、ended；``ended`` 空白＝有效）；後來加的選填鍵
    （隱藏，以及職位的說明、事件時間等各模型自己的）沒有值時由模型的 ``model_dump`` 自己拿掉（``_Item._OMIT_WHEN_EMPTY``）。

    Raises:
        InvalidNoteFieldsError: if the item is invalid
            若格式不對
    """
    if field in PERSON_HISTORY_FIELDS:
        model, title = PERSON_HISTORY_FIELDS[field].model, PERSON_HISTORY_FIELDS[field].title
    else:
        model, title = PERSON_JSON_ITEMS[field]
    try:
        validated = model.model_validate(item)
    except ValidationError as e:
        raise InvalidNoteFieldsError(f"{title}的內容不正確：{_first_error(e)}") from e
    data = validated.model_dump()
    if field in PERSON_HISTORY_FIELDS:
        return data
    # 選填的空字串、None、False 不存（卡片也不顯示），清單比較乾淨；必要的鍵與種類、狀態、方向一律保留
    keep = {name for name, info in model.model_fields.items() if info.is_required()} | {"type", "status", "direction"}
    # 用 is False 比對：0 == False，用 in 會把數值 0 當成空值丟掉
    return {key: value for key, value in data.items() if key in keep or not (value in ("", None) or value is False)}


def _first_error(error: ValidationError) -> str:
    """Return the first validation error as 「field：reason」 text.

    第一個驗證錯誤的「欄位：原因」。
    """
    first = error.errors()[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    return f"{location}：{first.get('msg', '')}" if location else str(first.get("msg", ""))
