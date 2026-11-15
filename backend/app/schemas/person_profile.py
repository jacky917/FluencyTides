"""人脈卡（Person_Profile_Dark）的建立參數與 JSON 欄位格式。

Create parameters and JSON field item formats of the person profile card.

欄位的意義與顯示見 ``docs/archive/person_profile_card_FEAT_2026-10-23.md`` §3.2、§3.3。所有日期都是
``YYYY-MM-DD`` 文字（字典序就是時間序，也能在 Anki 搜尋）；生日不知年份時寫 ``--MM-DD``；職位的任職起訖
可以只寫年或年月，離職可以是 ``?``（見 :func:`normalize_period`）。
JSON 項目的模型都允許多餘的鍵（``extra="allow"``）：既有資料若有模型沒列出的鍵，讀進來、存回去
都原樣保留。

Field meanings are in the plan; dates are ``YYYY-MM-DD`` text; JSON item
models keep unknown keys.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar
from datetime import date
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    StrictBool,
    ValidationInfo,
    field_validator,
    model_serializer,
    model_validator,
)

#: 筆記類型。The note type.
PERSON_NOTE_TYPE = "Person_Profile_Dark"

#: 有這個標籤的人：「AI 分析與話題」送出的內文不遮名字（姓名欄位照樣不送）。用標籤而不是欄位：
#: 改筆記類型的欄位會讓 AnkiWeb 要求完整同步；標籤在 Anki app 也能直接加減。
#: Notes with this tag send body text unmasked to the topics LLM.
TEXT_UNMASKED_TAG = "AI內文不遮名字"

#: 空字串或 ``YYYY-MM-DD``。Empty or an ISO date.
OPTIONAL_DATE = r"^$|^\d{4}-\d{2}-\d{2}$"
#: ``YYYY-MM-DD``。An ISO date.
DATE = r"^\d{4}-\d{2}-\d{2}$"
#: 空字串、``YYYY-MM-DD`` 或不知年份的 ``--MM-DD``。Empty, an ISO date, or ``--MM-DD``.
BIRTHDAY = r"^$|^(\d{4}|-)-\d{2}-\d{2}$"

_TEXTAREA = {"x-ui": "textarea"}


def _text(title: str, description: str | None = None, *, multiline: bool = False, pattern: str | None = None):
    """選填的文字欄位。An optional text field."""
    return Field(
        default="",
        title=title,
        description=description,
        pattern=pattern,
        json_schema_extra=_TEXTAREA if multiline else None,
    )


def _rows(
    title: str,
    description: str | None = None,
    *,
    multiline: bool = False,
    tags: bool = False,
    quick: tuple[str, ...] = (),
):
    """可以一筆一筆輸入的欄位（會變的欄位裡不是單值的）：網頁表單畫成一列一列、可「+」新增。

    A field typed one entry per row; the web form shows rows with a "+".

    JSON Schema 的標記（前端 ``createForm`` 認得）：``x-ui: list``；``x-item: textarea`` 每列是多行；
    ``x-item: tag`` 畫成一個一個小標籤（短值的欄位：語言、興趣這種，橫向排、輸入後按 Enter 加入）；
    ``x-quick`` 快速選項（點一下加一筆，已有的不重複）。值可以是一段文字（舊的寫法，照
    :func:`history_from_text` 切）或每列一個值的清單。
    """
    if multiline and tags:
        raise ValueError("標籤欄位不能多行")
    extra: dict[str, object] = {"x-ui": "list"}
    if multiline:
        extra["x-item"] = "textarea"
    elif tags:
        extra["x-item"] = "tag"
    if quick:
        extra["x-quick"] = list(quick)
    return Field(default="", title=title, description=description, json_schema_extra=extra)


class PositionInput(BaseModel):
    """建卡表單上職位的一列：公司與職稱（都空的那列略過）。One position row of the create form."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    company: str = Field("", title="公司")
    title: str = Field("", title="職稱")


class PersonCreateParams(BaseModel):
    """網頁與 TG 建立人脈卡的參數：稱呼必填，其餘選填；JSON、照片、``Card_ID``、``TG_Bot``、
    ``Updated_At`` 由系統填（§3.11）。

    會變的欄位（:data:`PERSON_HISTORY_FIELDS`）建立時轉成清單、登錄日填今天（:func:`history_from_input`）：
    單值欄位（現居地、時區、關係、親密度）是一段文字；其他的可以一列一筆（``_rows``），也接受一段文字。
    職位一列是公司＋職稱；``Title`` 欄位已停用，不在建立參數裡。

    Parameters for creating a person card; only the display name is required.
    History fields become lists on creation; multi-entry ones take one value per row.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    Display_Name: str = Field(min_length=1, title="稱呼", description="平常怎麼稱呼他，例如「田中さん」")
    Name_ZH: str = _text("中文姓名")
    Name_EN: str = _text("英文名", "照對方的拼法")
    Name_JA: str = _text("日文姓名", "漢字，姓與名之間空一格")
    Name_JA_Kana: str = _text("日文讀音", "與日文姓名用空白對齊分段，例如「たなか ゆうき」")
    Name_Romaji: str = _text("羅馬拼音")
    Honorific: str = _text("稱呼與敬語", "例如「さん／敬語」")
    Name_Memo: str = _text("名字記憶法", "名字的由來、諧音或聯想口訣")
    Appearance: str = _text("外貌特徵", "沒有照片時，隱藏名字練認人的主要線索", multiline=True)
    Birthday: str = _text("生日", "例如 1994-03-15；不知年份只寫月日，例如 3/15", pattern=BIRTHDAY)
    Hometown: str = _text("出身地")
    Residence: str = _text("現居地")
    Languages: str | list[str] = _rows("語言", "點選或輸入後按 Enter 加入", tags=True, quick=("中文", "日文", "英文"))
    Timezone: str = _text("時區", "例如 Asia/Tokyo")
    Met_Date: str = _text("認識日期", "YYYY-MM-DD", pattern=OPTIONAL_DATE)
    Met_Where: str = _text("認識場合", "例如「Geekly 面談」")
    Met_Via: str = _text("認識管道", "例如「朋友介紹」")
    Introducer_Note_ID: str = _text("介紹人的筆記 ID", "介紹人也有人脈卡時填，背面會出現「查看介紹人」", pattern=r"^$|^\d+$")
    Relationship: str = _text("關係", "例如「客戶」「朋友」「同事」")
    Closeness: str = _text("親密度", "1–5", pattern=r"^$|^[1-5]$")
    First_Impression: str = _text("第一印象", multiline=True)
    Company: str | list[PositionInput] = _rows("職位", "一份工作一列：公司與職稱至少填一個")
    Work_Detail: str | list[str] = _rows("工作內容", "工作內容、目前的專案、職涯經歷；一件一列", multiline=True)
    Education: str | list[str] = _rows("學歷", "一個學歷一列")
    Interests: str | list[str] = _rows("興趣", "輸入後按 Enter 加入；背面也顯示成標籤", tags=True)
    Food_Drink: str | list[str] = _rows("飲食喜好", "喜歡吃喝的東西、愛去的店；輸入後按 Enter 加入", tags=True)
    Dislikes: str | list[str] = _rows("不喜歡的事", "討厭、在意的事（吃的放飲食限制，不要提的放敏感話題）；輸入後按 Enter 加入",
                                      tags=True)
    Dietary: str | list[str] = _rows("飲食限制", "過敏、不吃的東西；卡面用紅框提醒", multiline=True)
    Personality_Values: str | list[str] = _rows("個性與價值觀", "個性、價值觀、煩惱（夢想、目標記在「目標」）", multiline=True)
    Praise: str | list[str] = _rows("讚美素材", "具體、真實的成就或優點", multiline=True)
    Ask_Advice: str | list[str] = _rows("可以請教的事", multiline=True)
    Needs: str | list[str] = _rows("對方需要的幫助", "對方在找的人、資訊或機會；幫上了就劃掉", multiline=True)
    Common_Ground: str | list[str] = _rows("共同點", "同鄉、同校、共同的興趣或經歷（開話題用）", multiline=True)
    Sensitive: str | list[str] = _rows("敏感話題", "不要主動提的事；背面預設模糊", multiline=True)
    Comm_Prefs: str | list[str] = _rows("溝通偏好", "偏好的聯絡管道、時段、風格", multiline=True)
    Notes: str = _text("備註", multiline=True)
    Last_Contact: str = _text("上次聯絡", "YYYY-MM-DD", pattern=OPTIONAL_DATE)
    Contact_Cadence: str = _text("聯絡間隔（天）", "超過時背面的「N 天前」變紅", pattern=r"^$|^[1-9]\d{0,3}$")

    @field_validator("Birthday", mode="before")
    @classmethod
    def _normalize_birthday(cls, value: object) -> object:
        """生日的寫法放寬：有年份存 ``YYYY-MM-DD``，沒有年份存 ``--MM-DD``（見 :func:`normalize_birthday`）。"""
        return normalize_birthday(value) if isinstance(value, str) else value


_BIRTHDAY_INPUT = re.compile(
    r"\s*(?:(?:(\d{4})\s*[-/.年]\s*)|--)?(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?\s*"
)


def normalize_birthday(text: str) -> str:
    """生日的輸入整理成存放的寫法：``1994-03-15``、``1994/3/15``、``1994年3月15日`` → ``1994-03-15``；
    沒有年份的 ``3/15``、``03-15``、``3月15日``、``--03-15`` → ``--03-15``。

    Normalize a typed birthday; without a year it becomes ``--MM-DD``.

    看不懂或不是真的日期（``2/30``）時原樣回傳，交給欄位的格式檢查擋下；沒有年份時 2/29 也算合法。
    """
    m = _BIRTHDAY_INPUT.fullmatch(text or "")
    if not m:
        return text
    year, month, day = m.group(1), int(m.group(2)), int(m.group(3))
    try:
        date(int(year) if year else 2000, month, day)  # 2000 年是閏年：沒有年份時 2/29 也收
    except ValueError:
        return text
    return f"{year}-{month:02d}-{day:02d}" if year else f"--{month:02d}-{day:02d}"


# =============================================================================
# JSON 欄位的項目（§3.3）
# =============================================================================


def _hidden_last(schema: dict[str, object]) -> None:
    """JSON Schema 裡把「隱藏」排到最後（它定義在基底類別，原本會排第一；網頁表單照這個順序畫）。"""
    properties = schema.get("properties")
    if isinstance(properties, dict) and "hidden" in properties:
        properties["hidden"] = properties.pop("hidden")


class _Item(BaseModel):
    """JSON 項目的共同設定：保留多餘的鍵、去掉前後空白；每一筆都能隱藏（工作經歷計畫書 §5）。

    Common item config; every item can be hidden on the card. Keys in
    ``_OMIT_WHEN_EMPTY`` are left out of the dump while empty.

    後來加的選填鍵（:attr:`_OMIT_WHEN_EMPTY`：隱藏，職位另有任職起訖與說明）沒有值時不輸出。網頁整欄寫入與 TG
    新增都經 ``model_dump``，所以兩條路存下來的形狀相同，也和加這些鍵以前一樣。
    ``hidden`` 是嚴格布林：卡面與 :func:`is_hidden` 只認 ``true``，``"yes"``、``1`` 這種值寫入時就擋下，
    不會在下次網頁儲存時被偷偷轉成 True。
    """

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True, json_schema_extra=_hidden_last)

    #: 沒有值（空字串或 False）時不輸出的鍵。Keys omitted from the dump while empty.
    _OMIT_WHEN_EMPTY: ClassVar[tuple[str, ...]] = ("hidden",)

    hidden: StrictBool = Field(False, title="隱藏", description="卡面顯示成遮擋塊，點一下才看得到（只影響卡面）")

    @model_serializer(mode="wrap")
    def _omit_empty(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        data = handler(self)
        for key in self._OMIT_WHEN_EMPTY:
            # 用 is False／== "" 比對：0 == False，用 in 會把數值 0 當成空值
            if key in data and (data[key] is False or data[key] == ""):
                del data[key]
        return data


class Interaction(_Item):
    """一次見面或聊天。One meeting or conversation."""

    date: str = Field(title="日期", pattern=DATE)
    place: str = Field("", title="地點")
    topics: str = Field("", title="聊了什麼", json_schema_extra=_TEXTAREA)
    updates: str = Field("", title="對方的近況", json_schema_extra=_TEXTAREA)
    quote: str = Field("", title="對方說的一句話", json_schema_extra=_TEXTAREA)


class Followup(_Item):
    """待追蹤事項。A follow-up."""

    item: str = Field(min_length=1, title="內容")
    type: Literal["ask", "my_promise", "their_promise"] = Field(
        "ask", title="種類", description="ask：下次要問；my_promise：我答應的；their_promise：對方答應的"
    )
    status: Literal["open", "done"] = Field("open", title="狀態", description="open：未完成；done：已完成")
    added: str = Field("", title="記下的日期", pattern=OPTIONAL_DATE)
    due: str = Field("", title="期限", pattern=OPTIONAL_DATE)


class FamilyMember(_Item):
    """家人或寵物。A family member or pet."""

    relation: str = Field(min_length=1, title="關係", description="例如「妻子」「兒子」「貓」")
    name: str = Field("", title="名字")
    birth_year: int | None = Field(None, title="出生年", ge=1900, le=2100)
    note: str = Field("", title="備註")


class Gift(_Item):
    """送出或收到的禮物，或禮物靈感（對方想要的，``idea``；送出後把方向改成送出，同一筆接著用）。

    A gift given or received, or a gift idea.
    """

    item: str = Field(min_length=1, title="禮物")
    direction: Literal["given", "received", "idea"] = Field(
        "given", title="方向", description="given：送出；received：收到；idea：想要的（禮物靈感）"
    )
    date: str = Field("", title="日期", pattern=OPTIONAL_DATE)
    reaction: str = Field("", title="反應")


class KeyDate(_Item):
    """重要日期。An important date."""

    date: str = Field(title="日期", pattern=DATE)
    label: str = Field(min_length=1, title="名稱", description="例如「N1 考試」「結婚紀念日」")
    yearly: bool = Field(False, title="每年")


class Contact(_Item):
    """聯絡方式。A contact channel."""

    channel: str = Field(min_length=1, title="管道", description="例如 LINE、Email")
    handle: str = Field(min_length=1, title="帳號")
    preferred: bool = Field(False, title="偏好")


def append_field_html(current: str, addition: str) -> str:
    """把新的內容（已轉義的 HTML）接在文字欄位原本的內容後面，以換行（``<br>``）分隔。

    Append (escaped) HTML to a text field's content on a new line.
    """
    if not current.strip():
        return addition
    return f"{current}<br>{addition}"


#: 指紋用的鍵：每種項目的主要文字欄位（與模板 itemFingerprint 相同）。Keys the fingerprint reads.
FINGERPRINT_KEYS = ("date", "item", "topics", "name", "label", "handle", "relation", "channel")
#: 會變的欄位的項目另外加進指紋的鍵：只在項目有這個鍵時才接上（原有六種清單的指紋不變）。``added``、
#: ``ended`` 刻意不放：劃掉、取消劃掉後同一筆的指紋不變，TG 的「復原」按鈕與卡片上的舊連結才對得上。職位的
#: ``start``、``end``、``note`` 與每筆的 ``hidden`` 也刻意不放：補年月、離職、隱藏後連結仍對得上。
#: Extra keys of history items, appended only when present; the dates stay out so ending keeps the fingerprint.
FINGERPRINT_EXTRA_KEYS = ("value", "company", "title")


def item_fingerprint(item: object) -> str:
    """一筆 JSON 項目的指紋：主要文字欄位以 ``|`` 串起，djb2 雜湊（UTF-16 碼元），36 進位。

    The fingerprint of a JSON item, identical to the card template's
    ``itemFingerprint``: djb2 over the UTF-16 code units of the main text
    fields joined by ``|`` (plus the extra keys present), in base 36.

    卡片上的長按選單、標成完成、劃掉連結與 TG 的按鈕在序號後面附上它，用來確認要動的還是同一筆（序號可能過時）。
    會變的欄位的項目另外接上 :data:`FINGERPRINT_EXTRA_KEYS` 裡有的鍵。

    Args:
        item: 一筆項目（通常是 dict；其他型別視為沒有欄位）。The item.

    Returns:
        36 進位字串。A base-36 string.
    """
    values = item if isinstance(item, dict) else {}
    key = "|".join("" if values.get(k) is None else _js_string(values[k]) for k in FINGERPRINT_KEYS)
    key += "".join("|" + _js_string(values[k]) for k in FINGERPRINT_EXTRA_KEYS if values.get(k) is not None)
    h = 5381
    data = key.encode("utf-16-le")
    for i in range(0, len(data), 2):
        h = ((h * 33) ^ (data[i] | data[i + 1] << 8)) & 0xFFFFFFFF
    return _base36(h)


def _js_string(value: object) -> str:
    """與 JavaScript 的 ``String(value)`` 相同的寫法（布林、整數）。Mirrors JS ``String()``."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _base36(number: int) -> str:
    """非負整數轉 36 進位（小寫，同 JS ``toString(36)``）。Base 36, like JS ``toString(36)``."""
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if number == 0:
        return "0"
    out = ""
    while number:
        number, rest = divmod(number, 36)
        out = digits[rest] + out
    return out


# =============================================================================
# 會變的資料：每筆附登錄日與失效日（人脈卡登錄日計畫書 §4.1、§4.2）
# =============================================================================

#: 新增一筆時網頁表單預填今天（前端 ``newItem`` 認這個標記）。Prefill today in the web form.
_TODAY = {"x-default": "today"}


def _added_field():
    return Field("", title="登錄日", description="新增時系統填今天；空白表示日期不明", pattern=OPTIONAL_DATE,
                 json_schema_extra=_TODAY)


def _ended_field():
    return Field("", title="失效日", description="空白表示目前有效；劃掉時系統填今天", pattern=OPTIONAL_DATE)


class HistoryEntry(_Item):
    """會變的資料的一筆：內容、登錄日、失效日（空字串＝目前有效）。One history entry."""

    value: str = Field(min_length=1, title="內容")
    added: str = _added_field()
    ended: str = _ended_field()


class HistoryNote(HistoryEntry):
    """多行的一筆（一筆一段）。A multi-line history entry."""

    value: str = Field(min_length=1, title="內容", json_schema_extra=_TEXTAREA)


class ClosenessEntry(HistoryEntry):
    """親密度的一筆（1–5）。A closeness entry."""

    value: str = Field(title="親密度", description="1–5", pattern=r"^[1-5]$")


#: 任職起訖存放的形狀：空白、年、年月或完整日期（真實日期由 :func:`normalize_period` 檢查，pattern 只看形狀）。
#: The stored shape of a period bound; :func:`normalize_period` checks the date is real.
PERIOD = r"^$|^\d{4}(-\d{2}(-\d{2})?)?$"
#: 離職：同 :data:`PERIOD`，另外 ``?`` 表示已經離職但不知道時間。The end, where ``?`` means unknown.
PERIOD_END = r"^$|^\?$|^\d{4}(-\d{2}(-\d{2})?)?$"

# 年，或年月（年與月之間要有分隔），或年月日（月與日之間也要有分隔，或寫成「4月1日」）：
# 2021-123 這種少了分隔的打錯字不要被猜成 2021-12-03
_PERIOD_INPUT = re.compile(
    r"\s*(\d{4})\s*年?\s*(?:[-/.年]?\s*(?<=[-/.年\s])(\d{1,2})\s*月?\s*(?:(?:[-/.]|(?<=月))\s*(\d{1,2})\s*日?)?)?\s*"
)


def normalize_period(text: str, *, allow_unknown: bool = False) -> str | None:
    """任職起訖的寫法整理成存放的形狀：``2021``、``2021-4``、``2021/04``、``2021年4月`` → ``2021-04``；
    ``2021-04-01``、``2021年4月1日`` → ``2021-04-01``；空白不變。

    Normalize a period bound to ``YYYY``, ``YYYY-MM`` or ``YYYY-MM-DD``.

    Args:
        text: 輸入。The input.
        allow_unknown: 接受 ``?``（全形「？」也算）表示不知道（離職用）。Accept ``?`` for unknown.

    Returns:
        整理過的值；看不懂、年份不在 1900–2100、或不是真的日期（``2021-13``、``2021-02-31``）時為 None。
        The normalized value, or None when invalid.
    """
    text = (text or "").strip()
    if not text:
        return ""
    if allow_unknown and text in ("?", "？"):
        return "?"
    m = _PERIOD_INPUT.fullmatch(text)
    if not m:
        return None
    year, month, day = m.group(1), m.group(2), m.group(3)
    if not 1900 <= int(year) <= 2100:
        return None
    if month is None:
        return year
    try:
        date(int(year), int(month), int(day or 1))
    except ValueError:
        return None
    return f"{year}-{int(month):02d}" + (f"-{int(day):02d}" if day else "")


def period_order_error(start: str, end: str) -> str | None:
    """離職早於到職時的錯誤訊息；其他情況（任一邊空白或看不懂、離職是 ``?``）為 None。

    The error when the end is before the start, else None.

    比較的是補零後的字串（字典序就是時間序），所以先各自經 :func:`normalize_period` 整理：卡上手改留下的
    ``2021/4`` 這種寫法直接比會比錯。看不懂的值不在這裡報（由格式檢查報）。精度不同（2021 對 2021-04）時
    比共同的前綴：同一年算合理。所有離職先後檢查都用這裡，訊息一致。
    """
    start_value = normalize_period(start)
    end_value = normalize_period(end, allow_unknown=True)
    if not start_value or not end_value or end_value == "?":
        return None
    size = min(len(start_value), len(end_value))
    if end_value[:size] < start_value[:size]:
        return f"離職（{end_value}）不能早於到職（{start_value}）"
    return None


class Position(_Item):
    """一份工作（工作經歷計畫書 §4.1）：公司與職稱（至少一個）、任職起訖、說明、登錄日、失效日。
    現職＝沒有離職（``end``）也沒有劃掉（``ended``），見 :func:`is_active`。

    One job: company and/or title, period, note and dates. It is current
    while it has neither an ``end`` nor an ``ended`` date.

    ``end`` 與 ``ended`` 的組合：都空＝現職；網頁填了離職、TG 離職（兩個都有）、舊資料只有劃掉，都是以前的工作。
    任職起訖輸入時整理成存放的形狀（:func:`normalize_period`），離職不能早於到職。
    """

    _OMIT_WHEN_EMPTY: ClassVar[tuple[str, ...]] = ("hidden", "start", "end", "note")

    company: str = Field("", title="公司")
    title: str = Field("", title="職稱")
    start: str = Field("", title="到職", description="年、年月或日期，例如 2021-04", pattern=PERIOD)
    end: str = Field("", title="離職", description="空白＝現職；已離職但不知道時間填 ?", pattern=PERIOD_END)
    note: str = Field("", title="說明", description="這份工作做什麼（長的專案內容記在「工作內容」）")
    added: str = _added_field()
    ended: str = _ended_field()

    @field_validator("start", "end", mode="before")
    @classmethod
    def _normalize_period(cls, value: object, info: ValidationInfo) -> object:
        if not isinstance(value, str):
            return value
        normalized = normalize_period(value, allow_unknown=info.field_name == "end")
        if normalized is None:
            hint = "；不知道離職時間就填 ?" if info.field_name == "end" else ""
            raise ValueError(f"「{value.strip()}」不是可用的年月，請寫 2021、2021-04 或 2021-04-01{hint}")
        return normalized

    @model_validator(mode="after")
    def _check(self) -> Position:
        if not (self.company or self.title):
            raise ValueError("公司與職稱至少要填一個")
        error = period_order_error(self.start, self.end)
        if error:
            raise ValueError(error)
        return self


#: 目標的類型（快速選項；也可以自己填）。Quick choices of a goal's kind.
GOAL_KINDS = ("證照", "簽證", "資格", "升學", "職涯", "努力方向", "夢想", "願景")
#: 目標的狀態與中文。Goal statuses.
GOAL_STATUSES = {"open": "進行中", "done": "達成", "dropped": "放棄"}


class Goal(_Item):
    """一個目標（工作經歷計畫書 §6）：類型、內容、目標時間、狀態；達成或放棄的那天記在 ``closed``。

    A goal with its kind, target time and status.
    """

    kind: str = Field("", title="類型", description="例如：" + "、".join(GOAL_KINDS) + "；也可以自己填")
    item: str = Field(min_length=1, title="內容", description="例如「日本語能力試驗 N1」")
    target: str = Field("", title="目標時間", description="年、年月或日期，例如 2026-12；可空", pattern=PERIOD)
    status: Literal["open", "done", "dropped"] = Field(
        "open", title="狀態", description="open：進行中；done：達成；dropped：放棄"
    )
    added: str = _added_field()
    closed: str = Field("", title="結束日", description="改成達成或放棄時系統填今天", pattern=OPTIONAL_DATE)

    @field_validator("target", mode="before")
    @classmethod
    def _normalize_target(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = normalize_period(value)
        if normalized is None:
            raise ValueError(f"「{value.strip()}」不是可用的時間，請寫 2026、2026-12 或 2026-12-07")
        return normalized


def is_open_goal(item: object) -> bool:
    """進行中的目標（不是 dict 的不算）。Whether a goal is still open."""
    return isinstance(item, dict) and str(item.get("status") or "open") == "open"


#: JSON 欄位 → (項目模型, 標題)。JSON field → (item model, title).
PERSON_JSON_ITEMS: dict[str, tuple[type[_Item], str]] = {
    "Interactions_JSON": (Interaction, "互動紀錄"),
    "Followups_JSON": (Followup, "待追蹤"),
    "Family_JSON": (FamilyMember, "家人"),
    "Gifts_JSON": (Gift, "禮物"),
    "Key_Dates_JSON": (KeyDate, "重要日期"),
    "Contacts_JSON": (Contact, "聯絡方式"),
    "Goals_JSON": (Goal, "目標"),
}


@dataclass(frozen=True)
class HistoryField:
    """一個會變的欄位的設定。The settings of one history field.

    Attributes:
        model: 一筆的模型。The item model.
        title: 中文名稱。The display title.
        single: 單值欄位：新增時自動把原本有效的劃掉（登錄日計畫書 D5）。Ending the active one on add.
        split: 文字輸入時以逗號、頓號、換行切成多筆。Split typed text into several entries.
    """

    model: type[_Item]
    title: str
    single: bool = False
    split: bool = False

    def __post_init__(self) -> None:
        # 單值欄位一次只有一筆有效；切成多筆會在新增時互相劃掉
        if self.single and self.split:
            raise ValueError(f"{self.title}：單值欄位不能切成多筆")


#: 改成清單的欄位（登錄日計畫書 D4），大致依卡片背面的區塊（``Dietary`` 在見面前必看）。The history fields.
PERSON_HISTORY_FIELDS: dict[str, HistoryField] = {
    "Interests": HistoryField(HistoryEntry, "興趣", split=True),
    "Food_Drink": HistoryField(HistoryEntry, "飲食喜好", split=True),
    "Dislikes": HistoryField(HistoryEntry, "不喜歡的事", split=True),
    "Dietary": HistoryField(HistoryNote, "飲食限制"),
    "Company": HistoryField(Position, "職位"),
    "Work_Detail": HistoryField(HistoryNote, "工作內容"),
    "Education": HistoryField(HistoryEntry, "學歷"),
    "Personality_Values": HistoryField(HistoryNote, "個性與價值觀"),
    "Praise": HistoryField(HistoryNote, "讚美素材"),
    "Ask_Advice": HistoryField(HistoryNote, "可以請教的事"),
    "Needs": HistoryField(HistoryNote, "對方需要的幫助"),
    "Common_Ground": HistoryField(HistoryNote, "共同點"),
    "Comm_Prefs": HistoryField(HistoryNote, "溝通偏好"),
    "Residence": HistoryField(HistoryEntry, "現居地", single=True),
    "Languages": HistoryField(HistoryEntry, "語言", split=True),
    "Timezone": HistoryField(HistoryEntry, "時區", single=True),
    "Relationship": HistoryField(HistoryEntry, "關係", single=True),
    "Closeness": HistoryField(ClosenessEntry, "親密度", single=True),
    "Sensitive": HistoryField(HistoryNote, "敏感話題"),
}

#: 不再使用的欄位：職稱併進 ``Company`` 的職位（筆記類型的欄位保留，刪欄位要完整同步）。
#: The retired field; the note type keeps it.
RETIRED_FIELDS = ("Title",)

# 會變的欄位必須是建立參數裡的文字欄位，而且不能和原本的 JSON 清單重名（打錯欄位名時 import 就失敗）
assert set(PERSON_HISTORY_FIELDS) <= set(PersonCreateParams.model_fields), "PERSON_HISTORY_FIELDS 有不存在的欄位"
assert set(PERSON_HISTORY_FIELDS).isdisjoint(PERSON_JSON_ITEMS), "PERSON_HISTORY_FIELDS 與 PERSON_JSON_ITEMS 重複"

_SPLIT = re.compile(r"[,，、\n]+")


def is_active(item: object) -> bool:
    """這一筆目前有效：``ended`` 是空的，也沒有 ``end``（職位有離職年月就是以前的工作）。

    Whether an entry is current: neither ended nor, for a position, left.

    不看欄位：只有職位的模型有 ``end``；其他清單若因手改留下多餘的 ``end``，也算失效，TG 的取消劃掉會一併清掉它
    （:func:`app.services.task_handlers.person_profile_handler.set_history_status`）。
    """
    return (
        isinstance(item, dict)
        and not str(item.get("ended") or "").strip()
        and not str(item.get("end") or "").strip()
    )


def is_hidden(item: object) -> bool:
    """這一筆在卡面隱藏（``hidden`` 為真）。Whether an item is hidden on the card."""
    return isinstance(item, dict) and item.get("hidden") is True


#: 文字欄位在卡面隱藏的標籤前綴：``人脈卡隱藏::Appearance``（工作經歷計畫書 D8；用標籤不改筆記類型）。
#: The tag prefix hiding a text field on the card.
HIDDEN_TAG_PREFIX = "人脈卡隱藏::"


#: 卡面能隱藏的文字欄位（模板有對應的遮擋；tests/test_person_profile_template.py 比對）。名字只有
#: ``Display_Name`` 一個開關，遮整個名字區（各語言的名字、讀音、敬稱、名字記法）；``Photo`` 遮頭像照片。
#: TG 只對這些欄位顯示隱藏開關，免得按了卡面沒有變化。Text fields the card can hide.
HIDEABLE_TEXT_FIELDS = (
    "Display_Name", "Photo", "Name_Memo", "Appearance", "Birthday", "Hometown",
    "Met_Date", "Met_Where", "Met_Via", "Introducer_Note_ID", "First_Impression", "Notes",
)


def hidden_tag(field: str) -> str:
    """隱藏某個文字欄位的標籤。The tag hiding a text field."""
    return HIDDEN_TAG_PREFIX + field


def hidden_text_fields(tags: list[str]) -> frozenset[str]:
    """標籤裡隱藏的文字欄位。The text fields hidden by the tags."""
    return frozenset(tag[len(HIDDEN_TAG_PREFIX):] for tag in tags if tag.startswith(HIDDEN_TAG_PREFIX))


def position_period(item: object) -> str:
    """職位的任職期間文字：「2021-04～」「2019～2021」「～？」；劃掉卻沒有離職年月時，用劃掉的月份當離職；
    什麼都沒有時為空。

    The period of a position. A position ended without an ``end`` uses the
    month it was ended.
    """
    if not isinstance(item, dict):
        return ""
    start = str(item.get("start") or "").strip()
    end = str(item.get("end") or "").strip()
    if not end and str(item.get("ended") or "").strip():
        end = str(item.get("ended")).strip()[:7]
    if end == "?":
        end = "？"
    if not start and not end:
        return ""
    return f"{start}～{end}"


def entry_text(field: str, item: object) -> str:
    """一筆的文字：職位是「公司・職稱」，其他是內容。The text of an entry."""
    if not isinstance(item, dict):
        return str(item)
    if field == "Company":
        parts = (str(item.get(key) or "").strip() for key in ("company", "title"))
        return "・".join(part for part in parts if part)
    return str(item.get("value") or "").strip()


def entry_detail(field: str, item: object) -> str:
    """一筆給人（或 LLM）看的完整文字：職位另外附任職期間與說明（「QBC・QA（2025～）：測試」），
    其他同 :func:`entry_text`。比對、去重一律用 :func:`entry_text`。

    The readable text of an entry; positions add their period and note.
    """
    text = entry_text(field, item)
    if field != "Company" or not text or not isinstance(item, dict):
        return text
    period = position_period(item)
    note = str(item.get("note") or "").strip()
    return text + (f"（{period}）" if period else "") + (f"：{note}" if note else "")


def active_texts(field: str, items: list[object]) -> list[str]:
    """目前有效的每一筆的文字（空的不列）。The texts of the current entries."""
    return [text for item in items if is_active(item) and (text := entry_text(field, item))]


def history_from_text(field: str, text: str, added: str, *, title: str = "") -> list[dict[str, str]]:
    """文字輸入（建卡表單、舊資料）轉成清單：可切的欄位以逗號、頓號、換行切成多筆，其他整段一筆。

    Turn typed text into entries; splittable fields split on commas and line breaks.

    Args:
        field: 會變的欄位。The history field.
        text: 純文字（不是 HTML）。Plain text.
        added: 登錄日（建卡時是今天；舊資料轉換時空字串＝日期不明）。The added date.
        title: 職位的職稱（只有 ``Company`` 用）。The job title, for ``Company`` only.

    Returns:
        項目（沒有內容時為空）。The entries.
    """
    text = text.strip()
    if field == "Company":
        title = title.strip()
        return [{"company": text, "title": title, "added": added, "ended": ""}] if text or title else []
    values = [part.strip() for part in _SPLIT.split(text)] if PERSON_HISTORY_FIELDS[field].split else [text]
    # 同一次輸入裡重複的（「登山、登山」）只留一筆
    return [{"value": value, "added": added, "ended": ""} for value in dict.fromkeys(values) if value]


def history_from_input(field: str, value: object, added: str) -> list[dict[str, str]]:
    """建立參數的值轉成清單：一段文字照 :func:`history_from_text` 切；清單每列一筆（可切的欄位一列裡的
    逗號、頓號照樣切開）。職位一列是 ``{"company", "title"}``；重複的只留一筆，空的列略過。

    Turn a create parameter into entries: text is split as usual; a list gives one entry per row.

    Args:
        field: 會變的欄位。The history field.
        value: 一段文字，或每列一個值的清單（職位的列是 dict 或 :class:`PositionInput`）。The value.
        added: 登錄日。The added date.

    Returns:
        項目。The entries.
    """
    rows = value if isinstance(value, list) else [value]
    entries: list[dict[str, str]] = []
    for row in rows:
        if isinstance(row, BaseModel):
            row = row.model_dump()
        if field == "Company" and isinstance(row, dict):
            entries += history_from_text(field, str(row.get("company") or ""), added, title=str(row.get("title") or ""))
        elif row not in (None, ""):
            entries += history_from_text(field, str(row), added)
    # 去重看內容本身（職位看公司與職稱兩格：只有公司「A」和只有職稱「A」是不同的兩筆）
    unique: dict[tuple[str, ...], dict[str, str]] = {}
    for entry in entries:
        key = (entry.get("company", ""), entry.get("title", "")) if field == "Company" else (entry["value"],)
        unique.setdefault(key, entry)
    return list(unique.values())


_BREAK = re.compile(r"<\s*(br|/div|/p|/li)\s*/?\s*>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


def html_to_text(value: str) -> str:
    """Anki 欄位的 HTML 轉成多行純文字（``<br>``、段落結尾換行，其他標籤去掉）。Field HTML as text."""
    text = html.unescape(_TAG.sub("", _BREAK.sub("\n", value or ""))).replace("\xa0", " ")
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


@dataclass
class LegacyConversion:
    """一張筆記的舊資料轉換結果。The legacy conversion of one note.

    Attributes:
        changes: 欄位 → 新的值；不用轉時為空。Field to new value.
        dropped: 不合格式、沒有轉進去的內容 (欄位, 文字)，例如親密度寫「很熟」。Entries left out.
        skipped: 看起來是壞掉的 JSON、沒有轉換的欄位（包成文字會蓋掉還能手動修的資料）。Broken JSON fields.
        leftover_title: ``Company`` 已經是清單、``Title`` 卻還有字（不再顯示，要手動併進職位）。
            A title left behind.
    """

    changes: dict[str, str]
    dropped: list[tuple[str, str]]
    skipped: list[str]
    leftover_title: str = ""


def legacy_to_history(fields: dict[str, str], is_list: Callable[[str], bool]) -> LegacyConversion:
    """舊資料（會變的欄位還是文字）轉成清單（一次性轉換腳本用，登錄日計畫書 D2：不保證無損）。

    Convert one note's legacy text fields into lists.

    切法同網頁建卡表單（:func:`history_from_text`）；登錄日留空（日期不明）、全部有效；不合欄位模型的
    內容不轉（列在 ``dropped``）。公司與職稱合成一筆職位，``Title`` 清空。已經是清單的欄位不動，所以可以
    重複執行；以 ``[``、``{`` 開頭卻解析不了的視為壞掉的 JSON，不轉（列在 ``skipped``）。

    Args:
        fields: 欄位原始值（HTML）。The raw field values.
        is_list: 判斷欄位原始值是否已經是 JSON 清單的函式。Tells whether a raw value is already a list.

    Returns:
        轉換結果。The conversion.
    """
    result = LegacyConversion(changes={}, dropped=[], skipped=[])
    title = html_to_text(fields.get("Title", ""))
    for name, spec in PERSON_HISTORY_FIELDS.items():
        raw = fields.get(name, "")
        has_title = name == "Company" and bool(title)
        if raw.strip() and is_list(raw):
            if has_title:
                result.leftover_title = title
            continue
        if not raw.strip() and not has_title:
            continue
        text = html_to_text(raw)
        if text.lstrip().startswith(("[", "{")):
            result.skipped.append(name)
            continue
        items = []
        for item in history_from_text(name, text, "", title=title if name == "Company" else ""):
            try:
                spec.model.model_validate(item)
            except ValueError:
                result.dropped.append((name, entry_text(name, item)))
                continue
            items.append(item)
        result.changes[name] = html.escape(json.dumps(items, ensure_ascii=False)) if items else ""
    if "Company" in result.changes and fields.get("Title", "").strip():
        result.changes["Title"] = ""
    return result
