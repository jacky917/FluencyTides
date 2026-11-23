"""人脈卡經歷欄位（``Experiences``）的測試（工作經歷計畫書 person_career_goals_FEAT_2026-11-14 §14）。

Tests for the person card's experiences field.
"""

from datetime import date
from pathlib import Path

from app.bot.handlers import person_profile
from app.bot.utils import person_profile_view as view
from app.schemas.person_profile import PERSON_HISTORY_FIELDS, PersonCreateParams, WhenNote
from app.services import person_profile_ingest as ingest
from app.services import person_profile_topics as topics
from app.services.task_handlers.person_profile_handler import PersonProfileHandler
from tests.test_bot_replies import _fsm, _message, _run, _sent
from tests.test_person_goals import _migration_client, _tap
from tests.test_person_profile_bot import _card_service, _note_service
from tests.test_person_profile_ingest import _result

TODAY = date(2026, 11, 15)
FRONT = Path(__file__).resolve().parents[1] / "app" / "anki_models" / "Person_Profile_Dark_front.html"
YUSHAN = {"value": "去過玉山", "added": "2026-11-15", "ended": ""}


class TestField:
    def test_is_a_history_field_of_whole_sentences_with_a_time(self):
        spec = PERSON_HISTORY_FIELDS["Experiences"]
        assert spec.model is WhenNote and not spec.split and not spec.single

    def test_comes_right_after_interests_in_the_create_form(self):
        names = list(PersonCreateParams.model_fields)
        assert names.index("Experiences") == names.index("Interests") + 1

    def test_searched_with_interests(self):
        (field,) = [f for f in PersonProfileHandler.SEARCH_FIELDS if f.key == "interests"]
        assert field.fields == ("Interests", "Experiences")


class TestTg:
    def test_list_code_section_and_full_view(self):
        assert view.CODE_OF_LIST["Experiences"] == "EX"
        assert "Experiences" in view.SECTION_FIELDS["interests"]
        assert any("Experiences" in names for _, names in view.DISPLAY_GROUPS)

    def test_add_experience(self):
        state, note_service, card_service = _fsm(), _note_service(), _card_service()
        message, _ = _message()
        _run(person_profile.start_add(message, state, 5, "Experiences"))
        typed, _ = _message("去過玉山")
        _run(person_profile.process_add_text(typed, state))
        assert "什麼時候的事" in _sent(typed.answer)[-1]
        when, _ = _message("2019/8")
        _run(person_profile.process_add_text(when, state))
        assert "去過玉山（2019-08）" in _sent(when.answer)[-1]
        _tap("pp:save", state, note_service, card_service)
        note_service.run_action.assert_awaited_once_with(
            5, "add_history", {"field": "Experiences", "item": {"value": "去過玉山", "when": "2019-08"}}
        )


class TestCard:
    def test_shown_under_interests(self):
        source = FRONT.read_text(encoding="utf-8")
        assert '<div id="f-Experiences">{{Experiences}}</div>' in source
        assert "historyRow('經歷', 'Experiences', { byTime: true })" in source


class TestAi:
    def test_ingest_proposes_an_experience(self):
        result = _result(followups=[], field_updates=[], list_updates=[
            {"field": "Experiences", "op": "add", "value": "爬過玉山（2026-10）", "reason": "說上個月爬了玉山"},
        ])
        (proposal,) = [p for p in ingest.proposals_from(result, {}, {}, TODAY) if p.kind == "history_add"]
        assert proposal.data["field"] == "Experiences"
        assert proposal.data["item"]["value"] == "爬過玉山（2026-10）"

    def test_known_experience_is_not_proposed_again(self):
        result = _result(followups=[], field_updates=[], list_updates=[
            {"field": "Experiences", "op": "add", "value": "去過玉山", "reason": "又提到玉山"},
        ])
        proposals = ingest.proposals_from(result, {}, {"Experiences": [YUSHAN]}, TODAY)
        assert not [p for p in proposals if p.kind == "history_add"]

    def test_ingest_prompt_explains_the_split_with_interests_and_goals(self):
        prompt = ingest.build_prompt({"Display_Name": "陳さん"}, {"Experiences": [YUSHAN]}, "上個月爬了玉山", TODAY)
        assert "`Experiences`" in prompt and "去過玉山" in prompt
        assert "做過的具體事件放經歷，還想做的放目標" in prompt  # 興趣的分工說明（欄位分工計畫書 D2）

    def test_topics_read_experiences(self):
        assert "去過玉山" in topics.history_text("Experiences", [YUSHAN])


class TestMigration:
    def test_after_pr2_only_the_new_field_is_added(self):
        from scripts.local_anki.Person_Profile_Dark import migrate_single_card as migration

        fields = [name for name in migration.target_fields() if name != "Experiences"]
        migration, client = _migration_client(fields, ["人脈卡"])
        plan = _run(migration.make_plan(client))
        assert (plan.add_fields, plan.rename, plan.remove, plan.problems) == (["Experiences"], False, False, [])
