"""去重鍵漏洞修復的回歸測試（docs/archive/dedup_canonical_lemma_FIX_2026-09-02.md §4）。

Regression tests for the dedup-key fix.

釘住三個根因：
- R1 管線寫入搜尋關鍵字 → DedupManager 只認正規表記
- R2 刪卡工具鏈寫入帶標音表層 → _verb_pair_lemma 去標音
- R3 無文字層去重 → 同文異 id 的分身被擋下
以及存量修復腳本的合併/衝突規則與啟動防線的按母卡判斷。
"""

import asyncio
import json

import scripts.common.env  # noqa: F401

from scripts.common.database.canonicalize_verb_lemma import (
    Row,
    load_keyword_map,
    plan_canonicalization,
)
from scripts.common.sentence_normalize import normalize_sentence
from scripts.common.verb_lemma import canonical_verb_lemma, is_non_canonical_lemma
from scripts.fastapi_client.JP_VerbPair.pipeline_components.dedup_manager import DedupManager
from scripts.local_anki.common.deletion.profiles import get_profile


# ============================================================
# helpers
# ============================================================

class TestNormalizeSentence:
    def test_orthographic_noise_is_flattened(self):
        a = "「けどまあ、そう思ってるならいい機会じゃないか。魅力的な相手と距離が縮まった今こそ」"
        b = "けどまあ　そう思ってるならいい機会じゃないか，魅力的な相手と距離が縮まった今こそ！"
        assert normalize_sentence(a) == normalize_sentence(b)

    def test_fullwidth_and_furigana_and_html(self):
        assert normalize_sentence("２人で<b>話</b>をまとめる[まとめる]と") == normalize_sentence("2人で話をまとめると")

    def test_long_vowel_and_repeat_marks_are_kept(self):
        assert normalize_sentence("えーっと、時々") == "えーっと時々"

    def test_kana_vs_kanji_are_not_equal(self):
        assert normalize_sentence("話をまとめる") != normalize_sentence("話を纏める")

    def test_empty(self):
        assert normalize_sentence("") == ""
        assert normalize_sentence("……！？") == ""


class TestCanonicalVerbLemma:
    def test_strips_furigana(self):
        assert canonical_verb_lemma("纏[まと]める") == "纏める"
        assert canonical_verb_lemma(" 浮[う]かべる ") == "浮かべる"

    def test_plain_unchanged(self):
        assert canonical_verb_lemma("まとめる") == "まとめる"
        assert canonical_verb_lemma("") == ""
        assert canonical_verb_lemma(None) == ""


# ============================================================
# R2: 刪卡工具鏈的 verb_lemma 抽取掛鉤
# ============================================================

class TestVerbPairLemmaHook:
    def test_used_side_is_canonicalized(self):
        profile = get_profile("jp_verb_pair")
        fields = {"Verb_Pair_JSON": {"value": (
            '{"intransitive": "纏[まと]まる", "transitive": "纏[まと]める", "used": "transitive"}'
        )}}
        assert profile.extract_verb_lemma(fields, None) == "纏める"


# ============================================================
# R1 + R3: DedupManager
# ============================================================

class FakeRepo:
    def __init__(self, record=None, logged=None):
        self.record = record
        self.logged = logged or []
        self.dialogue_queries = 0
        self.success_calls = []
        self.failure_calls = []

    async def get_record(self, session, script_id, verb_lemma, *, project):
        return self.record

    async def get_logged_dialogues(self, session, verb_lemma, *, project):
        self.dialogue_queries += 1
        return list(self.logged)

    async def create_or_restore_record(self, session, record_data, *, project):
        self.success_calls.append(record_data)

    async def increment_failure_count(self, session, *args, project):
        self.failure_calls.append(args)


class FakeBuilder:
    def __init__(self):
        self.calls = 0

    async def build(self, **kwargs):
        self.calls += 1
        return [{"is_target": True, "dialogue": "x"}]


def _manager(repo, builder):
    mgr = DedupManager(
        session=None, voice_dir=None, avatar_dir=None, source_game="g", project="jp_verb_pair",
    )
    mgr.repo = repo
    mgr.builder = builder
    return mgr


class TestDedupManagerTextLayer:
    def test_twin_of_logged_line_is_skipped(self):
        repo = FakeRepo(record=None, logged=["「話はまとまった。だったら、ほら早く帰りなさい」"])
        builder = FakeBuilder()
        mgr = _manager(repo, builder)
        result = asyncio.run(mgr.prepare_generation(
            999, "纏まる", "ch", dialogue="話はまとまった、だったらほら早く帰りなさい！",
        ))
        assert result is None
        assert builder.calls == 0

    def test_twin_within_same_run_is_skipped(self):
        repo = FakeRepo(record=None, logged=[])
        builder = FakeBuilder()
        mgr = _manager(repo, builder)
        first = asyncio.run(mgr.prepare_generation(1, "代わる", "ch", dialogue="仕事、代わってくれないかな"))
        second = asyncio.run(mgr.prepare_generation(2, "代わる", "ch", dialogue="「仕事代わってくれないかな？」"))
        assert first is not None
        assert second is None
        assert builder.calls == 1
        assert repo.dialogue_queries == 1  # 每個動詞只從 DB 載入一次

    def test_different_verbs_do_not_interfere(self):
        repo = FakeRepo(record=None, logged=[])
        builder = FakeBuilder()
        mgr = _manager(repo, builder)
        assert asyncio.run(mgr.prepare_generation(1, "出る", "ch", dialogue="出た出た")) is not None
        assert asyncio.run(mgr.prepare_generation(1, "出す", "ch", dialogue="出た出た")) is not None

    def test_no_dialogue_keeps_legacy_behaviour(self):
        repo = FakeRepo(record=None, logged=["同じ文"])
        builder = FakeBuilder()
        mgr = _manager(repo, builder)
        assert asyncio.run(mgr.prepare_generation(1, "v", "ch")) is not None
        assert repo.dialogue_queries == 0

    def test_restored_soft_deleted_record_is_not_blocked_by_its_own_text(self):
        record = {"id": 1, "is_deleted": True, "delete_count": 1, "failure_count": 0, "has_been_generated": True}
        repo = FakeRepo(record=record, logged=["この句"])
        builder = FakeBuilder()
        mgr = _manager(repo, builder)
        assert asyncio.run(mgr.prepare_generation(1, "v", "ch", dialogue="この句")) is not None


class TestDedupManagerRecords:
    def test_success_writes_canonical_lemma_only(self):
        repo = FakeRepo()
        mgr = _manager(repo, FakeBuilder())
        asyncio.run(mgr.record_success(1, "纏める", "ch", 10, llm_model="m"))
        assert repo.success_calls[0]["verb_lemma"] == "纏める"
        assert "search_keyword" not in repo.success_calls[0]

    def test_failure_passes_positional_fields(self):
        repo = FakeRepo()
        mgr = _manager(repo, FakeBuilder())
        asyncio.run(mgr.record_failure(1, "纏める", "ch", 10, "m"))
        assert repo.failure_calls == [(1, "纏める", "g", "ch", 10, "m")]


# ============================================================
# 存量修復腳本
# ============================================================

def _row(id, lemma, *, script_id=1, live=True, deleted=False, dc=0, fc=0, master=100):
    return Row(
        id=id, script_id=script_id, verb_lemma=lemma, project="jp_verb_pair", master_note_id=master,
        is_deleted=deleted, has_card=live, delete_count=dc, failure_count=fc,
    )


KEYWORD_MAP = {"100": {"まとめる": "纏める"}}


class TestCanonicalizePlan:
    def test_plain_rename(self):
        plan = plan_canonicalization([_row(1, "まとめる")], KEYWORD_MAP)
        assert plan.updates == [(1, "纏める")]
        assert not plan.merges and not plan.conflicts

    def test_furigana_stripped_without_map(self):
        plan = plan_canonicalization([_row(1, "纏[まと]める")], {})
        assert plan.updates == [(1, "纏める")]

    def test_already_canonical_is_untouched(self):
        plan = plan_canonicalization([_row(1, "纏める")], KEYWORD_MAP)
        assert not plan.updates and not plan.merges and not plan.conflicts

    def test_live_and_dead_collision_merges_into_live_with_max_counts(self):
        rows = [
            _row(1, "纏める", live=True, dc=0, fc=0),
            _row(2, "まとめる", live=False, deleted=True, dc=3, fc=1),
        ]
        plan = plan_canonicalization(rows, KEYWORD_MAP)
        assert plan.merges == [(1, "纏める", 3, 1, [2])]
        assert not plan.conflicts

    def test_two_live_rows_is_a_conflict_left_untouched(self):
        rows = [_row(128, "纏める"), _row(560, "まとめる"), _row(745, "纏[まと]める")]
        plan = plan_canonicalization(rows, KEYWORD_MAP)
        assert plan.conflicts == [("纏める", [128, 560, 745])]
        assert not plan.updates and not plan.merges

    def test_two_dead_rows_keep_lowest_id(self):
        rows = [_row(5, "まとめる", live=False, deleted=True), _row(3, "纏[まと]める", live=False, deleted=True)]
        plan = plan_canonicalization(rows, KEYWORD_MAP)
        assert plan.merges == [(3, "纏める", 0, 0, [5])]

    def test_core_verb_rows_ignore_keyword_map(self):
        row = Row(
            id=1, script_id=1, verb_lemma="まとめる", project="jp_core_verb", master_note_id=100,
            is_deleted=False, has_card=True, delete_count=0, failure_count=0,
        )
        assert not plan_canonicalization([row], KEYWORD_MAP).updates


class TestLoadKeywordMap:
    def test_accepts_both_config_formats(self, tmp_path):
        """舊格式（list）與新格式（dict 含 extra_keywords）都能映射回標準表層。"""
        cfg = tmp_path / "kw.json"
        cfg.write_text(json.dumps({
            "100": {"捲[まく]る": ["まくる"], "捲れる": {"extra_keywords": ["まくれる"], "allow_auxiliary": True}},
            "200": {"繋げる": {"allow_auxiliary": True}},
        }), encoding="utf-8")
        km = load_keyword_map(cfg)
        assert km["100"] == {"まくる": "捲る", "まくれる": "捲れる"}
        assert km["200"] == {}

    def test_missing_file_is_empty(self, tmp_path):
        assert load_keyword_map(tmp_path / "nope.json") == {}


class TestIsNonCanonicalLemma:
    """啟動防線的判斷必須按母卡,不能用全域關鍵字集合。"""

    KM = {"557": {"汚す": "穢す", "汚れる": "穢れる"}, "540": {"まくる": "捲る"}}

    def test_keyword_of_own_master_is_non_canonical(self):
        assert is_non_canonical_lemma("汚す", 557, self.KM) is True
        assert is_non_canonical_lemma("まくる", "540", self.KM) is True

    def test_same_string_is_canonical_for_another_master(self):
        # 汚す 是母卡 921(汚[よご]す)的標準表層,不能因為它是 557 的關鍵字而誤判
        assert is_non_canonical_lemma("汚す", 921, self.KM) is False

    def test_furigana_is_always_non_canonical(self):
        assert is_non_canonical_lemma("纏[まと]める", 999, {}) is True

    def test_plain_canonical_is_fine(self):
        assert is_non_canonical_lemma("捲る", 540, self.KM) is False
        assert is_non_canonical_lemma("止める", 711, {}) is False
