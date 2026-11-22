"""The short title of a trilingual speaking card, which is also the name of its child deck.

三語對話卡的短標題，也是它同名子牌組的名稱。

A card with a ``Title`` whose deck has a child deck of the same name owns the cards in that deck (the topic
tree, ``docs/wip/trilingual_topic_tree_FEAT_2026-11-22.md`` D1, D2). The title is therefore used as one segment of
an Anki deck name: it must not contain ``::`` (Anki's deck separator) or ``"`` (deck names are quoted in search
queries), and Anki drops leading and trailing spaces of each segment, so they are stripped here as well.

有 ``Title`` 的卡，若所在牌組底下有同名子牌組，那個子牌組裡的卡就是它的子卡（主題樹，
``docs/wip/trilingual_topic_tree_FEAT_2026-11-22.md`` D1、D2）。所以標題會成為 Anki 牌組名的一段：不能含 ``::``
（Anki 的牌組分隔符號）或 ``"``（搜尋時牌組名要用引號包起來）；Anki 會去掉每段前後的空白，這裡也一樣去掉。

The write side (creating a card) rejects invalid titles; the read side (the graph) only strips what Anki stores
and treats a title it cannot use as missing.

寫入端（建卡）拒絕不合格的標題；讀取端（圖譜）只清掉 Anki 存的多餘內容，用不了的標題當成沒有標題。
"""

from __future__ import annotations

#: The Anki field holding the title.
#: 存標題的 Anki 欄位。
TITLE_FIELD = "Title"

#: The maximum title length in characters.
#: 標題的字數上限。
TITLE_MAX_LENGTH = 40

# 不能出現在標題裡的字串（牌組分隔符號、搜尋用的引號）
_FORBIDDEN = ("::", '"')


def normalize_title(value: str) -> str:
    """Return the title with surrounding whitespace removed, or raise if it cannot name a deck.

    去掉前後空白後回傳標題；不能當牌組名時拋錯。

    Args:
        value: the raw title
            原始標題

    Returns:
        the normalized title
        正規化後的標題

    Raises:
        ValueError: if the title is blank, longer than :data:`TITLE_MAX_LENGTH`, contains ``::`` or ``"``, or
            contains a line break
            若標題空白、超過 :data:`TITLE_MAX_LENGTH` 字、含 ``::`` 或 ``"``，或含換行
    """
    title = value.strip()
    if not title:
        raise ValueError("標題不能空白")
    if len(title) > TITLE_MAX_LENGTH:
        raise ValueError(f"標題最多 {TITLE_MAX_LENGTH} 字（現在 {len(title)} 字）")
    for forbidden in _FORBIDDEN:
        if forbidden in title:
            raise ValueError(f"標題不能含 {forbidden}（標題也是子牌組的名稱）")
    if "\n" in title or "\r" in title:
        raise ValueError("標題不能換行")
    return title


def usable_title(value: str) -> str | None:
    """Return the title read from Anki if it can name a child deck, otherwise ``None``.

    從 Anki 讀到的標題能當子牌組名時回傳它，否則回傳 ``None``。

    Unlike :func:`normalize_title` this never raises: a title edited by hand in Anki that breaks the rules only
    means the card has no child deck.

    和 :func:`normalize_title` 不同，這裡不拋錯：在 Anki 手動改成不合規則的標題，只代表這張卡沒有子牌組。

    Args:
        value: the field value, HTML already removed
            欄位值（已去掉 HTML）

    Returns:
        the normalized title, or ``None`` if it is blank or unusable
        正規化後的標題；空白或不能用時為 ``None``
    """
    try:
        return normalize_title(value)
    except ValueError:
        return None
