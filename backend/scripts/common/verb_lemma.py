"""動詞原型的正規表記（generated_sentences_log.verb_lemma 的單一規則）。

Canonical verb-lemma spelling: the single rule behind
generated_sentences_log.verb_lemma.

``verb_lemma`` 的語意是「母卡標準表層去標音」（``纏[まと]める`` →
``纏める``），**不是**命中的搜尋關鍵字（假名擴展 ``まとめる`` 等另存
``search_keyword``）。所有寫入 DB 的路徑（生成管線、刪卡工具鏈的完整性
修復、存量修復腳本）都應經過這裡，否則同一句會因拼寫不同而被重複生成
（docs/archive/dedup_canonical_lemma_FIX_2026-09-02.md §2 R1/R2）。

``verb_lemma`` means "the master card's standard surface with furigana
stripped"; never the matched search keyword. Every DB writer must go
through this helper, or the same sentence gets regenerated under a
different spelling.

JP_CoreVerb 漏斗內的 ``funnel.strip_furigana`` 是同一條規則的另一份實作
（該模組另有自己的鍵表記契約），本模組不取代它。
"""

import re

# ``見[み]る`` 的標音括號。Furigana brackets as in ``見[み]る``.
_FURIGANA_PATTERN = re.compile(r"\[.*?\]")


def canonical_verb_lemma(surface: str) -> str:
    """把母卡表層轉成 DB 用的正規表記：去標音括號、去前後空白。

    Turn a master-card surface into the canonical DB spelling: strip
    furigana brackets and surrounding whitespace.

    Args:
        surface: 母卡欄位或 Verb_Pair_JSON 中的表層，可含標音。Surface
            text from the master field or Verb_Pair_JSON, possibly with
            furigana.

    Returns:
        str: 正規表記；輸入為空時回傳空字串。Canonical spelling, or ""
        for empty input.
    """
    return _FURIGANA_PATTERN.sub("", surface or "").strip()
