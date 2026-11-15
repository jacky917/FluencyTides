"""沒有公司與職稱的工作經歷（工作經歷計畫書 person_career_goals_FEAT_2026-11-14 §17）。

Tests for jobs recorded without a company or a title.
"""

import json

import pytest

from app.core.exceptions import InvalidNoteFieldsError
from app.schemas.person_profile import PERSON_HISTORY_FIELDS, UNNAMED_POSITION, entry_text, is_active
from tests.test_person_profile_handler import PersonAnki, _act, _list, person
from tests.test_person_profile_template import SOURCE

ABROAD = {"company": "", "title": "", "end": "?", "note": "外商，工作 7 年"}


def _add(anki, item):
    _act(anki, 1, action="add_history", field="Company", item=item)
    return _list(anki, 1, "Company")


class TestUnnamedJob:
    def test_note_alone_is_a_job(self):
        job = PERSON_HISTORY_FIELDS["Company"].model.model_validate({**ABROAD, "added": "", "ended": ""})
        assert job.note == "外商，工作 7 年"

    def test_empty_job_is_refused(self):
        anki = PersonAnki([person(1, Display_Name="陳さん")])
        with pytest.raises(InvalidNoteFieldsError, match="至少要有公司、職稱、任職時間或說明"):
            _add(anki, {"company": "", "title": ""})

    def test_shown_as_unknown_company(self):
        assert entry_text("Company", ABROAD) == UNNAMED_POSITION == "公司不明"
        assert "|| '公司不明'" in SOURCE  # 卡面 entryText 相同

    def test_past_jobs_with_different_notes_are_both_kept(self):
        anki = PersonAnki([person(1, Display_Name="陳さん")])
        _add(anki, ABROAD)
        items = _add(anki, {**ABROAD, "note": "日商，工作 2 年"})
        assert [item["note"] for item in items] == ["外商，工作 7 年", "日商，工作 2 年"]

    def test_same_past_job_twice_is_a_duplicate(self):
        anki = PersonAnki([person(1, Display_Name="陳さん")])
        _add(anki, ABROAD)
        with pytest.raises(InvalidNoteFieldsError, match="已經有這份以前的工作"):
            _add(anki, ABROAD)

    def test_current_unnamed_jobs_are_not_merged(self):
        """兩份不同的「公司不明」現職不能被當成同一份（不然第二份的說明會被補進第一份或被擋）。"""
        anki = PersonAnki([person(1, Display_Name="陳さん")])
        _add(anki, {"company": "", "title": "", "note": "做業務"})
        items = _add(anki, {"company": "", "title": "", "note": "週末兼差"})
        assert [item["note"] for item in items] == ["做業務", "週末兼差"] and all(map(is_active, items))

    def test_named_jobs_still_merge(self):
        anki = PersonAnki([person(1, Display_Name="陳さん", Company=json.dumps(
            [{"company": "QBC", "title": "QA", "added": "2026-11-08", "ended": ""}]))])
        (job,) = _add(anki, {"company": "QBC", "title": "QA", "start": "2025"})
        assert job["start"] == "2025"

    def test_edit_can_clear_company_and_title(self):
        from app.schemas.person_profile import item_fingerprint

        job = {"company": "外商", "title": "", "end": "?", "note": "工作 7 年", "added": "2026-11-08", "ended": ""}
        anki = PersonAnki([person(1, Display_Name="陳さん", Company=json.dumps([job]))])
        _act(anki, 1, action="edit_item", field="Company", index=0, fingerprint=item_fingerprint(job),
             values={"company": "", "title": "", "start": "", "end": "?", "note": "外商，工作 7 年"})
        (edited,) = _list(anki, 1, "Company")
        assert entry_text("Company", edited) == "公司不明" and edited["note"] == "外商，工作 7 年"


class TestUnnamedJobReview:
    """§18.5 審查修正。"""

    def test_restoring_a_job_that_only_has_an_end_is_refused(self):
        """只有離職時間的工作恢復現職就什麼都沒有了：擋下、不寫入。"""
        from app.schemas.person_profile import item_fingerprint

        only_end = {"company": "", "title": "", "end": "?", "added": "2026-11-15", "ended": ""}
        anki = PersonAnki([person(1, Display_Name="陳さん", Company=json.dumps([only_end]))])
        with pytest.raises(InvalidNoteFieldsError, match="先按「修改」補上公司、職稱或說明"):
            _act(anki, 1, action="set_history_status", field="Company", index=0,
                 fingerprint=item_fingerprint(only_end), ended=False)
        assert _list(anki, 1, "Company") == [only_end]

    def test_restoring_a_job_with_a_note_still_works(self):
        from app.schemas.person_profile import item_fingerprint

        anki = PersonAnki([person(1, Display_Name="陳さん", Company=json.dumps([{**ABROAD, "added": "", "ended": ""}]))])
        _act(anki, 1, action="set_history_status", field="Company", index=0,
             fingerprint=item_fingerprint({**ABROAD, "added": "", "ended": ""}), ended=False)
        (job,) = _list(anki, 1, "Company")
        assert is_active(job) and job["note"] == "外商，工作 7 年"

    def test_unnamed_jobs_have_different_fingerprints(self):
        from app.schemas.person_profile import item_fingerprint

        a, b = {**ABROAD, "added": "", "ended": ""}, {**ABROAD, "note": "日商", "added": "", "ended": ""}
        assert item_fingerprint(a) != item_fingerprint(b)
        # 有名字的指紋不含說明（補說明後卡面的舊連結仍對得上）
        named = {"company": "QBC", "title": "QA", "added": "", "ended": ""}
        assert item_fingerprint(named) == item_fingerprint({**named, "note": "前端開發"})

    @pytest.mark.skipif(__import__("shutil").which("node") is None, reason="需要 node 才能執行卡片模板的 JS")
    def test_card_fingerprint_matches_for_unnamed_jobs(self):
        from app.schemas.person_profile import item_fingerprint
        from tests.test_person_profile_template import _run_card_js

        items = [{**ABROAD, "added": "", "ended": ""}, {"company": "", "title": None, "note": "日商"},
                 {"company": "QBC", "note": "x"}, "壞掉的項目"]
        result = _run_card_js(["itemFingerprint"], f"console.log(JSON.stringify({json.dumps(items)}.map(itemFingerprint)))")
        assert result == [item_fingerprint(item) for item in items]

    def test_add_history_refuses_keys_of_other_fields(self):
        from app.core.exceptions import ActionContractError

        anki = PersonAnki([person(1, Display_Name="陳さん")])
        with pytest.raises(ActionContractError, match="沒有這些鍵：note、start"):
            _act(anki, 1, action="add_history", field="Interests", item={"value": "登山", "note": "y", "start": "2020"})

    def test_prompt_explains_unknown_company(self):
        from pathlib import Path

        prompt = Path("app/templates/prompts/anki/Person_Profile_Ingest.j2").read_text(encoding="utf-8")
        assert "`value` 也不要填「公司不明」" in prompt
