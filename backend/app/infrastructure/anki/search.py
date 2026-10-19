"""
組 Anki 搜尋語句時的跳脫（`docs/wip/relation_sync_and_search_escape_FIX_2026-10-19.md` §4）。

Escaping for values put into Anki search queries.

把使用者輸入、牌組名、欄位內容放進 Anki 搜尋時，一律經過這裡：在整段加引號的條件裡，
``\\``、``"``、``*``、``_``、``:`` 仍有特殊意義，要以反斜線跳脫（``\\`` 必須最先處理，否則會
重複跳脫）：

- ``*``、``_`` 是萬用字元。
- 冒號：不指定欄位時，值裡的第一個冒號會被當成「欄位名:值」的分隔；指定欄位時，值以 ``re:``、
  ``nc:`` 開頭會被當成正規表示式或忽略重音的搜尋。跳脫後兩種情況都照字面比對，牌組名的 ``::``
  跳脫後也照常比對（含子牌組）。

換行與 HTML 不需處理——欄位搜尋比對的是儲存的原始內容。以上都已在正式 Anki 上以唯讀查詢
驗證（設計書 §4.1）。

English summary:
    Escape values placed inside quoted Anki search terms: backslash, double
    quote, the two wildcards ``*`` / ``_`` and colons (field separators and
    ``re:`` / ``nc:`` prefixes). Newlines and HTML match literally.
"""

# 在引號內仍有特殊意義的字元；\ 要先處理，否則會重複跳脫
# Characters still special inside a quoted search term; backslash first.
SEARCH_SPECIALS: tuple[str, ...] = ("\\", '"', "*", "_", ":")


def escape_search_value(value: str) -> str:
    """跳脫值裡的 ``\\ " * _ :``，讓它在引號內照字面比對。Escape a value for a quoted term.

    Args:
        value: 要照字面比對的值。The value to match literally.

    Returns:
        跳脫後的值（不含外層引號）。The escaped value, without quotes.
    """
    for char in SEARCH_SPECIALS:
        value = value.replace(char, f"\\{char}")
    return value


def field_term(field: str, value: str) -> str:
    """只比對某個欄位「完全等於」這個值的條件。An exact-match term for one field.

    Args:
        field: 欄位名（不跳脫，由程式提供），例如 ``My_Original``。Field name.
        value: 要照字面比對的值。The value.

    Returns:
        例如 ``"My_Original:He said \\"hi\\""``；值以 ``re:`` 開頭也照字面比對。The search term.
    """
    return f'"{field}:{escape_search_value(value)}"'


def deck_term(deck: str) -> str:
    """只比對這個牌組（含子牌組）的條件，名稱中的萬用字元照字面比對。

    A term matching exactly this deck and its sub-decks.

    Args:
        deck: 牌組完整名稱，例如 ``日本語::外語糾錯``。Full deck name.

    Returns:
        例如 ``deck:"日本語\\:\\:外語糾錯"``（跳脫後的 ``::`` 照常比對子牌組）。The search term.
    """
    return f'deck:"{escape_search_value(deck)}"'


def note_type_term(note_type: str) -> str:
    """只比對這個筆記類型的條件。A term matching one note type.

    Args:
        note_type: 筆記類型名稱，例如 ``Expression_Master_Dark``。Note type name.

    Returns:
        例如 ``note:"Expression\\_Master\\_Dark"``（``\\_`` 照字面比對底線；已在正式 Anki 驗證結果
        與不跳脫相同）。The search term.
    """
    return f'note:"{escape_search_value(note_type)}"'


def note_types_query(note_types: list[str], deck: str | None = None) -> str:
    """任一個筆記類型（可限定牌組）的查詢。A query for any of the note types, optionally in one deck.

    Args:
        note_types: 筆記類型名稱。Note type names.
        deck: 牌組完整名稱；None 或空字串表示不限。The deck, or None for all.

    Returns:
        例如 ``deck:"日本語" (note:"A" OR note:"B")``。The query.
    """
    types = " OR ".join(note_type_term(name) for name in note_types)
    return f"{deck_term(deck)} ({types})" if deck else f"({types})"
