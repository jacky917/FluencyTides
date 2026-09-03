"""同表層多讀讀音判斷（判斷快取表 + 獨立判讀腳本 + 查表過濾）的單元測試。

Unit tests for the reading-judgment feature
(docs/wip/verb_reading_judgments_FEAT_2026-09-02.md §5 回歸測試).
"""

import argparse
import asyncio
from types import SimpleNamespace
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import scripts.common.env  # noqa: F401

from app.api.jp_verb_readings import router as jp_router
from app.core.config import settings
from app.core.exceptions.infrastructure import LLMServiceError
from app.infrastructure.llm import factory as factory_mod
from app.infrastructure.llm.claude_code_client import ClaudeCodeLLMClient
from app.schemas.llm.jp_verb_reading import (
    JudgeReadingItem,
    JudgeReadingsResponse,
    ReadingJudgment,
    ReadingJudgmentLLMOutput,
)
from app.services import jp_verb_reading_service as svc_mod
from scripts.common.jp_homograph_table import HomographEntry, build_homograph_table, reading_of
from scripts.common.jp_reading_filter import PASS, SKIP, UNJUDGED, ReadingFilter, verdict
from scripts.fastapi_client.JP_Common.judge_verb_readings import (
    SurfacePlan,
    reconcile,
    validate_rejudge_args,
)
from scripts.local_anki.common.deletion.profiles import get_profile


# ============================================================
# 多讀表
# ============================================================

def _note(nid, intr, trans):
    return SimpleNamespace(noteId=nid, fields={
        "Intransitive_Word": {"value": intr}, "Transitive_Word": {"value": trans},
    })


class TestHomographTable:
    def test_reading_of(self):
        assert reading_of("埋[う]まる") == "うまる"
        assert reading_of("汚[けが]す") == "けがす"
        assert reading_of("まくる") == "まくる"
        assert reading_of("汚す") == ""  # 無標音含漢字 → 取不到

    def test_keeps_only_multi_reading_surfaces(self):
        profile = get_profile("jp_verb_pair")
        notes = [
            _note(921, "汚[けが]れる", "汚[けが]す"),
            _note(341, "汚[よご]れる", "汚[よご]す"),
            _note(446, "繋[つな]がる", "繋[つな]げる"),
            _note(469, "繋[つな]がる", "繋[つな]ぐ"),   # 繋がる 同讀跨母卡 → 不算多讀
            _note(654, "退[しりぞ]く", "退[しりぞ]ける"),
            _note(657, "退[ど]く", "退[ど]ける"),
            _note(661, "退[の]く", "退[の]ける"),
        ]
        table = build_homograph_table(notes, profile)
        assert set(table) == {"汚れる", "汚す", "退く", "退ける"}
        assert table["汚す"].candidates == ["けがす", "よごす"]
        assert table["汚す"].readings == {"けがす": [921], "よごす": [341]}
        assert table["退く"].candidates == ["しりぞく", "どく", "のく"]

    def test_master_deck_property(self):
        assert get_profile("jp_verb_pair").master_deck == "日本語::自他動詞::Master"
        assert get_profile("jp_core_verb").master_verb_fields == ("Word",)


# ============================================================
# 生卡側查表過濾
# ============================================================

class FakeJudgmentRepo:
    def __init__(self, data):
        self.data = data
        self.calls = 0

    async def get_by_surface(self, session, surface):
        self.calls += 1
        return {sid: SimpleNamespace(reading=r) for sid, r in self.data.get(surface, {}).items()}


def _filter(data):
    table = {"汚す": HomographEntry("汚す", {"けがす": [921], "よごす": [341]})}
    f = ReadingFilter(table)
    f._repo = FakeJudgmentRepo(data)
    return f


class TestReadingFilter:
    def test_verdict(self):
        assert verdict(None, "けがす") == UNJUDGED
        assert verdict("けがす", "けがす") == PASS
        assert verdict("よごす", "けがす") == SKIP
        assert verdict("", "けがす") == SKIP  # 無法判定 → 所有母卡都跳過

    def test_apply_filters_and_counts(self):
        f = _filter({"汚す": {1: "けがす", 2: "よごす", 3: ""}})
        rows = [{"script_id": 1}, {"script_id": 2}, {"script_id": 3}, {"script_id": 4}]
        kept = asyncio.run(f.apply(None, "汚す", "けがす", rows))
        assert [r["script_id"] for r in kept] == [1, 4]
        assert f.stats == {"skipped": 2, "unjudged": 1, "excluded": 0}
        # 同表層只從 DB 載入一次
        asyncio.run(f.apply(None, "汚す", "よごす", rows))
        assert f._repo.calls == 1

    def test_non_homograph_surface_is_untouched(self):
        f = _filter({})
        rows = [{"script_id": 1}]
        assert asyncio.run(f.apply(None, "止める", "やめる", rows)) == rows
        assert f._repo.calls == 0

    def test_apply_accepts_custom_key(self):
        """CoreVerb 的候選是物件不是 dict,靠 key 取 script_id。"""
        f = _filter({"汚す": {1: "けがす", 2: "よごす"}})
        rows = [SimpleNamespace(script_id=1), SimpleNamespace(script_id=2)]
        kept = asyncio.run(f.apply(None, "汚す", "けがす", rows, key=lambda r: r.script_id))
        assert [r.script_id for r in kept] == [1]

    def test_excluded_ids_for_downstream_filtering(self):
        f = _filter({"汚す": {1: "けがす", 2: "よごす", 3: ""}})
        ids = asyncio.run(f.excluded_ids(None, "汚す", "けがす"))
        assert ids == {2, 3}          # 他讀與「無法判定」都排除
        assert f.stats["excluded"] == 2
        assert asyncio.run(f.excluded_ids(None, "止める", "やめる")) == set()

    def test_reading_for_master(self):
        f = _filter({})
        assert f.reading_for_master("汚す", 921) == "けがす"
        assert f.reading_for_master("汚す", 341) == "よごす"
        assert f.reading_for_master("汚す", 999) == ""     # 不屬於此表層的母卡
        assert f.reading_for_master("止める", 711) == ""   # 非多讀表層

    def test_empty_table_behaves_like_today(self):
        f = _filter({})
        rows = [{"script_id": 1}, {"script_id": 2}]
        assert asyncio.run(f.apply(None, "汚す", "けがす", rows)) == rows
        assert f.stats == {"skipped": 0, "unjudged": 2, "excluded": 0}


# ============================================================
# 後端服務與端點
# ============================================================

def _items():
    return [
        JudgeReadingItem(script_id=1, surface="汚す", candidates=["けがす", "よごす"], line="a"),
        JudgeReadingItem(script_id=2, surface="汚す", candidates=["けがす", "よごす"], line="b"),
    ]


class TestServiceNormalize:
    def test_out_of_candidates_and_missing_become_empty(self):
        raw = ReadingJudgmentLLMOutput(results=[
            ReadingJudgment(script_id=1, reading="よごす"),
            ReadingJudgment(script_id=99, reading="けがす"),   # 不在請求內
        ])
        out = svc_mod.normalize_results(_items(), raw)
        assert [(r.script_id, r.reading) for r in out] == [(1, "よごす"), (2, "")]

        raw2 = ReadingJudgmentLLMOutput(results=[ReadingJudgment(script_id=1, reading="きたなす")])
        assert svc_mod.normalize_results(_items()[:1], raw2)[0].reading == ""

    def test_model_whitelist(self):
        with mock.patch.object(svc_mod, "get_modifiable_configs", return_value={"LLM_MODEL_NAME": ["a", "b"]}):
            svc_mod.validate_model_override(None)
            svc_mod.validate_model_override("a")
            try:
                svc_mod.validate_model_override("zzz")
                assert False, "should raise"
            except svc_mod.InvalidOverrideError as e:
                assert "a, b" in str(e)
        with mock.patch.object(svc_mod, "get_modifiable_configs", return_value={}):
            svc_mod.validate_model_override("anything")  # 無白名單 → 不限


def _app():
    app = FastAPI()
    app.include_router(jp_router, prefix="/api/v1")
    return app


class TestEndpoint:
    def test_judge_ok_passes_overrides_and_returns_model(self):
        captured = {}

        async def fake_judge(self, items, *, model=None, effort=None):
            captured.update(model=model, effort=effort, n=len(items))
            return JudgeReadingsResponse(llm_model="(claude-code)haiku-4-5@medium",
                                         results=[ReadingJudgment(script_id=i.script_id, reading="よごす") for i in items])

        with mock.patch.object(svc_mod.JpVerbReadingService, "judge", fake_judge):
            resp = TestClient(_app()).post("/api/v1/jp/verb-readings/judge", json={
                "items": [i.model_dump() for i in _items()], "model": "claude-haiku-4-5", "effort": "medium",
            })
        assert resp.status_code == 200
        assert resp.json()["llm_model"] == "(claude-code)haiku-4-5@medium"
        assert captured == {"model": "claude-haiku-4-5", "effort": "medium", "n": 2}

    def test_more_than_40_items_rejected(self):
        item = _items()[0].model_dump()
        resp = TestClient(_app()).post("/api/v1/jp/verb-readings/judge", json={"items": [item] * 41})
        assert resp.status_code == 422

    def test_invalid_override_is_422(self):
        async def bad(self, items, *, model=None, effort=None):
            raise svc_mod.InvalidOverrideError("model 'x' 不在可用清單內")

        with mock.patch.object(svc_mod.JpVerbReadingService, "judge", bad):
            resp = TestClient(_app()).post("/api/v1/jp/verb-readings/judge",
                                           json={"items": [_items()[0].model_dump()], "model": "x"})
        assert resp.status_code == 422
        assert "不在可用清單" in resp.json()["detail"]


# ============================================================
# LLM client 覆寫
# ============================================================

class TestClientOverrides:
    def test_claude_code_override_is_instance_scoped(self):
        with mock.patch.object(settings, "LLM_PROVIDER", "claude-code"), \
             mock.patch.object(settings, "LLM_MODEL_NAME", "claude-opus-5"), \
             mock.patch.object(settings, "LLM_CLAUDE_CODE_EFFORT", "medium"), \
             mock.patch.object(settings, "LLM_CLAUDE_CODE_OAUTH_TOKEN", ""), \
             mock.patch.object(ClaudeCodeLLMClient, "_resolve_cli_path", return_value="/fake/claude"):
            default = ClaudeCodeLLMClient()
            override = ClaudeCodeLLMClient(model="claude-haiku-4-5", effort="high")
        assert default._formatted_model_name == "(claude-code)opus-5@medium"
        assert override._formatted_model_name == "(claude-code)haiku-4-5@high"
        assert settings.LLM_MODEL_NAME == "claude-opus-5"  # 設定未被改動

    def test_invalid_effort_override_rejected(self):
        with mock.patch.object(settings, "LLM_CLAUDE_CODE_EFFORT", "medium"), \
             mock.patch.object(ClaudeCodeLLMClient, "_resolve_cli_path", return_value="/fake/claude"):
            try:
                ClaudeCodeLLMClient(effort="ultra")
                assert False
            except LLMServiceError as e:
                assert "ultra" in str(e)

    def test_effort_override_on_non_claude_provider_rejected(self):
        with mock.patch.object(settings, "LLM_PROVIDER", "google"):
            try:
                factory_mod.create_llm_client(effort="high")
                assert False
            except LLMServiceError as e:
                assert "claude-code" in str(e)


# ============================================================
# 判讀腳本
# ============================================================

def _args(**kw):
    base = dict(rejudge=False, rejudge_empty=False, rejudge_model=None, surface=None, batch_size=20)
    base.update(kw)
    return argparse.Namespace(**base)


class TestJudgeScript:
    def test_rejudge_args(self):
        assert validate_rejudge_args(_args()) is None
        assert "互斥" in validate_rejudge_args(_args(rejudge=True, rejudge_empty=True, surface=["汚す"]))
        assert "--surface" in validate_rejudge_args(_args(rejudge=True))
        assert validate_rejudge_args(_args(rejudge=True, surface=["汚す"])) is None
        assert validate_rejudge_args(_args(rejudge_empty=True)) is None
        assert "batch-size" in validate_rejudge_args(_args(batch_size=41))

    def test_plan_pending_and_batches(self):
        p = SurfacePlan("汚す", ["けがす", "よごす"],
                        es_ids={1, 2, 3, 4}, existing_ids={4, 5}, judged_ids={2, 5}, rejudge_ids={5})
        assert p.new_ids == {1, 3, 4}
        assert p.pending_ids == [1, 3, 4, 5]   # 重判的 5 即使已判也納入
        assert p.batches(2) == 2
        assert p.batches(20) == 1
        assert SurfacePlan("x", []).batches(20) == 0
        assert "待判    4" in p.summary(20)

    def test_reconcile_lists_mismatches_and_undetermined(self):
        entry = HomographEntry("汚す", {"けがす": [921], "よごす": [341]})
        existing = [(10, 100, 921), (11, 101, 341), (12, 102, 341), (13, 103, 341)]
        judged = {100: "けがす", 101: "けがす", 102: "", 103: "よごす"}
        out = reconcile(existing, judged, entry)
        assert out == [(11, 101, "よごす", "けがす"), (12, 102, "よごす", "")]
