"""Create parameters and JSON field item formats of the person profile card.

人脈卡（Person_Profile_Dark）的建立參數與 JSON 欄位格式。

The meaning and display of the fields are described in
``docs/archive/person_profile_card_FEAT_2026-10-23.md`` §3.2 and §3.3. All
dates are ``YYYY-MM-DD`` text (lexicographic order is chronological order,
and they can be searched in Anki); a birthday without a known year is written
``--MM-DD``. Event times (a position's start and end, an education's start and
finish, the time of an experience, and so on; see :data:`HISTORY_PERIOD_KEYS`)
may give only the year or the year and month, and a position's end may be
``?`` (see :func:`normalize_period`). Every JSON item model allows extra keys
(``extra="allow"``): keys in existing data that a model does not list are kept
unchanged when read and saved back.

欄位的意義與顯示見 ``docs/archive/person_profile_card_FEAT_2026-10-23.md`` §3.2、§3.3。所有日期都是
``YYYY-MM-DD`` 文字（字典序就是時間序，也能在 Anki 搜尋）；生日不知年份時寫 ``--MM-DD``；事件時間（職位的任職
起訖、學歷的入學畢業、經歷的時間等，``HISTORY_PERIOD_KEYS``）可以只寫年或年月，離職可以是 ``?``
（見 ``normalize_period``）。
JSON 項目的模型都允許多餘的鍵（``extra="allow"``）：既有資料若有模型沒列出的鍵，讀進來、存回去
都原樣保留。
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
    ValidationError,
    ValidationInfo,
    field_validator,
    model_serializer,
    model_validator,
)

#: The note type.
#: 筆記類型。
PERSON_NOTE_TYPE = "Person_Profile_Dark"

#: For people with this tag, the body text sent by "AI analysis and topics" does not mask names (the name
#: fields are still not sent). A tag is used instead of a field: changing the note type's fields makes AnkiWeb
#: require a full sync, and a tag can also be added or removed directly in the Anki app.
#: 有這個標籤的人：「AI 分析與話題」送出的內文不遮名字（姓名欄位照樣不送）。用標籤而不是欄位：
#: 改筆記類型的欄位會讓 AnkiWeb 要求完整同步；標籤在 Anki app 也能直接加減。
TEXT_UNMASKED_TAG = "AI內文不遮名字"

#: An empty string or ``YYYY-MM-DD``.
#: 空字串或 ``YYYY-MM-DD``。
OPTIONAL_DATE = r"^$|^\d{4}-\d{2}-\d{2}$"
#: ``YYYY-MM-DD``.
#: ``YYYY-MM-DD``。
DATE = r"^\d{4}-\d{2}-\d{2}$"
#: An empty string, ``YYYY-MM-DD``, or ``--MM-DD`` when the year is unknown.
#: 空字串、``YYYY-MM-DD`` 或不知年份的 ``--MM-DD``。
BIRTHDAY = r"^$|^(\d{4}|-)-\d{2}-\d{2}$"

_TEXTAREA = {"x-ui": "textarea"}


def _text(title: str, description: str | None = None, *, multiline: bool = False, pattern: str | None = None):
    """Return an optional text field.

    回傳選填的文字欄位。

    Args:
        title: the field title shown in the form
            表單上顯示的欄位標題
        description: the field description, or ``None`` for none
            欄位說明；``None`` 表示沒有
        multiline: whether the web form shows it as a multi-line text area
            網頁表單是否畫成多行文字框
        pattern: the regular expression the value must match, or ``None`` for no restriction
            值必須符合的正規表示式；``None`` 表示不限制

    Returns:
        a ``Field`` with an empty string as the default
        預設為空字串的 ``Field``
    """
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
    """Return a field typed one entry per row.

    回傳可以一筆一筆輸入的欄位。

    Used for history fields that are not single-valued; the web form shows it
    row by row, with "+" to add one. The JSON Schema markers (recognised by the
    frontend's ``createForm``) are: ``x-ui: list``; ``x-item: textarea`` makes
    each row multi-line; ``x-item: tag`` shows the values as small tags (for
    short values such as languages or interests, laid out horizontally and
    added with Enter); ``x-quick`` gives quick choices (one click adds an entry,
    and existing ones are not repeated). The value may be a block of text (the
    old form, split by :func:`history_from_text`) or a list with one value per
    row.

    用於會變的欄位裡不是單值的：網頁表單畫成一列一列、可「+」新增。
    JSON Schema 的標記（前端 ``createForm`` 認得）：``x-ui: list``；``x-item: textarea`` 每列是多行；
    ``x-item: tag`` 畫成一個一個小標籤（短值的欄位：語言、興趣這種，橫向排、輸入後按 Enter 加入）；
    ``x-quick`` 快速選項（點一下加一筆，已有的不重複）。值可以是一段文字（舊的寫法，照
    ``history_from_text`` 切）或每列一個值的清單。

    Args:
        title: the field title shown in the form
            表單上顯示的欄位標題
        description: the field description, or ``None`` for none
            欄位說明；``None`` 表示沒有
        multiline: whether each row is multi-line (``x-item: textarea``)
            每列是否為多行（``x-item: textarea``）
        tags: whether the values are shown as tags (``x-item: tag``)
            是否畫成小標籤（``x-item: tag``）
        quick: the quick choices (``x-quick``); empty for none
            快速選項（``x-quick``）；空的表示沒有

    Returns:
        a ``Field`` with an empty string as the default and the markers above
        預設為空字串、帶上述標記的 ``Field``

    Raises:
        ValueError: if both ``multiline`` and ``tags`` are set
            若同時設定 ``multiline`` 與 ``tags``
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
    """One position row of the create form: company and title.

    建卡表單上職位的一列：公司與職稱。

    A row where both are empty is skipped.

    都空的那列略過。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    company: str = Field("", title="公司")
    title: str = Field("", title="職稱")


class PersonCreateParams(BaseModel):
    """The parameters for creating a person card from the web or Telegram.

    網頁與 TG 建立人脈卡的參數。

    The display name is required and everything else is optional; the JSON
    fields, the photo, ``Card_ID``, ``TG_Bot``, and ``Updated_At`` are filled by
    the system (§3.11).

    稱呼必填，其餘選填；JSON、照片、``Card_ID``、``TG_Bot``、``Updated_At`` 由系統填（§3.11）。

    History fields (:data:`PERSON_HISTORY_FIELDS`) are turned into lists on
    creation, with today as the added date (:func:`history_from_input`).
    Single-valued fields (residence, time zone, relationship, closeness) are a
    block of text; the others can take one entry per row (``_rows``) and also
    accept a block of text. A position row is company plus title; the retired
    fields (:data:`RETIRED_FIELDS`: ``Title``, ``Work_Detail``) are not in the
    create parameters.

    會變的欄位（``PERSON_HISTORY_FIELDS``）建立時轉成清單、登錄日填今天（``history_from_input``）：
    單值欄位（現居地、時區、關係、親密度）是一段文字；其他的可以一列一筆（``_rows``），也接受一段文字。
    職位一列是公司＋職稱；停用的欄位（``RETIRED_FIELDS``：``Title``、``Work_Detail``）不在建立參數裡。
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
    Daily_Life: str | list[str] = _rows("生活圈與日常", "通勤路線、常去的地方、作息；一件一列", multiline=True)
    Languages: str | list[str] = _rows("語言", "點選或輸入後按 Enter 加入", tags=True, quick=("中文", "日文", "英文"))
    # 一筆是「工作簽證」這類短語（細節記在說明），和項目模型的單行內容一致
    Credentials: str | list[str] = _rows("身分與證照", "簽證與在留資格、駕照、已取得的證照、兵役；一項一列")
    Timezone: str = _text("時區", "例如 Asia/Tokyo")
    Met_Date: str = _text("認識日期", "YYYY-MM-DD", pattern=OPTIONAL_DATE)
    Met_Where: str = _text("認識場合", "例如「Geekly 面談」")
    Met_Via: str = _text("認識管道", "例如「朋友介紹」")
    Introducer_Note_ID: str = _text("介紹人的筆記 ID", "介紹人也有人脈卡時填，背面會出現「查看介紹人」", pattern=r"^$|^\d+$")
    Relationship: str = _text("關係", "例如「客戶」「朋友」「同事」")
    Closeness: str = _text("親密度", "1–5", pattern=r"^$|^[1-5]$")
    First_Impression: str = _text("第一印象", multiline=True)
    # 建卡表單一列只有公司、職稱兩格（都空的列略過）；只有任職時間或說明的工作，建卡後在工作經歷的清單加（§17）
    Company: str | list[PositionInput] = _rows("工作經歷", "一份工作一列：填公司或職稱（只有時間或說明的，建卡後在清單加）")
    Education: str | list[str] = _rows("學歷", "一個學歷一列")
    Interests: str | list[str] = _rows("興趣", "輸入後按 Enter 加入；背面也顯示成標籤", tags=True)
    Experiences: str | list[str] = _rows("經歷", "去過的地方、做過的事、印象深的故事；一件一列（想做的記在「目標」）",
                                         multiline=True)
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
        """Accept looser birthday formats before validation.

        生日的寫法放寬。

        With a year it is stored as ``YYYY-MM-DD``, without one as ``--MM-DD``
        (see :func:`normalize_birthday`); non-string values pass through.

        有年份存 ``YYYY-MM-DD``，沒有年份存 ``--MM-DD``（見 ``normalize_birthday``）；不是字串的值原樣通過。
        """
        return normalize_birthday(value) if isinstance(value, str) else value


_BIRTHDAY_INPUT = re.compile(
    r"\s*(?:(?:(\d{4})\s*[-/.年]\s*)|--)?(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?\s*"
)


def normalize_birthday(text: str) -> str:
    """Normalize a typed birthday into the stored form.

    生日的輸入整理成存放的寫法。

    ``1994-03-15``, ``1994/3/15``, and ``1994年3月15日`` become ``1994-03-15``;
    without a year, ``3/15``, ``03-15``, ``3月15日``, and ``--03-15`` become
    ``--03-15``. Input that cannot be read or is not a real date (``2/30``) is
    returned unchanged, so the field's format check rejects it; without a year,
    2/29 also counts as valid.

    ``1994-03-15``、``1994/3/15``、``1994年3月15日`` → ``1994-03-15``；
    沒有年份的 ``3/15``、``03-15``、``3月15日``、``--03-15`` → ``--03-15``。
    看不懂或不是真的日期（``2/30``）時原樣回傳，交給欄位的格式檢查擋下；沒有年份時 2/29 也算合法。

    Args:
        text: the birthday as typed
            輸入的生日

    Returns:
        the normalized birthday, or ``text`` unchanged if it cannot be normalized
        整理過的生日；無法整理時原樣回傳 ``text``
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
    """Move "hidden" to the end of the JSON Schema properties, in place.

    JSON Schema 裡把「隱藏」排到最後。

    It is defined in the base class, so it would otherwise come first, and the
    web form draws the fields in this order.

    它定義在基底類別，原本會排第一；網頁表單照這個順序畫。

    Args:
        schema: the JSON Schema to modify
            要修改的 JSON Schema
    """
    properties = schema.get("properties")
    if isinstance(properties, dict) and "hidden" in properties:
        properties["hidden"] = properties.pop("hidden")


class _Item(BaseModel):
    """The common settings of JSON items.

    JSON 項目的共同設定。

    Extra keys are kept and surrounding whitespace is stripped; every item can
    be hidden (work history plan §5).

    保留多餘的鍵、去掉前後空白；每一筆都能隱藏（工作經歷計畫書 §5）。

    Optional keys added later (:attr:`_OMIT_WHEN_EMPTY`: hidden, plus each
    model's own optional keys, such as a position's note and event times) are
    not output while they have no value. Both the web whole-field write and the
    Telegram add go through ``model_dump``, so both paths store the same shape,
    which is also the same as before these keys were added. ``hidden`` is a
    strict bool: the card and :func:`is_hidden` accept only ``true``, and values
    such as ``"yes"`` or ``1`` are rejected on write instead of being silently
    turned into True at the next web save.

    後來加的選填鍵（``_OMIT_WHEN_EMPTY``：隱藏，以及各模型自己的選填鍵，例如職位的說明、事件時間）沒有值時不輸出。
    網頁整欄寫入與 TG 新增都經 ``model_dump``，所以兩條路存下來的形狀相同，也和加這些鍵以前一樣。
    ``hidden`` 是嚴格布林：卡面與 ``is_hidden`` 只認 ``true``，``"yes"``、``1`` 這種值寫入時就擋下，
    不會在下次網頁儲存時被偷偷轉成 True。
    """

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True, json_schema_extra=_hidden_last)

    #: The keys left out of the output while they have no value (an empty string or False).
    #: 沒有值（空字串或 False）時不輸出的鍵。
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
    """One meeting or conversation.

    一次見面或聊天。
    """

    date: str = Field(title="日期", pattern=DATE)
    place: str = Field("", title="地點")
    topics: str = Field("", title="聊了什麼", json_schema_extra=_TEXTAREA)
    updates: str = Field("", title="對方的近況", json_schema_extra=_TEXTAREA)
    quote: str = Field("", title="對方說的一句話", json_schema_extra=_TEXTAREA)


class Followup(_Item):
    """A follow-up item.

    待追蹤事項。
    """

    item: str = Field(min_length=1, title="內容")
    type: Literal["ask", "my_promise", "their_promise"] = Field(
        "ask", title="種類", description="ask：下次要問；my_promise：我答應的；their_promise：對方答應的"
    )
    status: Literal["open", "done"] = Field("open", title="狀態", description="open：未完成；done：已完成")
    added: str = Field("", title="記下的日期", pattern=OPTIONAL_DATE)
    due: str = Field("", title="期限", pattern=OPTIONAL_DATE)


class FamilyMember(_Item):
    """A family member or pet.

    家人或寵物。
    """

    relation: str = Field(min_length=1, title="關係", description="例如「妻子」「兒子」「貓」")
    name: str = Field("", title="名字")
    birth_year: int | None = Field(None, title="出生年", ge=1900, le=2100)
    note: str = Field("", title="備註")


class Gift(_Item):
    """A gift given or received, or a gift idea.

    送出或收到的禮物，或禮物靈感。

    A gift idea (``idea``) is something the person wants; after giving it,
    change the direction to given and keep using the same entry.

    禮物靈感（``idea``）是對方想要的；送出後把方向改成送出，同一筆接著用。
    """

    item: str = Field(min_length=1, title="禮物")
    direction: Literal["given", "received", "idea"] = Field(
        "given", title="方向", description="given：送出；received：收到；idea：想要的（禮物靈感）"
    )
    date: str = Field("", title="日期", pattern=OPTIONAL_DATE)
    reaction: str = Field("", title="反應")


class KeyDate(_Item):
    """An important date.

    重要日期。
    """

    date: str = Field(title="日期", pattern=DATE)
    label: str = Field(min_length=1, title="名稱", description="例如「N1 考試」「結婚紀念日」")
    yearly: bool = Field(False, title="每年")


class Contact(_Item):
    """A contact channel.

    聯絡方式。
    """

    channel: str = Field(min_length=1, title="管道", description="例如 LINE、Email")
    handle: str = Field(min_length=1, title="帳號")
    preferred: bool = Field(False, title="偏好")


def append_field_html(current: str, addition: str) -> str:
    """Append new content (escaped HTML) after a text field's existing content.

    把新的內容（已轉義的 HTML）接在文字欄位原本的內容後面。

    The two are separated by a line break (``<br>``).

    以換行（``<br>``）分隔。

    Args:
        current: the field's existing content
            欄位原本的內容
        addition: the escaped HTML to append
            要接上的已轉義 HTML

    Returns:
        the combined content; just ``addition`` when ``current`` is blank
        合併後的內容；``current`` 為空白時只有 ``addition``
    """
    if not current.strip():
        return addition
    return f"{current}<br>{addition}"


#: The keys the fingerprint reads: the main text fields of each item type (the same as the template's
#: itemFingerprint).
#: 指紋用的鍵：每種項目的主要文字欄位（與模板 itemFingerprint 相同）。
FINGERPRINT_KEYS = ("date", "item", "topics", "name", "label", "handle", "relation", "channel")
#: The extra keys of history-field items added to the fingerprint, appended only when the item has the key (so
#: the fingerprints of the original six lists do not change). ``added`` and ``ended`` are deliberately left out:
#: an entry keeps its fingerprint after being struck out and restored, so Telegram's "undo" button and old links
#: on the card still match. Event times (:data:`HISTORY_PERIOD_KEYS`, including a position's ``start`` and
#: ``end``), the note (``note``), and each item's ``hidden`` are also left out on purpose, so links still match
#: after adding a time, leaving a job, or hiding. The exception is a job without company and title (§17): its
#: note is appended, otherwise all such jobs share one fingerprint and, when an index is stale, delete, hide, or
#: strike out would act on another entry (see :func:`item_fingerprint`).
#: 會變的欄位的項目另外加進指紋的鍵：只在項目有這個鍵時才接上（原有六種清單的指紋不變）。``added``、
#: ``ended`` 刻意不放：劃掉、取消劃掉後同一筆的指紋不變，TG 的「復原」按鈕與卡片上的舊連結才對得上。事件時間
#: （``HISTORY_PERIOD_KEYS``，含職位的 ``start``、``end``）、說明（``note``）與每筆的 ``hidden`` 也刻意不放：
#: 補時間、離職、隱藏後連結仍對得上。例外是沒有公司與職稱的工作（§17）：接上說明，不然這種工作的指紋全部相同，
#: 序號過時時刪除、隱藏、劃掉會動到另一筆（見 ``item_fingerprint``）。
FINGERPRINT_EXTRA_KEYS = ("value", "company", "title")


def item_fingerprint(item: object) -> str:
    """Return the fingerprint of a JSON item.

    回傳一筆 JSON 項目的指紋。

    The fingerprint is identical to the card template's ``itemFingerprint``:
    the main text fields joined by ``|``, hashed with djb2 over the UTF-16 code
    units, in base 36. The card's long-press menu, mark-done and strike-out
    links, and the Telegram buttons append it after the index, to confirm that
    the entry being changed is still the same one (the index may be stale).
    History-field items also append the keys in :data:`FINGERPRINT_EXTRA_KEYS`
    that they have, and a position without company and title also appends its
    note.

    指紋與卡片模板的 ``itemFingerprint`` 相同：主要文字欄位以 ``|`` 串起，djb2 雜湊（UTF-16 碼元），36 進位。
    卡片上的長按選單、標成完成、劃掉連結與 TG 的按鈕在序號後面附上它，用來確認要動的還是同一筆（序號可能過時）。
    會變的欄位的項目另外接上 ``FINGERPRINT_EXTRA_KEYS`` 裡有的鍵；沒有公司與職稱的職位另外接上說明。

    Args:
        item: one item (usually a dict; other types are treated as having no fields)
            一筆項目（通常是 dict；其他型別視為沒有欄位）

    Returns:
        a base-36 string
        36 進位字串
    """
    values = item if isinstance(item, dict) else {}
    key = "|".join("" if values.get(k) is None else _js_string(values[k]) for k in FINGERPRINT_KEYS)
    key += "".join("|" + _js_string(values[k]) for k in FINGERPRINT_EXTRA_KEYS if values.get(k) is not None)
    if ("company" in values or "title" in values) and not _position_name(values):
        key += "|" + str(values.get("note") or "")
    h = 5381
    data = key.encode("utf-16-le")
    for i in range(0, len(data), 2):
        h = ((h * 33) ^ (data[i] | data[i + 1] << 8)) & 0xFFFFFFFF
    return _base36(h)


def _js_string(value: object) -> str:
    """Convert a value to text the way JavaScript's ``String(value)`` does.

    與 JavaScript 的 ``String(value)`` 相同的寫法。

    Bools become ``true`` / ``false`` and integral floats lose the ``.0``.

    布林寫成 ``true`` / ``false``，整數值的浮點數去掉 ``.0``。

    Args:
        value: the value to convert
            要轉換的值

    Returns:
        the text form
        文字寫法
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _base36(number: int) -> str:
    """Convert a non-negative integer to base 36 (lowercase, like JS ``toString(36)``).

    非負整數轉 36 進位（小寫，同 JS ``toString(36)``）。

    Args:
        number: the non-negative integer
            非負整數

    Returns:
        the base-36 string
        36 進位字串
    """
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

#: Makes the web form prefill today when adding an entry (the frontend's ``newItem`` recognises this marker).
#: 新增一筆時網頁表單預填今天（前端 ``newItem`` 認這個標記）。
_TODAY = {"x-default": "today"}


def _added_field():
    return Field("", title="登錄日", description="新增時系統填今天；空白表示日期不明", pattern=OPTIONAL_DATE,
                 json_schema_extra=_TODAY)


def _ended_field():
    return Field("", title="失效日", description="空白表示目前有效；劃掉時系統填今天", pattern=OPTIONAL_DATE)


class HistoryEntry(_Item):
    """One history entry: content, added date, and ended date.

    會變的資料的一筆：內容、登錄日、失效日。

    An empty ended date means the entry is currently valid.

    失效日為空字串＝目前有效。
    """

    value: str = Field(min_length=1, title="內容")
    added: str = _added_field()
    ended: str = _ended_field()


class HistoryNote(HistoryEntry):
    """A multi-line history entry (one paragraph per entry).

    多行的一筆（一筆一段）。
    """

    value: str = Field(min_length=1, title="內容", json_schema_extra=_TEXTAREA)


class ClosenessEntry(HistoryEntry):
    """A closeness entry (1–5).

    親密度的一筆（1–5）。
    """

    value: str = Field(title="親密度", description="1–5", pattern=r"^[1-5]$")


#: The stored shape of an event time: blank, a year, a year and month, or a full date (:func:`normalize_period`
#: checks that the date is real; the pattern only checks the shape).
#: 事件時間存放的形狀：空白、年、年月或完整日期（真實日期由 ``normalize_period`` 檢查，pattern 只看形狀）。
PERIOD = r"^$|^\d{4}(-\d{2}(-\d{2})?)?$"
#: The end of a position: the same as :data:`PERIOD`, plus ``?`` meaning already left but the time is unknown.
#: 離職：同 ``PERIOD``，另外 ``?`` 表示已經離職但不知道時間。
PERIOD_END = r"^$|^\?$|^\d{4}(-\d{2}(-\d{2})?)?$"

# 年，或年月（年與月之間要有分隔），或年月日（月與日之間也要有分隔，或寫成「4月1日」）：
# 2021-123 這種少了分隔的打錯字不要被猜成 2021-12-03
_PERIOD_INPUT = re.compile(
    r"\s*(\d{4})\s*年?\s*(?:[-/.年]?\s*(?<=[-/.年\s])(\d{1,2})\s*月?\s*(?:(?:[-/.]|(?<=月))\s*(\d{1,2})\s*日?)?)?\s*"
)


def normalize_period(text: str, *, allow_unknown: bool = False) -> str | None:
    """Normalize a typed event time into the stored shape.

    事件時間的寫法整理成存放的形狀。

    ``2021`` stays ``2021``; ``2021-4``, ``2021/04``, and ``2021年4月`` become
    ``2021-04``; ``2021-04-01`` and ``2021年4月1日`` become ``2021-04-01``.
    Blank input (after stripping) becomes an empty string.

    ``2021``、``2021-4``、``2021/04``、``2021年4月`` → ``2021-04``；
    ``2021-04-01``、``2021年4月1日`` → ``2021-04-01``；空白不變（去掉前後空白後回傳空字串）。

    Args:
        text: the input
            輸入
        allow_unknown: whether to accept ``?`` (the full-width "？" also counts) meaning unknown (used for the end
            of a position)
            接受 ``?``（全形「？」也算）表示不知道（離職用）

    Returns:
        the normalized value (``"?"`` for an accepted unknown); ``None`` when the input cannot be read, the year
        is not within 1900–2100, or it is not a real date (``2021-13``, ``2021-02-31``)
        整理過的值（接受的「不知道」為 ``"?"``）；看不懂、年份不在 1900–2100、或不是真的日期
        （``2021-13``、``2021-02-31``）時為 ``None``
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


def period_order_error(start: str, end: str, *, labels: tuple[str, str] = ("到職", "離職")) -> str | None:
    """Return the error message when the end is earlier than the start.

    回傳結束早於開始時的錯誤訊息。

    The comparison uses the zero-padded strings (lexicographic order is
    chronological order), so each side first goes through
    :func:`normalize_period`: comparing a hand-edited ``2021/4`` left on a card
    directly would give the wrong result. Values that cannot be read are not
    reported here (the format check reports them). With different precision
    (2021 against 2021-04), the common prefix is compared, so the same year
    counts as reasonable. Every start/end order check (positions, educations,
    credentials) uses this function, so the messages are consistent.

    比較的是補零後的字串（字典序就是時間序），所以先各自經 ``normalize_period`` 整理：卡上手改留下的
    ``2021/4`` 這種寫法直接比會比錯。看不懂的值不在這裡報（由格式檢查報）。精度不同（2021 對 2021-04）時
    比共同的前綴：同一年算合理。所有起訖先後檢查（職位、學歷、身分與證照）都用這裡，訊息一致。

    Args:
        start: the start
            開始
        end: the end; ``?`` means unknown
            結束；``?`` 表示不知道
        labels: the names of the start and the end used in the message (defaults to a position's start and end;
            educations pass 「入學」「畢業」, credentials 「取得」「到期」)
            訊息裡開始與結束的名稱（預設是職位的「到職」「離職」，學歷傳「入學」「畢業」、身分與證照傳「取得」「到期」）

    Returns:
        the error message, or ``None`` in every other case (either side blank or unreadable, or the end is ``?``)
        錯誤訊息；其他情況（任一邊空白或看不懂、結束是 ``?``）為 ``None``
    """
    start_value = normalize_period(start)
    end_value = normalize_period(end, allow_unknown=True)
    if not start_value or not end_value or end_value == "?":
        return None
    size = min(len(start_value), len(end_value))
    if end_value[:size] < start_value[:size]:
        return f"{labels[1]}（{end_value}）不能早於{labels[0]}（{start_value}）"
    return None


class Position(_Item):
    """One job (work history plan §4.1).

    一份工作（工作經歷計畫書 §4.1）。

    It has a company, title, start and end, note, added date, and ended date.
    Company and title may both be empty (they are often not asked in a chat,
    §17), but at least one of company, title, start, end, and note must be set;
    a job without company and title is shown as :data:`UNNAMED_POSITION`. A job
    is current when it has neither an end (``end``) nor a strike-out
    (``ended``); see :func:`is_active`.

    公司、職稱、任職起訖、說明、登錄日、失效日。公司與職稱都可以空
    （聊天常常沒問到，§17），但公司、職稱、任職起訖、說明至少要有一項；沒有公司與職稱的顯示成
    ``UNNAMED_POSITION``。現職＝沒有離職（``end``）也沒有劃掉（``ended``），見 ``is_active``。

    Combinations of ``end`` and ``ended``: both empty means the current job; an
    end filled in on the web, a Telegram "left the job" (both set), and old data
    with only a strike-out are all former jobs. The start and end are normalized
    into the stored shape on input (:func:`normalize_period`), and the end must
    not be earlier than the start.

    ``end`` 與 ``ended`` 的組合：都空＝現職；網頁填了離職、TG 離職（兩個都有）、舊資料只有劃掉，都是以前的工作。
    任職起訖輸入時整理成存放的形狀（``normalize_period``），離職不能早於到職。
    """

    _OMIT_WHEN_EMPTY: ClassVar[tuple[str, ...]] = ("hidden", "start", "end", "note")

    company: str = Field("", title="公司")
    title: str = Field("", title="職稱")
    start: str = Field("", title="到職", description="年、年月或日期，例如 2021-04", pattern=PERIOD)
    end: str = Field("", title="離職", description="空白＝現職；已離職但不知道時間填 ?", pattern=PERIOD_END)
    note: str = Field("", title="說明", description="這份工作做什麼、負責的專案（可以多行）", json_schema_extra=_TEXTAREA)
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
        if not (self.company or self.title or self.start or self.end or self.note):
            raise ValueError("工作經歷至少要有公司、職稱、任職時間或說明其中一項")
        error = period_order_error(self.start, self.end)
        if error:
            raise ValueError(error)
        return self


def _period_value(value: object, what: str) -> object:
    """Normalize a typed event time into the stored shape (:func:`normalize_period`).

    事件時間的輸入整理成存放的形狀（``normalize_period``）。

    Args:
        value: the input; non-string values are returned unchanged
            輸入；不是字串的值原樣回傳
        what: the name of the time, used in the error message
            時間的名稱，用在錯誤訊息

    Returns:
        the normalized value
        整理過的值

    Raises:
        ValueError: if the value cannot be read (reported as a validation error)
            若看不懂（成為驗證錯誤）
    """
    if not isinstance(value, str):
        return value
    normalized = normalize_period(value)
    if normalized is None:
        raise ValueError(f"{what}「{value.strip()}」看不懂，請寫 2021、2021-04 或 2021-04-01")
    return normalized


class WhenNote(HistoryNote):
    """A history note with the time the thing happened (experiences, praise material).

    有發生時間的一筆（經歷、讚美素材）。

    ``when`` is the time of the event (a year, a year and month, or a date; may
    be empty), which differs from the added date (the day it was written down);
    see the work history plan §15.

    ``when`` 是那件事的時間（年、年月或日期，可空），和登錄日（記下的那天）不同（工作經歷計畫書 §15）。
    """

    _OMIT_WHEN_EMPTY: ClassVar[tuple[str, ...]] = ("hidden", "when")

    when: str = Field("", title="時間", description="那件事的時間：年、年月或日期，例如 2019、2026-10", pattern=PERIOD)

    @field_validator("when", mode="before")
    @classmethod
    def _normalize_when(cls, value: object) -> object:
        return _period_value(value, "時間")


class SinceEntry(HistoryEntry):
    """A history entry with the time it began.

    有開始時間的一筆。

    For example when an interest started or when the person moved to the
    current residence: ``since`` (a year, a year and month, or a date; may be
    empty).

    例如興趣從什麼時候開始、現居地什麼時候搬來：``since``（年、年月或日期，可空）。
    """

    _OMIT_WHEN_EMPTY: ClassVar[tuple[str, ...]] = ("hidden", "since")

    since: str = Field("", title="開始", description="從什麼時候開始：年、年月或日期，例如 2015", pattern=PERIOD)

    @field_validator("since", mode="before")
    @classmethod
    def _normalize_since(cls, value: object) -> object:
        return _period_value(value, "開始時間")


#: The names of an education's start and finish (used in messages).
#: 學歷起訖的名稱（訊息用）。
EDUCATION_LABELS = ("入學", "畢業")


class EducationEntry(HistoryEntry):
    """One education entry.

    學歷的一筆。

    It has the start ``start`` and the finish ``finish`` (a year, a year and
    month, or a date, both may be empty; the finish must not be earlier than
    the start) and the note ``note`` (the major and so on; work history plan
    §18).

    入學 ``start``、畢業 ``finish``（年、年月或日期，都可空；畢業不能早於入學）、說明 ``note``
    （科系等，工作經歷計畫書 §18）。

    The finish is not called ``end``: ``is_active`` treats an entry with
    ``end`` as no longer valid (a position's end), but an education that was
    finished is still valid data.

    畢業不叫 ``end``：``is_active`` 把有 ``end`` 的當成失效（職位的離職），畢業過的學歷仍是有效的資料。
    """

    _OMIT_WHEN_EMPTY: ClassVar[tuple[str, ...]] = ("hidden", "start", "finish", "note")

    start: str = Field("", title="入學", description="年、年月或日期，例如 2011-09", pattern=PERIOD)
    finish: str = Field("", title="畢業", description="畢業或離開的時間；還在讀就空白", pattern=PERIOD)
    note: str = Field("", title="說明", description="科系、研究、社團（可以多行）", json_schema_extra=_TEXTAREA)

    @field_validator("start", "finish", mode="before")
    @classmethod
    def _normalize_bounds(cls, value: object, info: ValidationInfo) -> object:
        return _period_value(value, EDUCATION_LABELS[0 if info.field_name == "start" else 1])

    @model_validator(mode="after")
    def _check_order(self) -> EducationEntry:
        error = period_order_error(self.start, self.finish, labels=EDUCATION_LABELS)
        if error:
            raise ValueError(error)
        return self


#: The names of a credential's start and expiry (used in messages).
#: 身分與證照起訖的名稱（訊息用）。
CREDENTIAL_LABELS = ("取得", "到期")


class CredentialEntry(HistoryEntry):
    """One status or credential: a visa, residence status, driving licence, certification, military service.

    身分與證照的一筆：簽證、在留資格、駕照、證照、兵役（欄位分工計畫書 D1）。

    It has when it was obtained ``since``, when it expires ``until`` (a year, a year and month, or a date, both may
    be empty; the expiry must not be earlier than the start) and the note ``note`` (for example 「效期 3 年」); see
    the field routing plan D1. The expiry is not called ``end``: ``is_active`` treats an entry with ``end`` as no
    longer valid, but a credential with an expiry date is still valid until it is struck out. Passing the expiry
    does not strike it out automatically: the user does (a renewal strikes out the old entry and adds a new one).

    取得 ``since``、到期 ``until``（年、年月或日期，都可空；到期不能早於取得）、說明 ``note``（例如「效期 3 年」）。
    到期不叫 ``end``：``is_active`` 把有 ``end`` 的當成失效，有到期日的證照在劃掉前仍是有效的資料。過了到期日
    也不會自動劃掉，由使用者劃掉（續簽是劃掉舊的、新增一筆）。
    """

    _OMIT_WHEN_EMPTY: ClassVar[tuple[str, ...]] = ("hidden", "since", "until", "note")

    value: str = Field(min_length=1, title="內容", description="例如「人文簽證」「普通駕照」「沒當過兵」")
    since: str = Field("", title="取得", description="年、年月或日期，例如 2024-04", pattern=PERIOD)
    until: str = Field("", title="到期", description="效期到什麼時候；沒有期限或不知道就空白", pattern=PERIOD)
    note: str = Field("", title="說明", description="例如「效期 3 年」「每年要更新」（可以多行）", json_schema_extra=_TEXTAREA)

    @field_validator("since", "until", mode="before")
    @classmethod
    def _normalize_bounds(cls, value: object, info: ValidationInfo) -> object:
        return _period_value(value, CREDENTIAL_LABELS[0 if info.field_name == "since" else 1])

    @model_validator(mode="after")
    def _check_order(self) -> CredentialEntry:
        error = period_order_error(self.since, self.until, labels=CREDENTIAL_LABELS)
        if error:
            raise ValueError(error)
        return self


#: The kinds of a goal (quick choices; any other value may also be typed).
#: 目標的類型（快速選項；也可以自己填）。
GOAL_KINDS = ("證照", "簽證", "資格", "升學", "職涯", "努力方向", "夢想", "願景")
#: The goal statuses and their Chinese names.
#: 目標的狀態與中文。
GOAL_STATUSES = {"open": "進行中", "done": "達成", "dropped": "放棄"}


class Goal(_Item):
    """A goal (work history plan §6): kind, content, target time, and status.

    一個目標（工作經歷計畫書 §6）：類型、內容、目標時間、狀態。

    The day it was reached or dropped is recorded in ``closed``; an open goal
    always has an empty ``closed``.

    達成或放棄的那天記在 ``closed``；進行中的目標 ``closed`` 一律為空。
    """

    kind: str = Field("", title="類型", description="例如：" + "、".join(GOAL_KINDS) + "；也可以自己填")
    item: str = Field(min_length=1, title="內容", description="例如「日本語能力試驗 N1」")
    target: str = Field("", title="目標時間", description="年、年月或日期，例如 2026-12；可空", pattern=PERIOD)
    status: Literal["open", "done", "dropped"] = Field(
        "open", title="狀態", description="open：進行中；done：達成；dropped：放棄"
    )
    added: str = _added_field()
    closed: str = Field("", title="結束日", description="達成或放棄的那天（TG 按的時候問，預設今天）", pattern=OPTIONAL_DATE)

    @model_validator(mode="after")
    def _open_has_no_closed(self) -> Goal:
        # 進行中的目標沒有結束日：網頁把狀態改回進行中時，留著的結束日會讓卡面寫成「進行中 2026-11-01」
        if self.status == "open":
            self.closed = ""
        return self

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
    """Return whether a goal is still open.

    回傳目標是否進行中。

    Args:
        item: the goal item; anything that is not a dict does not count
            目標項目；不是 dict 的不算

    Returns:
        ``True`` if the status is ``open`` or missing
        狀態為 ``open`` 或沒有狀態時為 ``True``
    """
    return isinstance(item, dict) and str(item.get("status") or "open") == "open"


#: JSON field mapped to (item model, title).
#: JSON 欄位 → (項目模型, 標題)。
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
    """The settings of one history field.

    一個會變的欄位的設定。

    Construction raises ``ValueError`` if both ``single`` and ``split`` are set.

    ``single`` 與 ``split`` 同時設定時，建立時拋出 ``ValueError``。

    Attributes:
        model: the model of one entry
            一筆的模型
        title: the Chinese display name
            中文名稱
        single: whether this is a single-valued field: adding an entry automatically strikes out the currently
            valid one (registration-date plan D5)
            單值欄位：新增時自動把原本有效的劃掉（登錄日計畫書 D5）
        split: whether typed text is split into several entries on commas, ideographic commas, and line breaks
            文字輸入時以逗號、頓號、換行切成多筆
    """

    model: type[_Item]
    title: str
    single: bool = False
    split: bool = False

    def __post_init__(self) -> None:
        # 單值欄位一次只有一筆有效；切成多筆會在新增時互相劃掉
        if self.single and self.split:
            raise ValueError(f"{self.title}：單值欄位不能切成多筆")


#: The fields changed into lists (registration-date plan D4), roughly in the order of the sections on the back of
#: the card (``Dietary`` is a must-read before meeting).
#: 改成清單的欄位（登錄日計畫書 D4），大致依卡片背面的區塊（``Dietary`` 在見面前必看）。
PERSON_HISTORY_FIELDS: dict[str, HistoryField] = {
    "Interests": HistoryField(SinceEntry, "興趣", split=True),
    "Experiences": HistoryField(WhenNote, "經歷"),
    "Food_Drink": HistoryField(HistoryEntry, "飲食喜好", split=True),
    "Dislikes": HistoryField(HistoryEntry, "不喜歡的事", split=True),
    "Dietary": HistoryField(HistoryNote, "飲食限制"),
    "Company": HistoryField(Position, "工作經歷"),
    "Education": HistoryField(EducationEntry, "學歷"),
    "Personality_Values": HistoryField(HistoryNote, "個性與價值觀"),
    "Praise": HistoryField(WhenNote, "讚美素材"),
    "Ask_Advice": HistoryField(HistoryNote, "可以請教的事"),
    "Needs": HistoryField(HistoryNote, "對方需要的幫助"),
    "Common_Ground": HistoryField(HistoryNote, "共同點"),
    "Comm_Prefs": HistoryField(HistoryNote, "溝通偏好"),
    "Residence": HistoryField(SinceEntry, "現居地", single=True),
    "Daily_Life": HistoryField(HistoryNote, "生活圈與日常"),
    "Languages": HistoryField(HistoryEntry, "語言", split=True),
    "Credentials": HistoryField(CredentialEntry, "身分與證照"),
    "Timezone": HistoryField(HistoryEntry, "時區", single=True),
    "Relationship": HistoryField(HistoryEntry, "關係", single=True),
    "Closeness": HistoryField(ClosenessEntry, "親密度", single=True),
    "Sensitive": HistoryField(HistoryNote, "敏感話題"),
}

#: The fields no longer used, with the explanation shown when a write is rejected (the note type keeps the
#: fields, because deleting a field needs a full sync): the title is merged into the work history's title, and
#: the work detail into each job's note (work history plan §18).
#: 不再使用的欄位與擋下寫入時的說明（筆記類型的欄位保留，刪欄位要完整同步）：職稱併進工作經歷的職稱，
#: 工作內容併進每份工作的說明（工作經歷計畫書 §18）。
RETIRED_FIELDS: dict[str, str] = {
    "Title": "「職稱」已併進「工作經歷」（Company 欄位）：請在工作經歷的清單裡改。",
    "Work_Detail": "「工作內容」已併進「工作經歷」每份工作的說明（Company 欄位的 note）：請在工作經歷的清單裡改。",
}

#: The history fields whose entries have a note (``note``): what the job is for the work history, the major for
#: the education (§18), details such as the validity for a credential (field routing plan D1).
#: 每筆有說明（``note``）的會變欄位：工作經歷是做什麼，學歷是科系（§18），身分與證照是效期等細節（欄位分工計畫書 D1）。
NOTE_FIELDS = ("Company", "Education", "Credentials")

#: The keys of each history field that record an "event time" (work history plan §15): the time of the event,
#: which differs from the added date and the strike-out date filled by the system. The order is the order of the
#: follow-up questions when adding; the LLM's ``time_start`` maps to the first and ``time_end`` to the second, so
#: a field has at most two. A second key must not be called ``end`` unless it really ends the entry:
#: :func:`is_active` treats an entry with ``end`` as no longer valid (only a position's leaving date means that;
#: education uses ``finish`` and credentials ``until`` for this reason).
#: 會變的欄位裡記「事件時間」的鍵（工作經歷計畫書 §15）：那件事的時間，和系統填的登錄日、劃掉日不同。
#: 順序是新增時追問的順序；LLM 的 ``time_start`` 對應第一個、``time_end`` 對應第二個，所以最多兩個。第二個鍵除非
#: 真的代表失效，不能叫 ``end``：``is_active`` 把有 ``end`` 的當成失效（只有職位的離職是這個意思，學歷用
#: ``finish``、身分與證照用 ``until`` 就是這個原因）。
HISTORY_PERIOD_KEYS: dict[str, tuple[str, ...]] = {
    "Company": ("start", "end"),
    "Education": ("start", "finish"),
    "Experiences": ("when",),
    "Praise": ("when",),
    "Interests": ("since",),
    "Residence": ("since",),
    "Credentials": ("since", "until"),
}

#: The keys "edit one item" may change (work history plan §16), in the order of the Telegram questions: for
#: history fields, the content, event times, and note (for a position: company, title, start and end, and note);
#: for other lists, the items asked when adding. The added date, strike-out date, hidden, status, and a goal's
#: closed date are not here (each has its own button; :data:`SYSTEM_ITEM_KEYS`).
#: 「修改一筆」能改的鍵（工作經歷計畫書 §16），順序同 TG 的問答：會變的欄位是內容、事件時間與說明（職位是
#: 公司、職稱、任職起訖與說明），其他清單是新增時問的那幾項。登錄日、劃掉日、隱藏、狀態與目標的結束日不在
#: 這裡（各有自己的按鈕，``SYSTEM_ITEM_KEYS``）。
ITEM_EDIT_KEYS: dict[str, tuple[str, ...]] = {
    "Interactions_JSON": ("date", "topics", "updates", "place", "quote"),
    "Followups_JSON": ("type", "item", "due"),
    "Family_JSON": ("relation", "name", "birth_year", "note"),
    "Gifts_JSON": ("direction", "item", "date", "reaction"),
    "Key_Dates_JSON": ("label", "date", "yearly"),
    "Contacts_JSON": ("channel", "handle", "preferred"),
    "Goals_JSON": ("kind", "item", "target"),
    "Company": ("company", "title", "start", "end", "note"),
    **{name: ("value", *HISTORY_PERIOD_KEYS.get(name, ()), *(("note",) if name in NOTE_FIELDS else ()))
       for name in PERSON_HISTORY_FIELDS if name != "Company"},
}

# 會變的欄位必須是建立參數裡的文字欄位，而且不能和原本的 JSON 清單重名（打錯欄位名時 import 就失敗）
assert set(PERSON_HISTORY_FIELDS) <= set(PersonCreateParams.model_fields), "PERSON_HISTORY_FIELDS 有不存在的欄位"
assert set(PERSON_HISTORY_FIELDS).isdisjoint(PERSON_JSON_ITEMS), "PERSON_HISTORY_FIELDS 與 PERSON_JSON_ITEMS 重複"
assert set(ITEM_EDIT_KEYS) == set(PERSON_HISTORY_FIELDS) | set(PERSON_JSON_ITEMS), "ITEM_EDIT_KEYS 沒有涵蓋每個清單"
assert all(set(keys) <= set((PERSON_HISTORY_FIELDS[name].model if name in PERSON_HISTORY_FIELDS
                             else PERSON_JSON_ITEMS[name][0]).model_fields)
           for name, keys in ITEM_EDIT_KEYS.items()), "ITEM_EDIT_KEYS 有模型沒有的鍵"
#: The system-owned keys: the added date, strike-out date, hidden, status, and a goal's closed date; each has its
#: own button, and editing must not touch them.
#: 系統管理的鍵：登錄日、劃掉日、隱藏、狀態與目標的結束日，各有自己的按鈕，修改不能碰。
SYSTEM_ITEM_KEYS = frozenset({"added", "ended", "hidden", "status", "closed"})
assert all(SYSTEM_ITEM_KEYS.isdisjoint(keys) for keys in ITEM_EDIT_KEYS.values()), "ITEM_EDIT_KEYS 含系統管理的鍵"
assert set(RETIRED_FIELDS).isdisjoint(PersonCreateParams.model_fields) \
    and set(RETIRED_FIELDS).isdisjoint(PERSON_HISTORY_FIELDS), "停用的欄位還在建立參數或會變的欄位裡"
# 有說明鍵的模型剛好是 NOTE_FIELDS（漏列時說明不能改、整理的說明會被當成用不到而丟掉）
assert {name for name, spec in PERSON_HISTORY_FIELDS.items() if "note" in spec.model.model_fields} == set(NOTE_FIELDS), \
    "NOTE_FIELDS 與項目模型不一致"
# LLM 的輸出只有 time_start、time_end 兩個時間（第三個鍵會被靜默丟掉）
assert all(len(keys) <= 2 for keys in HISTORY_PERIOD_KEYS.values()), "HISTORY_PERIOD_KEYS 最多兩個時間鍵"
# 訊息用的起訖名稱和模型的欄位名稱相同
assert EDUCATION_LABELS == (EducationEntry.model_fields["start"].title, EducationEntry.model_fields["finish"].title)
assert CREDENTIAL_LABELS == (CredentialEntry.model_fields["since"].title, CredentialEntry.model_fields["until"].title)


def item_model(name: str) -> type[BaseModel]:
    """Return the model of one item of a list (a history field or a JSON list).

    回傳清單一筆的模型（會變的欄位或 JSON 清單）。

    Args:
        name: the history field or JSON field name
            會變的欄位或 JSON 欄位名稱

    Returns:
        the item model
        項目模型

    Raises:
        KeyError: if ``name`` is neither a history field nor a JSON list
            若 ``name`` 既不是會變的欄位也不是 JSON 清單
    """
    return PERSON_HISTORY_FIELDS[name].model if name in PERSON_HISTORY_FIELDS else PERSON_JSON_ITEMS[name][0]


def item_error_keys(name: str, entry: dict[str, object]) -> set[str]:
    """Return the keys of an item that fail model validation.

    回傳這一筆過不了模型驗證的鍵。

    These are usually values broken by hand edits on the card.

    通常是卡上手改壞的值。

    Args:
        name: the history field or JSON field name
            會變的欄位或 JSON 欄位名稱
        entry: the item to validate
            要驗證的一筆

    Returns:
        the failing keys; an error at the whole-item level (for example a finish earlier than the start) is the
        empty string; an empty set when there is no error
        過不了驗證的鍵；整筆層級的錯（例如畢業早於入學）是空字串；沒有錯時為空集合
    """
    try:
        item_model(name).model_validate(entry)
    except ValidationError as e:
        return {str(error["loc"][0]) if error["loc"] else "" for error in e.errors()}
    return set()

_SPLIT = re.compile(r"[,，、\n]+")


def is_active(item: object) -> bool:
    """Return whether an entry is currently valid.

    回傳這一筆目前是否有效。

    An entry is valid when ``ended`` is empty and it has no ``end`` (a position
    with an end date is a former job). The field is not checked: only the
    position model has ``end``; if hand edits leave an extra ``end`` in another
    list, it also counts as no longer valid, and Telegram's "restore" clears it
    as well
    (:func:`app.services.task_handlers.person_profile_handler.set_history_status`).

    ``ended`` 是空的，也沒有 ``end``（職位有離職年月就是以前的工作）。
    不看欄位：只有職位的模型有 ``end``；其他清單若因手改留下多餘的 ``end``，也算失效，TG 的取消劃掉會一併清掉它
    （``app.services.task_handlers.person_profile_handler.set_history_status``）。

    Args:
        item: the entry; anything that is not a dict is not valid
            一筆；不是 dict 的不算有效

    Returns:
        ``True`` if the entry is currently valid
        目前有效時為 ``True``
    """
    return (
        isinstance(item, dict)
        and not str(item.get("ended") or "").strip()
        and not str(item.get("end") or "").strip()
    )


def is_hidden(item: object) -> bool:
    """Return whether an item is hidden on the card.

    回傳這一筆是否在卡面隱藏。

    Args:
        item: the item; anything that is not a dict is not hidden
            一筆；不是 dict 的不算隱藏

    Returns:
        ``True`` only when ``hidden`` is exactly ``True``
        只有 ``hidden`` 正好是 ``True`` 時為 ``True``
    """
    return isinstance(item, dict) and item.get("hidden") is True


#: The tag prefix that hides a text field on the card, as in ``人脈卡隱藏::Appearance`` (work history plan D8; a tag
#: is used so the note type does not change).
#: 文字欄位在卡面隱藏的標籤前綴：``人脈卡隱藏::Appearance``（工作經歷計畫書 D8；用標籤不改筆記類型）。
HIDDEN_TAG_PREFIX = "人脈卡隱藏::"


#: The text fields the card can hide (the template has matching covers; tests/test_person_profile_template.py
#: compares them). Names have a single switch, ``Display_Name``, which covers the whole name area (the names in
#: each language, the readings, the honorific, and the name memo); ``Photo`` covers the avatar photo. Telegram
#: shows the hide switch only for these fields, so pressing it always changes the card.
#: 卡面能隱藏的文字欄位（模板有對應的遮擋；tests/test_person_profile_template.py 比對）。名字只有
#: ``Display_Name`` 一個開關，遮整個名字區（各語言的名字、讀音、敬稱、名字記法）；``Photo`` 遮頭像照片。
#: TG 只對這些欄位顯示隱藏開關，免得按了卡面沒有變化。
HIDEABLE_TEXT_FIELDS = (
    "Display_Name", "Photo", "Name_Memo", "Appearance", "Birthday", "Hometown",
    "Met_Date", "Met_Where", "Met_Via", "Introducer_Note_ID", "First_Impression", "Notes",
)


def hidden_tag(field: str) -> str:
    """Return the tag that hides a text field.

    回傳隱藏某個文字欄位的標籤。

    Args:
        field: the text field name
            文字欄位名稱

    Returns:
        the tag
        標籤
    """
    return HIDDEN_TAG_PREFIX + field


def hidden_text_fields(tags: list[str]) -> frozenset[str]:
    """Return the text fields hidden by the tags.

    回傳標籤裡隱藏的文字欄位。

    Args:
        tags: the note's tags
            筆記的標籤

    Returns:
        the names of the hidden text fields
        隱藏的文字欄位名稱
    """
    return frozenset(tag[len(HIDDEN_TAG_PREFIX):] for tag in tags if tag.startswith(HIDDEN_TAG_PREFIX))


def position_period(item: object) -> str:
    """Return the period of a position as text.

    回傳職位的任職期間文字。

    For example 「2021-04～」, 「2019～2021」, or 「～？」. A position struck out
    without an end date uses the month it was struck out as the end.

    例如「2021-04～」「2019～2021」「～？」；劃掉卻沒有離職年月時，用劃掉的月份當離職。

    Args:
        item: the position item
            職位項目

    Returns:
        the period text; empty when there is nothing, or when ``item`` is not a dict
        任職期間文字；什麼都沒有、或 ``item`` 不是 dict 時為空
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


#: The text shown for a job without company and title (the same as the card's ``entryText``).
#: 沒有公司與職稱的工作經歷顯示的文字（卡面 ``entryText`` 相同）。
UNNAMED_POSITION = "公司不明"


def entry_text(field: str, item: object) -> str:
    """Return the text of an entry.

    回傳一筆的文字。

    Args:
        field: the history field name
            會變的欄位名稱
        item: the entry
            一筆

    Returns:
        「company・title」 for a position (:data:`UNNAMED_POSITION` when it has neither), the stripped content
        otherwise; ``str(item)`` when ``item`` is not a dict
        職位是「公司・職稱」（都沒有時是 ``UNNAMED_POSITION``），其他是去掉前後空白的內容；
        ``item`` 不是 dict 時為 ``str(item)``
    """
    if not isinstance(item, dict):
        return str(item)
    if field == "Company":
        return _position_name(item) or UNNAMED_POSITION
    return str(item.get("value") or "").strip()


def _position_name(item: dict[str, object]) -> str:
    """Return 「company・title」 of a position.

    回傳職位的「公司・職稱」。

    Args:
        item: the position item
            職位項目

    Returns:
        the joined text; an empty string when it has neither
        串起的文字；都沒有時是空字串
    """
    parts = (str(item.get(key) or "").strip() for key in ("company", "title"))
    return "・".join(part for part in parts if part)


def entry_key(field: str, item: object) -> str:
    """Return the key used to compare and deduplicate entries.

    回傳比對、去重用的鍵。

    It is usually just :func:`entry_text`. A job without company and title uses
    its note and start instead (§17), otherwise two different 「公司不明」
    jobs would be treated as the same one. The cost is that such a current job
    cannot be "added again as the same job with a note filled in": a different
    note means another job, so use edit to change the note.

    通常就是 ``entry_text``。沒有公司與職稱的工作經歷改看說明與到職（§17），不然兩份
    不同的「公司不明」會被當成同一份。代價是這種現職沒辦法「同一份再新增、補上說明」：說明不同就是另一份，
    要改說明請用修改。

    Args:
        field: the history field name
            會變的欄位名稱
        item: the entry
            一筆

    Returns:
        the comparison key
        比對用的鍵
    """
    if field == "Company" and isinstance(item, dict) and not _position_name(item):
        return "\x1f".join(("", str(item.get("note") or "").strip(), str(item.get("start") or "").strip()))
    return entry_text(field, item)


def history_period(field: str, item: object) -> str:
    """Return the event time of an entry as text (:data:`HISTORY_PERIOD_KEYS`).

    回傳一筆的事件時間文字（``HISTORY_PERIOD_KEYS``）。

    For example 「2021-04～」 for a position, 「2011～2015」 for an education,
    「2024-04～2029-04」 for a credential (「～2028-03」 with only an expiry),
    「2019」 for experiences and praise material, and 「2015 起」 for interests
    and residence.

    職位「2021-04～」、學歷「2011～2015」、身分與證照「2024-04～2029-04」（只有到期時「～2028-03」）、
    經歷與讚美素材「2019」、興趣與現居地「2015 起」。

    Args:
        field: the history field name
            會變的欄位名稱
        item: the entry
            一筆

    Returns:
        the event time text; empty when there is none, when the field has no event time, or when ``item`` is not a
        dict
        事件時間文字；沒有時、欄位沒有事件時間、或 ``item`` 不是 dict 時為空
    """
    if not isinstance(item, dict) or field not in HISTORY_PERIOD_KEYS:
        return ""
    if field == "Company":
        return position_period(item)
    values = [str(item.get(key) or "").strip() for key in HISTORY_PERIOD_KEYS[field]]
    if len(values) == 2:
        return f"{values[0]}～{values[1]}" if any(values) else ""
    if not values[0]:
        return ""
    return f"{values[0]} 起" if HISTORY_PERIOD_KEYS[field][0] == "since" else values[0]


def entry_detail(field: str, item: object) -> str:
    """Return the full text of an entry for people (or an LLM) to read.

    回傳一筆給人（或 LLM）看的完整文字。

    It includes the event time (:func:`history_period`); fields with a note
    (:data:`NOTE_FIELDS`) also include the note, with multiple lines joined into
    one (「QBC・QA（2025～）：前端開發／帶新人」, 「爬過玉山（2026-10）」).
    Comparing and deduplicating within a list uses :func:`entry_key`.

    附事件時間（``history_period``），有說明的欄位（``NOTE_FIELDS``）
    另外附說明，多行的接成一行（「QBC・QA（2025～）：前端開發／帶新人」「爬過玉山（2026-10）」）。清單裡的
    比對、去重用 ``entry_key``。

    Args:
        field: the history field name
            會變的欄位名稱
        item: the entry
            一筆

    Returns:
        the full text, on one line
        一行的完整文字
    """
    text = entry_text(field, item)
    if not text or not isinstance(item, dict):
        return text
    period = history_period(field, item)
    note = one_line(str(item.get("note") or "")) if field in NOTE_FIELDS else ""
    return text + (f"（{period}）" if period else "") + (f"：{note}" if note else "")


def one_line(text: str) -> str:
    """Join a multi-line note into one line, separated by 「／」.

    多行的說明接成一行（「／」分開）。

    Used where the text is shown on a single line; blank lines are dropped.

    給一行顯示的地方用；空白行略過。

    Args:
        text: the multi-line text
            多行文字

    Returns:
        the single-line text
        一行的文字
    """
    return "／".join(line.strip() for line in text.splitlines() if line.strip())


def merged_note(existing: str, new: str) -> str | None:
    """Return the note after adding to the same entry again (§18).

    回傳對同一筆再新增時的說明（§18）。

    "Contains" is a substring check: when the existing note is 「負責 PMO」, a
    new 「PM」 already counts as present. The new content is usually a single
    sentence and a false match only costs one missing line (which can be edited
    later), so no finer comparison is made.

    「包含」是子字串比對：原本「負責 PMO」時新的「PM」也算已經有；
    新的內容通常是一句話，誤判的代價只是少接一行（之後可以修改），所以不做更細的比對。

    Args:
        existing: the existing note
            原本的說明
        new: the new note
            新的說明

    Returns:
        the new note when the existing one is empty; the existing note with the new one appended on a new line
        when the new one is not contained in it; ``None`` when there is nothing new (the new one is empty or
        already contained)
        原本是空的就用新的；新的不在原本的裡面就換行接在後面；沒有新東西（新的是空的、或原本已經包含）時為 ``None``
    """
    existing, new = existing.strip(), new.strip()
    if not new or new in existing:
        return None
    return f"{existing}\n{new}" if existing else new


def active_texts(field: str, items: list[object]) -> list[str]:
    """Return the texts of the currently valid entries.

    回傳目前有效的每一筆的文字。

    Args:
        field: the history field name
            會變的欄位名稱
        items: the entries
            各筆

    Returns:
        the texts of the valid entries, leaving out empty ones
        有效各筆的文字（空的不列）
    """
    return [text for item in items if is_active(item) and (text := entry_text(field, item))]


def history_from_text(field: str, text: str, added: str, *, title: str = "") -> list[dict[str, str]]:
    """Turn typed text (from the create form or old data) into entries.

    文字輸入（建卡表單、舊資料）轉成清單。

    Splittable fields are split into several entries on commas, ideographic
    commas, and line breaks; other fields become one entry. Duplicates within
    the same input keep only one entry.

    可切的欄位以逗號、頓號、換行切成多筆，其他整段一筆。同一次輸入裡重複的只留一筆。

    Args:
        field: the history field
            會變的欄位
        text: plain text (not HTML)
            純文字（不是 HTML）
        added: the added date (today when creating a card; an empty string, meaning unknown, when converting old data)
            登錄日（建卡時是今天；舊資料轉換時空字串＝日期不明）
        title: the job title (used only by ``Company``)
            職位的職稱（只有 ``Company`` 用）

    Returns:
        the entries (empty when there is no content)
        項目（沒有內容時為空）
    """
    text = text.strip()
    if field == "Company":
        title = title.strip()
        return [{"company": text, "title": title, "added": added, "ended": ""}] if text or title else []
    values = [part.strip() for part in _SPLIT.split(text)] if PERSON_HISTORY_FIELDS[field].split else [text]
    # 同一次輸入裡重複的（「登山、登山」）只留一筆
    return [{"value": value, "added": added, "ended": ""} for value in dict.fromkeys(values) if value]


def history_from_input(field: str, value: object, added: str) -> list[dict[str, str]]:
    """Turn a create parameter's value into entries.

    建立參數的值轉成清單。

    A block of text is split by :func:`history_from_text`; a list gives one
    entry per row (commas and ideographic commas within a row of a splittable
    field are still split). A position row is ``{"company", "title"}``;
    duplicates keep only one entry, and empty rows are skipped.

    一段文字照 ``history_from_text`` 切；清單每列一筆（可切的欄位一列裡的
    逗號、頓號照樣切開）。職位一列是 ``{"company", "title"}``；重複的只留一筆，空的列略過。

    Args:
        field: the history field
            會變的欄位
        value: a block of text, or a list with one value per row (a position row is a dict or a
            :class:`PositionInput`)
            一段文字，或每列一個值的清單（職位的列是 dict 或 ``PositionInput``）
        added: the added date
            登錄日

    Returns:
        the entries
        項目
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
    """Convert an Anki field's HTML into multi-line plain text.

    Anki 欄位的 HTML 轉成多行純文字。

    ``<br>`` and the ends of paragraphs become line breaks, other tags are
    removed, and blank lines are dropped.

    ``<br>``、段落結尾換行，其他標籤去掉，空白行略過。

    Args:
        value: the field HTML
            欄位的 HTML

    Returns:
        the plain text
        純文字
    """
    text = html.unescape(_TAG.sub("", _BREAK.sub("\n", value or ""))).replace("\xa0", " ")
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


@dataclass
class LegacyConversion:
    """The legacy conversion result of one note.

    一張筆記的舊資料轉換結果。

    Attributes:
        changes: field mapped to its new value; empty when nothing needs converting
            欄位 → 新的值；不用轉時為空
        dropped: the content that did not fit the format and was not converted, as (field, text), for example a
            closeness written as 「很熟」
            不合格式、沒有轉進去的內容 (欄位, 文字)，例如親密度寫「很熟」
        skipped: the fields that look like broken JSON and were not converted (wrapping them as text would
            overwrite data that can still be fixed by hand)
            看起來是壞掉的 JSON、沒有轉換的欄位（包成文字會蓋掉還能手動修的資料）
        leftover_title: the text still in ``Title`` when ``Company`` is already a list (it is no longer shown and
            must be merged into a position by hand)
            ``Company`` 已經是清單、``Title`` 卻還有字（不再顯示，要手動併進職位）
    """

    changes: dict[str, str]
    dropped: list[tuple[str, str]]
    skipped: list[str]
    leftover_title: str = ""


def legacy_to_history(fields: dict[str, str], is_list: Callable[[str], bool]) -> LegacyConversion:
    """Convert one note's legacy data (history fields still stored as text) into lists.

    舊資料（會變的欄位還是文字）轉成清單。

    Used by the one-off conversion script; registration-date plan D2: the
    conversion is not guaranteed to be lossless. Splitting is the same as in
    the web create form (:func:`history_from_text`); the added date is left
    empty (unknown) and every entry is valid; content that does not fit the
    field model is not converted (it is listed in ``dropped``). Company and
    title are combined into one position and ``Title`` is cleared. Fields that
    are already lists are left alone, so the conversion can be run repeatedly;
    a value starting with ``[`` or ``{`` that cannot be parsed is treated as
    broken JSON and not converted (it is listed in ``skipped``).

    一次性轉換腳本用，登錄日計畫書 D2：不保證無損。
    切法同網頁建卡表單（``history_from_text``）；登錄日留空（日期不明）、全部有效；不合欄位模型的
    內容不轉（列在 ``dropped``）。公司與職稱合成一筆職位，``Title`` 清空。已經是清單的欄位不動，所以可以
    重複執行；以 ``[``、``{`` 開頭卻解析不了的視為壞掉的 JSON，不轉（列在 ``skipped``）。

    Args:
        fields: the raw field values (HTML)
            欄位原始值（HTML）
        is_list: a function that tells whether a raw field value is already a JSON list
            判斷欄位原始值是否已經是 JSON 清單的函式

    Returns:
        the conversion result
        轉換結果
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
