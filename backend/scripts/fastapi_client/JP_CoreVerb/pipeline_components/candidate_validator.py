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
REJECTION_COMPOUND_SUFFIX = "複合動詞後項"
REJECTION_AUXILIARY = "補助動詞"
REJECTION_LEMMA_MISMATCH = "lemma 不符"
REJECTION_READING_MISMATCH = "讀音不符"
REJECTION_COMPOUND_MEMBER = "屬其他複合動詞"


#: ``derive_target_lemmas`` 的快取：{目標動詞: ((lemma, pos1), ...)}。
#: 同一次執行對同一動詞會呼叫數百次，分詞結果不變故可快取。
#: 外層以 ``id(tagger)`` 分隔——不同分詞器對同一表層可能切得不同，
#: 共用一個 flat 快取會互相污染（測試間尤其明顯）。
_TARGET_LEMMA_CACHE: dict[int, dict[str, tuple[tuple[str, str, str], ...]]] = {}


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


def token_orth_base(token: Any) -> str:
    """取得 token 的書字形基本形（UniDic feature[10]，如帰る句的「帰る」）。

    Get the token's orthographic base form (UniDic ``feature[10]``).

    UniDic 的語彙素（lemma）會把同義異體字形統合到單一寫法
    （帰る→返る、治る→直る、刺す→差す-他動詞…），orthBase 則保留
    句中實際書寫的基本形——lemma 精確比對誤殺的字形變體靠它兜底。
    UniDic lemmas unify orthographic variants under one canonical spelling;
    orthBase keeps the actually-written base form and rescues variants the
    strict lemma comparison would reject.

    Args:
        token: fugashi node 或具 ``feature`` 可索引序列的假 token。A fugashi
            node or fake token with an indexable ``feature`` sequence.

    Returns:
        str: 書字形基本形；無法取得時回傳空字串。Orth base, or "".
    """
    feature = getattr(token, "feature", None)
    if feature is None:
        return ""
    try:
        orth_base = feature[10]
    except (IndexError, KeyError, TypeError):
        return ""
    if orth_base and orth_base != "*":
        return orth_base
    return ""


def lemma_matches_target(token: Any, target_verb: str) -> bool:
    """判定 token 是否對應目標動詞（lemma 或 orthBase 任一相符）。

    Decide whether the token corresponds to the target verb via lemma or
    orthBase.

    比對順序：
    1. lemma 全等，或去掉 UniDic 語彙素細分後綴（``差す-他動詞`` → 差す）
       後全等。
    2. orthBase（書字形基本形）全等——兜底 UniDic 字形統合
       （帰る lemma=返る，但 orthBase=帰る）。
    「による」（lemma=因る、orthBase=よる）對目標「撚る」兩關皆不符,
    仍會被正確拒絕。

    Args:
        token: fugashi node 或假 token。A fugashi node or fake token.
        target_verb: 目標動詞字典形（去標音）。Target verb dictionary form.

    Returns:
        bool: 是否視為目標動詞。Whether the token matches the target.
    """
    lemma = token_lemma(token)
    if lemma == target_verb or lemma.split("-", 1)[0] == target_verb:
        return True
    orth_base = token_orth_base(token)
    return bool(orth_base) and orth_base == target_verb


def derive_target_lemmas(
    target_verb: str, tagger: Callable[[str], Iterable[Any]]
) -> tuple[tuple[str, str, str], ...]:
    """把目標動詞本身丟給同一個分詞器，導出 ``((lemma, pos1), ...)`` 序列。

    Tokenize the target verb itself with the same tagger to derive its
    ``((lemma, pos1), ...)`` sequence.

    UniDic 把複合動詞與接尾辭派生切成多個 token，句中沒有任何**單一** token
    的 lemma 會等於「走り出す」，因此單 token 比對必然全滅（2026-09-03 實測
    16 個核心動詞如此）。孤立表層與句中活用形切出的 lemma 序列一致——
    ``走り出す`` → ``走る／出す``，句中 ``走り出した`` 亦為 ``走る／出す``——
    故可自動推導，無需人工設定：

    ==================  ==========================
    母卡表層            導出序列
    ==================  ==========================
    ``見る``            ``見る``（單 token，行為不變）
    ``走り出す``        ``走る`` → ``出す``
    ``恥ずかしがる``    ``恥ずかしい`` → ``がる``
    ``気に入る``        ``気`` → ``に`` → ``入る``
    ``知らせる``        ``知る`` → ``せる``
    ==================  ==========================

    結果按 ``target_verb`` 快取——同一次執行會對同一動詞呼叫數百次。
    Results are cached per target verb.

    Args:
        target_verb: 目標動詞字典形（去標音、去 ruby 分隔空白）。Target verb
            in canonical dictionary form.
        tagger: 與驗證候選句時同一個分詞器。The same tagger used for
            candidate sentences.

    Returns:
        tuple[tuple[str, str, str], ...]: ``((lemma, pos1, surface), ...)``；
        無法分詞時回傳單一元素。The derived sequence.
    """
    per_tagger = _TARGET_LEMMA_CACHE.setdefault(id(tagger), {})
    cached = per_tagger.get(target_verb)
    if cached is not None:
        return cached
    # 分詞失敗（假 tagger 未涵蓋該字串、分詞器異常）時退回單 token 序列——
    # 等同本擴充前的行為，寧可少擋也不讓整條管線中斷。
    # Fall back to a single-token sequence on any tagger failure: identical
    # to the pre-window behaviour, and a tagger fault must not abort the run.
    try:
        seq = tuple(
            (token_lemma(tok) or token_surface(tok), token_pos1(tok), token_surface(tok))
            for tok in tagger(target_verb)
        )
    except Exception:
        seq = ()
    if not seq:
        seq = ((target_verb, "動詞", target_verb),)
    per_tagger[target_verb] = seq
    return seq


def match_lemma_window(
    tokens: list, start: int, target_seq: tuple[tuple[str, str, str], ...]
) -> bool:
    """判定 ``tokens[start:start+len(target_seq)]`` 是否逐位對應目標序列。

    Whether the window starting at ``start`` matches the target sequence
    position by position.

    每個位置沿用 :func:`lemma_matches_target`（保留 lemma／orthBase／
    UniDic 語彙素細分後綴的容忍度），並要求**最後一個** token 的詞性與
    導出序列一致——活用發生在最後一個 token，其詞性因動詞而異
    （``出す`` 動詞／``せる`` 助動詞／``がる`` 接尾辞），故取自資料而非
    硬寫清單。
    The last token's part of speech must equal the derived one; it varies
    by verb, so it comes from the data rather than a hardcoded list.

    Args:
        tokens: 整句分詞結果。Sentence tokens.
        start: 視窗起始索引。Window start index.
        target_seq: :func:`derive_target_lemmas` 的結果。Derived sequence.

    Returns:
        bool: 是否整段相符。Whether the whole window matches.
    """
    end = start + len(target_seq)
    if end > len(tokens):
        return False
    for offset, (lemma, _pos, surface) in enumerate(target_seq):
        token = tokens[start + offset]
        if lemma_matches_target(token, lemma):
            continue
        # 非末位允許表層相符兜底：分詞器對孤立表層與句中的前項判定可能不同
        # （乗り遅れる：孤立 乗り lemma=乗り[名詞]，句中 lemma=乗る[動詞]，
        # 表層皆為「乗り」）。末位仍嚴格比 lemma + 詞性，視窗整體才不會鬆掉。
        # Non-final positions accept a surface match; the final token still
        # requires lemma + POS agreement.
        if offset < len(target_seq) - 1 and token_surface(token) == surface:
            continue
        return False
    return token_pos1(tokens[end - 1]) == target_seq[-1][1]


def covered_by_compound(
    tokens: list, index: int, compound_seqs: tuple[tuple[tuple[str, str, str], ...], ...]
) -> bool:
    """判定 ``tokens[index]`` 是否落在某個已註冊複合動詞的視窗內。

    Whether the token at ``index`` is part of a registered compound verb.

    ``気に入る`` 的第三個 token 就是獨立動詞 ``入る``，中間的「に」是助詞而非
    動詞，因此既有的複合動詞前／後項規則（只看緊鄰 token 的詞性）擋不住——
    ``入る`` 母卡會把「気に入った」的句子收走，而那些句子屬於 ``気に入る``。
    需要跨動詞的知識：本專案所有多 token 目標的序列。
    The neighbour-POS rules cannot catch this because the intervening token
    is a particle; cross-verb knowledge is required.

    Args:
        tokens: 整句分詞結果。Sentence tokens.
        index: 單 token 命中的位置。The matched single-token index.
        compound_seqs: 本專案全部多 token 目標的序列（``derive_target_lemmas``
            的結果，僅含 ``len > 1`` 者）。All multi-token target sequences.

    Returns:
        bool: 是否應讓給複合動詞。Whether a compound verb owns this token.
    """
    for seq in compound_seqs:
        width = len(seq)
        for start in range(max(0, index - width + 1), index + 1):
            if start + width > len(tokens):
                continue
            if match_lemma_window(tokens, start, seq):
                return True
    return False


def token_reading(token: Any) -> str:
    """取得 token 的語彙素読み（UniDic feature[6]，片假名，如「ウマル」）。

    Get the token's lexeme reading (UniDic ``feature[6]``, katakana).

    與 ``token_lemma`` 的 feature[7] 相鄰同源；unidic-lite 實測
    （2026-08-27）：埋まっ→ウマル、めくる→メクル、まくる→マクル、
    よっ→ヨル（lemma 因る）。取不到有效值時回傳空字串——
    呼叫端對空值**放行**讀音關（寧可漏擋不誤殺）。

    Args:
        token: fugashi node 或具 ``feature`` 可索引序列的假 token。A fugashi
            node or fake token with an indexable ``feature`` sequence.

    Returns:
        str: 片假名讀音；無法取得時回傳空字串。Katakana reading, or "".
    """
    feature = getattr(token, "feature", None)
    if feature is None:
        return ""
    try:
        reading = feature[6]
    except (IndexError, KeyError, TypeError):
        return ""
    if reading and reading != "*":
        return reading
    return ""


def to_katakana(text: str) -> str:
    """把字串中的平假名轉為片假名（讀音比對用的正規化）。

    Convert hiragana characters to katakana for reading comparison.

    Args:
        text: 任意字串。Any string.

    Returns:
        str: 平假名皆轉為片假名後的字串。The string with hiragana
        converted to katakana.
    """
    return "".join(
        chr(ord(ch) + 0x60) if "ぁ" <= ch <= "ゖ" else ch for ch in text or ""
    )


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
        span_token_index: 目標動詞**最後一個** token 在 ``tokens`` 中的索引
            （活用發生處；單 token 動詞即該 token 本身）。分桶的維度 B
            以此為基準。
        span_token_start: 目標動詞**第一個** token 的索引——複合動詞
            （走り＋出す）的搭配詞在它的前一個 token，維度 A 以此為基準；
            單 token 動詞時等於 ``span_token_index``。
        rejection_reason: 驗證通過時恆為 ``None``（保留欄位對齊計劃描述）。
    """

    sentence: str
    span: tuple[int, int]
    tokens: list = field(default_factory=list)
    span_token_index: int = -1
    span_token_start: int = -1
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
    *,
    expected_reading: str | None = None,
    allow_compound_suffix: bool = False,
    compound_seqs: tuple[tuple[tuple[str, str, str], ...], ...] = (),
) -> ValidationResult:
    """驗證候選句是否包含目標動詞的獨立用法（計劃 §6.1 規則 + VerbPair 擴充）。

    Validate that the sentence contains an independent usage of the target
    verb (plan §6.1 rules plus the VerbPair extensions).

    通過條件：句中存在一個 token 同時滿足——
        1. 詞性大類為「動詞」且 lemma 等於目標動詞（已去標音的字典形）。
        1'. 讀音驗證（``expected_reading`` 有值時）：token 的語彙素読み
            （feature[6]）與期待讀音片假名比對相符——擋同表層異讀
            （めくる[メクル] vs まくる[マクル] 的 lemma 同為捲る）。
            token 讀音缺值時放行本關（寧可漏擋不誤殺）。
        2. 緊鄰的下一 token 不是動詞（複合動詞前項拒絕：見＋送る）。
        2'. 緊鄰的前一 token 不是動詞（複合動詞後項拒絕：使い＋切れ、
            弾き＋まくる——連用形直接接續的後項非獨立用法）；
            ``allow_compound_suffix=True`` 時放行。
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
        expected_reading: 期待的動詞讀音（平/片假名皆可，如「うまる」）；
            ``None`` 或空字串時不驗讀音——CoreVerb 既有呼叫端不傳，
            行為完全回歸。Expected verb reading (hira/katakana); reading
            validation is skipped when None/empty, keeping legacy callers
            unchanged.
        allow_compound_suffix: 是否放行複合動詞後項用法（per-verb 設定）。
            Whether to allow compound-suffix usage (per-verb setting).
        compound_seqs: 本專案全部多 token 目標動詞的序列；單 token 目標命中時，
            若該位置落在其中任一視窗內即拒絕（``気に入る`` 的「入る」不該被
            ``入る`` 母卡收走）。空 tuple 時不做此檢查，既有呼叫端行為不變。
            All multi-token target sequences of the project.

    Returns:
        ValidationResult: ``accepted=True`` 時附帶 ``VerifiedCandidate``
        （含 span 與 tokens）；否則附帶拒絕原因。多個 lemma 命中 token 中
        只要有一個通過即整句通過；全數被拒時回報第一個被拒 token 的原因。
        Carries a ``VerifiedCandidate`` on acceptance, otherwise the first
        rejected token's reason.
    """
    # 目標動詞的 lemma 序列：單 token（見る）走既有路徑，多 token
    # （走り出す → 走る／出す）以視窗比對，詳見 derive_target_lemmas。
    # **必須在對句子分詞之前推導**——fugashi 的 node 物件綁在 tagger 內部的
    # lattice 上，再次呼叫 tagger 會覆寫先前 node 指向的記憶體，句子 tokens
    # 會靜默變成目標動詞的 tokens（2026-09-03 實測：誤判 6 個動詞、誤收 2 例）。
    # Derive before tokenizing the sentence: fugashi nodes point into the
    # tagger's lattice, which a second call overwrites.
    target_seq = derive_target_lemmas(target_verb, tagger)
    width = len(target_seq)
    is_compound = width > 1

    tokens = list(tagger(sentence))
    offsets = _compute_char_offsets(sentence, tokens)

    expected_kata = to_katakana(expected_reading) if expected_reading else ""

    # 讓位檢查用的複合序列：排除「目標自己的序列」，否則以單 token 命中的
    # 動詞會被自己的序列擋掉（無くなる 孤立切兩 token、句中卻是一個 token）。
    other_compounds = tuple(seq for seq in compound_seqs if seq != target_seq)

    rejection_reasons: list[str] = []
    for index, token in enumerate(tokens):
        matched_single = (
            token_pos1(token) == "動詞" and lemma_matches_target(token, target_verb)
        )
        if matched_single:
            # 單 token 路徑（既有行為）：分詞器把整個動詞切成一個 token 時走這裡。
            # 即使目標孤立分詞是多 token 也要嘗試——``無くなる`` 孤立切成
            # 無く[ない]＋なる[成る]，句中卻是單一 token（lemma 無くなる），
            # 只走視窗會漏掉（2026-09-03 實測 VerbPair 少一張）。
            # Always try the single-token rule: the tagger may emit the whole
            # verb as one token even when the isolated form splits.
            if other_compounds and covered_by_compound(tokens, index, other_compounds):
                # 讓位給更長的複合動詞（気に入る 覆蓋 入る）——中間夾非動詞
                # token 時，緊鄰詞性規則擋不到，需要跨動詞的序列清單。
                rejection_reasons.append(REJECTION_COMPOUND_MEMBER)
                continue
            start = last = index
        elif is_compound and match_lemma_window(tokens, index, target_seq):
            # 視窗路徑：逐位對應導出序列；index 為視窗**起點**
            start, last = index, index + width - 1
        else:
            continue

        # 規則 ①'：讀音驗證——同表層異讀（メクル vs マクル）靠語彙素読み區分。
        # token 讀音缺值（lForm 為 * 或欄位不存在）時放行，避免誤殺。
        # 視窗命中（跨多個 token）時不驗：歧義已由 lemma 序列本身消除。
        if expected_kata and start == last:
            actual = token_reading(token)
            if actual and to_katakana(actual) != expected_kata:
                rejection_reasons.append(REJECTION_READING_MISMATCH)
                continue

        # 規則 ②：複合動詞前項拒絕（視窗後一 token 是動詞 → 見＋送る）。
        # 複合動詞的內部後項已在視窗內，看的是視窗**之後**那個 token。
        if last + 1 < len(tokens) and token_pos1(tokens[last + 1]) == "動詞":
            rejection_reasons.append(REJECTION_COMPOUND_VERB)
            continue

        # 規則 ②'：複合動詞後項拒絕（視窗前一 token 是動詞 → 使い＋切れ、
        # 弾き＋まくる）。連用形直接接續的後項在自他動詞教學語境下
        # 必然不是獨立用法；per-verb 可以 allow_compound_suffix 放行。
        if (
            not allow_compound_suffix
            and start > 0
            and token_pos1(tokens[start - 1]) == "動詞"
        ):
            rejection_reasons.append(REJECTION_COMPOUND_SUFFIX)
            continue

        # 規則 ③：補助動詞拒絕——視窗前一 token 是「て／で」且再前一 token 為
        # 動詞或助動詞（食べ[動詞]＋て＋みる、食べ＋ない[助動詞]＋で＋みる
        # → 補助用法拒絕；あと[名詞]＋で＋見る 的「で」是格助詞 → 放行。
        # 注意：形容詞刻意不納入——「悲しくて見ていられない」的「見」是
        # 本動詞，補助動詞「みる」只接動詞て形／ないで，納入形容詞會誤殺）
        if (
            not allow_auxiliary
            and start > 1
            and token_surface(tokens[start - 1]) in ("て", "で")
            and token_pos1(tokens[start - 2]) in ("動詞", "助動詞")
        ):
            rejection_reasons.append(REJECTION_AUXILIARY)
            continue

        # 規則 ④：通過——span 涵蓋整個視窗（走り出し 而非只有 出し），
        # 隨候選句傳遞下游供分桶與後端挖空交叉驗證
        return ValidationResult(
            accepted=True,
            reason=None,
            candidate=VerifiedCandidate(
                sentence=sentence,
                span=(offsets[start][0], offsets[last][1]),
                tokens=tokens,
                span_token_index=last,
                span_token_start=start,
            ),
        )

    reason = rejection_reasons[0] if rejection_reasons else REJECTION_LEMMA_MISMATCH
    return ValidationResult(accepted=False, reason=reason, candidate=None)
