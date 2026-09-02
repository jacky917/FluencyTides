"""句子文字層去重用的正規化（同文異 id 台詞的識別）。

Sentence normalization for text-level deduplication (recognizing the
same line under different script ids).

VN 語料裡同一句台詞常以不同 ``script_id`` 重複出現（分支、回想、重複
場景），只差標點或全半形。去重鍵按 ``script_id`` 看不出來，因此在
生成前把候選句正規化後與該動詞已記錄的台詞比對
（docs/archive/dedup_canonical_lemma_FIX_2026-09-02.md §3.2）。

規則刻意保守——只抹平**書寫層**差異（標點、空白、全半形、標音、HTML
標籤），**不**做假名／漢字等價或語意層等價，那不是資料層該猜的事。
The rules deliberately flatten only orthographic noise; kana/kanji or
semantic equivalence is out of scope here.
"""

import html
import re
import unicodedata

_FURIGANA = re.compile(r"\[.*?\]")
_HTML_TAG = re.compile(r"<[^>]+>")


def normalize_sentence(text: str) -> str:
    """把台詞正規化成可直接比對的鍵。

    Normalize a dialogue line into a directly comparable key.

    步驟：HTML 反轉義 → 去標籤 → 去標音括號 → NFKC（全半形、組合字）→
    只保留 Unicode 類別為字母（L*）與數字（N*）的字元。長音「ー」與
    疊字「々」屬 Lm，自然保留；標點、空白、符號、表情符號全部去除。

    Args:
        text: 原始台詞（可含 HTML、標音、任意標點）。Raw dialogue text.

    Returns:
        str: 正規化鍵；輸入為空時回傳空字串。Normalized key, or "" for
        empty input.
    """
    if not text:
        return ""
    s = html.unescape(text)
    s = _HTML_TAG.sub("", s)
    s = _FURIGANA.sub("", s)
    s = unicodedata.normalize("NFKC", s)
    return "".join(ch for ch in s if unicodedata.category(ch)[0] in ("L", "N"))
