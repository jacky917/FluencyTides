"""純呻吟句偵測（兩條生卡管線共用）。

Pure-moan sentence detection, shared by both generation pipelines.

R18 語料裡有大量「擬態音節堆疊」的台詞——動詞用法本身合法，但教學價值
近乎零（整句只有喘息與擬聲）。這類句子在 ES 檢索與 token 驗證都會通過，
必須靠字面樣式擋下。
Such lines pass both ES retrieval and token validation, so they must be
caught by surface pattern.

判定採**雙條件**（兩者皆中才過濾），把誤殺率壓到零：

- ``_MOAN_HINT``：伏字／音符記號，或小假名・促音三連以上，或擬態音節
  三連以上——「像呻吟」的訊號。
- ``_MOAN_DENSITY``：擬態音節與感嘆符號連續 12 字以上——「整句都是呻吟」
  的密度特徵。

只帶「♪」等單一記號的正常句只中 HINT、不中 DENSITY，會被放行
（2026-08-27 抽驗誤殺率為零，見
docs/archive/verbpair_fugashi_validation_FEAT_2026-08-27.md）。
"""

import re

# 「像呻吟」的訊號：伏字/音符、小假名或促音三連、擬態音節三連
_MOAN_HINT = re.compile(r'[●♪]|[ぁぃぅぇぉゃゅょっ]{3,}|(?:ちゅ|じゅる|れろ|ぷち|んん|はぁ){3,}')
# 「整句都是呻吟」的密度：擬態音節與感嘆符號連續 12 字以上
_MOAN_DENSITY = re.compile(r'(?:[ぁぃぅぇぉゃゅょっんあはぅ、…！ッ]|ぢゅ|ちゅ|れろ|じゅ){12,}')

# 拒絕原因標籤（進 rejection_stats / filter_drops 報告）
REJECTION_MOAN = "呻吟句樣式"


def is_moan_sentence(text: str) -> bool:
    """判定句子是否為「純呻吟句」（擬態音節密度過高的 R18 台詞）。

    Detect "pure moan" sentences (R18 lines dominated by onomatopoeic
    syllables) — the verb usage in them is usually valid but the teaching
    value is near zero.

    Args:
        text: 已去除注音標記的句子。Sentence with furigana stripped.

    Returns:
        bool: True 表示應過濾。True when the sentence should be filtered.
    """
    return bool(_MOAN_HINT.search(text) and _MOAN_DENSITY.search(text))
