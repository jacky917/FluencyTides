"""候選句 token 級驗證器（計劃 §6.1）。

Token-level candidate validator (plan §6.1): uses an injected fugashi
tagger to verify that a sentence truly contains the target verb as an
independent lexeme, rejecting compound-verb prefixes and auxiliary usages.

在 ES 檢索之後、多樣性選句之前，以 fugashi（MeCab / UniDic）逐句驗證候選句
是否真的包含「以目標動詞為獨立語彙素」的用法，兜底以下兩類污染：

1. 複合動詞前項：短單位切分把「見送る」切成「見＋送る」——目標 token 的
   下一 token 若仍是動詞即拒絕。
2. 補助動詞用法：「食べてみる」的「みる」——目標 token 的前一 token 若為
   「て／で」即拒絕（per-verb 可以 ``allow_auxiliary=True`` 放行）。

設計要點：
    - tagger 由呼叫端注入（``fugashi.Tagger()`` 或測試用假 tagger），
      本模組不 import fugashi——單元測試可用假 token 物件完整覆蓋。
    - 驗證通過的分詞結果（tokens 與 span_token_index）隨
      ``VerifiedCandidate`` 傳遞下游，供 §6.3 分桶直接復用，零重複分詞成本。
    - 拒絕時回傳帶 ``reason`` 的 ``ValidationResult``，漏斗層據此統計
      拒絕原因分佈（複合動詞前項 / 補助動詞 / lemma 不符）。

token 介面約定（鴨子型別，fugashi UniDic node 與測試假 token 皆滿足）：
    - ``token.surface``：表層字串。
    - ``token.feature``：可索引序列；``feature[0]`` 為詞性大類（如「動詞」），
      ``feature[7]`` 為語彙素（lemma），取法對齊 ``build_nlp_index.py``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

# 拒絕原因常數（漏斗層統計用，字面對齊計劃 §6.7 報告項）
REJECTION_COMPOUND_VERB = "複合動詞前項"
REJECTION_AUXILIARY = "補助動詞"
REJECTION_LEMMA_MISMATCH = "lemma 不符"


def token_surface(token: Any) -> str:
    """取得 token 的表層字串。

    Get the token's surface string.

    Args:
        token: fugashi node 或具 ``surface`` 屬性的假 token。A fugashi node
            or fake token exposing ``surface``.

    Returns:
        str: 表層字串；無法取得時回傳空字串。Surface string, or "" if absent.
    """
    return getattr(token, "surface", "") or ""


def token_pos1(token: Any) -> str:
    """取得 token 的詞性大類（UniDic feature[0]，如「動詞」「助詞」）。

    Get the token's coarse POS category (UniDic ``feature[0]``).

    Args:
        token: fugashi node 或具 ``feature`` 可索引序列的假 token。A fugashi
            node or fake token with an indexable ``feature`` sequence.

    Returns:
        str: 詞性大類；無法取得時回傳空字串。POS category, or "" if absent.
    """
    feature = getattr(token, "feature", None)
    if feature is None:
        return ""
    try:
        value = feature[0]
    except (IndexError, KeyError, TypeError):
        return ""
    return value or ""


def token_lemma(token: Any) -> str:
    """取得 token 的語彙素（lemma，UniDic feature[7]）。

    Get the token's lemma (UniDic ``feature[7]``), falling back to the
    surface string when unavailable.

    取法對齊 ``scripts/database/MySQL/JP_VerbPair/build_nlp_index.py`` 的
    ``node.feature[7]``；解析失敗（``*`` 或缺欄）時退回表層字串。

    Args:
        token: fugashi node 或具 ``feature`` 可索引序列的假 token。A fugashi
            node or fake token with an indexable ``feature`` sequence.

    Returns:
        str: 語彙素字串；取不到有效值時回傳表層字串。Lemma string, or the
        surface string when no valid lemma is available.
    """
    feature = getattr(token, "feature", None)
    if feature is not None:
        try:
            lemma = feature[7]
        except (IndexError, KeyError, TypeError):
            lemma = None
        if lemma and lemma != "*":
            return lemma
    return token_surface(token)


@dataclass
class VerifiedCandidate:
    """通過驗證的候選句，攜帶下游分桶與挖空定位所需的全部資訊。

    A validated candidate sentence carrying everything downstream bucketing
    and cloze positioning need.

    Attributes:
        sentence: 候選句原文（已去除注音標記的乾淨字串）。
        span: 目標動詞 token 在 ``sentence`` 中的字元區間 ``(start, end)``，
            隨 payload 傳給後端做挖空交叉驗證（``target_verb_span``）。
        tokens: 整句的分詞結果，供 §6.3 分桶復用。
        span_token_index: 目標動詞 token 在 ``tokens`` 中的索引。
        rejection_reason: 驗證通過時恆為 ``None``（保留欄位對齊計劃描述）。
    """

    sentence: str
    span: tuple[int, int]
    tokens: list = field(default_factory=list)
    span_token_index: int = -1
    rejection_reason: str | None = None


@dataclass
class ValidationResult:
    """單句驗證結果。

    Validation result for one sentence.

    Attributes:
        accepted: 是否通過驗證。
        reason: 未通過時的拒絕原因（``REJECTION_*`` 常數之一）；通過時為 ``None``。
        candidate: 通過時的 ``VerifiedCandidate``；未通過時為 ``None``。
    """

    accepted: bool
    reason: str | None
    candidate: VerifiedCandidate | None


def _compute_char_offsets(sentence: str, tokens: list) -> list[tuple[int, int]]:
    """以游標式搜尋累加計算每個 token 在原句中的字元偏移。

    Compute each token's character offsets in the sentence via cursor-based
    search, since MeCab drops whitespace and other non-token characters.

    MeCab 會吃掉空白等非 token 字元，因此不能單純以 surface 長度累加；
    改以 ``str.find(surface, cursor)`` 逐一定位，找不到時退化為緊接游標。

    Args:
        sentence: 原句。The original sentence.
        tokens: 分詞結果。The tokenization result.

    Returns:
        list[tuple[int, int]]: 與 ``tokens`` 等長的 ``(start, end)`` 清單。
        A ``(start, end)`` list parallel to ``tokens``.
    """
    offsets: list[tuple[int, int]] = []
    cursor = 0
    for token in tokens:
        surface = token_surface(token)
        start = sentence.find(surface, cursor) if surface else cursor
        if start == -1:
            start = cursor
        end = start + len(surface)
        offsets.append((start, end))
        cursor = end
    return offsets


def validate_candidate(
    sentence: str,
    target_verb: str,
    allow_auxiliary: bool,
    tagger: Callable[[str], Iterable[Any]],
) -> ValidationResult:
    """驗證候選句是否包含目標動詞的獨立用法（計劃 §6.1 四條規則）。

    Validate that the sentence contains an independent usage of the target
    verb (the four rules of plan §6.1).

    通過條件：句中存在一個 token 同時滿足——
        1. 詞性大類為「動詞」且 lemma 等於目標動詞（已去標音的字典形）。
        2. 緊鄰的下一 token 不是動詞（複合動詞前項拒絕：見＋送る）。
        3. 補助動詞拒絕：前一 token 為「て／で」**且再前一 token 為動詞**
           時拒絕（食べ[動詞]てみる → 拒；あと[名詞]で見る → 放行——
           格助詞「で」不構成補助動詞接續）；``allow_auxiliary=True`` 時放行。

    Args:
        sentence: 候選句（呼叫端應先去除注音標記 ``[...]``）。Candidate
            sentence with furigana already stripped by the caller.
        target_verb: 目標動詞字典形（去標音，如「見る」）。Target verb in
            dictionary form, furigana stripped.
        allow_auxiliary: 是否放行補助動詞用法（per-verb 設定）。Whether to
            allow auxiliary-verb usage (per-verb setting).
        tagger: 注入的分詞器——以句子呼叫後回傳 token 可迭代物
            （``fugashi.Tagger()`` 實例或測試假 tagger）。Injected tokenizer
            returning an iterable of tokens when called with a sentence.

    Returns:
        ValidationResult: ``accepted=True`` 時附帶 ``VerifiedCandidate``
        （含 span 與 tokens）；否則附帶拒絕原因。多個 lemma 命中 token 中
        只要有一個通過即整句通過；全數被拒時回報第一個被拒 token 的原因。
        Carries a ``VerifiedCandidate`` on acceptance, otherwise the first
        rejected token's reason.
    """
    tokens = list(tagger(sentence))
    offsets = _compute_char_offsets(sentence, tokens)

    rejection_reasons: list[str] = []
    for index, token in enumerate(tokens):
        if token_pos1(token) != "動詞":
            continue
        if token_lemma(token) != target_verb:
            continue

        # 規則 ②：複合動詞前項拒絕（下一 token 是動詞 → 見＋送る）
        if index + 1 < len(tokens) and token_pos1(tokens[index + 1]) == "動詞":
            rejection_reasons.append(REJECTION_COMPOUND_VERB)
            continue

        # 規則 ③：補助動詞拒絕——前一 token 是「て／で」且再前一 token 為
        # 動詞或助動詞（食べ[動詞]＋て＋みる、食べ＋ない[助動詞]＋で＋みる
        # → 補助用法拒絕；あと[名詞]＋で＋見る 的「で」是格助詞 → 放行。
        # 注意：形容詞刻意不納入——「悲しくて見ていられない」的「見」是
        # 本動詞，補助動詞「みる」只接動詞て形／ないで，納入形容詞會誤殺）
        if (
            not allow_auxiliary
            and index > 1
            and token_surface(tokens[index - 1]) in ("て", "で")
            and token_pos1(tokens[index - 2]) in ("動詞", "助動詞")
        ):
            rejection_reasons.append(REJECTION_AUXILIARY)
            continue

        # 規則 ④：通過——記錄字元 span 與分詞結果，隨候選句傳遞下游
        return ValidationResult(
            accepted=True,
            reason=None,
            candidate=VerifiedCandidate(
                sentence=sentence,
                span=offsets[index],
                tokens=tokens,
                span_token_index=index,
            ),
        )

    reason = rejection_reasons[0] if rejection_reasons else REJECTION_LEMMA_MISMATCH
    return ValidationResult(accepted=False, reason=reason, candidate=None)
