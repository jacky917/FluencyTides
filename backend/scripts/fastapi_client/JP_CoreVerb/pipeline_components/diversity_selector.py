"""兩維度分桶與多樣性配額分配器（計劃 §6.3-6.5）。

Two-dimension bucketing and diversity quota allocator (plan §6.3-6.5):
classifies candidates by collocation and conjugation, then allocates the
small quota to maximize coverage of the verb's usage space.

核心動詞命中量巨大（數百至上千句）而配額僅約 15 張，本模組負責在配額內
最大化覆蓋動詞的變化空間：

- 維度 A（搭配桶，語意代理）：``classify_collocation``——取目標 token 前方
  token 組桶鍵（名詞＋助詞 / 副詞・形容詞修飾 / （無助詞））。
- 維度 B（活用形桶）：``classify_conjugation``——計劃 §6.3 的 14 桶分類表，
  順序敏感、先長後短。
- 配額分配：``select_diverse``——優先搭配席位（per-verb
  ``priority_collocations`` 必收）＋ Pass 1 搭配保底（≥2 次的慣用桶
  zigzag 取桶，一次性桶降級殿後）＋ Pass 2 活用形補洞，
  ``max_per_chapter`` 硬約束全程生效，並支援 §6.5 增量平衡
  （已生成句的桶佔用注入）。

全部為純函數：不觸網、不讀設定、不依賴 fugashi（token 以鴨子型別存取，
介面約定見 ``candidate_validator`` 模組 docstring）。分桶誤判只影響
多樣性品質、不影響卡片正確性——降級安全。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from scripts.fastapi_client.JP_CoreVerb.pipeline_components.candidate_validator import (
    token_lemma,
    token_pos1,
    token_surface,
)

# --- 桶鍵常數 -------------------------------------------------------------

NO_PARTICLE_BUCKET = "（無助詞）"

CONJ_CAUSATIVE = "使役"
CONJ_PASSIVE_POTENTIAL = "受身/可能"
CONJ_PROGRESSIVE = "進行/補助連結"
CONJ_TE = "て形"
CONJ_TA = "た形"
CONJ_TARI = "たり形"
CONJ_NAI = "ない形"
CONJ_MASU = "ます系"
CONJ_CONDITIONAL = "条件"
CONJ_VOLITIONAL = "意向"
CONJ_TAI = "たい形"
CONJ_PURPOSE_NI = "目的の「に」"
CONJ_DICTIONARY = "辞書形/連体"
CONJ_OTHER = "その他"

#: 全部活用形桶（計劃 §6.3 分類表順序），供報告的未覆蓋清單使用。
ALL_CONJUGATION_BUCKETS = [
    CONJ_CAUSATIVE,
    CONJ_PASSIVE_POTENTIAL,
    CONJ_PROGRESSIVE,
    CONJ_TE,
    CONJ_TA,
    CONJ_TARI,
    CONJ_NAI,
    CONJ_MASU,
    CONJ_CONDITIONAL,
    CONJ_VOLITIONAL,
    CONJ_TAI,
    CONJ_PURPOSE_NI,
    CONJ_DICTIONARY,
    CONJ_OTHER,
]

#: tie-break 用的「句長適中」錨點（字元數）；離此值越近視為越適中。
_IDEAL_SENTENCE_LENGTH = 20

#: 「目的の「に」」桶認定的移動動詞 lemma。
_MOTION_VERB_LEMMAS = {"行く", "来る", "いく", "くる", "参る", "戻る", "帰る"}

#: 「進行/補助連結」桶認定的存在系補助動詞 lemma（て／で 之後）。
_PROGRESSIVE_AUX_LEMMAS = {"いる", "居る", "てる", "おる"}

#: 長単位 tokenizer 退路：以目標 token 表層做的順序敏感（先長後短）後綴表。
_SURFACE_SUFFIX_FALLBACK: list[tuple[str, str]] = [
    ("させられ", CONJ_PASSIVE_POTENTIAL),
    ("ましょう", CONJ_VOLITIONAL),
    ("たかっ", CONJ_TAI),
    ("ている", CONJ_PROGRESSIVE),
    ("でいる", CONJ_PROGRESSIVE),
    ("てる", CONJ_PROGRESSIVE),
    ("させ", CONJ_CAUSATIVE),
    ("られ", CONJ_PASSIVE_POTENTIAL),
    ("たい", CONJ_TAI),
    ("ます", CONJ_MASU),
    ("ました", CONJ_MASU),
    ("ません", CONJ_MASU),
    ("ない", CONJ_NAI),
    ("なかっ", CONJ_NAI),
    ("たら", CONJ_CONDITIONAL),
    ("だら", CONJ_CONDITIONAL),
    ("れば", CONJ_CONDITIONAL),
    ("よう", CONJ_VOLITIONAL),
    ("たり", CONJ_TARI),
    ("だり", CONJ_TARI),
    ("て", CONJ_TE),
    ("で", CONJ_TE),
    ("た", CONJ_TA),
    ("だ", CONJ_TA),
    ("ず", CONJ_NAI),
    ("ぬ", CONJ_NAI),
]


# --- 資料結構 -------------------------------------------------------------


@dataclass(frozen=True)
class SelectionCandidate:
    """進入配額分配階段的候選句（已通過驗證與分桶）。

    A validated, bucketed candidate entering the quota-allocation stage.

    Attributes:
        script_id: 台詞在 MySQL 的原始主鍵。
        sentence: 候選句原文（乾淨字串）。
        span: 目標動詞的字元區間 ``(start, end)``。
        collocation: 搭配桶鍵（維度 A）。
        conjugation: 活用形桶鍵（維度 B）。
        chapter: 章節（tie-break 與 ``max_per_chapter`` 硬約束用）。
        speaker: 說話者（tie-break 用）。
    """

    script_id: int
    sentence: str
    span: tuple[int, int]
    collocation: str
    conjugation: str
    chapter: str = ""
    speaker: str = ""


@dataclass(frozen=True)
class SelectedItem:
    """一筆選中結果，附帶選取來源的 Pass 標記。

    A single selected item tagged with the pass that picked it.

    Attributes:
        candidate: 被選中的候選句。
        pass_label: ``"Pass1"``（搭配保底）或 ``"Pass2"``（活用形補洞）。
    """

    candidate: SelectionCandidate
    pass_label: str


@dataclass
class BucketOccupancy:
    """增量平衡（§6.5）的已生成佔用統計。

    Bucket occupancy of already-generated cards for incremental balancing
    (§6.5).

    腳本重跑時，先把該動詞已生成句同樣分桶後灌進此結構，
    ``select_diverse`` 會把它計入桶佔用——後續生成自動優先填補空桶。

    Attributes:
        collocations: 搭配桶 → 已生成張數。
        conjugations: 活用形桶 → 已生成張數。
        chapters: 章節 → 已生成張數（``max_per_chapter`` 一體檢查）。
        total: 已生成總張數（扣減剩餘配額用）。
    """

    collocations: dict[str, int] = field(default_factory=dict)
    conjugations: dict[str, int] = field(default_factory=dict)
    chapters: dict[str, int] = field(default_factory=dict)
    total: int = 0

    @classmethod
    def empty(cls) -> "BucketOccupancy":
        """建立零佔用的空結構（首次生成或測試腳本用）。

        Create an empty, zero-occupancy structure (first run or tests).
        """
        return cls()


@dataclass
class SelectionResult:
    """``select_diverse`` 的輸出。

    Output of ``select_diverse``.

    Attributes:
        selected: 選中清單（含 Pass 標記），順序即選取順序。
        uncovered_collocations: 有候選但最終未被任何選中/佔用覆蓋的搭配桶。
        uncovered_conjugations: 有候選但最終未被任何選中/佔用覆蓋的活用形桶。
    """

    selected: list[SelectedItem] = field(default_factory=list)
    uncovered_collocations: list[str] = field(default_factory=list)
    uncovered_conjugations: list[str] = field(default_factory=list)


# --- 維度 A：搭配桶 --------------------------------------------------------


def classify_collocation(tokens: list, span_token_index: int) -> str:
    """以目標 token 前方的 token 決定搭配桶鍵（維度 A，語意代理）。

    Derive the collocation bucket key (dimension A, a semantic proxy) from
    the tokens preceding the target token.

    規則（計劃 §6.3）：
        1. 前方是「助詞」且再前方是「名詞／代名詞」→ 桶鍵＝「名詞＋助詞」
           表層連接（如「様子を」「大目に」「〜から」前有名詞時的「Xから」）。
        2. 前方是「副詞」或「形容詞」（連用修飾，如「甘く」見る）→ 桶鍵＝
           該修飾語表層。
        3. 其餘（口語省略助詞、句首動詞等）→ ``（無助詞）`` 桶。

    Args:
        tokens: 整句分詞結果（驗證器復用，零額外分詞成本）。Sentence tokens
            reused from the validator.
        span_token_index: 目標動詞 token 的索引。Index of the target verb
            token.

    Returns:
        str: 搭配桶鍵。The collocation bucket key.
    """
    if span_token_index <= 0 or span_token_index >= len(tokens):
        return NO_PARTICLE_BUCKET

    prev_token = tokens[span_token_index - 1]
    prev_pos = token_pos1(prev_token)

    if prev_pos in ("副詞", "形容詞"):
        return token_surface(prev_token)

    if prev_pos == "助詞":
        if span_token_index >= 2:
            prev2_token = tokens[span_token_index - 2]
            # 接尾辞：如「憧子＋さん＋を」——さん 是接尾辞而非名詞，
            # 不納入會退化成裸「を」桶；桶鍵取「さんを」（人名類自然合併）。
            if token_pos1(prev2_token) in ("名詞", "代名詞", "接尾辞"):
                return token_surface(prev2_token) + token_surface(prev_token)
        # 助詞前不是名詞（如句首「を」殘片）→ 只以助詞表層成桶
        return token_surface(prev_token)

    return NO_PARTICLE_BUCKET


# --- 維度 B：活用形桶 ------------------------------------------------------


def classify_conjugation(tokens: list, span_token_index: int) -> str:
    """判定目標動詞在句中的活用形桶（維度 B，計劃 §6.3 十四桶）。

    Classify the target verb's conjugation bucket (dimension B, the 14
    buckets of plan §6.3), order-sensitive with longest-match-first.

    主路徑假設 UniDic 短単位切分（助動詞／接続助詞為獨立 token），
    依「順序敏感、先長後短」原則檢查目標 token 的後續 token：
    使役 → 受身/可能 → たい形 → ます系 → ない形 → 条件 → 意向 →
    たり形 → た形 → 進行/て形 → 目的の「に」 → 辞書形/連体 → その他。

    退路：後續 token 無法判定時，改以目標 token 自身表層後綴走
    ``_SURFACE_SUFFIX_FALLBACK`` 順序表（容忍長単位 tokenizer）。

    Args:
        tokens: 整句分詞結果。Sentence tokens.
        span_token_index: 目標動詞 token 的索引。Index of the target verb
            token.

    Returns:
        str: 活用形桶鍵（``ALL_CONJUGATION_BUCKETS`` 之一）。One of
        ``ALL_CONJUGATION_BUCKETS``.
    """
    target = tokens[span_token_index]
    surface = token_surface(target)

    nxt = tokens[span_token_index + 1] if span_token_index + 1 < len(tokens) else None
    nxt2 = tokens[span_token_index + 2] if span_token_index + 2 < len(tokens) else None
    n_surface = token_surface(nxt) if nxt is not None else ""
    n_lemma = token_lemma(nxt) if nxt is not None else ""

    if nxt is not None:
        # 使役（かけさせ〜）
        if n_lemma in ("させる", "さす", "せる"):
            return CONJ_CAUSATIVE
        # 受身/可能（かけられ〜/見られ〜/見れ（ら抜き））
        if n_lemma in ("られる", "れる"):
            return CONJ_PASSIVE_POTENTIAL
        # たい形（かけたい/見たい）
        if n_lemma == "たい":
            return CONJ_TAI
        # ます系（かけました：次 token 為「まし」lemma ます）
        if n_lemma == "ます":
            return CONJ_MASU
        # ない形（かけない/見ず）
        if n_lemma in ("ない", "ず", "ぬ") or n_surface in ("ず", "ぬ"):
            return CONJ_NAI
        # 条件（かければ/見たら）——「たら」的 lemma 是「た」，須先於た形檢查
        if n_surface == "ば":
            return CONJ_CONDITIONAL
        if n_lemma == "た" and n_surface in ("たら", "だら"):
            return CONJ_CONDITIONAL
        # 意向（かけよう：見よ＋う）
        if n_lemma == "う" and n_surface in ("う", "よう"):
            return CONJ_VOLITIONAL
        # たり形（かけたり）
        if n_surface in ("たり", "だり"):
            return CONJ_TARI
        # た形（かけた/見た）
        if n_lemma == "た" or n_surface in ("た", "だ"):
            return CONJ_TA
        # 進行/補助連結 vs て形（かけている/見てる vs かけて/見て）
        if n_surface in ("て", "で"):
            if nxt2 is not None and token_lemma(nxt2) in _PROGRESSIVE_AUX_LEMMAS:
                return CONJ_PROGRESSIVE
            return CONJ_TE
        # 目的の「に」（見に行く：連用形＋に＋移動動詞）
        if (
            n_surface == "に"
            and nxt2 is not None
            and token_lemma(nxt2) in _MOTION_VERB_LEMMAS
        ):
            return CONJ_PURPOSE_NI

    # 辞書形/連体（表層即語彙素：見る/かける）
    if surface and surface == token_lemma(target):
        return CONJ_DICTIONARY

    # 長単位退路：以表層後綴走順序敏感（先長後短）分類表
    for suffix, bucket in _SURFACE_SUFFIX_FALLBACK:
        if surface.endswith(suffix):
            return bucket

    return CONJ_OTHER


# --- 配額分配 --------------------------------------------------------------


def _zigzag_order(buckets: list[str]) -> list[str]:
    """對「已按桶大小降冪排序」的桶鍵做頭尾交錯（zigzag）排列。

    Interleave head and tail of a size-descending bucket list (zigzag).

    最大→最小→次大→次小…輪流，讓高頻搭配與低頻高價值慣用句各佔一半，
    避免降冪取桶把珍稀慣用句全數擠掉（計劃 §6.4 Pass 1）。

    Args:
        buckets: 已按大小降冪排序的桶鍵清單。Bucket keys sorted descending
            by size.

    Returns:
        list[str]: zigzag 順序的桶鍵清單。Bucket keys in zigzag order.
    """
    result: list[str] = []
    left, right = 0, len(buckets) - 1
    take_front = True
    while left <= right:
        if take_front:
            result.append(buckets[left])
            left += 1
        else:
            result.append(buckets[right])
            right -= 1
        take_front = not take_front
    return result


def select_diverse(
    candidates: list[SelectionCandidate],
    quota: int,
    max_per_chapter: int,
    occupied_buckets: BucketOccupancy | None = None,
    priority_collocations: list[str] | None = None,
) -> SelectionResult:
    """兩段式多樣性配額分配（計劃 §6.4-6.5）。

    Two-pass diversity quota allocation (plan §6.4-6.5): priority seats,
    conjugation/collocation coverage passes, then hole-filling, with the
    per-chapter hard constraint enforced throughout.

    Pass 1 前置（優先搭配席位）：``priority_collocations`` 指定的搭配桶
    只要有候選就保證先取 1 句（per-verb 人工圈定的必收用法，如
    掛ける 的「電話を」、見る 的「大目に」）——解決 zigzag 尾端被
    一次性噪音桶佔據、高教學價值搭配反而落選的問題。

    Pass 1 活用形保底：每個有候選且尚未被佔用的活用形桶先取 1 句
    （按 ``ALL_CONJUGATION_BUCKETS`` 順序），桶內優先選「搭配桶尚未
    被使用」的候選——確保維度 B 每桶至少 1 句後才進入搭配保底。

    Pass 1（搭配保底）：每個搭配桶取 1 句，桶內優先選「活用形尚未被使用」
    者；搭配桶數超過配額時以 zigzag 順序取桶。**桶排序時出現 ≥2 次的
    搭配優先於一次性（count=1）桶**——重複出現的搭配才更可能是真實的
    慣用模式，1-count 桶多為任意名詞賓語，降級為最後備選。
    已被 ``occupied_buckets`` 佔用的搭配桶視為已保底、直接跳過。

    Pass 2（活用形補洞）：剩餘配額每輪選「目前佔用最少的活用形桶」補一句。

    tie-break（兩個 Pass 一致）：章節分散 > 說話者多樣性 > 句長適中 >
    script_id（確保結果確定性）。``max_per_chapter`` 硬約束全程生效
    （含已生成佔用的章節計數；``<= 0`` 視為不限制）。

    Args:
        candidates: 已驗證、已分桶的候選句清單。Validated, bucketed
            candidates.
        quota: 本次可選張數（呼叫端已扣除已生成張數）。Number of picks
            available (caller already subtracted generated count).
        max_per_chapter: 同一章節最多取句數（含已生成佔用）。Per-chapter cap
            including occupied counts.
        occupied_buckets: §6.5 增量平衡的已生成佔用；``None`` 視為零佔用。
            Occupancy from prior runs; ``None`` means empty.
        priority_collocations: 優先保證席位的搭配桶鍵（順序即優先序）；
            ``None`` 或空清單時跳過前置階段。Collocation buckets guaranteed
            a seat, in priority order.

    Returns:
        SelectionResult: 選中清單（含 Pass 標記）與未覆蓋桶清單。Selected
        items with pass labels plus uncovered-bucket lists.
    """
    occupied = occupied_buckets or BucketOccupancy.empty()

    chapter_counts: Counter[str] = Counter(occupied.chapters)
    speaker_counts: Counter[str] = Counter()
    conj_counts: Counter[str] = Counter(occupied.conjugations)
    used_collocations: set[str] = {
        key for key, count in occupied.collocations.items() if count > 0
    }

    selected: list[SelectedItem] = []
    selected_ids: set[int] = set()

    def can_take(candidate: SelectionCandidate) -> bool:
        """檢查硬約束：未重複選取、章節未達上限。Check hard constraints."""
        if candidate.script_id in selected_ids:
            return False
        if max_per_chapter > 0 and chapter_counts[candidate.chapter] >= max_per_chapter:
            return False
        return True

    def tie_break_key(candidate: SelectionCandidate) -> tuple:
        """tie-break 排序鍵：章節分散 > 說話者多樣性 > 句長適中 > 確定性。

        Tie-break sort key: chapter spread > speaker variety > ideal
        length > determinism.
        """
        return (
            chapter_counts[candidate.chapter],
            speaker_counts[candidate.speaker],
            abs(len(candidate.sentence) - _IDEAL_SENTENCE_LENGTH),
            candidate.script_id,
        )

    def take(candidate: SelectionCandidate, pass_label: str) -> None:
        """登記一筆選中並更新所有計數器。Record a pick and update counters."""
        selected.append(SelectedItem(candidate=candidate, pass_label=pass_label))
        selected_ids.add(candidate.script_id)
        chapter_counts[candidate.chapter] += 1
        speaker_counts[candidate.speaker] += 1
        conj_counts[candidate.conjugation] += 1
        used_collocations.add(candidate.collocation)

    # 按搭配桶分組（排序在下方 multi/singles 分層時進行）
    groups: dict[str, list[SelectionCandidate]] = {}
    for candidate in candidates:
        groups.setdefault(candidate.collocation, []).append(candidate)

    def take_one_from(bucket: str, pass_label: str) -> bool:
        """自指定搭配桶取 1 句（Pass 1 前置與 Pass 1 共用的取桶邏輯）。

        Take one sentence from the given collocation bucket (shared by the
        priority stage and Pass 1).

        桶內優先選「活用形尚未被使用」的候選，無 fresh 候選時退回全池，
        一律以 ``tie_break_key`` 收斂；硬約束（重複選取／章節上限）由
        ``can_take`` 過濾，全數被擋時不取。

        Args:
            bucket: 搭配桶鍵（須存在於 ``groups``）。Collocation bucket key
                (must exist in ``groups``).
            pass_label: 選取軌跡標記（``"Pass1"`` 或 ``"Pass1-priority"``）。
                Trace label for the pick.

        Returns:
            bool: 成功取到 1 句回傳 ``True``；桶內無可取候選回傳 ``False``。
            ``True`` if a sentence was taken, ``False`` otherwise.
        """
        pool = [c for c in groups[bucket] if can_take(c)]
        if not pool:
            return False
        fresh = [c for c in pool if conj_counts[c.conjugation] == 0]
        take(min(fresh or pool, key=tie_break_key), pass_label)
        return True

    # --- Pass 1 前置：優先搭配席位（per-verb 人工圈定的必收用法） ---
    for bucket in priority_collocations or []:
        if len(selected) >= quota:
            break
        if bucket in groups and bucket not in used_collocations:
            take_one_from(bucket, "Pass1-priority")

    # --- Pass 1 活用形保底：每個有候選的活用形桶先取 1 句 ---
    # 順序沿用 ALL_CONJUGATION_BUCKETS（確定性）；桶內優先選
    # 「搭配桶尚未被使用」的候選，同時推進維度 A 的覆蓋。
    conj_groups: dict[str, list[SelectionCandidate]] = {}
    for candidate in candidates:
        conj_groups.setdefault(candidate.conjugation, []).append(candidate)
    for conj_bucket in ALL_CONJUGATION_BUCKETS:
        if len(selected) >= quota:
            break
        if conj_bucket not in conj_groups or conj_counts[conj_bucket] > 0:
            continue
        pool = [c for c in conj_groups[conj_bucket] if can_take(c)]
        if not pool:
            continue
        fresh = [c for c in pool if c.collocation not in used_collocations]
        take(min(fresh or pool, key=tie_break_key), "Pass1-conj")

    # 桶排序：出現 ≥2 次的搭配（真實慣用模式）優先 zigzag，
    # 一次性（count=1）桶多為任意名詞賓語 → 降級殿後備選
    multi_buckets = sorted(
        (k for k in groups if len(groups[k]) >= 2),
        key=lambda key: (-len(groups[key]), key),
    )
    single_buckets = sorted(k for k in groups if len(groups[k]) == 1)

    # --- Pass 1：搭配保底（zigzag 取桶；singles 殿後） ---
    for bucket in _zigzag_order(multi_buckets) + single_buckets:
        if len(selected) >= quota:
            break
        if bucket in used_collocations:
            continue  # 已生成佔用（或已被優先席位選中）視為已保底
        take_one_from(bucket, "Pass1")

    # --- Pass 2：活用形補洞 ---
    while len(selected) < quota:
        remaining = [c for c in candidates if can_take(c)]
        if not remaining:
            break
        by_conj: dict[str, list[SelectionCandidate]] = {}
        for candidate in remaining:
            by_conj.setdefault(candidate.conjugation, []).append(candidate)
        # 選目前佔用最少的活用形桶（同佔用時取候選較多者，再以桶鍵定序）
        best_conj = min(
            by_conj,
            key=lambda key: (conj_counts[key], -len(by_conj[key]), key),
        )
        take(min(by_conj[best_conj], key=tie_break_key), "Pass2")

    uncovered_collocations = sorted(
        bucket for bucket in groups if bucket not in used_collocations
    )
    candidate_conjugations = {c.conjugation for c in candidates}
    uncovered_conjugations = [
        bucket
        for bucket in ALL_CONJUGATION_BUCKETS
        if bucket in candidate_conjugations and conj_counts[bucket] == 0
    ]

    return SelectionResult(
        selected=selected,
        uncovered_collocations=uncovered_collocations,
        uncovered_conjugations=uncovered_conjugations,
    )
