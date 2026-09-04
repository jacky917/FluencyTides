"""動詞原型的正規表記（generated_sentences_log.verb_lemma 的單一規則）。

Canonical verb-lemma spelling: the single rule behind
generated_sentences_log.verb_lemma.

``verb_lemma`` 的語意是「母卡標準表層去標音」（``纏[まと]める`` →
``纏める``），**不是**命中的搜尋關鍵字（假名擴展 ``まとめる`` 等只用於
ES 檢索，不落 DB）。所有寫入 DB 的路徑（生成管線、刪卡工具鏈的完整性
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
# Anki 標音的分隔空白：``聞[き]き 返[かえ]す`` 的空格是讓 Anki 知道下一個
# ruby 從哪個漢字起算的必要分隔符，去掉括號後必須連空白一起清掉——日文
# 動詞不含內部空格，殘留空白會讓 lemma 對不上 ES 與 UniDic
# （2026-09-03 實測：95/344 個核心動詞因此完全生不出卡）。
# The space in Anki furigana notation is a ruby separator, not part of the
# verb; leaving it in makes the lemma unmatchable in ES and UniDic.
_WHITESPACE = re.compile(r"\s+")


def is_non_canonical_lemma(
    lemma: str, master_note_id: int | str, keyword_map: dict[str, dict[str, str]]
) -> bool:
    """判斷某筆紀錄的 ``verb_lemma`` 是否仍為非正規拼寫（帶標音、或是**該母卡**的擴展關鍵字）。

    Whether a row's ``verb_lemma`` is still non-canonical: carries furigana,
    or equals one of **that master card's** extra search keywords.

    必須按母卡判斷，不能用全域關鍵字集合——同一個字串可以同時是 A 母卡的
    標準表層與 B 母卡的擴展關鍵字（例：``汚す`` 是 ``汚[よご]す`` 的表層，
    也是 ``穢す`` 的關鍵字），全域集合會把 A 的正規紀錄誤判為非正規。
    The check must be per master: the same string can be master A's
    canonical surface and master B's search keyword at once.

    Args:
        lemma: 紀錄的 verb_lemma。The row's verb_lemma.
        master_note_id: 紀錄所屬母卡。The row's master note id.
        keyword_map: ``{母卡 nid: {關鍵字: 標準表層}}``（見
            ``canonicalize_verb_lemma.load_keyword_map``）。Per-master
            keyword → canonical surface map.

    Returns:
        bool: True 表示非正規、需先跑存量修復。True when non-canonical.
    """
    if "[" in (lemma or ""):
        return True
    return lemma in keyword_map.get(str(master_note_id), {})


def canonical_verb_lemma(surface: str) -> str:
    """把母卡表層轉成 DB 用的正規表記：去標音括號、去全部空白。

    Turn a master-card surface into the canonical DB spelling: strip
    furigana brackets and all whitespace.

    ``見[み]る`` → ``見る``；``聞[き]き 返[かえ]す`` → ``聞き返す``（Anki 的
    ruby 分隔空白不屬於動詞本身，見模組頂部說明）。
    The ruby separator space is not part of the verb.

    Args:
        surface: 母卡欄位或 Verb_Pair_JSON 中的表層，可含標音。Surface
            text from the master field or Verb_Pair_JSON, possibly with
            furigana.

    Returns:
        str: 正規表記；輸入為空時回傳空字串。Canonical spelling, or ""
        for empty input.
    """
    return _WHITESPACE.sub("", _FURIGANA_PATTERN.sub("", surface or ""))
