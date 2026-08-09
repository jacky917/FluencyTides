"""
Telegram 訊息文字安全化工具。

Text-safety helpers for Telegram messages.

背景：Anki 欄位本身就是 HTML（`<div>`、`<br>`、furigana 的 `<ruby>`、樣式
`<span>` 等），而 Telegram 的 HTML parse mode 只接受
`<b>/<i>/<s>/<u>/<code>/<pre>/<a>` 等少數白名單標籤。把欄位原文直接插入訊息
會導致 `TelegramBadRequest`，訊息完全發不出去。

Background: Anki fields are themselves HTML (`<div>`, `<br>`, furigana
`<ruby>`, styling `<span>`, ...), while Telegram's HTML parse mode only
accepts a small whitelist (`<b>/<i>/<s>/<u>/<code>/<pre>/<a>`). Embedding a
raw field in a message raises `TelegramBadRequest` and the message is never
delivered.

設計決策：
- 處理順序固定為「去標籤 → 還原實體 → 截斷 → 轉義」。先截斷再轉義，可避免
  截斷點落在 `&amp;` 這類實體中間而產生半截實體；最後才轉義則保證輸出中不再
  存在任何未轉義的 `<`、`&`。
- 只做純文字化，不嘗試把 Anki HTML 對映到 Telegram 標籤——對映規則脆弱且
  容易再次產生非法標記。

Design decisions:
- The fixed order is strip tags → unescape entities → truncate → escape.
  Truncating before escaping avoids cutting through an entity such as
  `&amp;`; escaping last guarantees no unescaped `<` or `&` survives.
- It only produces plain text and never maps Anki HTML onto Telegram tags:
  such mappings are brittle and tend to reintroduce invalid markup.

對應修復：docs/15_Bug_Scan_Report.md 的 S010（同族問題 S014 亦適用本工具）。
Addresses finding S010 in docs/15_Bug_Scan_Report.md (the related S014 family
can reuse the same helper).
"""

import html
import re

# 匹配任意 HTML 標籤（含屬性），用於把 Anki 欄位純文字化
# Matches any HTML tag (including attributes) to plain-text an Anki field.
_TAG_RE = re.compile(r"<[^>]+>")

# Anki 常以 <br>/<div> 表示換行，純文字化時轉為空白避免字詞黏連
# Anki uses <br>/<div> for line breaks; convert them to spaces so words do
# not run together once the tags are stripped.
_BREAK_RE = re.compile(r"<\s*(br|/div|/p)\s*/?\s*>", re.IGNORECASE)


def anki_field_to_tg_text(raw: str, limit: int = 300) -> str:
    """把 Anki 欄位內容轉為可安全嵌入 Telegram HTML 訊息的純文字。

    Convert an Anki field value into plain text safe to embed in a
    Telegram HTML message.

    Args:
        raw: Anki 欄位的原始值（可能含任意 HTML）。The raw Anki field
            value, possibly containing arbitrary HTML.
        limit: 截斷長度上限，超過則附加省略號。Maximum length before
            truncation; an ellipsis is appended when exceeded.

    Returns:
        已去除標籤並完成 HTML 轉義的純文字。Plain text with tags removed
        and HTML escaped.
    """
    if not raw:
        return ""

    # 1. 換行類標籤轉空白，其餘標籤直接移除
    text = _BREAK_RE.sub(" ", raw)
    text = _TAG_RE.sub("", text)

    # 2. 還原 HTML 實體（Anki 會把 & 存成 &amp;），並壓縮多餘空白
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()

    # 3. 先截斷（此時尚未轉義，不會切斷實體）
    if len(text) > limit:
        text = text[:limit] + "..."

    # 4. 最後統一轉義，保證輸出不含未轉義的 < 與 &
    return html.escape(text)


def escape_tg(text: str, limit: int | None = None) -> str:
    """轉義任意純文字，使其可安全嵌入 Telegram HTML 訊息。

    Escape arbitrary plain text so it is safe inside a Telegram HTML
    message.

    適用於使用者輸入、card_id、單字等本來就不含 HTML 的字串。

    Suitable for user input, card IDs, vocabulary words and other strings
    that are not expected to contain HTML.

    Args:
        text: 待轉義的純文字。The plain text to escape.
        limit: 可選的截斷長度上限（先截斷再轉義）。Optional maximum
            length; truncation happens before escaping.

    Returns:
        轉義後的字串。The escaped string.
    """
    if not text:
        return ""
    if limit is not None and len(text) > limit:
        text = text[:limit] + "..."
    return html.escape(text)
