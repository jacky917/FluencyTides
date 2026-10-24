"""人脈卡（Person_Profile_Dark）的建立參數與 JSON 欄位格式。

Create parameters and JSON field item formats of the person profile card.

欄位的意義與顯示見 ``docs/wip/person_profile_card_FEAT_2026-10-23.md`` §3.2、§3.3。所有日期都是
``YYYY-MM-DD`` 文字（字典序就是時間序，也能在 Anki 搜尋）；生日不知年份時寫 ``--MM-DD``。
JSON 項目的模型都允許多餘的鍵（``extra="allow"``）：既有資料若有模型沒列出的鍵，讀進來、存回去
都原樣保留。

Field meanings are in the plan; dates are ``YYYY-MM-DD`` text; JSON item
models keep unknown keys.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: 筆記類型。The note type.
PERSON_NOTE_TYPE = "Person_Profile_Dark"

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


class PersonCreateParams(BaseModel):
    """網頁與 TG 建立人脈卡的參數：稱呼必填，其餘選填；JSON、照片、``Card_ID``、``TG_Bot``、
    ``Updated_At`` 由系統填（§3.11）。

    Parameters for creating a person card; only the display name is required.
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
    Appearance: str = _text("外貌特徵", "沒有照片時是「認人」卡的主要線索", multiline=True)
    Birthday: str = _text("生日", "YYYY-MM-DD；不知年份寫 --MM-DD", pattern=BIRTHDAY)
    Hometown: str = _text("出身地")
    Residence: str = _text("現居地")
    Languages: str = _text("語言", "逗號分隔")
    Timezone: str = _text("時區", "例如 Asia/Tokyo")
    Met_Date: str = _text("認識日期", "YYYY-MM-DD", pattern=OPTIONAL_DATE)
    Met_Where: str = _text("認識場合", "例如「Geekly 面談」")
    Met_Via: str = _text("認識管道", "例如「朋友介紹」")
    Introducer_Note_ID: str = _text("介紹人的筆記 ID", "介紹人也有人脈卡時填，背面會出現「查看介紹人」", pattern=r"^$|^\d+$")
    Relationship: str = _text("關係", "例如「客戶」「朋友」「同事」")
    Closeness: str = _text("親密度", "1–5", pattern=r"^$|^[1-5]$")
    First_Impression: str = _text("第一印象", multiline=True)
    Company: str = _text("公司")
    Title: str = _text("職稱")
    Work_Detail: str = _text("工作內容", "工作內容、目前的專案、職涯經歷", multiline=True)
    Education: str = _text("學歷")
    Interests: str = _text("興趣", "短項目用逗號分隔會顯示成標籤", multiline=True)
    Food_Drink: str = _text("飲食喜好", "喜歡吃喝的東西、愛去的店", multiline=True)
    Dietary: str = _text("飲食限制", "過敏、不吃的東西；背面用紅框提醒", multiline=True)
    Personality_Values: str = _text("個性與價值觀", "個性、價值觀、夢想、煩惱", multiline=True)
    Praise: str = _text("讚美素材", "具體、真實的成就或優點", multiline=True)
    Ask_Advice: str = _text("可以請教的事", multiline=True)
    Sensitive: str = _text("敏感話題", "不要主動提的事；背面預設模糊", multiline=True)
    Comm_Prefs: str = _text("溝通偏好", "偏好的聯絡管道、時段、風格", multiline=True)
    Notes: str = _text("備註", multiline=True)
    Last_Contact: str = _text("上次聯絡", "YYYY-MM-DD", pattern=OPTIONAL_DATE)
    Contact_Cadence: str = _text("聯絡間隔（天）", "超過時背面的「N 天前」變紅", pattern=r"^$|^[1-9]\d{0,3}$")


# =============================================================================
# JSON 欄位的項目（§3.3）
# =============================================================================


class _Item(BaseModel):
    """JSON 項目的共同設定：保留多餘的鍵、去掉前後空白。Common item config."""

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)


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
    status: Literal["open", "done"] = Field("open", title="狀態")
    added: str = Field("", title="記下的日期", pattern=OPTIONAL_DATE)
    due: str = Field("", title="期限", pattern=OPTIONAL_DATE)


class FamilyMember(_Item):
    """家人或寵物。A family member or pet."""

    relation: str = Field(min_length=1, title="關係", description="例如「妻子」「兒子」「貓」")
    name: str = Field("", title="名字")
    birth_year: int | None = Field(None, title="出生年", ge=1900, le=2100)
    note: str = Field("", title="備註")


class Gift(_Item):
    """送出或收到的禮物。A gift given or received."""

    item: str = Field(min_length=1, title="禮物")
    direction: Literal["given", "received"] = Field("given", title="方向", description="given：送出；received：收到")
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


#: 指紋用的鍵：每種項目的主要文字欄位（與模板 itemFingerprint 相同）。Keys the fingerprint reads.
FINGERPRINT_KEYS = ("date", "item", "topics", "name", "label", "handle", "relation", "channel")


def item_fingerprint(item: object) -> str:
    """一筆 JSON 項目的指紋：主要文字欄位以 ``|`` 串起，djb2 雜湊（UTF-16 碼元），36 進位。

    The fingerprint of a JSON item, identical to the card template's
    ``itemFingerprint``: djb2 over the UTF-16 code units of the main text
    fields joined by ``|``, in base 36.

    卡片上的刪除、標成完成連結在序號後面附上它，用來確認要動的還是同一筆（序號可能過時）。

    Args:
        item: 一筆項目（通常是 dict；其他型別視為沒有欄位）。The item.

    Returns:
        36 進位字串。A base-36 string.
    """
    values = item if isinstance(item, dict) else {}
    key = "|".join("" if values.get(k) is None else _js_string(values[k]) for k in FINGERPRINT_KEYS)
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


#: JSON 欄位 → (項目模型, 標題)。JSON field → (item model, title).
PERSON_JSON_ITEMS: dict[str, tuple[type[_Item], str]] = {
    "Interactions_JSON": (Interaction, "互動紀錄"),
    "Followups_JSON": (Followup, "待追蹤"),
    "Family_JSON": (FamilyMember, "家人"),
    "Gifts_JSON": (Gift, "禮物"),
    "Key_Dates_JSON": (KeyDate, "重要日期"),
    "Contacts_JSON": (Contact, "聯絡方式"),
}
