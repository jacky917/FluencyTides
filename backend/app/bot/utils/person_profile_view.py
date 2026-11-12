"""
人脈卡在 TG 上的顯示與輸入規則（純函式，與 aiogram 無關，方便測試）。

Display and input rules of the person profile card on Telegram: pure
functions, independent of aiogram.

- 搜尋：用名字找人的 Anki 查詢。
- 顯示：摘要（見面前必看）、全部資料、清單的每一筆。會變的欄位（興趣、職位等）摘要與全部資料只顯示
  有效的，清單裡列出全部（附登錄日，劃掉的附失效日）。
- 輸入：新增一筆時各清單要問的步驟、日期的寫法、文字欄位轉成 Anki 的 HTML。

計畫書 ``docs/archive/person_profile_card_FEAT_2026-10-23.md`` §3.13、
``docs/wip/person_field_history_FEAT_2026-11-07.md`` §4.5。
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.bot.utils.formatting import anki_field_to_tg_text, escape_tg
from app.core.exceptions import AnkiFieldCorruptedError
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager
from app.infrastructure.anki.search import escape_search_value, note_type_term
from app.schemas.llm.person_profile import LLMPersonTopicsResult
from app.schemas.person_profile import (
    PERSON_HISTORY_FIELDS,
    PERSON_JSON_ITEMS,
    PERSON_NOTE_TYPE,
    RETIRED_FIELDS,
    PersonCreateParams,
    active_texts,
    entry_text,
    is_active,
)

#: 用名字找人時比對的欄位。Fields a name search looks at.
NAME_FIELDS = ("Display_Name", "Name_ZH", "Name_EN", "Name_JA", "Name_JA_Kana", "Name_Romaji")

#: 一次最多列幾個搜尋結果。Most search results listed as buttons.
MAX_SEARCH_RESULTS = 10

#: callback data 裡的清單代號（callback data 上限 64 bytes），順序同「清單」選單。
#: 前六個是原本的 JSON 清單，其餘是會變的欄位。Short list codes for callback data, in menu order.
LIST_CODES = {
    "F": "Followups_JSON",
    "I": "Interactions_JSON",
    "M": "Family_JSON",
    "G": "Gifts_JSON",
    "D": "Key_Dates_JSON",
    "C": "Contacts_JSON",
    "IN": "Interests",
    "FD": "Food_Drink",
    "DT": "Dietary",
    "CO": "Company",
    "WK": "Work_Detail",
    "ED": "Education",
    "PV": "Personality_Values",
    "PR": "Praise",
    "AA": "Ask_Advice",
    "CP": "Comm_Prefs",
    "RS": "Residence",
    "LG": "Languages",
    "TZ": "Timezone",
    "RL": "Relationship",
    "CL": "Closeness",
    "SN": "Sensitive",
}
CODE_OF_LIST = {name: code for code, name in LIST_CODES.items()}

#: 卡片背面區塊的「+」（深連結 ``psec``）對應的欄位：多個時 TG 先問新增哪一項（登錄日計畫書 §4.4）。
#: 鍵要與模板 ``addLink({ section: ... })`` 的代號相同。清單之外也可以放文字欄位（``EDITABLE_FIELDS``），
#: 按鈕會進「改資料」的那個欄位。
#: 只對應一個欄位的區塊（家庭、互動等）直接用 ``padd``。The fields behind each section's "+".
SECTION_FIELDS: dict[str, tuple[str, ...]] = {
    "must": ("Followups_JSON", "Dietary"),
    "interests": ("Interests", "Food_Drink"),
    "work": ("Company", "Work_Detail", "Education"),
    "personality": ("Personality_Values", "Praise", "Ask_Advice"),
    "contacts": ("Contacts_JSON", "Comm_Prefs"),
    # 生日是文字欄位：沒填時卡片上沒有那一列可以長按，從這裡新增（走改資料）
    "dates": ("Birthday", "Key_Dates_JSON"),
    "context": ("Residence", "Languages", "Timezone", "Relationship", "Closeness"),
}

#: 一般欄位（能經 TG「改資料」改的）：會變的欄位走清單操作，``Title`` 已不使用；順序同建立參數，
#: callback data 以序號指欄位——增刪或調整順序會讓聊天紀錄裡舊的「改資料」按鈕指到別的欄位
#: （編輯畫面會先顯示欄位名稱，看得出來）。Editable text fields, by index.
EDITABLE_FIELDS: tuple[str, ...] = tuple(
    name for name in PersonCreateParams.model_fields if name not in PERSON_HISTORY_FIELDS and name not in RETIRED_FIELDS
)

#: 改資料的分組（只有文字欄位）。Field groups of the edit menu (text fields only).
FIELD_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("姓名", ("Display_Name", "Name_ZH", "Name_EN", "Name_JA", "Name_JA_Kana", "Name_Romaji", "Honorific", "Name_Memo")),
    ("外貌與基本資料", ("Appearance", "Birthday", "Hometown")),
    ("認識", ("Met_Date", "Met_Where", "Met_Via", "Introducer_Note_ID", "First_Impression")),
    ("聯絡與其他", ("Notes", "Last_Contact", "Contact_Cadence")),
)

#: 「全部資料」的分組：文字欄位與會變的欄位（只列有效的）。Groups of the full view.
DISPLAY_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    FIELD_GROUPS[0],
    ("外貌與基本資料", ("Appearance", "Birthday", "Hometown", "Residence", "Languages", "Timezone")),
    ("認識", ("Met_Date", "Met_Where", "Met_Via", "Introducer_Note_ID", "Relationship", "Closeness", "First_Impression")),
    ("工作", ("Company", "Work_Detail", "Education")),
    ("興趣與飲食", ("Interests", "Food_Drink", "Dietary")),
    ("個性與價值觀", ("Personality_Values", "Praise", "Ask_Advice", "Sensitive")),
    ("聯絡與其他", ("Comm_Prefs", "Notes", "Last_Contact", "Contact_Cadence")),
)

FOLLOWUP_TYPES = {"ask": "下次要問", "my_promise": "我答應的", "their_promise": "對方答應的"}

#: TG 訊息長度上限（留一點餘裕）。Telegram's message limit, with margin.
MESSAGE_LIMIT = 3800


def field_title(name: str) -> str:
    """欄位的中文名稱：會變的欄位取它的標題（``Company`` 是「職位」），其他取自建立參數。

    The display title of a field.
    """
    if name in PERSON_HISTORY_FIELDS:
        return PERSON_HISTORY_FIELDS[name].title
    info = PersonCreateParams.model_fields.get(name)
    return info.title if info and info.title else name


def list_title(name: str) -> str:
    """清單（JSON 清單或會變的欄位）的中文名稱。The title of a list."""
    return PERSON_JSON_ITEMS[name][1] if name in PERSON_JSON_ITEMS else field_title(name)


def is_history(name: str) -> bool:
    """會變的欄位（每筆附登錄日與失效日）。Whether a list is a history field."""
    return name in PERSON_HISTORY_FIELDS


#: 清單格式壞了時的說明（不要顯示成「還沒有」）。Shown for a broken list.
BROKEN_NOTE = "⚠️ 資料格式壞了（可能是還沒轉換的舊文字），請在網頁或 Anki 修正"


def broken_lists(fields: dict[str, str]) -> frozenset[str]:
    """格式壞掉、解析不了的清單（六種 JSON 清單與會變的欄位）。The lists that fail to parse.

    TG 讀取時把它們當成空清單（摘要等照常顯示），顯示的地方用這個標出來，免得看起來像沒有資料
    ——例如還沒跑轉換腳本的舊文字，飲食限制不能就這樣從「見面前必看」消失。
    """
    broken = set()
    for name in (*PERSON_JSON_ITEMS, *PERSON_HISTORY_FIELDS):
        try:
            AnkiJsonFieldManager.parse_field_string(fields.get(name, ""))
        except AnkiFieldCorruptedError:
            broken.add(name)
    return frozenset(broken)


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
    return f"<b>{display}</b>" + (f"（{'・'.join(others)}）" if others else "")


def summary_text(fields: dict[str, str], lists: dict[str, list[object]], today: date) -> str:
    """摘要：姓名、工作與關係、見面前必看（同卡片背面）。The summary (the card's must-see part).

    Args:
        fields: 筆記的欄位值。The note's field values.
        lists: 解析好的清單（解析失敗的欄位是空清單，另以 :func:`broken_lists` 標出）。Parsed lists.
        today: 今天。Today.

    Returns:
        TG HTML 訊息。The Telegram HTML message.
    """
    lines = [name_line(fields)]
    meta = [x for x in (_active(lists, "Company", "／", 80), _active(lists, "Relationship", "・", 20)) if x]
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
            must.append(f"<b>{label}</b>：" + "；".join(escape_tg(str(f.get("item", "")), 80) for f in open_items))
    dietary = _active(lists, "Dietary", "；", 160)
    if "Dietary" in broken_lists(fields):
        must.append(f"<b>飲食</b>：{BROKEN_NOTE}")
    elif dietary:
        must.append(f"<b>飲食</b>：{dietary}")
    interactions = [i for i in lists.get("Interactions_JSON", []) if isinstance(i, dict)]
    if interactions:
        latest = max(interactions, key=lambda i: str(i.get("date", "")))
        text = "｜".join(filter(None, [str(latest.get("topics", "")), str(latest.get("updates", ""))]))
        must.append(f"<b>上次</b>（{escape_tg(str(latest.get('date', '')), 10)}）：{escape_tg(text, 160)}")
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
        must.append("<b>近期</b>：" + "；".join(text for _, text in sorted(soon)))
    lines.append("")
    lines.extend(must or ["還沒有要注意的事"])
    return "\n".join(lines)


def _active(lists: dict[str, list[object]], name: str, separator: str, limit: int) -> str:
    """會變的欄位目前有效的內容（已轉義）。The current entries of a history field, escaped."""
    return escape_tg(separator.join(active_texts(name, lists.get(name, []))), limit)


def full_text(fields: dict[str, str], lists: dict[str, list[object]]) -> str:
    """全部資料：所有有值的一般欄位、會變的欄位（有效的）與各清單的筆數。Every non-empty field and list counts."""
    lines = [name_line(fields), ""]
    broken = broken_lists(fields)
    for group, names in DISPLAY_GROUPS:
        values = {
            name: (BROKEN_NOTE if name in broken else _active(lists, name, "、", 300))
            if is_history(name) else _plain(fields, name, 300)
            for name in names
        }
        rows = [f"• {field_title(name)}：{value}" for name, value in values.items() if value]
        if rows:
            lines.append(f"<b>{group}</b>")
            lines.extend(rows)
    counts = [f"{PERSON_JSON_ITEMS[name][1]} {len(lists.get(name, []))}" for name in PERSON_JSON_ITEMS]
    lines.append("")
    lines.append("｜".join(counts))
    return truncate("\n".join(lines))


def item_line(field_name: str, item: object) -> str:
    """清單裡一筆的一行文字（已轉義）；會變的欄位附登錄日，劃掉的加刪除線與失效日。One item as an escaped line."""
    if not isinstance(item, dict):
        return escape_tg(str(item), 80)
    if is_history(field_name):
        text = escape_tg(entry_text(field_name, item), 200)
        added = str(item.get("added") or "")
        dates = f"{escape_tg(added, 10)} 登錄" if added else "日期不明"
        if not is_active(item):
            return f"<s>{text}</s>（{dates}，{escape_tg(str(item.get('ended')), 10)} 劃掉）"
        return f"{text}（{dates}）"
    get = lambda key: escape_tg(str(item.get(key, "") or ""), 120)  # noqa: E731
    if field_name == "Interactions_JSON":
        return f"{get('date')}：{get('topics') or get('place')}" + (f"｜{get('updates')}" if item.get("updates") else "")
    if field_name == "Followups_JSON":
        label = "已完成" if item.get("status") == "done" else FOLLOWUP_TYPES.get(str(item.get("type") or "ask"), "")
        return f"[{label}] {get('item')}" + (f"（期限 {get('due')}）" if item.get("due") else "")
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
        Step("date", "哪一天？（YYYY-MM-DD 或 MM-DD）", kind="date", required=True, default_today=True),
        Step("topics", "聊了什麼？", required=True),
        Step("updates", "對方的近況？"),
        Step("place", "在哪裡？"),
        Step("quote", "對方說的一句話？"),
        Step("_followups", "下次要問的事？（每行一件）", kind="lines"),
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
             choices=(("given", "送出"), ("received", "收到"))),
        Step("item", "什麼禮物？", required=True),
        Step("date", "哪一天？", kind="date", default_today=True),
        Step("reaction", "對方的反應？"),
    ),
    "Key_Dates_JSON": (
        Step("label", "什麼日子？（例如「N1 考試」）", required=True),
        Step("date", "日期？（YYYY-MM-DD）", kind="date", required=True),
        Step("yearly", "每年都有嗎？", kind="choice", required=True, choices=(("1", "每年"), ("0", "只有一次"))),
    ),
    "Contacts_JSON": (
        Step("channel", "哪種聯絡方式？", required=True,
             choices=(("LINE", "LINE"), ("Email", "Email"), ("電話", "電話"), ("Instagram", "Instagram"))),
        Step("handle", "帳號或號碼？", required=True),
        Step("preferred", "是偏好的聯絡方式嗎？", kind="choice", required=True, choices=(("1", "是"), ("0", "否"))),
    ),
}


def _history_steps(name: str) -> tuple[Step, ...]:
    """會變的欄位新增時要問的：只問內容（日期由系統填）。The steps of a history field."""
    title = field_title(name)
    if name == "Company":
        return (Step("company", "公司？（沒有就略過）"), Step("title", "職稱？（沒有就略過）"))
    if name == "Closeness":
        return (Step("value", "親密度？（1 最生疏、5 最親近）", kind="choice", required=True,
                     choices=tuple((str(n), str(n)) for n in range(1, 6))),)
    if name == "Relationship":
        return (Step("value", "關係？（可以點建議，或直接輸入）", required=True,
                     choices=tuple((x, x) for x in ("客戶", "朋友", "同事", "前同事", "同學", "語言交換"))),)
    if name == "Timezone":
        return (Step("value", "時區？（例如 Asia/Tokyo）", required=True,
                     choices=(("Asia/Tokyo", "Asia/Tokyo"), ("Asia/Taipei", "Asia/Taipei"))),)
    hint = "（多個用頓號或逗號分開，會存成多筆）" if PERSON_HISTORY_FIELDS[name].split else ""
    return (Step("value", f"{title}？{hint}", required=True),)


ADD_STEPS.update({name: _history_steps(name) for name in PERSON_HISTORY_FIELDS})


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
    title = list_title(field_name)
    if is_history(field_name):
        lines = [f"<b>新增{title}</b>", escape_tg(entry_text(field_name, item), 300), "<i>登錄日：今天</i>"]
        if PERSON_HISTORY_FIELDS[field_name].single:
            lines.append("<i>原本有效的那筆會自動劃掉（失效日＝今天）。</i>")
        return "\n".join(lines)
    lines = [f"<b>新增{title}</b>", item_line(field_name, {k: v for k, v in item.items() if not k.startswith("_")})]
    extra = item.get("_followups")
    if isinstance(extra, list) and extra:
        lines.append("下次要問：" + "；".join(escape_tg(str(x), 60) for x in extra))
    if field_name == "Interactions_JSON":
        for key, label in (("updates", "近況"), ("place", "地點"), ("quote", "原話")):
            if item.get(key):
                lines.append(f"{label}：{escape_tg(str(item[key]), 200)}")
    return "\n".join(lines)


# =============================================================================
# 用一段話記錄（LLM 整理的建議）
# =============================================================================


def proposal_line(kind: str, data: dict[str, object]) -> str:
    """一項建議的文字（已轉義）。One proposal as escaped text."""
    get = lambda key, limit=200: escape_tg(str(data.get(key, "") or ""), limit)  # noqa: E731
    if kind == "interaction":
        lines = [f"<b>互動</b> {get('date')}：{get('topics')}"]
        for key, label in (("updates", "近況"), ("place", "地點"), ("quote", "原話")):
            if data.get(key):
                lines.append(f"    {label}：{get(key)}")
        return "\n".join(lines)
    if kind == "followup":
        label = FOLLOWUP_TYPES.get(str(data.get("type") or "ask"), "下次要問")
        return f"<b>{label}</b>：{get('item')}" + (f"（期限 {get('due')}）" if data.get("due") else "")
    reason = f"\n    <i>（{get('reason', 120)}）</i>" if data.get("reason") else ""
    if kind in ("history_add", "history_end"):
        name = str(data.get("field", ""))
        title = escape_tg(field_title(name))
        text = escape_tg(str(data.get("text", "") or ""), 300)
        if kind == "history_end":
            return f"<b>{title}</b> 劃掉：<s>{text}</s>" + reason
        single = PERSON_HISTORY_FIELDS.get(name)
        extra = "（原本的會劃掉）" if single and single.single else ""
        return f"<b>{title}</b> 新增：{text}{extra}" + reason
    mode = "接在後面" if data.get("mode") == "append" else "改成"
    return f"<b>{escape_tg(field_title(str(data.get('field', ''))))}</b> {mode}：{get('value', 300)}" + reason


#: 預覽與按鈕上的勾選記號（文字，不用 emoji）。Check marks used in the preview and buttons.
CHECKED, UNCHECKED, SAVED = "✓", "－", "已存"

#: 預覽的開頭與結尾提示（放不下、已儲存的說明）預留的字數。Room kept for the preview's header and notes.
_PREVIEW_RESERVE = 300


def _proposal_entry(index: int, proposal: dict[str, object], mark: str) -> str:
    data = proposal.get("data")
    return f"{mark} {index + 1}. {proposal_line(str(proposal.get('kind', '')), data if isinstance(data, dict) else {})}"


def fitting_count(proposals: list[dict[str, object]]) -> int:
    """一則訊息放得下前幾項建議（勾選記號的長度固定，切換勾選不影響）。How many proposals fit in one message."""
    used = _PREVIEW_RESERVE
    for index, proposal in enumerate(proposals):
        used += len(_proposal_entry(index, proposal, CHECKED)) + 1
        if used > MESSAGE_LIMIT:
            return index
    return len(proposals)


def ingest_preview(
    proposals: list[dict[str, object]], selected: list[bool], *, saved: frozenset[int] = frozenset(), overflow: int = 0
) -> str:
    """整理結果的預覽：每項前面標勾選狀態與編號。The proposals with their checkboxes.

    Args:
        proposals: 建議（呼叫端先用 :func:`fitting_count` 截到放得下的數量）。The proposals.
        selected: 每項是否勾選。Whether each is picked.
        saved: 已經寫入的項目（上次儲存中途失敗時）。Proposals already written.
        overflow: 放不下、沒有列出的項數。How many were left out.
    """
    lines = ["<b>整理好了</b>，勾選要記下的項目（點下面的編號切換）：", ""]
    for index, (proposal, chosen) in enumerate(zip(proposals, selected)):
        mark = SAVED if index in saved else (CHECKED if chosen else UNCHECKED)
        lines.append(_proposal_entry(index, proposal, mark))
    if saved:
        lines += ["", f"<i>標「{SAVED}」的 {len(saved)} 項已經存了；再按一次儲存只會補存其他勾選的項目。</i>"]
    if overflow:
        lines += ["", f"<i>還有 {overflow} 項放不下，沒有列出也不會寫入；需要的話請分次記錄。</i>"]
    return "\n".join(lines)


# =============================================================================
# AI 分析與話題
# =============================================================================


def topics_text(fields: dict[str, str], result: LLMPersonTopicsResult) -> str:
    """分析與話題的訊息（已轉義）。The insight and topics as a Telegram HTML message."""
    insight = result.insight
    lines = [f"<b>{_plain(fields, 'Display_Name', 60)} 是個怎樣的人</b>", escape_tg(insight.portrait, 600), ""]
    for label, text in (("在意的事", insight.values), ("相處方式", insight.communication),
                        ("最近", insight.current_state), ("留意", insight.cautions)):
        if text.strip():
            lines.append(f"<b>{label}</b>：{escape_tg(text, 500)}")
    lines += ["", "<b>可以聊的話題</b>"]
    for index, topic in enumerate(result.topics, 1):
        lines.append(f"{index}. <b>{escape_tg(topic.title, 80)}</b>")
        if topic.why.strip():
            lines.append(f"    <i>{escape_tg(topic.why, 200)}</i>")
        if topic.opener.strip():
            lines.append(f"    「{escape_tg(topic.opener, 300)}」")
    return truncate("\n".join(lines))
