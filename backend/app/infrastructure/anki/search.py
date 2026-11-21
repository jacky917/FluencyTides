"""Escaping for values put into Anki search queries.

組 Anki 搜尋語句時的跳脫（設計書 `relation_sync_and_search_escape_FIX_2026-10-19.md` §4）。

See §4 of the design doc `relation_sync_and_search_escape_FIX_2026-10-19.md`. Every value put into an Anki search
(user input, deck names, field content) goes through here: inside a fully quoted term, ``\\``, ``"``, ``*``, ``_``
and ``:`` still have special meaning and must be escaped with a backslash (``\\`` must be handled first, or it gets
escaped twice):

- ``*`` and ``_`` are wildcards.
- Colons: without a field, the first colon in the value is taken as the "field:value" separator; with a field, a
  value starting with ``re:`` or ``nc:`` is taken as a regular expression or an accent-insensitive search. After
  escaping, both cases match literally, and an escaped ``::`` in a deck name still matches as usual (including
  sub-decks).

Newlines and HTML need no handling: field searches compare against the stored raw content. All of the above was
verified with read-only queries on the production Anki (design doc §4.1).

把使用者輸入、牌組名、欄位內容放進 Anki 搜尋時，一律經過這裡：在整段加引號的條件裡，
``\\``、``"``、``*``、``_``、``:`` 仍有特殊意義，要以反斜線跳脫（``\\`` 必須最先處理，否則會
重複跳脫）：

- ``*``、``_`` 是萬用字元。
- 冒號：不指定欄位時，值裡的第一個冒號會被當成「欄位名:值」的分隔；指定欄位時，值以 ``re:``、
  ``nc:`` 開頭會被當成正規表示式或忽略重音的搜尋。跳脫後兩種情況都照字面比對，牌組名的 ``::``
  跳脫後也照常比對（含子牌組）。

換行與 HTML 不需處理——欄位搜尋比對的是儲存的原始內容。以上都已在正式 Anki 上以唯讀查詢
驗證（設計書 §4.1）。
"""

# 在引號內仍有特殊意義的字元；\ 要先處理，否則會重複跳脫
# Characters still special inside a quoted search term; backslash first.
SEARCH_SPECIALS: tuple[str, ...] = ("\\", '"', "*", "_", ":")


def escape_search_value(value: str) -> str:
    """Escape ``\\ " * _ :`` in a value so it matches literally inside quotes.

    跳脫值裡的 ``\\ " * _ :``，讓它在引號內照字面比對。

    Args:
        value: the value to match literally
            要照字面比對的值

    Returns:
        the escaped value, without the outer quotes
        跳脫後的值（不含外層引號）
    """
    for char in SEARCH_SPECIALS:
        value = value.replace(char, f"\\{char}")
    return value


def field_term(field: str, value: str) -> str:
    """Build a term matching one field whose value equals ``value`` exactly.

    只比對某個欄位「完全等於」這個值的條件。

    Args:
        field: the field name (not escaped; supplied by the code), for example ``My_Original``
            欄位名（不跳脫，由程式提供），例如 ``My_Original``
        value: the value to match literally
            要照字面比對的值

    Returns:
        the search term, for example ``"My_Original:He said \\"hi\\""``; a value starting with ``re:`` also matches
        literally
        搜尋條件，例如 ``"My_Original:He said \\"hi\\""``；值以 ``re:`` 開頭也照字面比對
    """
    return f'"{field}:{escape_search_value(value)}"'


def contains_term(field: str | None, value: str) -> str:
    """Build a term matching a field that contains ``value``.

    欄位「包含」這個值的條件。

    When ``field`` is ``None`` (or empty), any field matches; without a field, Anki already does partial matching.

    ``field`` 為 ``None``（或空字串）時不限欄位（Anki 不指定欄位時本來就是部分比對）。

    Args:
        field: the field name (not escaped; supplied by the code); ``None`` means any field
            欄位名（不跳脫，由程式提供），``None`` 表示任何欄位
        value: the value to match literally (wildcards and the like are escaped)
            要照字面比對的值（萬用字元等會被跳脫）

    Returns:
        the search term, for example ``"Interactions_JSON:*餐廳*"`` or ``"餐廳"``
        搜尋條件，例如 ``"Interactions_JSON:*餐廳*"``、``"餐廳"``
    """
    escaped = escape_search_value(value)
    return f'"{field}:*{escaped}*"' if field else f'"{escaped}"'


def tag_term(tag: str) -> str:
    """Build a term matching notes with this tag or its child tags, compared literally.

    有這個標籤（含子標籤）的條件，照字面比對。

    For example, ``Interview`` also finds ``Interview::Final``.

    例如 ``Interview`` 也找到 ``Interview::Final``。

    Args:
        tag: the tag, for example ``Business_Japanese``
            標籤，例如 ``Business_Japanese``

    Returns:
        the search term, for example ``"tag:Business\\_Japanese"``
        搜尋條件，例如 ``"tag:Business\\_Japanese"``
    """
    return f'"tag:{escape_search_value(tag)}"'


def deck_term(deck: str) -> str:
    """Build a term matching exactly this deck and its sub-decks; wildcards in the name match literally.

    只比對這個牌組（含子牌組）的條件，名稱中的萬用字元照字面比對。

    Args:
        deck: the full deck name, for example ``日本語::外語糾錯``
            牌組完整名稱，例如 ``日本語::外語糾錯``

    Returns:
        the search term, for example ``deck:"日本語\\:\\:外語糾錯"`` (the escaped ``::`` still matches sub-decks)
        搜尋條件，例如 ``deck:"日本語\\:\\:外語糾錯"``（跳脫後的 ``::`` 照常比對子牌組）
    """
    return f'deck:"{escape_search_value(deck)}"'


def note_type_term(note_type: str) -> str:
    """Build a term matching one note type.

    只比對這個筆記類型的條件。

    Args:
        note_type: the note type name, for example ``Expression_Master_Dark``
            筆記類型名稱，例如 ``Expression_Master_Dark``

    Returns:
        the search term, for example ``note:"Expression\\_Master\\_Dark"`` (``\\_`` matches an underscore
        literally; verified on the production Anki to give the same result as not escaping)
        搜尋條件，例如 ``note:"Expression\\_Master\\_Dark"``（``\\_`` 照字面比對底線；已在正式 Anki 驗證結果
        與不跳脫相同）
    """
    return f'note:"{escape_search_value(note_type)}"'


def note_types_query(note_types: list[str], deck: str | None = None) -> str:
    """Build a query for any of the note types, optionally limited to one deck.

    任一個筆記類型（可限定牌組）的查詢。

    Args:
        note_types: the note type names; must not be empty
            筆記類型名稱，不可為空
        deck: the full deck name; ``None`` or an empty string means any deck
            牌組完整名稱；``None`` 或空字串表示不限

    Returns:
        the query, for example ``deck:"日本語" (note:"A" OR note:"B")``
        查詢語句，例如 ``deck:"日本語" (note:"A" OR note:"B")``
    """
    types = " OR ".join(note_type_term(name) for name in note_types)
    return f"{deck_term(deck)} ({types})" if deck else f"({types})"
