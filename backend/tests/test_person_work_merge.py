"""工作內容併進工作經歷、學歷加說明（工作經歷計畫書 person_career_goals_FEAT_2026-11-14 §18）。

Tests for merging the work-detail field into each job's note.
"""

import json
from datetime import date

import pytest

from app.bot.utils import person_profile_view as view
from app.core.exceptions import FieldNotEditableError, InvalidNoteFieldsError
from app.schemas.person_profile import (
    ITEM_EDIT_KEYS,
    PERSON_HISTORY_FIELDS,
    RETIRED_FIELDS,
    PersonCreateParams,
    entry_detail,
    item_fingerprint,
    merged_note,
)
from app.services import person_profile_ingest as ingest
from tests.test_person_profile_handler import PersonAnki, _act, _list, person
from tests.test_person_profile_ingest import _result
from tests.test_person_profile_template import SOURCE

QBC = {"company": "QBC", "title": "リーダー", "note": "前端開發", "added": "2026-11-08", "ended": ""}
NTU = {"value": "台大", "start": "2011", "finish": "2015", "added": "2026-11-01", "ended": ""}


def _add(anki, field, item):
    _act(anki, 1, action="add_history", field=field, item=item)
    return _list(anki, 1, field)


class TestRetiredWorkDetail:
    def test_not_a_list_any_more(self):
        assert "Work_Detail" not in PERSON_HISTORY_FIELDS
        assert "Work_Detail" not in PersonCreateParams.model_fields
        assert "Work_Detail" not in view.LIST_CODES.values()
        assert "historyRow('工作內容'" not in SOURCE

    def test_writing_it_is_refused(self):
        anki = PersonAnki([person(1, Display_Name="陳さん")])
        with pytest.raises(FieldNotEditableError, match="已併進「工作經歷」每份工作的說明"):
            _act(anki, 1, action="update_fields", fields={"Work_Detail": "前端開發"})

    def test_clearing_it_is_allowed(self):
        anki = PersonAnki([person(1, Display_Name="陳さん", Work_Detail="舊的")])
        _act(anki, 1, action="update_fields", fields={"Work_Detail": ""})
        assert anki.value(1, "Work_Detail") == ""
        assert set(RETIRED_FIELDS) == {"Title", "Work_Detail"}


class TestMergedNote:
    @pytest.mark.parametrize("existing,new,expected", [
        ("", "前端開發", "前端開發"),
        ("前端開發", "帶新人", "前端開發\n帶新人"),
        ("前端開發\n帶新人", "帶新人", None),
        ("前端開發", "", None),
    ])
    def test_rules(self, existing, new, expected):
        assert merged_note(existing, new) == expected

    def test_detail_is_one_line(self):
        assert entry_detail("Company", {**QBC, "note": "前端開發\n帶新人"}) == "QBC・リーダー：前端開發／帶新人"
        assert entry_detail("Education", {**NTU, "note": "資工系"}) == "台大（2011～2015）：資工系"


class TestJobNote:
    def test_new_note_is_appended_to_the_current_job(self):
        anki = PersonAnki([person(1, Display_Name="陳さん", Company=json.dumps([QBC]))])
        (job,) = _add(anki, "Company", {"company": "QBC", "title": "リーダー", "note": "最近在帶新人"})
        assert job["note"] == "前端開發\n最近在帶新人"

    def test_same_note_again_is_a_duplicate(self):
        anki = PersonAnki([person(1, Display_Name="陳さん", Company=json.dumps([QBC]))])
        with pytest.raises(InvalidNoteFieldsError, match="已經有這份現職"):
            _add(anki, "Company", {"company": "QBC", "title": "リーダー", "note": "前端開發"})


class TestEducationNote:
    def test_added_with_a_note(self):
        anki = PersonAnki([person(1, Display_Name="陳さん")])
        (school,) = _add(anki, "Education", {"value": "台大", "start": "2011", "note": "資工系"})
        assert school["note"] == "資工系"

    def test_same_school_appends_the_note(self):
        anki = PersonAnki([person(1, Display_Name="陳さん", Education=json.dumps([{**NTU, "note": "資工系"}]))])
        (school,) = _add(anki, "Education", {"value": "台大", "note": "做影像辨識"})
        assert school["note"] == "資工系\n做影像辨識" and school["start"] == "2011"

    def test_note_is_editable(self):
        assert ITEM_EDIT_KEYS["Education"] == ("value", "start", "finish", "note")
        anki = PersonAnki([person(1, Display_Name="陳さん", Education=json.dumps([NTU]))])
        _act(anki, 1, action="edit_item", field="Education", index=0, fingerprint=item_fingerprint(NTU),
             values={"value": "台大", "start": "2011", "finish": "2015", "note": "資工系"})
        assert _list(anki, 1, "Education") == [{**NTU, "note": "資工系"}]

    def test_tg_line_and_card_show_the_note(self):
        line = view.item_line("Education", {**NTU, "note": "資工系\n吉他社"})
        assert "台大（2011～2015）｜資工系／吉他社" in line
        assert "appendNote(line, name, e.item)" in SOURCE and "var NOTE_FIELDS = ['Company', 'Education']" in SOURCE


TODAY = date(2026, 11, 15)


def _adds(list_updates, lists):
    result = _result(followups=[], field_updates=[], list_updates=list_updates)
    return [p for p in ingest.proposals_from(result, {}, lists, TODAY) if p.kind == "history_add"]


class TestIngestNote:
    def test_new_work_content_for_a_known_job_is_proposed(self):
        (p,) = _adds([{"field": "Company", "op": "add", "value": "QBC", "job_title": "リーダー",
                       "note": "最近在帶新人", "reason": "說最近在帶新人"}], {"Company": [QBC]})
        assert p.data["item"]["note"] == "最近在帶新人"

    def test_known_work_content_is_not_proposed(self):
        assert _adds([{"field": "Company", "op": "add", "value": "QBC", "job_title": "リーダー",
                       "note": "前端開發", "reason": ""}], {"Company": [QBC]}) == []

    def test_education_note(self):
        (p,) = _adds([{"field": "Education", "op": "add", "value": "台大", "time_start": "2011",
                       "note": "資工系", "reason": "說台大資工畢業"}], {})
        assert p.data["item"]["note"] == "資工系"

    def test_note_on_other_fields_is_ignored(self):
        (p,) = _adds([{"field": "Interests", "op": "add", "value": "登山", "note": "x", "reason": ""}], {})
        assert "note" not in p.data["item"]


class TestWorkMergeReview:
    """§18.5 審查修正。"""

    def test_same_school_with_a_known_note_is_refused(self):
        anki = PersonAnki([person(1, Display_Name="陳さん", Education=json.dumps([{**NTU, "note": "資工系"}]))])
        with pytest.raises(InvalidNoteFieldsError, match="已經有「台大（2011～2015）：資工系」，沒有新增"):
            _add(anki, "Education", {"value": "台大", "note": "資工系"})

    def test_education_note_proposals(self):
        known = {"Education": [{**NTU, "note": "資工系"}]}
        assert _adds([{"field": "Education", "op": "add", "value": "台大", "note": "資工系", "reason": ""}], known) == []
        (p,) = _adds([{"field": "Education", "op": "add", "value": "台大", "note": "吉他社", "reason": ""}], known)
        assert p.data["item"]["note"] == "吉他社"

    def test_prompt_examples_use_the_output_fields(self):
        """提示詞範例的 list_updates 鍵要和結構化輸出的欄位相同（job_note 改名 note 時手改了好幾處）。"""
        import re
        from pathlib import Path

        from app.schemas.llm.person_profile import IngestListUpdate

        prompt = Path("app/templates/prompts/anki/Person_Profile_Ingest.j2").read_text(encoding="utf-8")
        examples = [line for line in prompt.splitlines() if line.strip().startswith('{"field":') and '"op"' in line]
        assert examples
        for line in examples:
            assert set(re.findall(r'"(\w+)":', line)) == set(IngestListUpdate.model_fields), line
