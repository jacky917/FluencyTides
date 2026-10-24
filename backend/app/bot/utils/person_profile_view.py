"""
人脈卡在 TG 上的顯示與輸入規則（純函式，與 aiogram 無關，方便測試）。

Display and input rules of the person profile card on Telegram: pure
functions, independent of aiogram.

- 搜尋：用名字找人的 Anki 查詢。
- 顯示：摘要（見面前必看）、全部資料、清單的每一筆。
- 輸入：新增一筆時各清單要問的步驟、日期的寫法、文字欄位轉成 Anki 的 HTML。

計畫書 ``docs/wip/person_profile_card_FEAT_2026-10-23.md`` §3.13。
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.bot.utils.formatting import anki_field_to_tg_text, escape_tg
from app.infrastructure.anki.search import escape_search_value, note_type_term
from app.schemas.person_profile import PERSON_JSON_ITEMS, PERSON_NOTE_TYPE, PersonCreateParams

#: 用名字找人時比對的欄位。Fields a name search looks at.
NAME_FIELDS = ("Display_Name", "Name_ZH", "Name_EN", "Name_JA", "Name_JA_Kana", "Name_Romaji")

#: 一次最多列幾個搜尋結果。Most search results listed as buttons.
MAX_SEARCH_RESULTS = 10

#: callback data 裡的清單代號（callback data 上限 64 bytes）。Short list codes for callback data.
LIST_CODES = {
    "I": "Interactions_JSON",
    "F": "Followups_JSON",
    "M": "Family_JSON",
    "G": "Gifts_JSON",
    "D": "Key_Dates_JSON",
    "C": "Contacts_JSON",
}
CODE_OF_LIST = {name: code for code, name in LIST_CODES.items()}

#: 一般欄位（能經 TG 改的），順序同建立參數；callback data 以序號指欄位。Editable fields, by index.
EDITABLE_FIELDS: tuple[str, ...] = tuple(PersonCreateParams.model_fields)

#: 改資料的分組。Field groups of the edit menu.
FIELD_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("姓名", ("Display_Name", "Name_ZH", "Name_EN", "Name_JA", "Name_JA_Kana", "Name_Romaji", "Honorific", "Name_Memo")),
    ("外貌與基本資料", ("Appearance", "Birthday", "Hometown", "Residence", "Languages", "Timezone")),
    ("認識", ("Met_Date", "Met_Where", "Met_Via", "Introducer_Note_ID", "Relationship", "Closeness", "First_Impression")),
    ("工作", ("Company", "Title", "Work_Detail", "Education")),
    ("興趣與飲食", ("Interests", "Food_Drink", "Dietary")),
    ("個性與價值觀", ("Personality_Values", "Praise", "Ask_Advice", "Sensitive")),
    ("聯絡與其他", ("Comm_Prefs", "Notes", "Last_Contact", "Contact_Cadence")),
)

FOLLOWUP_TYPES = {"ask": "❓ 下次要問", "my_promise": "🤝 我答應的", "their_promise": "📌 對方答應的"}

#: TG 訊息長度上限（留一點餘裕）。Telegram's message limit, with margin.
MESSAGE_LIMIT = 3800


def field_title(name: str) -> str:
    """一般欄位的中文名稱（取自建立參數）。The display title of a plain field."""
    info = PersonCreateParams.model_fields.get(name)
    return info.title if info and info.title else name


def is_multiline(name: str) -> bool:
    """文字區段（多行）欄位：TG 上可以選「接在後面」。Whether a field is a multi-line text section."""
    info = PersonCreateParams.model_fields.get(name)
    extra = info.json_schema_extra if info else None
    return isinstance(extra, dict) and extra.get("x-ui") == "textarea"


# =============================================================================
# 搜尋
# =============================================================================


def search_query(text: str) -> str:
    """用名字找人的 Anki 查詢：在姓名相關欄位做「包含」比對。

    The Anki query finding people whose name fields contain the text.

    Args:
        text: 使用者輸入的名字（照字面比對，萬用字元會跳脫）。The name, matched literally.

    Returns:
        例如 ``note:"Person\\_Profile\\_Dark" ("Display_Name:*田中*" OR ...)``。The query.
    """
    value = escape_search_value(text.strip())
    terms = " OR ".join(f'"{name}:*{value}*"' for name in NAME_FIELDS)
    return f"{note_type_term(PERSON_NOTE_TYPE)} ({terms})"


def card_id_query(card_id: str) -> str:
    """以 ``Card_ID`` 找人的查詢。The query finding a person by Card_ID."""
    return f'{note_type_term(PERSON_NOTE_TYPE)} "Card_ID:{escape_search_value(card_id)}"'


# =============================================================================
# 顯示
# =============================================================================


def _plain(fields: dict[str, str], name: str, limit: int = 200) -> str:
    """欄位轉成 TG 純文字（去 HTML、已轉義）。A field as escaped Telegram text."""
    return anki_field_to_tg_text(fields.get(name, ""), limit)


def parse_date(text: str) -> tuple[int | None, int, int] | None:
    """``YYYY-MM-DD`` 或 ``--MM-DD`` → (年|None, 月, 日)；不對時為 None。Parse a stored date."""
    m = re.fullmatch(r"(\d{4}|-)-(\d{2})-(\d{2})", (text or "").strip())
    if not m:
        return None
    year = None if m.group(1) == "-" else int(m.group(1))
    month, day = int(m.group(2)), int(m.group(3))
    try:
        date(year or 2000, month, day)
    except ValueError:
        return None
    return year, month, day


def days_until(month: int, day: int, today: date) -> int:
    """下一次這個月日還有幾天（今天為 0；2/29 在平年視為 3/1）。Days until the next occurrence."""

    def occurrence(year: int) -> date:
        try:
            return date(year, month, day)
        except ValueError:
            return date(year, 3, 1)

    next_day = occurrence(today.year)
    if next_day < today:
        next_day = occurrence(today.year + 1)
    return (next_day - today).days


def name_line(fields: dict[str, str]) -> str:
    """「田中さん（田中 悠希・たなか ゆうき）」。The name line."""
    display = _plain(fields, "Display_Name", 60)
    others = [_plain(fields, name, 60) for name in ("Name_JA", "Name_JA_Kana", "Name_ZH", "Name_EN")]
    others = [o for i, o in enumerate(others) if o and o != display and o not in others[:i]]
    return f"👤 <b>{display}</b>" + (f"（{'・'.join(others)}）" if others else "")


def summary_text(fields: dict[str, str], lists: dict[str, list[object]], today: date) -> str:
    """摘要：姓名、工作與關係、見面前必看（同卡片背面）。The summary (the card's must-see part).

    Args:
        fields: 筆記的欄位值。The note's field values.
        lists: 解析好的 JSON 清單（解析失敗的欄位不放）。Parsed JSON lists.
        today: 今天。Today.

    Returns:
        TG HTML 訊息。The Telegram HTML message.
    """
    lines = [name_line(fields)]
    meta = [x for x in ("・".join(filter(None, [_plain(fields, "Company", 40), _plain(fields, "Title", 40)])),
                        _plain(fields, "Relationship", 20)) if x]
    last = parse_date(fields.get("Last_Contact", ""))
    if last and last[0] is not None:
        days = (today - date(last[0], last[1], last[2])).days
        meta.append("今天聯絡過" if days <= 0 else f"上次聯絡 {days} 天前")
    if meta:
        lines.append("｜".join(meta))

    must: list[str] = []
    followups = [f for f in lists.get("Followups_JSON", []) if isinstance(f, dict)]
    for kind, label in FOLLOWUP_TYPES.items():
        open_items = [f for f in followups if (f.get("type") or "ask") == kind and f.get("status", "open") != "done"]
        if open_items:
            must.append(f"{label}：" + "；".join(escape_tg(str(f.get("item", "")), 80) for f in open_items))
    dietary = _plain(fields, "Dietary", 120)
    if dietary:
        must.append(f"⚠ 飲食：{dietary}")
    interactions = [i for i in lists.get("Interactions_JSON", []) if isinstance(i, dict)]
    if interactions:
        latest = max(interactions, key=lambda i: str(i.get("date", "")))
        text = "｜".join(filter(None, [str(latest.get("topics", "")), str(latest.get("updates", ""))]))
        must.append(f"💬 上次（{escape_tg(str(latest.get('date', '')), 10)}）：{escape_tg(text, 160)}")
    soon = []
    for item in lists.get("Key_Dates_JSON", []):
        if not isinstance(item, dict):
            continue
        parsed = parse_date(str(item.get("date", "")))
        if not parsed:
            continue
        year, month, day = parsed
        until = days_until(month, day, today) if item.get("yearly") or year is None else (
            date(year, month, day) - today).days
        if 0 <= until <= 60:
            soon.append((until, f"{escape_tg(str(item.get('label', '')), 40)}（{month:02d}/{day:02d}，"
                                f"{'就是今天' if until == 0 else f'還有 {until} 天'}）"))
    if soon:
        must.append("📅 近期：" + "；".join(text for _, text in sorted(soon)))
    lines.append("")
    lines.extend(must or ["⭐ 還沒有要注意的事"])
    return "\n".join(lines)


def full_text(fields: dict[str, str], lists: dict[str, list[object]]) -> str:
    """全部資料：所有有值的一般欄位與各清單的筆數。Every non-empty field and list counts."""
    lines = [name_line(fields), ""]
    for group, names in FIELD_GROUPS:
        rows = [f"• {field_title(name)}：{_plain(fields, name, 300)}" for name in names if _plain(fields, name, 300)]
        if rows:
            lines.append(f"<b>{group}</b>")
            lines.extend(rows)
    counts = [f"{PERSON_JSON_ITEMS[name][1]} {len(lists.get(name, []))}" for name in PERSON_JSON_ITEMS]
    lines.append("")
    lines.append("📋 " + "｜".join(counts))
    return truncate("\n".join(lines))


def item_line(field_name: str, item: object) -> str:
    """清單裡一筆的一行文字（已轉義）。One item as an escaped line."""
    if not isinstance(item, dict):
        return escape_tg(str(item), 80)
    get = lambda key: escape_tg(str(item.get(key, "") or ""), 120)  # noqa: E731
    if field_name == "Interactions_JSON":
        return f"{get('date')}：{get('topics') or get('place')}" + (f"｜{get('updates')}" if item.get("updates") else "")
    if field_name == "Followups_JSON":
        mark = "✅" if item.get("status") == "done" else FOLLOWUP_TYPES.get(str(item.get("type") or "ask"), "❓")[:1]
        return f"{mark} {get('item')}" + (f"（期限 {get('due')}）" if item.get("due") else "")
    if field_name == "Family_JSON":
        return f"{get('relation')}：{get('name')}" + (f"（{item.get('birth_year')} 年生）" if item.get("birth_year") else "")
    if field_name == "Gifts_JSON":
        direction = "收到" if item.get("direction") == "received" else "送出"
        return f"{direction} {get('item')}" + (f"（{get('date')}）" if item.get("date") else "")
    if field_name == "Key_Dates_JSON":
        return f"{get('label')}：{get('date')}" + ("（每年）" if item.get("yearly") else "")
    if field_name == "Contacts_JSON":
        return f"{get('channel')}：{get('handle')}" + ("（偏好）" if item.get("preferred") else "")
    return escape_tg(str(item), 80)


def truncate(text: str, limit: int = MESSAGE_LIMIT) -> str:
    """超過 TG 上限時截斷（在換行處），加上「…」。Truncate at a line break when too long."""
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit)
    return text[: cut if cut > 0 else limit] + "\n…"


# =============================================================================
# 輸入
# =============================================================================


@dataclass(frozen=True)
class Step:
    """新增一筆時的一個問題。One question when adding an item.

    Attributes:
        key: 寫進項目的鍵；``_followups`` 為互動紀錄附帶的「下次要問」（每行一筆）。The item key.
        prompt: 問題。The prompt.
        kind: ``text``、``date``、``int``、``choice``、``lines``。The input kind.
        required: 必填（沒有「略過」）。Whether it can be skipped.
        choices: ``choice`` 的選項 (值, 按鈕文字)；``text`` 有選項時是建議按鈕。Choices.
        default_today: 日期步驟提供「今天」「昨天」按鈕。Offer today / yesterday buttons.
    """

    key: str
    prompt: str
    kind: str = "text"
    required: bool = False
    choices: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    default_today: bool = False


ADD_STEPS: dict[str, tuple[Step, ...]] = {
    "Interactions_JSON": (
        Step("date", "📅 哪一天？（YYYY-MM-DD 或 MM-DD）", kind="date", required=True, default_today=True),
        Step("topics", "💬 聊了什麼？", required=True),
        Step("updates", "🗞 對方的近況？"),
        Step("place", "📍 在哪裡？"),
        Step("quote", "🗨 對方說的一句話？"),
        Step("_followups", "❓ 下次要問的事？（每行一件）", kind="lines"),
    ),
    "Followups_JSON": (
        Step("type", "要記哪一種？", kind="choice", required=True,
             choices=tuple((k, v) for k, v in FOLLOWUP_TYPES.items())),
        Step("item", "內容是？", required=True),
        Step("due", "⏰ 期限？（YYYY-MM-DD 或 MM-DD）", kind="date"),
    ),
    "Family_JSON": (
        Step("relation", "關係？", required=True,
             choices=(("妻子", "妻子"), ("丈夫", "丈夫"), ("兒子", "兒子"), ("女兒", "女兒"), ("貓", "貓"), ("狗", "狗"))),
        Step("name", "名字？"),
        Step("birth_year", "出生年？（例如 2021）", kind="int"),
        Step("note", "備註？"),
    ),
    "Gifts_JSON": (
        Step("direction", "送出還是收到？", kind="choice", required=True,
             choices=(("given", "🎁 送出"), ("received", "📦 收到"))),
        Step("item", "什麼禮物？", required=True),
        Step("date", "📅 哪一天？", kind="date", default_today=True),
        Step("reaction", "對方的反應？"),
    ),
    "Key_Dates_JSON": (
        Step("label", "什麼日子？（例如「N1 考試」）", required=True),
        Step("date", "📅 日期？（YYYY-MM-DD）", kind="date", required=True),
        Step("yearly", "每年都有嗎？", kind="choice", required=True, choices=(("1", "🔁 每年"), ("0", "1️⃣ 只有一次"))),
    ),
    "Contacts_JSON": (
        Step("channel", "哪種聯絡方式？", required=True,
             choices=(("LINE", "LINE"), ("Email", "Email"), ("電話", "電話"), ("Instagram", "Instagram"))),
        Step("handle", "帳號或號碼？", required=True),
        Step("preferred", "是偏好的聯絡方式嗎？", kind="choice", required=True, choices=(("1", "⭐ 是"), ("0", "否"))),
    ),
}


def parse_date_input(text: str, today: date) -> str | None:
    """使用者輸入的日期 → ``YYYY-MM-DD``：接受 ``2026-10-01``、``2026/10/1``、``10-01``、``10/1``（今年）。

    Parse a typed date; month-day alone means this year. None when invalid.
    """
    m = re.fullmatch(r"\s*(?:(\d{4})[-/.])?(\d{1,2})[-/.](\d{1,2})\s*", text or "")
    if not m:
        return None
    year = int(m.group(1)) if m.group(1) else today.year
    try:
        return date(year, int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


def today_choice(today: date, offset: int) -> str:
    """「今天」「昨天」按鈕的日期。The date behind the today / yesterday buttons."""
    return (today - timedelta(days=offset)).isoformat()


def coerce_answer(step: Step, value: str) -> object:
    """把一個回答轉成要存的值（選項 1/0 轉布林、出生年轉整數）。Turn an answer into the stored value.

    Raises:
        ValueError: 整數或日期格式不對。On a malformed number.
    """
    if step.key in ("yearly", "preferred"):
        return value == "1"
    if step.kind == "int":
        number = int(value.strip())
        if not 1900 <= number <= 2100:
            raise ValueError("年份要在 1900 到 2100 之間")
        return number
    return value.strip()


def text_to_field_html(text: str) -> str:
    """TG 輸入的文字 → Anki 欄位 HTML（轉義、換行變 ``<br>``）。Typed text as Anki field HTML."""
    return html.escape(text.strip()).replace("\n", "<br>")


def preview_item(field_name: str, item: dict[str, object]) -> str:
    """儲存前的預覽。The preview before saving."""
    title = PERSON_JSON_ITEMS[field_name][1]
    lines = [f"<b>新增{title}</b>", item_line(field_name, {k: v for k, v in item.items() if not k.startswith("_")})]
    extra = item.get("_followups")
    if isinstance(extra, list) and extra:
        lines.append("❓ 下次要問：" + "；".join(escape_tg(str(x), 60) for x in extra))
    if field_name == "Interactions_JSON":
        for key, label in (("updates", "🗞 近況"), ("place", "📍 地點"), ("quote", "🗨")):
            if item.get(key):
                lines.append(f"{label}：{escape_tg(str(item[key]), 200)}")
    return "\n".join(lines)
