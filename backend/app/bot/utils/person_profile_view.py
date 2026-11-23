"""Display and input rules of the person profile card on Telegram.

人脈卡在 TG 上的顯示與輸入規則。

Pure functions, independent of aiogram, so they are easy to test.

純函式，與 aiogram 無關，方便測試。

- Search: the Anki query that finds a person by name.
- Display: the summary (must-see before meeting), the full data, and each entry of a list. For fields that
  change over time (interests, positions and so on), the summary and full data show only the valid entries
  (for positions, the current job; the full data also lists past jobs), while the list shows all of them
  (with the date added; struck-out entries also show the date ended; positions show their tenure).
  An entry hidden on the card face is marked 「卡面隱藏」 in the list (Telegram still shows its content).
- Input: the steps each list asks when adding an entry, how dates are written, and converting text fields
  into Anki HTML.

- 搜尋：用名字找人的 Anki 查詢。
- 顯示：摘要（見面前必看）、全部資料、清單的每一筆。會變的欄位（興趣、職位等）摘要與全部資料只顯示
  有效的（職位是現職；全部資料另列以前的工作），清單裡列出全部（附登錄日，劃掉的附失效日；職位附任職期間）。
  在卡面隱藏的那一筆，清單裡標「卡面隱藏」（TG 照樣顯示內容）。
- 輸入：新增一筆時各清單要問的步驟、日期的寫法、文字欄位轉成 Anki 的 HTML。

Plans: ``docs/archive/person_profile_card_FEAT_2026-10-23.md`` §3.13,
``docs/wip/person_field_history_FEAT_2026-11-07.md`` §4.5.

計畫書 ``docs/archive/person_profile_card_FEAT_2026-10-23.md`` §3.13、
``docs/wip/person_field_history_FEAT_2026-11-07.md`` §4.5。
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

from app.bot.utils.formatting import anki_field_to_tg_text, escape_tg
from app.core.exceptions import AnkiFieldCorruptedError
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager
from app.infrastructure.anki.search import escape_search_value, note_type_term
from app.schemas.llm.person_profile import LLMPersonTopicsResult
from app.schemas.person_profile import (
    GOAL_KINDS,
    GOAL_STATUSES,
    EDUCATION_LABELS,
    HIDEABLE_TEXT_FIELDS,
    HISTORY_PERIOD_KEYS,
    ITEM_EDIT_KEYS,
    PERSON_HISTORY_FIELDS,
    PERSON_JSON_ITEMS,
    PERSON_NOTE_TYPE,
    NOTE_FIELDS,
    RETIRED_FIELDS,
    PersonCreateParams,
    active_texts,
    entry_detail,
    entry_text,
    history_from_text,
    history_period,
    is_active,
    is_hidden,
    is_open_goal,
    normalize_period,
    one_line,
    position_period,
)

#: Fields matched when searching for a person by name.
#: 用名字找人時比對的欄位。
NAME_FIELDS = ("Display_Name", "Name_ZH", "Name_EN", "Name_JA", "Name_JA_Kana", "Name_Romaji")

#: The most search results listed at once.
#: 一次最多列幾個搜尋結果。
MAX_SEARCH_RESULTS = 10

#: Short list codes used in callback data (callback data is limited to 64 bytes), in the order of the
#: "lists" menu. The first seven are JSON lists (goals were added later); the rest are history fields.
#: callback data 裡的清單代號（callback data 上限 64 bytes），順序同「清單」選單。
#: 前七個是 JSON 清單（目標是後來加的），其餘是會變的欄位。
LIST_CODES = {
    "F": "Followups_JSON",
    "I": "Interactions_JSON",
    "M": "Family_JSON",
    "G": "Gifts_JSON",
    "D": "Key_Dates_JSON",
    "C": "Contacts_JSON",
    "GL": "Goals_JSON",
    "IN": "Interests",
    "EX": "Experiences",
    "FD": "Food_Drink",
    "DL": "Dislikes",
    "DT": "Dietary",
    "CO": "Company",
    "ED": "Education",
    "PV": "Personality_Values",
    "PR": "Praise",
    "AA": "Ask_Advice",
    "ND": "Needs",
    "CG": "Common_Ground",
    "CP": "Comm_Prefs",
    "RS": "Residence",
    "LG": "Languages",
    "TZ": "Timezone",
    "RL": "Relationship",
    "CL": "Closeness",
    "SN": "Sensitive",
}
CODE_OF_LIST = {name: code for code, name in LIST_CODES.items()}

#: The fields behind the "+" of each section on the card back (deep link ``psec``): when there are several,
#: Telegram first asks which one to add (date-added plan §4.4). Keys must match the codes in the template's
#: ``addLink({ section: ... })``. Besides lists, text fields (``EDITABLE_FIELDS``) may be listed too; their
#: button opens that field in "edit data". Sections that map to a single field (family, interactions and so
#: on) use ``padd`` directly.
#: 卡片背面區塊的「+」（深連結 ``psec``）對應的欄位：多個時 TG 先問新增哪一項（登錄日計畫書 §4.4）。
#: 鍵要與模板 ``addLink({ section: ... })`` 的代號相同。清單之外也可以放文字欄位（``EDITABLE_FIELDS``），
#: 按鈕會進「改資料」的那個欄位。
#: 只對應一個欄位的區塊（家庭、互動等）直接用 ``padd``。
SECTION_FIELDS: dict[str, tuple[str, ...]] = {
    "must": ("Followups_JSON", "Dietary"),
    "interests": ("Interests", "Experiences", "Food_Drink", "Dislikes"),
    "work": ("Company", "Education"),
    "personality": ("Personality_Values", "Praise", "Ask_Advice", "Needs"),
    "contacts": ("Contacts_JSON", "Comm_Prefs"),
    # 生日是文字欄位：沒填時卡片上沒有那一列可以長按，從這裡新增（走改資料）
    "dates": ("Birthday", "Key_Dates_JSON"),
    "context": ("Residence", "Languages", "Timezone", "Relationship", "Closeness"),
}

#: Plain fields editable through Telegram's "edit data": history fields go through list operations and
#: ``Title`` is no longer used. The order follows the create parameters, and callback data refers to a field
#: by index, so adding, removing or reordering fields makes old "edit data" buttons in the chat history point
#: at another field (the edit screen shows the field name first, so this is noticeable).
#: 一般欄位（能經 TG「改資料」改的）：會變的欄位走清單操作，``Title`` 已不使用；順序同建立參數，
#: callback data 以序號指欄位——增刪或調整順序會讓聊天紀錄裡舊的「改資料」按鈕指到別的欄位
#: （編輯畫面會先顯示欄位名稱，看得出來）。
EDITABLE_FIELDS: tuple[str, ...] = tuple(
    name for name in PersonCreateParams.model_fields if name not in PERSON_HISTORY_FIELDS and name not in RETIRED_FIELDS
)

#: Field groups of the "edit data" menu (text fields only).
#: 改資料的分組（只有文字欄位）。
FIELD_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("姓名", ("Display_Name", "Name_ZH", "Name_EN", "Name_JA", "Name_JA_Kana", "Name_Romaji", "Honorific", "Name_Memo")),
    ("外貌與基本資料", ("Appearance", "Birthday", "Hometown")),
    ("認識", ("Met_Date", "Met_Where", "Met_Via", "Introducer_Note_ID", "First_Impression")),
    ("聯絡與其他", ("Notes", "Last_Contact", "Contact_Cadence")),
)

#: Groups of the "full data" view: text fields and history fields (only valid entries are listed).
#: 「全部資料」的分組：文字欄位與會變的欄位（只列有效的）。
DISPLAY_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    FIELD_GROUPS[0],
    ("外貌與基本資料", ("Appearance", "Birthday", "Hometown", "Residence", "Languages", "Timezone")),
    ("認識", ("Met_Date", "Met_Where", "Met_Via", "Introducer_Note_ID", "Relationship", "Closeness", "First_Impression")),
    ("工作", ("Company", "Education")),
    ("興趣、經歷與飲食", ("Interests", "Experiences", "Food_Drink", "Dislikes", "Dietary")),
    ("個性與價值觀", ("Personality_Values", "Praise", "Ask_Advice", "Needs", "Common_Ground", "Sensitive")),
    ("聯絡與其他", ("Comm_Prefs", "Notes", "Last_Contact", "Contact_Cadence")),
)

FOLLOWUP_TYPES = {"ask": "下次要問", "my_promise": "我答應的", "their_promise": "對方答應的"}

#: Telegram's message length limit, with some margin.
#: TG 訊息長度上限（留一點餘裕）。
MESSAGE_LIMIT = 3800


def field_title(name: str) -> str:
    """Return the Chinese display title of a field.

    回傳欄位的中文名稱。

    History fields use their own title (``Company`` is 「工作經歷」, the same as on the card face); other
    fields take the title from the create parameters.

    會變的欄位取它的標題（``Company`` 是「工作經歷」，和卡面相同），其他取自建立參數。

    Args:
        name: the Anki field name
            Anki 欄位名稱

    Returns:
        the title, or ``name`` itself when no title is defined
        欄位標題；沒有定義標題時為 ``name`` 本身
    """
    if name in PERSON_HISTORY_FIELDS:
        return PERSON_HISTORY_FIELDS[name].title
    info = PersonCreateParams.model_fields.get(name)
    return info.title if info and info.title else name


def list_title(name: str) -> str:
    """Return the Chinese title of a list (a JSON list or a history field).

    回傳清單（JSON 清單或會變的欄位）的中文名稱。

    Args:
        name: the Anki field name of the list
            清單的 Anki 欄位名稱

    Returns:
        the list title
        清單名稱
    """
    return PERSON_JSON_ITEMS[name][1] if name in PERSON_JSON_ITEMS else field_title(name)


def is_history(name: str) -> bool:
    """Return whether a list is a history field (each entry has a date added and a date ended).

    回傳清單是否為會變的欄位（每筆附登錄日與失效日）。

    Args:
        name: the Anki field name
            Anki 欄位名稱

    Returns:
        ``True`` for a history field
        會變的欄位時為 ``True``
    """
    return name in PERSON_HISTORY_FIELDS


#: The note shown when a list's format is broken (instead of showing it as empty).
#: 清單格式壞了時的說明（不要顯示成「還沒有」）。
BROKEN_NOTE = "⚠️ 資料格式壞了（可能是還沒轉換的舊文字），請在網頁或 Anki 修正"


def broken_lists(fields: dict[str, str]) -> frozenset[str]:
    """Return the lists whose format is broken and cannot be parsed (JSON lists and history fields).

    回傳格式壞掉、解析不了的清單（JSON 清單與會變的欄位）。

    Telegram reads them as empty lists (the summary and so on still display), and display code uses this to
    mark them so they do not look like missing data. For example, old text not yet run through the
    conversion script must not make dietary restrictions silently vanish from the must-see section.

    TG 讀取時把它們當成空清單（摘要等照常顯示），顯示的地方用這個標出來，免得看起來像沒有資料
    ——例如還沒跑轉換腳本的舊文字，飲食限制不能就這樣從「見面前必看」消失。

    Args:
        fields: the note's raw field values
            筆記的原始欄位值

    Returns:
        the names of the lists that fail to parse
        解析失敗的清單名稱
    """
    broken = set()
    for name in (*PERSON_JSON_ITEMS, *PERSON_HISTORY_FIELDS):
        try:
            AnkiJsonFieldManager.parse_field_string(fields.get(name, ""))
        except AnkiFieldCorruptedError:
            broken.add(name)
    return frozenset(broken)


def is_multiline(name: str) -> bool:
    """Return whether a field is a multi-line text section, which offers "append" on Telegram.

    回傳欄位是否為文字區段（多行）欄位：TG 上可以選「接在後面」。

    Args:
        name: the Anki field name
            Anki 欄位名稱

    Returns:
        ``True`` if the create parameter is rendered as a textarea
        建立參數以 textarea 呈現時為 ``True``
    """
    info = PersonCreateParams.model_fields.get(name)
    extra = info.json_schema_extra if info else None
    return isinstance(extra, dict) and extra.get("x-ui") == "textarea"


# =============================================================================
# 搜尋
# =============================================================================


def search_query(text: str) -> str:
    """Return the Anki query that finds people by name, with a "contains" match on the name fields.

    回傳用名字找人的 Anki 查詢：在姓名相關欄位做「包含」比對。

    Args:
        text: the name the user typed; matched literally (wildcards are escaped) after stripping
            surrounding whitespace
            使用者輸入的名字（去掉前後空白後照字面比對，萬用字元會跳脫）

    Returns:
        the query, for example ``note:"Person\\_Profile\\_Dark" ("Display_Name:*田中*" OR ...)``
        查詢字串，例如 ``note:"Person\\_Profile\\_Dark" ("Display_Name:*田中*" OR ...)``
    """
    value = escape_search_value(text.strip())
    terms = " OR ".join(f'"{name}:*{value}*"' for name in NAME_FIELDS)
    return f"{note_type_term(PERSON_NOTE_TYPE)} ({terms})"


def card_id_query(card_id: str) -> str:
    """Return the Anki query that finds a person by ``Card_ID``.

    回傳以 ``Card_ID`` 找人的 Anki 查詢。

    Args:
        card_id: the card ID, matched exactly (escaped for Anki search)
            卡片 ID，完全比對（會為 Anki 搜尋跳脫）

    Returns:
        the query string
        查詢字串
    """
    return f'{note_type_term(PERSON_NOTE_TYPE)} "Card_ID:{escape_search_value(card_id)}"'


# =============================================================================
# 顯示
# =============================================================================


def _plain(fields: dict[str, str], name: str, limit: int = 200) -> str:
    """Return a field as Telegram plain text (HTML stripped, escaped).

    回傳欄位轉成的 TG 純文字（去 HTML、已轉義）。
    """
    return anki_field_to_tg_text(fields.get(name, ""), limit)


def parse_date(text: str) -> tuple[int | None, int, int] | None:
    """Parse a stored date, ``YYYY-MM-DD`` or ``--MM-DD``, into (year or ``None``, month, day).

    把儲存的日期 ``YYYY-MM-DD`` 或 ``--MM-DD`` 解析成（年或 ``None``, 月, 日）。

    Args:
        text: the stored date; surrounding whitespace is ignored and ``None`` or empty is accepted
            儲存的日期；忽略前後空白，接受 ``None`` 或空字串

    Returns:
        ``(year, month, day)`` with ``year`` ``None`` for the year-less form; ``None`` if the text does not
        match or is not a real date (February 29 is accepted without a year)
        ``(年, 月, 日)``，沒有年份的寫法年為 ``None``；格式不符或不是真的日期時為 ``None``（沒有年份時接受 2/29）
    """
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
    """Return the number of days until the next occurrence of this month and day.

    回傳下一次這個月日還有幾天。

    Args:
        month: the month
            月
        day: the day of the month; February 29 counts as March 1 in common years
            日；2/29 在平年視為 3/1
        today: the reference date
            今天的日期

    Returns:
        the number of days; ``0`` if it is today
        天數；今天為 ``0``
    """

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
    """Return the name line, for example 「田中さん（田中 悠希・たなか ゆうき）」.

    回傳姓名那一行，例如「田中さん（田中 悠希・たなか ゆうき）」。

    The display name is bold; the Japanese, kana, Chinese and English names follow in parentheses, skipping
    empty ones and those equal to the display name or an earlier one.

    顯示名稱為粗體；日文、假名、中文、英文名接在括號裡，略過空的以及與顯示名稱或前面重複的。

    Args:
        fields: the note's field values
            筆記的欄位值

    Returns:
        the name line in Telegram HTML
        Telegram HTML 格式的姓名行
    """
    display = _plain(fields, "Display_Name", 60)
    others = [_plain(fields, name, 60) for name in ("Name_JA", "Name_JA_Kana", "Name_ZH", "Name_EN")]
    others = [o for i, o in enumerate(others) if o and o != display and o not in others[:i]]
    return f"<b>{display}</b>" + (f"（{'・'.join(others)}）" if others else "")


def summary_text(fields: dict[str, str], lists: dict[str, list[object]], today: date) -> str:
    """Return the summary: name, work and relationship, and the must-see part (same as the card back).

    回傳摘要：姓名、工作與關係、見面前必看（同卡片背面）。

    Args:
        fields: the note's field values
            筆記的欄位值
        lists: the parsed lists; a field that failed to parse is an empty list and is marked separately with
            :func:`broken_lists`
            解析好的清單（解析失敗的欄位是空清單，另以 ``broken_lists`` 標出）
        today: today's date
            今天

    Returns:
        the Telegram HTML message
        TG HTML 訊息
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
    for goal in lists.get("Goals_JSON", []):
        # 有完整日期、60 天內到期的進行中目標（例如考試當天）也算近期
        parsed = parse_date(str(goal.get("target", ""))) if is_open_goal(goal) else None
        if not parsed or parsed[0] is None:
            continue
        until = (date(*parsed) - today).days  # type: ignore[arg-type]
        if 0 <= until <= 60:
            soon.append((until, f"{escape_tg(str(goal.get('item', '')), 40)}（{parsed[1]:02d}/{parsed[2]:02d}，"
                                f"{'就是今天' if until == 0 else f'還有 {until} 天'}）"))
    if soon:
        must.append("<b>近期</b>：" + "；".join(text for _, text in sorted(soon)))
    lines.append("")
    lines.extend(must or ["還沒有要注意的事"])
    return "\n".join(lines)


def _active(lists: dict[str, list[object]], name: str, separator: str, limit: int) -> str:
    """Return the currently valid entries of a history field, joined and escaped.

    回傳會變的欄位目前有效的內容（已轉義）。
    """
    return escape_tg(separator.join(active_texts(name, lists.get(name, []))), limit)


def full_text(fields: dict[str, str], lists: dict[str, list[object]]) -> str:
    """Return the full data view.

    回傳全部資料。

    Lists every non-empty plain field, the history fields (valid entries only; work also lists past jobs)
    and the number of entries in each JSON list. Truncated to the message limit.

    列出所有有值的一般欄位、會變的欄位（有效的；工作另列以前的工作）與各 JSON 清單的筆數。超過訊息上限時截斷。

    Args:
        fields: the note's field values
            筆記的欄位值
        lists: the parsed lists
            解析好的清單

    Returns:
        the Telegram HTML message
        TG HTML 訊息
    """
    lines = [name_line(fields), ""]
    broken = broken_lists(fields)
    for group, names in DISPLAY_GROUPS:
        values = {
            name: (BROKEN_NOTE if name in broken else _active(lists, name, "、", 300))
            if is_history(name) else _plain(fields, name, 300)
            for name in names
        }
        # 工作經歷這裡只列現職（以前的工作接在下面另一列），標「現職」才不會被當成全部的經歷
        rows = [f"• {'現職' if name == 'Company' else field_title(name)}：{value}"
                for name, value in values.items() if value]
        if "Company" in names and "Company" not in broken:
            past = [_position_text(item) for item in lists.get("Company", []) if not is_active(item)]
            if past := [text for text in past if text]:
                rows.append(f"• 以前的工作：{escape_tg('、'.join(past), 300)}")
        if rows:
            lines.append(f"<b>{group}</b>")
            lines.extend(rows)
    counts = [f"{PERSON_JSON_ITEMS[name][1]} {len(lists.get(name, []))}" for name in PERSON_JSON_ITEMS]
    lines.append("")
    lines.append("｜".join(counts))
    return truncate("\n".join(lines))


def _position_text(item: object) -> str:
    """Return a position's text with its tenure, for example 「QBC・QA（2025～）」 (unescaped).

    回傳職位的文字加任職期間：「QBC・QA（2025～）」（未轉義）。
    """
    text = entry_text("Company", item)
    period = position_period(item)
    return f"{text}（{period}）" if text and period else text


def _history_text(field_name: str, item: object) -> str:
    """Return one history entry's text with its event time, for example 「爬過玉山（2026-10）」 (unescaped).

    回傳會變的欄位一筆的文字加事件時間：「爬過玉山（2026-10）」（未轉義）。
    """
    text = entry_text(field_name, item)
    period = history_period(field_name, item)
    return f"{text}（{period}）" if text and period else text


def is_hideable_field(name: str) -> bool:
    """Return whether the card face can hide this text field (Telegram shows the hide toggle only for these).

    回傳是否為卡面能隱藏的文字欄位（TG 只對這些顯示隱藏開關）。

    Args:
        name: the Anki field name
            Anki 欄位名稱

    Returns:
        ``True`` if the field can be hidden
        可以隱藏時為 ``True``
    """
    return name in HIDEABLE_TEXT_FIELDS


def goal_buttons(status: str) -> tuple[tuple[str, str], ...]:
    """Return the status buttons of a goal.

    回傳目標一筆的狀態按鈕。

    Args:
        status: the goal's status; ``open`` means in progress
            目標的狀態；``open`` 表示進行中

    Returns:
        ``(button text, code)`` pairs: an open goal can be achieved or abandoned, a closed one reopened
        ``(按鈕文字, 代碼)``：進行中可以達成或放棄，結束的可以重開
    """
    if status == "open":
        return (("達成", "d"), ("放棄", "x"))
    return (("重開", "o"),)


def history_toggle_label(field_name: str, active: bool) -> str:
    """Return the text of a history field's "strike out / restore" button.

    回傳會變的欄位「劃掉／取消劃掉」按鈕的文字。

    Args:
        field_name: the history field; for ``Company`` the texts are 「離職／恢復現職」
            會變的欄位；``Company`` 的文字是「離職／恢復現職」
        active: whether the entry is currently valid
            這一筆目前是否有效

    Returns:
        the button text
        按鈕文字
    """
    if field_name == "Company":
        return "離職" if active else "恢復現職"
    return "劃掉" if active else "取消劃掉"


def item_line(field_name: str, item: object) -> str:
    """Return one list entry as a single escaped line.

    回傳清單裡一筆的一行文字（已轉義）。

    History fields show the date added; struck-out entries get a strikethrough and the date ended; education
    shows its note. Positions are prefixed with 「[現職]」 or 「[以前]」 and show their tenure and note; past
    jobs are not struck through (the period is already included). Entries hidden on the card face get
    「〔卡面隱藏〕」. A multi-line note is joined into one line.

    會變的欄位附登錄日，劃掉的加刪除線與失效日，學歷附說明；職位前面標「[現職]」或「[以前]」，附任職期間與
    說明，以前的工作不加刪除線（期間已寫在裡面）；在卡面隱藏的加「〔卡面隱藏〕」。多行的說明接成一行。

    Args:
        field_name: the list the entry belongs to
            這一筆所屬的清單
        item: the entry; a non-dict entry is shown as its string
            這一筆；不是字典時顯示其字串

    Returns:
        the line in Telegram HTML
        Telegram HTML 格式的一行
    """
    line = _item_line(field_name, item)
    return f"{line}〔卡面隱藏〕" if is_hidden(item) else line


def _item_line(field_name: str, item: object) -> str:
    if not isinstance(item, dict):
        return escape_tg(str(item), 80)
    if field_name == "Company":
        text = escape_tg(_position_text(item), 200)
        added = str(item.get("added") or "")
        dates = f"{escape_tg(added, 10)} 登錄" if added else "日期不明"
        note = f"｜{escape_tg(one_line(str(item.get('note') or '')), 120)}" if item.get("note") else ""
        state = "現職" if is_active(item) else "以前"
        return f"[{state}] {text}{note}（{dates}）"
    if is_history(field_name):
        text = escape_tg(_history_text(field_name, item), 200)
        if field_name in NOTE_FIELDS and item.get("note"):
            text += f"｜{escape_tg(one_line(str(item['note'])), 120)}"
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
        direction = {"received": "收到", "idea": "想要"}.get(str(item.get("direction") or ""), "送出")
        return f"{direction} {get('item')}" + (f"（{get('date')}）" if item.get("date") else "")
    if field_name == "Goals_JSON":
        status = str(item.get("status") or "open")
        line = f"[{get('kind') or '目標'}] {get('item')}" + (f"（目標 {get('target')}）" if item.get("target") else "")
        if status != "open":
            closed = f" {get('closed')}" if item.get("closed") else ""
            line = f"<s>{line}</s>（{GOAL_STATUSES.get(status, status)}{closed}）"
        return line
    if field_name == "Key_Dates_JSON":
        return f"{get('label')}：{get('date')}" + ("（每年）" if item.get("yearly") else "")
    if field_name == "Contacts_JSON":
        return f"{get('channel')}：{get('handle')}" + ("（偏好）" if item.get("preferred") else "")
    return escape_tg(str(item), 80)


def truncate(text: str, limit: int = MESSAGE_LIMIT) -> str:
    """Truncate the text at a line break when it exceeds the Telegram limit, appending 「…」.

    超過 TG 上限時截斷（在換行處），加上「…」。

    Args:
        text: the message text
            訊息文字
        limit: the maximum length; when no line break precedes it, the text is cut at the limit itself
            長度上限；上限之前沒有換行時直接在上限處截斷

    Returns:
        ``text`` unchanged if it fits, otherwise the truncated text followed by a line with 「…」
        放得下時為原本的 ``text``，否則為截斷後的文字並另起一行加「…」
    """
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit)
    return text[: cut if cut > 0 else limit] + "\n…"


# =============================================================================
# 輸入
# =============================================================================


@dataclass(frozen=True)
class Step:
    """One question asked when adding or editing an entry.

    新增或修改一筆時的一個問題。

    Editing reuses the adding steps; see :func:`edit_steps`.

    修改沿用新增的步驟，見 ``edit_steps``。

    Attributes:
        key: the key written into the entry; ``_followups`` is the "ask next time" list attached to an
            interaction (one per line)
            寫進項目的鍵；``_followups`` 為互動紀錄附帶的「下次要問」（每行一筆）
        prompt: the question
            問題
        kind: the input kind: ``text``, ``date``, ``int``, ``choice``, ``lines`` or ``period`` (a year,
            year-month or date, used for event times; see :data:`HISTORY_DETAIL_STEPS`)
            輸入種類：``text``、``date``、``int``、``choice``、``lines``、``period``（年、年月或日期，事件時間用，
            見 ``HISTORY_DETAIL_STEPS``）
        required: whether it is required: no "skip" when adding; when editing it can be skipped (keeping the
            current value) but not cleared
            必填：新增時沒有「略過」；修改時可以略過（＝保留目前的值），但不能清空
        choices: the options of a ``choice`` step as (value, button text); for a ``text`` step they are
            suggestion buttons
            ``choice`` 的選項 (值, 按鈕文字)；``text`` 有選項時是建議按鈕
        default_today: whether a date step offers "today" and "yesterday" buttons
            日期步驟是否提供「今天」「昨天」按鈕
        skip_hint: what "skip" means when adding (shown below the question, not shown for required steps);
            when editing, skipping always means keeping the value and the handler explains that separately,
            so the question itself only states the question and format
            新增時「略過」代表什麼（接在問題下面，必填的不顯示）；修改時略過一律是保留，由 handler 另外
            說明，所以問題本身只寫問句與格式
        allow_unknown: whether a ``period`` step also accepts ``?`` (left the job but the time is unknown)
            ``period`` 步驟是否也收 ``?``（已離職但不知道時間）
        add_hint: a note shown below the question only when adding (for example "separate several with 、":
            editing can only change it to one)
            只有新增時接在問題下面的說明（例如「多個用頓號分開」：修改時只能改成一個）
    """

    key: str
    prompt: str
    kind: Literal["text", "date", "int", "choice", "lines", "period"] = "text"
    required: bool = False
    choices: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    default_today: bool = False
    skip_hint: str = ""
    allow_unknown: bool = False
    add_hint: str = ""


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
        Step("direction", "送出、收到，還是想要的（禮物靈感）？", kind="choice", required=True,
             choices=(("given", "送出"), ("received", "收到"), ("idea", "想要的"))),
        Step("item", "什麼禮物？", required=True),
        Step("date", "哪一天？（想要的禮物就略過）", kind="date", default_today=True),
        Step("reaction", "對方的反應？"),
    ),
    "Key_Dates_JSON": (
        Step("label", "什麼日子？（例如「N1 考試」）", required=True),
        Step("date", "日期？（YYYY-MM-DD）", kind="date", required=True),
        Step("yearly", "每年都有嗎？", kind="choice", required=True, choices=(("1", "每年"), ("0", "只有一次"))),
    ),
    "Goals_JSON": (
        Step("kind", "哪一種目標？（可以點建議，或直接輸入）", required=True,
             choices=tuple((kind, kind) for kind in GOAL_KINDS)),
        Step("item", "目標是什麼？（例如「日本語能力試驗 N1」）", required=True),
        Step("target", "目標時間？（例如 2026-12，或只寫年 2027）", kind="period", skip_hint="沒有就略過。"),
    ),
    "Contacts_JSON": (
        Step("channel", "哪種聯絡方式？", required=True,
             choices=(("LINE", "LINE"), ("Email", "Email"), ("電話", "電話"), ("Instagram", "Instagram"))),
        Step("handle", "帳號或號碼？", required=True),
        Step("preferred", "是偏好的聯絡方式嗎？", kind="choice", required=True, choices=(("1", "是"), ("0", "否"))),
    ),
}


#: The tenure (start and end) and note steps of a position, asked after company and title when adding and
#: editing. The end step accepts ``?``; the handler's ``_store_answer`` checks at the ``end`` step that the
#: end is not before the start.
#: 職位的任職起訖與說明：新增與修改時接在公司、職稱後面問。離職那一步接受 ``?``；離職不早於到職由 handler 的
#: ``_store_answer`` 在 ``end`` 那一步檢查。
POSITION_DETAIL_STEPS: tuple[Step, ...] = (
    Step("start", "什麼時候到職？（例如 2021-04，或只寫年 2021）", kind="period", skip_hint="不知道就略過。"),
    Step("end", "什麼時候離職？（例如 2023-03；離職了但不知道時間就寫 ?）", kind="period",
         skip_hint="還在這裡工作就略過。", allow_unknown=True),
    Step("note", "這份工作做什麼？（可以多行，例如「前端開發」「負責轉職顧問，工作 7 年」）", skip_hint="沒有就略過。"),
)

#: The event-time steps asked after the content when adding a history field (work-history plan §15; keys as
#: in :data:`HISTORY_PERIOD_KEYS`), also asked when editing (§16). Fields with a note (:data:`NOTE_FIELDS`:
#: positions and education) then ask for the note after the time (§18).
#: 會變的欄位新增時接在內容後面追問的事件時間（工作經歷計畫書 §15；鍵同 :data:`HISTORY_PERIOD_KEYS`），修改
#: （§16）也問。有說明的欄位（:data:`NOTE_FIELDS`：職位、學歷）時間後面再問說明（§18）。
HISTORY_DETAIL_STEPS: dict[str, tuple[Step, ...]] = {
    "Company": POSITION_DETAIL_STEPS,
    "Education": (
        Step("start", "什麼時候入學？（例如 2011-09，或只寫年 2011）", kind="period", skip_hint="不知道就略過。"),
        Step("finish", "什麼時候畢業（或離開）？（例如 2015）", kind="period", skip_hint="還在讀或不知道就略過。"),
        Step("note", "科系或說明？（例如「資工系」，可以多行）", skip_hint="沒有就略過。"),
    ),
    "Experiences": (
        Step("when", "什麼時候的事？（例如 2019、2026-10）", kind="period", skip_hint="不知道就略過。"),
    ),
    "Praise": (
        Step("when", "什麼時候的事？（例如 2025、2026-10）", kind="period", skip_hint="不知道就略過。"),
    ),
    "Interests": (
        Step("since", "從什麼時候開始的？（例如 2015）", kind="period", skip_hint="不知道就略過。"),
    ),
    "Residence": (
        Step("since", "什麼時候搬來的？（例如 2023-04）", kind="period", skip_hint="不知道就略過。"),
    ),
}
# 步驟的鍵要和 HISTORY_PERIOD_KEYS 一致（有說明的欄位時間後面多了說明，只比前面的時間）
assert {name: tuple(step.key for step in steps)[:len(HISTORY_PERIOD_KEYS[name])]
        for name, steps in HISTORY_DETAIL_STEPS.items()} == HISTORY_PERIOD_KEYS, "HISTORY_DETAIL_STEPS 與 HISTORY_PERIOD_KEYS 不一致"

#: Fields with a start and an end: (start key, end key, start label, end label); when the end step is answered,
#: it is checked not to be earlier than the start.
#: 有起訖的欄位：(開始的鍵, 結束的鍵, 開始的名稱, 結束的名稱)；問到結束那一步時檢查不早於開始。
PERIOD_PAIRS: dict[str, tuple[str, str, str, str]] = {
    "Company": ("start", "end", "到職", "離職"),
    "Education": ("start", "finish", *EDUCATION_LABELS),
}
assert all(pair[:2] == HISTORY_PERIOD_KEYS[name] for name, pair in PERIOD_PAIRS.items()), "PERIOD_PAIRS 的鍵與 HISTORY_PERIOD_KEYS 不一致"


def skip_detail_step(field_name: str, item: dict[str, object], step: Step) -> bool:
    """Return whether to skip this step when adding.

    回傳新增時是否跳過這一步。

    For a splittable field that also has an event time (currently only interests), the time is not asked
    when one answer adds several entries: applying the same time to each would record it wrongly, and it
    can be filled in later by long-pressing that entry.

    可切、又有事件時間的欄位（目前只有興趣）一次輸入了好幾個時不問時間（同一個時間套到
    每一個會記錯，之後長按那一筆再補）。

    Args:
        field_name: the list being added to
            正在新增的清單
        item: the answers collected so far; ``value`` holds the content
            目前收集到的回答；``value`` 是內容
        step: the step about to be asked
            準備要問的步驟

    Returns:
        ``True`` if the step should be skipped
        應該跳過時為 ``True``
    """
    if step.key not in HISTORY_PERIOD_KEYS.get(field_name, ()) or not PERSON_HISTORY_FIELDS[field_name].split:
        return False
    return len(history_from_text(field_name, str(item.get("value") or ""), "")) > 1


def _history_steps(name: str) -> tuple[Step, ...]:
    """Return the steps asked when adding (and editing) a history field.

    回傳會變的欄位新增（與修改）時要問的步驟。

    The date added is filled in by the system; fields with an event time then ask for the time, and fields
    with a note then ask for the note (:data:`HISTORY_DETAIL_STEPS`; a position asks company, title, tenure
    and note).

    登錄日由系統填；有事件時間的欄位接著問時間，有說明的欄位再問說明
    （``HISTORY_DETAIL_STEPS``；職位是公司、職稱、任職起訖與說明）。
    """
    title = field_title(name)
    if name == "Company":
        return (
            Step("company", "公司？", skip_hint="沒有就略過。"),
            Step("title", "職稱？", skip_hint="沒有就略過；公司、職稱都不知道也沒關係，後面寫時間或說明就好。"),
            *POSITION_DETAIL_STEPS,
        )
    if name == "Closeness":
        return (Step("value", "親密度？（1 最生疏、5 最親近）", kind="choice", required=True,
                     choices=tuple((str(n), str(n)) for n in range(1, 6))),)
    if name == "Relationship":
        return (Step("value", "關係？（可以點建議，或直接輸入）", required=True,
                     choices=tuple((x, x) for x in ("客戶", "朋友", "同事", "前同事", "同學", "語言交換"))),)
    if name == "Timezone":
        return (Step("value", "時區？（例如 Asia/Tokyo）", required=True,
                     choices=(("Asia/Tokyo", "Asia/Tokyo"), ("Asia/Taipei", "Asia/Taipei"))),)
    hint = "多個用頓號或逗號分開，會存成多筆。" if PERSON_HISTORY_FIELDS[name].split else ""
    return (Step("value", f"{title}？", required=True, add_hint=hint), *HISTORY_DETAIL_STEPS.get(name, ()))


ADD_STEPS.update({name: _history_steps(name) for name in PERSON_HISTORY_FIELDS})


def edit_steps(name: str) -> tuple[Step, ...]:
    """Return the steps asked when editing an entry (work-history plan §16).

    回傳修改一筆時問的步驟（工作經歷計畫書 §16）。

    Same as adding, but without the attached steps whose key starts with ``_`` (they are not keys of this
    entry; currently only the "ask next time" list attached to an interaction).

    同新增，但不問 ``_`` 開頭的附帶步驟（不是這一筆的鍵，目前只有互動附帶的「下次要問」）。

    Args:
        name: the list name; must be a key of ``ADD_STEPS``
            清單名稱；必須是 ``ADD_STEPS`` 的鍵

    Returns:
        the editing steps in order
        依序的修改步驟
    """
    return tuple(step for step in ADD_STEPS[name] if not step.key.startswith("_"))


# 修改問的就是後端能改的鍵（ITEM_EDIT_KEYS），順序也相同
assert {name: tuple(step.key for step in edit_steps(name)) for name in ADD_STEPS} == ITEM_EDIT_KEYS, \
    "修改的步驟與 ITEM_EDIT_KEYS 不一致"


#: The yes / no steps: answers become booleans (:func:`coerce_answer`); ``False`` is not written when saving, so
#: a missing key means "no".
#: 是／否的步驟：回答轉成布林（:func:`coerce_answer`）；存檔時 False 不寫，所以沒有這個鍵就是「否」。
BOOL_STEP_KEYS = ("yearly", "preferred")


def current_value_text(step: Step, value: object) -> str:
    """Return the value shown after 「目前：」 while editing (unescaped).

    回傳修改時「目前：」顯示的值（未轉義）。

    Choice steps show the button text (every year, preferred and so on; a yes / no step with no value is
    「否」); other steps give an empty string when there is no value.

    選擇題顯示按鈕文字（每年、偏好等；是／否的沒有值就是「否」），其他沒有值時為空字串。

    Args:
        step: the step being edited
            正在修改的步驟
        value: the entry's current value for ``step.key``; may be ``None``
            這一筆 ``step.key`` 目前的值，可為 ``None``

    Returns:
        the display text; the raw value as a string when no choice matches
        顯示文字；沒有對應的選項時為原值的字串
    """
    if step.key in BOOL_STEP_KEYS:
        value = bool(value)
    if value is None or value == "":
        return ""
    if step.kind == "choice":
        code = ("1" if value else "0") if isinstance(value, bool) else str(value)
        for choice, label in step.choices:
            if choice == code:
                return label
    return str(value)


def parse_date_input(text: str, today: date) -> str | None:
    """Parse a date typed by the user into ``YYYY-MM-DD``.

    把使用者輸入的日期轉成 ``YYYY-MM-DD``。

    Accepts ``2026-10-01``, ``2026/10/1``, ``10-01`` and ``10/1`` (this year); ``.`` also works as the
    separator.

    接受 ``2026-10-01``、``2026/10/1``、``10-01``、``10/1``（今年）；分隔符號也可以用 ``.``。

    Args:
        text: the typed text; ``None`` or empty is accepted
            輸入的文字，接受 ``None`` 或空字串
        today: the reference date whose year is used when the year is omitted
            參考日期，省略年份時用它的年份

    Returns:
        the ISO date, or ``None`` if the text does not match or is not a real date
        ISO 日期；格式不符或不是真的日期時為 ``None``
    """
    m = re.fullmatch(r"\s*(?:(\d{4})[-/.])?(\d{1,2})[-/.](\d{1,2})\s*", text or "")
    if not m:
        return None
    year = int(m.group(1)) if m.group(1) else today.year
    try:
        return date(year, int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


def parse_past_date_input(text: str, today: date) -> str | None:
    """Parse a day that has already happened (a goal achieved or abandoned).

    解析已經發生的日子（目標達成、放棄）。

    Same as :func:`parse_date_input`, but when only the month and day are written and the result falls
    after today, it is taken as last year (typing 「12-28」 in early January means last December). A date
    with an explicit year is returned as-is (the caller rejects future dates).

    同 ``parse_date_input``，但只寫月日、算出來在今天之後時當成去年
    （一月初輸入「12-28」是去年 12 月）。寫了年份的照原樣（未來的由呼叫端擋）。

    Args:
        text: the typed text
            輸入的文字
        today: today's date
            今天

    Returns:
        the ISO date, or ``None`` if invalid (including February 29 moved to a common year)
        ISO 日期；無效時為 ``None``（包含移到平年的 2/29）
    """
    value = parse_date_input(text, today)
    if value is None or value <= today.isoformat() or re.match(r"\s*\d{4}", text or ""):
        return value
    try:
        return date.fromisoformat(value).replace(year=today.year - 1).isoformat()
    except ValueError:  # 2/29 在去年不存在
        return None


def parse_period_input(text: str, *, allow_unknown: bool = False) -> str | None:
    """Parse a typed event time into ``YYYY``, ``YYYY-MM`` or ``YYYY-MM-DD``.

    把事件時間的輸入轉成 ``YYYY``、``YYYY-MM`` 或 ``YYYY-MM-DD``。

    Uses the same rules as the web form (see :func:`normalize_period`: the year is 1900–2100 and the date
    must be real).

    規則同網頁（見 ``normalize_period``：年份 1900–2100、要是真的日期）。

    Args:
        text: the typed text
            輸入的文字
        allow_unknown: also accept ``?`` (used for leaving a job)
            也收 ``?``（離職用）

    Returns:
        the normalized period, or ``None`` if blank or not understood
        正規化後的時間；空白或看不懂時為 ``None``
    """
    return normalize_period(text, allow_unknown=allow_unknown) or None


def today_choice(today: date, offset: int) -> str:
    """Return the date behind the "today" and "yesterday" buttons.

    回傳「今天」「昨天」按鈕的日期。

    Args:
        today: today's date
            今天
        offset: how many days before today; ``0`` is today, ``1`` is yesterday
            今天往前幾天；``0`` 是今天，``1`` 是昨天

    Returns:
        the ISO date
        ISO 日期
    """
    return (today - timedelta(days=offset)).isoformat()


def coerce_answer(step: Step, value: str) -> object:
    """Turn one answer into the value to store.

    把一個回答轉成要存的值。

    Yes / no options ``1``/``0`` become booleans and a birth year becomes an integer; other answers are
    stripped of surrounding whitespace.

    選項 1/0 轉布林、出生年轉整數；其他回答去掉前後空白。

    Args:
        step: the step being answered
            正在回答的步驟
        value: the answer text or button value
            回答的文字或按鈕值

    Returns:
        the value to store
        要存的值

    Raises:
        ValueError: if an ``int`` answer is not an integer or is outside 1900–2100
            若 ``int`` 步驟的回答不是整數，或不在 1900–2100 之間
    """
    if step.key in BOOL_STEP_KEYS:
        return value == "1"
    if step.kind == "int":
        number = int(value.strip())
        if not 1900 <= number <= 2100:
            raise ValueError("年份要在 1900 到 2100 之間")
        return number
    return value.strip()


def text_to_field_html(text: str) -> str:
    """Convert text typed in Telegram into Anki field HTML (escaped, line breaks become ``<br>``).

    把 TG 輸入的文字轉成 Anki 欄位 HTML（轉義、換行變 ``<br>``）。

    Args:
        text: the typed text; surrounding whitespace is stripped
            輸入的文字，會去掉前後空白

    Returns:
        the field HTML
        欄位 HTML
    """
    return html.escape(text.strip()).replace("\n", "<br>")


def preview_item(field_name: str, item: dict[str, object], *, editing: bool = False) -> str:
    """Return the preview shown before saving an entry.

    回傳儲存前的預覽。

    Args:
        field_name: the list the entry belongs to
            這一筆所屬的清單
        item: the collected entry; keys starting with ``_`` are attachments such as ``_followups``
            收集好的這一筆；``_`` 開頭的鍵是附帶資料，例如 ``_followups``
        editing: whether this edits an existing entry (§16) rather than adding one
            是否為修改一筆（§16）而非新增

    Returns:
        the preview in Telegram HTML
        Telegram HTML 格式的預覽
    """
    title = list_title(field_name)
    if field_name == "Company":
        state = "現職" if is_active(item) else "以前的工作"
        lines = [f"<b>{'修改' if editing else '新增'}{title}</b>（{state}）", escape_tg(_position_text(item), 300)]
        if item.get("note"):
            lines.append(f"說明：{escape_tg(str(item['note']), 200)}")
        if not editing:
            lines.append("<i>登錄日：今天</i>")
        return "\n".join(lines)
    if is_history(field_name):
        note = [f"說明：{escape_tg(str(item['note']), 200)}"] if field_name in NOTE_FIELDS and item.get("note") else []
        if editing:
            return "\n".join([f"<b>修改{title}</b>", escape_tg(_history_text(field_name, item), 300), *note])
        lines = [f"<b>新增{title}</b>", escape_tg(_history_text(field_name, item), 300), *note, "<i>登錄日：今天</i>"]
        if PERSON_HISTORY_FIELDS[field_name].single:
            lines.append("<i>原本有效的那筆會自動劃掉（失效日＝今天）。</i>")
        return "\n".join(lines)
    lines = [f"<b>{'修改' if editing else '新增'}{title}</b>",
             item_line(field_name, {k: v for k, v in item.items() if not k.startswith("_")})]
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
    """Return the text of one proposal from the LLM (escaped).

    回傳 LLM 一項建議的文字（已轉義）。

    Args:
        kind: the proposal kind: ``interaction``, ``followup``, ``goal``, ``family``, ``history_add``,
            ``history_end``; anything else is treated as a text-field update
            建議種類：``interaction``、``followup``、``goal``、``family``、``history_add``、``history_end``；其他視為
            文字欄位更新
        data: the proposal's data
            建議的資料

    Returns:
        the proposal in Telegram HTML, possibly over several lines
        Telegram HTML 格式的建議，可能有多行
    """
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
    if kind == "goal":
        target = f"（目標 {get('target')}）" if data.get("target") else ""
        return f"<b>目標</b> 新增：[{get('kind') or '目標'}] {get('item')}{target}" + reason
    if kind == "family":
        name = f"（{get('name', 40)}）" if data.get("name") else ""
        note = f"：{get('note', 200)}" if data.get("note") else ""
        return f"<b>家人</b> 新增：{get('relation', 20)}{name}{note}" + reason
    if kind in ("history_add", "history_end"):
        name = str(data.get("field", ""))
        title = escape_tg(field_title(name))
        text = escape_tg(str(data.get("text", "") or ""), 300)
        if kind == "history_end":
            return f"<b>{title}</b> {history_toggle_label(name, True)}：<s>{text}</s>" + reason
        single = PERSON_HISTORY_FIELDS.get(name)
        extra = "（原本的會劃掉）" if single and single.single else ""
        item = data.get("item")
        if name == "Company" and isinstance(item, dict):
            extra = "（現職）" if is_active(item) else "（以前的工作）"
        return f"<b>{title}</b> 新增：{text}{extra}" + reason
    mode = "接在後面" if data.get("mode") == "append" else "改成"
    return f"<b>{escape_tg(field_title(str(data.get('field', ''))))}</b> {mode}：{get('value', 300)}" + reason


#: Check marks used in the preview and on buttons (text, not emoji).
#: 預覽與按鈕上的勾選記號（文字，不用 emoji）。
CHECKED, UNCHECKED, SAVED = "✓", "－", "已存"

#: Characters reserved for the preview's header and closing notes (overflow and already-saved notes).
#: 預覽的開頭與結尾提示（放不下、已儲存的說明）預留的字數。
_PREVIEW_RESERVE = 300


def _proposal_entry(index: int, proposal: dict[str, object], mark: str) -> str:
    data = proposal.get("data")
    return f"{mark} {index + 1}. {proposal_line(str(proposal.get('kind', '')), data if isinstance(data, dict) else {})}"


def fitting_count(proposals: list[dict[str, object]]) -> int:
    """Return how many leading proposals fit in one message.

    回傳一則訊息放得下前幾項建議。

    The check marks have a fixed length, so toggling selections does not change the result.

    勾選記號的長度固定，切換勾選不影響。

    Args:
        proposals: the proposals
            建議

    Returns:
        the number of proposals that fit, counted from the start
        從頭算起放得下的項數
    """
    used = _PREVIEW_RESERVE
    for index, proposal in enumerate(proposals):
        used += len(_proposal_entry(index, proposal, CHECKED)) + 1
        if used > MESSAGE_LIMIT:
            return index
    return len(proposals)


def ingest_preview(
    proposals: list[dict[str, object]], selected: list[bool], *, saved: frozenset[int] = frozenset(), overflow: int = 0
) -> str:
    """Return the preview of the organized proposals, each prefixed with its check state and number.

    回傳整理結果的預覽：每項前面標勾選狀態與編號。

    Args:
        proposals: the proposals; the caller first cuts them to what fits with :func:`fitting_count`
            建議（呼叫端先用 ``fitting_count`` 截到放得下的數量）
        selected: whether each proposal is selected
            每項是否勾選
        saved: indexes of proposals already written (when the previous save failed midway)
            已經寫入的項目索引（上次儲存中途失敗時）
        overflow: how many proposals did not fit and are not listed
            放不下、沒有列出的項數

    Returns:
        the preview in Telegram HTML
        Telegram HTML 格式的預覽
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
    """Return the insight and topic suggestions as a Telegram HTML message (escaped).

    回傳分析與話題的訊息（已轉義）。

    Args:
        fields: the note's field values (for the display name)
            筆記的欄位值（取顯示名稱）
        result: the LLM's insight and topics
            LLM 產生的分析與話題

    Returns:
        the message, truncated to the message limit
        訊息，超過上限時截斷
    """
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
