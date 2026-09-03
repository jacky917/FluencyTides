"""JP_CoreVerb 選句管線的參數化單元測試（計劃 §6.1 / §6.3 / §6.4）。

Parameterized unit tests for the JP_CoreVerb selection pipeline: a fake
token layer runnable anywhere, plus a real-fugashi regression layer.

分兩層：

1. **假 token 層（本機可跑，不依賴 fugashi）**——驗證器與分桶純函數的
   token 介面是鴨子型別（``surface`` + 可索引 ``feature``），以 ``FakeToken``
   手工鋪 UniDic 短単位風格的 token 流，覆蓋計劃指名的陷阱句
   （見つける/見当たる/見かける/見送った/食べてみる/純見る句）、
   十四個活用形桶與 zigzag 兩段配額行為。
2. **真 fugashi 層（``pytest.importorskip`` 守衛）**——本機（macOS）沒有
   fugashi 時自動跳過；在具備 UniDic 的環境對真實分詞結果回歸。
"""

import asyncio
import sys
from pathlib import Path

import pytest

# 確保 sys.path 包含 backend 根目錄（不載入 .env——受測模組零 settings 依賴）
_backend_dir = Path(__file__).resolve().parents[4]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from scripts.fastapi_client.JP_CoreVerb.pipeline_components.candidate_validator import (
    REJECTION_AUXILIARY,
    REJECTION_COMPOUND_SUFFIX,
    REJECTION_COMPOUND_VERB,
    REJECTION_LEMMA_MISMATCH,
    REJECTION_READING_MISMATCH,
    to_katakana,
    validate_candidate,
)
from scripts.fastapi_client.JP_CoreVerb.pipeline_components.diversity_selector import (
    NO_PARTICLE_BUCKET,
    BucketOccupancy,
    SelectionCandidate,
    classify_collocation,
    classify_conjugation,
    select_diverse,
)
from scripts.fastapi_client.JP_CoreVerb.pipeline_components.funnel import (
    VerbSearchConfig,
    run_selection_funnel,
    strip_furigana,
)


class FakeToken:
    """模擬 fugashi UniDic node 的最小假 token。
    Minimal fake token mimicking a fugashi UniDic node.

    Attributes:
        surface: 表層字串。
        feature: 8 欄可索引序列——``feature[0]`` 詞性大類、``feature[7]`` lemma。
    """

    def __init__(
        self,
        surface: str,
        pos1: str,
        lemma: str | None = None,
        reading: str | None = None,
        orth_base: str | None = None,
    ):
        self.surface = surface
        feature = ["*"] * (11 if orth_base is not None else 8)
        feature[0] = pos1
        feature[7] = lemma if lemma is not None else surface
        if reading is not None:
            feature[6] = reading
        if orth_base is not None:
            feature[10] = orth_base
        self.feature = feature

    def __repr__(self) -> str:  # pragma: no cover - debug 輔助
        return f"FakeToken({self.surface}/{self.feature[0]}/{self.feature[7]})"


def make_tagger(token_map: dict[str, list[FakeToken]]):
    """建立以「句子 → token 流」查表的假 tagger。
    Build a lookup-table fake tagger (sentence -> token stream)."""

    def tagger(sentence: str) -> list[FakeToken]:
        return token_map[sentence]

    return tagger


def N(surface):
    """名詞 token 速記。Noun token shorthand."""
    return FakeToken(surface, "名詞")


def P(surface):
    """助詞 token 速記。Particle token shorthand."""
    return FakeToken(surface, "助詞")


def V(surface, lemma):
    """動詞 token 速記。Verb token shorthand."""
    return FakeToken(surface, "動詞", lemma)


def AUX(surface, lemma):
    """助動詞 token 速記。Auxiliary-verb token shorthand."""
    return FakeToken(surface, "助動詞", lemma)


# =============================================================================
# 驗證器（§6.1）——計劃指名的陷阱句，全部以 UniDic 短単位風格假 token 鋪設
# =============================================================================


@pytest.mark.parametrize(
    "sentence, tokens, expected_accepted, expected_reason",
    [
        # 純見る句：様子を見る → 通過
        (
            "様子を見る",
            [N("様子"), P("を"), V("見る", "見る")],
            True,
            None,
        ),
        # 見つける：獨立語彙素，lemma 不符 → 拒絕
        (
            "犯人を見つける",
            [N("犯人"), P("を"), V("見つける", "見つける")],
            False,
            REJECTION_LEMMA_MISMATCH,
        ),
        # 見当たる：獨立語彙素 → 拒絕
        (
            "何も見当たらない",
            [N("何"), P("も"), V("見当たら", "見当たる"), AUX("ない", "ない")],
            False,
            REJECTION_LEMMA_MISMATCH,
        ),
        # 見かける：獨立語彙素 → 拒絕
        (
            "街で見かけた",
            [N("街"), P("で"), V("見かけ", "見かける"), AUX("た", "た")],
            False,
            REJECTION_LEMMA_MISMATCH,
        ),
        # 見送った：短単位切成 見＋送っ＋た → 複合動詞前項拒絕
        (
            "彼女を見送った",
            [N("彼女"), P("を"), V("見", "見る"), V("送っ", "送る"), AUX("た", "た")],
            False,
            REJECTION_COMPOUND_VERB,
        ),
        # 食べてみる：前一 token 是「て」 → 補助動詞拒絕
        (
            "一口食べてみる",
            [N("一口"), V("食べ", "食べる"), P("て"), V("みる", "見る")],
            False,
            REJECTION_AUXILIARY,
        ),
    ],
)
def test_validate_candidate_trap_sentences(
    sentence, tokens, expected_accepted, expected_reason
):
    """計劃 §6.1 指名的陷阱句逐一斷言。Assert each trap sentence from §6.1."""
    tagger = make_tagger({sentence: tokens})
    result = validate_candidate(sentence, "見る", allow_auxiliary=False, tagger=tagger)
    assert result.accepted is expected_accepted
    assert result.reason == expected_reason
    if expected_accepted:
        assert result.candidate is not None
    else:
        assert result.candidate is None


def test_validate_candidate_allow_auxiliary_passes_te_miru():
    """allow_auxiliary=True 時放行補助動詞用法。
    allow_auxiliary=True lets auxiliary usage pass."""
    sentence = "一口食べてみる"
    tokens = [N("一口"), V("食べ", "食べる"), P("て"), V("みる", "見る")]
    result = validate_candidate(
        sentence, "見る", allow_auxiliary=True, tagger=make_tagger({sentence: tokens})
    )
    assert result.accepted is True
    assert result.candidate.span == (sentence.index("みる"), sentence.index("みる") + 2)


def test_validate_candidate_span_offsets():
    """span 以 token 字元偏移累加計算，指向目標動詞本體。
    Span is computed via char offsets and points at the target verb."""
    sentence = "様子を見る"
    tokens = [N("様子"), P("を"), V("見る", "見る")]
    result = validate_candidate(
        sentence, "見る", allow_auxiliary=False, tagger=make_tagger({sentence: tokens})
    )
    assert result.candidate.span == (3, 5)
    assert sentence[slice(*result.candidate.span)] == "見る"
    assert result.candidate.span_token_index == 2
    assert result.candidate.tokens == tokens


def test_validate_candidate_second_occurrence_can_pass():
    """句中多次命中 lemma 時，只要有一個 token 通過即整句通過。
    Any passing token among multiple lemma hits accepts the sentence."""
    sentence = "見送ったあとにまた見る"
    tokens = [
        V("見", "見る"),
        V("送っ", "送る"),
        AUX("た", "た"),
        N("あと"),
        P("に"),
        FakeToken("また", "副詞"),
        V("見る", "見る"),
    ]
    result = validate_candidate(
        sentence, "見る", allow_auxiliary=False, tagger=make_tagger({sentence: tokens})
    )
    assert result.accepted is True
    assert result.candidate.span_token_index == 6


def test_validate_candidate_rule3_kakujoshi_de_is_not_auxiliary():
    """規則 ③ 精修：格助詞「で」（前接名詞）不構成補助動詞接續——あとで見る 放行。

    Case-particle "de" after a noun is not an auxiliary link, so the
    sentence passes.

    補助動詞拒絕需要「動詞＋て/で＋目標動詞」的完整接續型態；
    「あと[名詞]＋で＋見る」的「で」是格助詞，見る 為獨立主動詞。
    """
    sentence = "あとで見る"
    tokens = [N("あと"), P("で"), V("見る", "見る")]
    result = validate_candidate(
        sentence, "見る", allow_auxiliary=False, tagger=make_tagger({sentence: tokens})
    )
    assert result.accepted is True
    assert result.candidate is not None


def test_validate_candidate_rule3_verb_te_still_rejected():
    """規則 ③ 精修後仍須拒絕真正的補助動詞接續：食べ[動詞]＋て＋みる。
    Genuine verb+te+miru auxiliary chains are still rejected."""
    sentence = "食べてみる"
    tokens = [V("食べ", "食べる"), P("て"), V("みる", "見る")]
    result = validate_candidate(
        sentence, "見る", allow_auxiliary=False, tagger=make_tagger({sentence: tokens})
    )
    assert result.accepted is False
    assert result.reason == REJECTION_AUXILIARY


# =============================================================================
# 驗證器擴充（VerbPair 計劃 §D2/D3：讀音關與複合動詞後項）
# =============================================================================


def test_reading_mismatch_rejected():
    """規則 ①'：同表層異讀靠語彙素読み區分——めくる卡拒絕まくる句。
    Same-surface different-reading tokens are rejected by the reading gate."""
    sentence = "ギターを弾きまくる"
    # UniDic 實測：まくる 的 lemma 是 捲る、lForm 是 マクル
    tokens = [N("ギター"), P("を"), FakeToken("まくる", "動詞", "捲る", "マクル")]
    result = validate_candidate(
        sentence, "捲る", allow_auxiliary=False,
        tagger=make_tagger({sentence: tokens}),
        expected_reading="めくる",
    )
    assert result.accepted is False
    assert result.reason == REJECTION_READING_MISMATCH


def test_reading_match_accepts_hiragana_expected():
    """規則 ①'：期待讀音給平假名也能與片假名 lForm 比對成功。
    Hiragana expected readings match katakana lForm values."""
    sentence = "布団をめくる"
    tokens = [N("布団"), P("を"), FakeToken("めくる", "動詞", "捲る", "メクル")]
    result = validate_candidate(
        sentence, "捲る", allow_auxiliary=False,
        tagger=make_tagger({sentence: tokens}),
        expected_reading="めくる",
    )
    assert result.accepted is True


def test_reading_gate_skipped_when_token_reading_missing():
    """規則 ①'：token 讀音缺值（*）時放行讀音關——寧可漏擋不誤殺。
    A missing token reading passes the gate (fail-open)."""
    sentence = "布団をめくる"
    tokens = [N("布団"), P("を"), V("めくる", "捲る")]  # feature[6] 維持 *
    result = validate_candidate(
        sentence, "捲る", allow_auxiliary=False,
        tagger=make_tagger({sentence: tokens}),
        expected_reading="めくる",
    )
    assert result.accepted is True


def test_reading_gate_disabled_by_default():
    """回歸保證：不傳 expected_reading 時完全不驗讀音（CoreVerb 舊呼叫端）。
    Legacy callers without expected_reading skip the gate entirely."""
    sentence = "ギターを弾きまくる"
    tokens = [N("ギター"), FakeToken("まくる", "動詞", "捲る", "マクル")]
    result = validate_candidate(
        sentence, "捲る", allow_auxiliary=False,
        tagger=make_tagger({sentence: tokens}),
    )
    assert result.accepted is True


def test_compound_suffix_rejected():
    """規則 ②'：連用形直接接續的後項拒絕——使い＋切れ（〜切れない）。
    Compound-suffix usages (renyoukei + target) are rejected."""
    sentence = "全部は使い切れない"
    tokens = [
        N("全部"), P("は"),
        V("使い", "使う"), V("切れ", "切れる"), AUX("ない", "ない"),
    ]
    result = validate_candidate(
        sentence, "切れる", allow_auxiliary=False,
        tagger=make_tagger({sentence: tokens}),
    )
    assert result.accepted is False
    assert result.reason == REJECTION_COMPOUND_SUFFIX


def test_compound_suffix_allowed_by_flag():
    """規則 ②'：per-verb 白名單 allow_compound_suffix=True 放行。
    The per-verb allow_compound_suffix flag lifts the rejection."""
    sentence = "全部は使い切れない"
    tokens = [
        N("全部"), P("は"),
        V("使い", "使う"), V("切れ", "切れる"), AUX("ない", "ない"),
    ]
    result = validate_candidate(
        sentence, "切れる", allow_auxiliary=False,
        tagger=make_tagger({sentence: tokens}),
        allow_compound_suffix=True,
    )
    assert result.accepted is True


def test_standalone_usage_passes_all_new_gates():
    """正例全關通過：集中力が切れた（獨立用法 + 讀音相符）。
    A genuine standalone usage passes every gate."""
    sentence = "集中力が切れた"
    tokens = [
        N("集中力"), P("が"),
        FakeToken("切れ", "動詞", "切れる", "キレル"), AUX("た", "た"),
    ]
    result = validate_candidate(
        sentence, "切れる", allow_auxiliary=False,
        tagger=make_tagger({sentence: tokens}),
        expected_reading="きれる",
    )
    assert result.accepted is True
    assert result.candidate is not None


def test_orth_base_rescues_unidic_lemma_unification():
    """UniDic 字形統合（帰る lemma=返る）靠 orthBase 兜底通過。
    orthBase rescues tokens whose lemma was unified to another spelling."""
    sentence = "家に帰る"
    tokens = [N("家"), P("に"), FakeToken("帰る", "動詞", "返る", "カエル", orth_base="帰る")]
    result = validate_candidate(
        sentence, "帰る", allow_auxiliary=False,
        tagger=make_tagger({sentence: tokens}),
        expected_reading="かえる",
    )
    assert result.accepted is True


def test_lemma_subdivision_suffix_stripped():
    """UniDic 語彙素細分後綴（差す-他動詞）去除後可與目標比對。
    Lexeme subdivision suffixes (差す-他動詞) are stripped before matching."""
    sentence = "ナイフで刺す"
    # 實測：刺す 的 lemma 是「差す-他動詞」、orthBase 是「刺す」
    tokens = [N("ナイフ"), P("で"), FakeToken("刺す", "動詞", "差す-他動詞", "サス", orth_base="刺す")]
    result = validate_candidate(
        sentence, "差す", allow_auxiliary=False,
        tagger=make_tagger({sentence: tokens}),
    )
    assert result.accepted is True


def test_niyoru_still_rejected_after_orth_base_loosening():
    """回歸防線：による（lemma=因る、orthBase=よる）對撚る兩關皆不符 → 拒。
    による remains rejected for target 撚る even with the orthBase fallback."""
    sentence = "規模による"
    tokens = [N("規模"), P("に"), FakeToken("よる", "動詞", "因る", "ヨル", orth_base="よる")]
    result = validate_candidate(
        sentence, "撚る", allow_auxiliary=False,
        tagger=make_tagger({sentence: tokens}),
        expected_reading="よる",
    )
    assert result.accepted is False
    assert result.reason == REJECTION_LEMMA_MISMATCH


def test_to_katakana_converts_hiragana_only():
    """to_katakana：平轉片，片假名與其他字元不動。
    Hiragana converts; katakana and other characters pass through."""
    assert to_katakana("うまる") == "ウマル"
    assert to_katakana("ウマル") == "ウマル"
    assert to_katakana("埋まる") == "埋マル"


# =============================================================================
# 搭配桶（§6.3 維度 A）
# =============================================================================


@pytest.mark.parametrize(
    "tokens, span_index, expected",
    [
        # 名詞＋助詞
        ([N("様子"), P("を"), V("見る", "見る")], 2, "様子を"),
        ([N("大目"), P("に"), V("見る", "見る")], 2, "大目に"),
        # 形容詞連用修飾（甘く見る）
        ([FakeToken("甘く", "形容詞", "甘い"), V("見る", "見る")], 1, "甘く"),
        # 副詞修飾
        ([FakeToken("じっと", "副詞"), V("見る", "見る")], 1, "じっと"),
        # 句首動詞 → 無助詞
        ([V("見る", "見る"), P("の"), N("こと")], 0, NO_PARTICLE_BUCKET),
        # 前方非助詞/副詞（動詞連接）→ 無助詞
        ([V("寝", "寝る"), V("見る", "見る")], 1, NO_PARTICLE_BUCKET),
        # 助詞前不是名詞 → 只以助詞成桶
        ([P("を"), V("見る", "見る")], 1, "を"),
    ],
)
def test_classify_collocation(tokens, span_index, expected):
    """搭配桶鍵：名詞＋助詞 / 修飾語 / （無助詞）。
    Collocation keys: noun+particle / modifier / no-particle."""
    assert classify_collocation(tokens, span_index) == expected


# =============================================================================
# 活用形桶（§6.3 維度 B，十四桶、順序敏感）
# =============================================================================


@pytest.mark.parametrize(
    "tokens, span_index, expected",
    [
        # 使役：かけさせる
        ([V("かけ", "掛ける"), AUX("させる", "させる")], 0, "使役"),
        # 受身/可能：見られる
        ([V("見", "見る"), AUX("られる", "られる")], 0, "受身/可能"),
        # たい形：見たい
        ([V("見", "見る"), AUX("たい", "たい")], 0, "たい形"),
        # ます系：見ました（まし lemma ます，先於た形）
        ([V("見", "見る"), AUX("まし", "ます"), AUX("た", "た")], 0, "ます系"),
        # ない形：見ない / 見ず
        ([V("見", "見る"), AUX("ない", "ない")], 0, "ない形"),
        ([V("見", "見る"), AUX("ず", "ず")], 0, "ない形"),
        # 条件：見れば / 見たら（たら lemma た、先於た形）
        ([V("見れ", "見る"), P("ば")], 0, "条件"),
        ([V("見", "見る"), AUX("たら", "た")], 0, "条件"),
        # 意向：見よう
        ([V("見よ", "見る"), AUX("う", "う")], 0, "意向"),
        # たり形：かけたり
        ([V("かけ", "掛ける"), P("たり")], 0, "たり形"),
        # た形：見た
        ([V("見", "見る"), AUX("た", "た")], 0, "た形"),
        # 進行/補助連結：見ている（て＋いる）
        ([V("見", "見る"), P("て"), V("いる", "いる")], 0, "進行/補助連結"),
        # て形：見て（後無存在系補助）
        ([V("見", "見る"), P("て"), N("それ")], 0, "て形"),
        ([V("見", "見る"), P("て")], 0, "て形"),
        # 目的の「に」：見に行く
        ([V("見", "見る"), P("に"), V("行く", "行く")], 0, "目的の「に」"),
        # 辞書形/連体：見る（表層＝lemma）
        ([N("映画"), P("を"), V("見る", "見る")], 2, "辞書形/連体"),
        # その他：判定不能（連用形結尾且後無線索）
        ([V("見", "見る")], 0, "その他"),
    ],
)
def test_classify_conjugation(tokens, span_index, expected):
    """十四桶活用形分類（順序敏感、先長後短）。
    Fourteen-bucket conjugation classification, longest-match-first."""
    assert classify_conjugation(tokens, span_index) == expected


def test_classify_conjugation_surface_fallback_long_unit():
    """長単位 tokenizer 退路：以表層後綴分類（先長後短）。
    Long-unit fallback: classify by surface suffix, longest first."""
    # 「見ている」被切成單一 token 的情境
    assert classify_conjugation([FakeToken("見ている", "動詞", "見る")], 0) == "進行/補助連結"
    assert classify_conjugation([FakeToken("見たい", "動詞", "見る")], 0) == "たい形"
    assert classify_conjugation([FakeToken("見た", "動詞", "見る")], 0) == "た形"


# =============================================================================
# 配額分配（§6.4-6.5）
# =============================================================================


def _cand(script_id, colloc, conj, chapter="ch1", speaker="A", length=20):
    """建立測試用 SelectionCandidate 的速記工廠。
    Shorthand factory for test SelectionCandidate objects."""
    return SelectionCandidate(
        script_id=script_id,
        sentence="あ" * length,
        span=(0, 2),
        collocation=colloc,
        conjugation=conj,
        chapter=chapter,
        speaker=speaker,
    )


def test_select_diverse_pass1_covers_each_collocation_once():
    """Pass 1：每個搭配桶各保底 1 句。
    Pass 1 guarantees one pick per collocation bucket."""
    candidates = [
        _cand(1, "様子を", "辞書形/連体", "ch1"),
        _cand(2, "様子を", "た形", "ch1"),
        _cand(3, "夢を", "た形", "ch2"),
        _cand(4, "大目に", "て形", "ch3"),
    ]
    result = select_diverse(candidates, quota=3, max_per_chapter=2)
    assert len(result.selected) == 3
    collocations = {item.candidate.collocation for item in result.selected}
    assert collocations == {"様子を", "夢を", "大目に"}
    assert all(item.pass_label == "Pass1" for item in result.selected)


def test_select_diverse_zigzag_mixes_large_and_small_buckets():
    """搭配桶數超過配額時，zigzag 讓最大桶與最小桶各佔一半。
    Zigzag mixes the largest and smallest buckets under tight quota."""
    candidates = []
    sid = 0
    # 桶大小：A=5, B=4, C=3, D=2（每句不同章節，排除章節約束干擾）
    for bucket, size in (("A", 5), ("B", 4), ("C", 3), ("D", 2)):
        for _ in range(size):
            sid += 1
            candidates.append(_cand(sid, bucket, "辞書形/連体", chapter=f"ch{sid}"))
    result = select_diverse(candidates, quota=2, max_per_chapter=1)
    picked = [item.candidate.collocation for item in result.selected]
    # zigzag：最大(A) → 最小(D)
    assert picked == ["A", "D"]
    # 有候選但未選中的桶進未覆蓋清單
    assert result.uncovered_collocations == ["B", "C"]


def test_select_diverse_singleton_buckets_demoted():
    """count=1 的一次性搭配桶降級殿後——count ≥ 2 的慣用桶優先佔席。
    Singleton buckets are demoted; recurring buckets take seats first."""
    candidates = []
    sid = 0
    # 噪音桶：5 個 1-count 桶（字典序在前，舊 zigzag 會先取到）
    for bucket in ("あ桶", "い桶", "う桶", "え桶", "お桶"):
        sid += 1
        candidates.append(_cand(sid, bucket, "辞書形/連体", chapter=f"ch{sid}"))
    # 慣用桶：電話を ×3、目で ×2
    for bucket, size in (("電話を", 3), ("目で", 2)):
        for _ in range(size):
            sid += 1
            candidates.append(_cand(sid, bucket, "た形", chapter=f"ch{sid}"))
    result = select_diverse(candidates, quota=3, max_per_chapter=1)
    picked = [item.candidate.collocation for item in result.selected]
    # 前兩席必屬 multi 桶（zigzag：最大 電話を → 最小 目で），第三席才輪到 singles
    assert picked[:2] == ["電話を", "目で"]
    assert picked[2] == "あ桶"


def test_select_diverse_priority_collocations_guaranteed_seat():
    """priority_collocations 指定的桶只要有候選就保證優先席位。
    Priority collocations get a guaranteed seat when candidates exist."""
    candidates = []
    sid = 0
    # 大量噪音桶把配額擠滿
    for i in range(6):
        sid += 1
        candidates.append(_cand(sid, f"噪音{i}を", "辞書形/連体", chapter=f"ch{sid}"))
        candidates.append(_cand(sid + 100, f"噪音{i}を", "た形", chapter=f"ch{sid + 100}"))
    # 目標桶只有 1 句（沒有優先席位時會被 multi 桶擠掉）
    sid += 1
    candidates.append(_cand(sid, "電話を", "て形", chapter=f"ch{sid}"))
    quota = 3
    baseline = select_diverse(candidates, quota=quota, max_per_chapter=1)
    assert "電話を" not in {i.candidate.collocation for i in baseline.selected}
    result = select_diverse(
        candidates, quota=quota, max_per_chapter=1,
        priority_collocations=["電話を", "不存在の桶"],
    )
    picked = [item.candidate.collocation for item in result.selected]
    assert picked[0] == "電話を"
    assert result.selected[0].pass_label == "Pass1-priority"
    # 不存在的優先桶被靜默略過，其餘配額照常分配
    assert len(result.selected) == quota


def test_select_diverse_priority_bucket_not_double_taken():
    """優先席位選過的桶在 zigzag 階段不再重複取。
    Priority-taken buckets are not re-taken during zigzag."""
    candidates = [
        _cand(1, "電話を", "た形", chapter="ch1"),
        _cand(2, "電話を", "て形", chapter="ch2"),
        _cand(3, "様子を", "辞書形/連体", chapter="ch3"),
    ]
    result = select_diverse(
        candidates, quota=2, max_per_chapter=2, priority_collocations=["電話を"]
    )
    picked = [item.candidate.collocation for item in result.selected]
    assert picked == ["電話を", "様子を"]


def test_select_diverse_pass2_fills_conjugation_holes():
    """Pass 2：剩餘配額優先補「尚未覆蓋的活用形桶」。
    Pass 2 fills uncovered conjugation buckets with leftover quota."""
    candidates = [
        _cand(1, "様子を", "辞書形/連体", "ch1"),
        _cand(2, "様子を", "た形", "ch2"),
        _cand(3, "様子を", "て形", "ch3"),
    ]
    result = select_diverse(candidates, quota=3, max_per_chapter=2)
    assert len(result.selected) == 3
    labels = [item.pass_label for item in result.selected]
    assert labels.count("Pass1") == 1  # 搭配桶只有一個
    assert labels.count("Pass2") == 2
    conjugations = {item.candidate.conjugation for item in result.selected}
    assert conjugations == {"辞書形/連体", "た形", "て形"}
    assert result.uncovered_conjugations == []


def test_select_diverse_respects_max_per_chapter():
    """max_per_chapter 硬約束在兩個 Pass 一體生效。
    max_per_chapter is enforced across both passes."""
    candidates = [_cand(i, f"桶{i}", "辞書形/連体", chapter="ch1") for i in range(1, 6)]
    result = select_diverse(candidates, quota=5, max_per_chapter=2)
    assert len(result.selected) == 2


def test_select_diverse_occupied_buckets_skip_and_count():
    """§6.5 增量平衡：已生成佔用的搭配桶視為已保底，章節計數一體檢查。
    Occupied buckets count as covered; chapter counts are combined."""
    candidates = [
        _cand(1, "様子を", "辞書形/連体", "ch1"),
        _cand(2, "夢を", "た形", "ch1"),
        _cand(3, "大目に", "て形", "ch2"),
    ]
    occupied = BucketOccupancy(
        collocations={"様子を": 1},
        conjugations={"辞書形/連体": 1},
        chapters={"ch1": 1},
        total=1,
    )
    result = select_diverse(candidates, quota=2, max_per_chapter=2, occupied_buckets=occupied)
    picked_ids = {item.candidate.script_id for item in result.selected}
    # 様子を 已佔用 → Pass1 跳過；夢を、大目に 各保底一句
    assert picked_ids == {2, 3}
    assert "様子を" not in result.uncovered_collocations


def test_select_diverse_quota_zero():
    """配額 0 → 不選任何句。Zero quota selects nothing."""
    result = select_diverse([_cand(1, "A", "た形")], quota=0, max_per_chapter=2)
    assert result.selected == []


def test_select_diverse_deterministic():
    """相同輸入必得相同輸出（tie-break 以 script_id 收斂）。
    Identical inputs yield identical outputs (script_id tie-break)."""
    candidates = [
        _cand(i, f"桶{i % 3}", "辞書形/連体", chapter=f"ch{i % 4}") for i in range(1, 20)
    ]
    first = select_diverse(candidates, quota=5, max_per_chapter=2)
    second = select_diverse(list(candidates), quota=5, max_per_chapter=2)
    assert [i.candidate.script_id for i in first.selected] == [
        i.candidate.script_id for i in second.selected
    ]


# =============================================================================
# 漏斗整合（假 es_fetcher + 假 tagger，本機可跑）
# =============================================================================


def test_run_selection_funnel_end_to_end_with_fakes():
    """整條漏斗：游標分頁 → 過濾 → 驗證 → 分桶 → 配額，全假件跑通。
    End-to-end funnel run with all fake components."""
    sentences = {
        101: ("様子を見るしかないだろう", [N("様子"), P("を"), V("見る", "見る"),
                                          N("しか"), P("ない"), N("だろう")]),
        102: ("彼女を見送った日のこと", [N("彼女"), P("を"), V("見", "見る"),
                                        V("送っ", "送る"), AUX("た", "た"),
                                        N("日"), P("の"), N("こと")]),
        103: ("短い", [FakeToken("短い", "形容詞", "短い")]),  # min_length 淘汰
        104: ("この映画を見たかったんだ", [N("この"), N("映画"), P("を"),
                                          V("見", "見る"), AUX("たかっ", "たい"),
                                          AUX("た", "た"), N("んだ")]),
        105: ("排除ワード入りの見る文です", [V("見る", "見る")]),  # exclude_keywords 淘汰
    }
    token_map = {clean: toks for clean, toks in sentences.values()}

    pages = [
        [{"script_id": sid, "dialogue": sentences[sid][0]} for sid in (101, 102)],
        [{"script_id": sid, "dialogue": sentences[sid][0]} for sid in (103, 104, 105)],
    ]

    async def es_fetcher(keyword, last_script_id, page_size):
        # 模擬游標分頁：第一頁滿頁（page_size=2）、第二頁不滿 → 停止
        if last_script_id == 0:
            return pages[0]
        if last_script_id == 102:
            return pages[1]
        return []

    async def metadata_fetcher(script_ids):
        return {sid: {"chapter": f"ch{sid % 2}", "speaker": "話者"} for sid in script_ids}

    cfg = VerbSearchConfig(
        verb_display="見[み]る",
        verb_lemma="見る",
        exclude_keywords=["排除ワード"],
        max_cards=15,
        max_per_chapter=2,
        min_sentence_length=5,
        page_size=2,
    )

    report = asyncio.run(
        run_selection_funnel(
            cfg,
            es_fetcher,
            occupied=[],
            tagger=make_tagger(token_map),
            metadata_fetcher=metadata_fetcher,
        )
    )

    assert report.funnel_counts["es_hits"] == 5
    assert report.filter_drops == {"exclude_keywords": 1, "min_sentence_length": 1}
    assert report.rejection_reasons == {REJECTION_COMPOUND_VERB: 1}
    assert report.funnel_counts["validated"] == 2
    assert report.funnel_counts["selected"] == 2
    picked_ids = {item.candidate.script_id for item in report.selected}
    assert picked_ids == {101, 104}
    # 分桶矩陣：様子を×辞書形、映画を×たい形
    assert report.bucket_matrix["様子を"]["辞書形/連体"] == 1
    assert report.bucket_matrix["映画を"]["たい形"] == 1


def test_strip_furigana():
    """去標音函數：與設定檔鍵表記共用同一規則。
    strip_furigana shares the config-key normalization rule."""
    assert strip_furigana("見[み]る") == "見る"
    assert strip_furigana("掛[か]ける") == "掛ける"
    assert strip_furigana("かける") == "かける"
    assert strip_furigana("") == ""


# =============================================================================
# 真 fugashi 層（本機無 fugashi 時自動跳過）
# =============================================================================


class TestWithRealFugashi:
    """以真實 UniDic 分詞回歸驗證器與分桶（需要 fugashi + unidic 字典）。
    Regression with real UniDic segmentation (needs fugashi + unidic)."""

    @pytest.fixture(scope="class")
    def tagger(self):
        """建立真實 fugashi Tagger（無法 import 時整類跳過）。
        Build a real fugashi Tagger; skip the class if unimportable."""
        fugashi = pytest.importorskip("fugashi")
        return fugashi.Tagger()

    @pytest.mark.parametrize(
        "sentence, expected_accepted",
        [
            ("様子を見るしかない", True),
            ("犯人を見つける", False),
            ("どこにも見当たらない", False),
            ("彼女を見送った", False),
            ("一口食べてみる", False),
        ],
    )
    def test_real_segmentation_traps(self, tagger, sentence, expected_accepted):
        """計劃 §6.1 陷阱句在真實分詞下的攔截行為。
        Trap-sentence interception under real segmentation."""
        result = validate_candidate(sentence, "見る", allow_auxiliary=False, tagger=tagger)
        assert result.accepted is expected_accepted

    def test_real_segmentation_bucketing(self, tagger):
        """真實分詞下的分桶行為抽查。Spot-check bucketing on real tokens."""
        result = validate_candidate("様子を見た", "見る", allow_auxiliary=False, tagger=tagger)
        assert result.accepted
        cand = result.candidate
        assert classify_collocation(cand.tokens, cand.span_token_index) == "様子を"
        assert classify_conjugation(cand.tokens, cand.span_token_index) == "た形"


# ============================================================
# 過濾層：純呻吟句（與 JP_VerbPair 共用同一套判定）
# ============================================================

def test_funnel_filters_moan_sentences():
    """純呻吟句在過濾層被擋下，並計入 FILTER_MOAN。"""
    from scripts.fastapi_client.JP_CoreVerb.pipeline_components.funnel import FILTER_MOAN
    from scripts.common.jp_moan_filter import is_moan_sentence

    moan = "んんっ、ちゅぱちゅぱ、れろれろ、あぁぁんっ、はぁはぁ、ちゅぅぅ……"
    normal = "「ドアが開いた音がしたので、様子を見に行った」"
    assert is_moan_sentence(moan) is True
    assert is_moan_sentence(normal) is False
    assert FILTER_MOAN == "呻吟句樣式"


def test_verb_search_config_filter_moan_defaults_on():
    """漏斗設定預設開啟呻吟過濾；可 per-run 關閉。"""
    from scripts.fastapi_client.JP_CoreVerb.pipeline_components.funnel import VerbSearchConfig

    assert VerbSearchConfig(verb_display="見[み]る", verb_lemma="見る").filter_moan is True
    assert VerbSearchConfig(
        verb_display="見[み]る", verb_lemma="見る", filter_moan=False
    ).filter_moan is False


def test_skip_narrator_forces_exclude_narration():
    """--skip-narrator 只能加嚴：per-verb 未設也強制排除旁白。"""
    from scripts.fastapi_client.JP_CoreVerb.generate_child_cards import _build_verb_cfg

    off = _build_verb_cfg("見[み]る", "見る", {}, "ゲーム")
    on = _build_verb_cfg("見[み]る", "見る", {}, "ゲーム", skip_narrator=True)
    per_verb = _build_verb_cfg("見[み]る", "見る", {"exclude_narration": True}, "ゲーム")
    assert off.exclude_narration is False
    assert on.exclude_narration is True
    assert per_verb.exclude_narration is True


# ============================================================
# 複合動詞：lemma 序列視窗比對（2026-09-03）
# ============================================================

def _seq(*items):
    """建 ((lemma, pos1, surface), ...) 序列。"""
    return tuple(items)


class _Tok:
    """假 token：feature 索引 0/7/10 對齊 UniDic。"""

    def __init__(self, surface, lemma, pos, orth=None):
        self.surface = surface
        self.feature = [pos, "", "", "", "", "", "", lemma, "", "", orth or lemma]


def _fake_tagger(mapping):
    """依字串回傳預設 token 列的假 tagger。"""
    def tagger(text):
        return mapping[text]
    return tagger


def test_derive_target_lemmas_single_and_compound():
    from scripts.fastapi_client.JP_CoreVerb.pipeline_components.candidate_validator import (
        derive_target_lemmas, _TARGET_LEMMA_CACHE)
    _TARGET_LEMMA_CACHE.clear()
    tagger = _fake_tagger({
        "見る": [_Tok("見る", "見る", "動詞")],
        "走り出す": [_Tok("走り", "走る", "動詞"), _Tok("出す", "出す", "動詞")],
    })
    assert derive_target_lemmas("見る", tagger) == _seq(("見る", "動詞", "見る"))
    assert derive_target_lemmas("走り出す", tagger) == _seq(
        ("走る", "動詞", "走り"), ("出す", "動詞", "出す"))
    # 快取：第二次不再呼叫 tagger（傳入會 KeyError 的 tagger 仍可取得結果）
    assert derive_target_lemmas("見る", _fake_tagger({})) == _seq(("見る", "動詞", "見る"))
    _TARGET_LEMMA_CACHE.clear()


def test_match_lemma_window_rules():
    from scripts.fastapi_client.JP_CoreVerb.pipeline_components.candidate_validator import (
        match_lemma_window)
    seq = _seq(("走る", "動詞", "走り"), ("出す", "動詞", "出す"))
    tokens = [_Tok("彼", "彼", "代名詞"), _Tok("走り", "走る", "動詞"), _Tok("出し", "出す", "動詞")]
    assert match_lemma_window(tokens, 1, seq) is True
    assert match_lemma_window(tokens, 0, seq) is False      # 位置不對
    assert match_lemma_window(tokens, 2, seq) is False      # 視窗超出尾端
    # 末位詞性必須一致（活用發生處）
    bad_pos = [_Tok("走り", "走る", "動詞"), _Tok("出し", "出す", "名詞")]
    assert match_lemma_window(bad_pos, 0, seq) is False
    # 非末位允許表層兜底（乗り遅れる：孤立 lemma 乗り[名詞]、句中 乗る[動詞]）
    seq2 = _seq(("乗り", "名詞", "乗り"), ("遅れる", "動詞", "遅れる"))
    tokens2 = [_Tok("乗り", "乗る", "動詞"), _Tok("遅れ", "遅れる", "動詞")]
    assert match_lemma_window(tokens2, 0, seq2) is True


def test_covered_by_compound():
    from scripts.fastapi_client.JP_CoreVerb.pipeline_components.candidate_validator import (
        covered_by_compound)
    # 気に入る = 気 + に + 入る；「入る」母卡不該收走這個 token
    seq = _seq(("気", "名詞", "気"), ("に", "助詞", "に"), ("入る", "動詞", "入る"))
    tokens = [_Tok("気", "気", "名詞"), _Tok("に", "に", "助詞"), _Tok("入っ", "入る", "動詞")]
    assert covered_by_compound(tokens, 2, (seq,)) is True
    assert covered_by_compound(tokens, 0, (seq,)) is True
    # 無複合清單時不做檢查
    assert covered_by_compound(tokens, 2, ()) is False
    # 不相關的複合序列不誤判
    other = _seq(("走る", "動詞", "走り"), ("出す", "動詞", "出す"))
    assert covered_by_compound(tokens, 2, (other,)) is False


def test_validate_candidate_compound_window_and_span():
    from scripts.fastapi_client.JP_CoreVerb.pipeline_components.candidate_validator import (
        validate_candidate, REJECTION_COMPOUND_MEMBER, _TARGET_LEMMA_CACHE)
    _TARGET_LEMMA_CACHE.clear()
    sentence = "彼が走り出した"
    tokens = [_Tok("彼", "彼", "代名詞"), _Tok("が", "が", "助詞"),
              _Tok("走り", "走る", "動詞"), _Tok("出し", "出す", "動詞"), _Tok("た", "た", "助動詞")]
    tagger = _fake_tagger({
        sentence: tokens,
        "走り出す": [_Tok("走り", "走る", "動詞"), _Tok("出す", "出す", "動詞")],
        "走る": [_Tok("走る", "走る", "動詞")],
    })
    r = validate_candidate(sentence, "走り出す", False, tagger)
    assert r.accepted
    # span 涵蓋整個複合動詞（走り出し），不是只有後項
    assert sentence[r.candidate.span[0]:r.candidate.span[1]] == "走り出し"
    assert r.candidate.span_token_start == 2 and r.candidate.span_token_index == 3

    # 短動詞「走る」被 compound_seqs 擋下
    compounds = (_seq(("走る", "動詞", "走り"), ("出す", "動詞", "出す")),)
    r2 = validate_candidate(sentence, "走る", False, tagger, compound_seqs=compounds)
    assert not r2.accepted and r2.reason == REJECTION_COMPOUND_MEMBER
    _TARGET_LEMMA_CACHE.clear()


def test_classify_collocation_uses_window_start():
    """複合動詞的搭配詞在第一個 token 之前,不是最後一個 token 之前。"""
    from scripts.fastapi_client.JP_CoreVerb.pipeline_components.diversity_selector import (
        NO_PARTICLE_BUCKET, classify_collocation)
    # ケーキ を 食べ 過ぎ  → 搭配應為「ケーキを」而非「食べ」
    tokens = [_Tok("ケーキ", "ケーキ", "名詞"), _Tok("を", "を", "助詞"),
              _Tok("食べ", "食べる", "動詞"), _Tok("過ぎ", "過ぎる", "動詞")]
    assert classify_collocation(tokens, 3, 2) == "ケーキを"
    # 不傳 start 時退回舊行為：只看最後一個 token 的前一個（食べ 是動詞,
    # 既不是助詞也不是修飾語）→ 無助詞桶,證明複合動詞非傳 start 不可
    assert classify_collocation(tokens, 3) == NO_PARTICLE_BUCKET
