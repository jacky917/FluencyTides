"""人脈卡「身分與證照」（``Credentials``）、「生活圈與日常」（``Daily_Life``）與欄位分工表的測試。

Tests for the credentials and daily-life fields and the field guides (person_field_routing_FEAT_2026-11-23).
"""

import json
import re
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.bot.handlers import person_profile
from app.bot.utils import person_profile_view as view
from app.schemas.person_profile import (
    HISTORY_PERIOD_KEYS,
    ITEM_EDIT_KEYS,
    NOTE_FIELDS,
    PERSON_HISTORY_FIELDS,
    CredentialEntry,
    HistoryNote,
    PersonCreateParams,
    history_period,
    is_active,
    item_fingerprint,
)
from app.services import person_profile_ingest as ingest
from app.services import person_profile_topics as topics
from app.services.person_field_guide import FIELD_GUIDES, FIELD_NOTE_HINTS, FIELD_TIME_HINTS
from app.services.task_handlers.person_profile_handler import PersonProfileHandler
from tests.test_bot_replies import _fsm, _message, _run, _sent
from tests.test_person_goals import _migration_client, _tap
from tests.test_person_profile_bot import _card_service, _note_service
from tests.test_person_profile_handler import PersonAnki, _act, _list, person
from tests.test_person_profile_ingest import _result

TODAY = date(2026, 11, 23)
FRONT = Path(__file__).resolve().parents[1] / "app" / "anki_models" / "Person_Profile_Dark_front.html"
VISA = {"value": "工作簽證", "added": "2026-11-23", "ended": "", "until": "2028-03"}
COMMUTE = {"value": "每天搭東急東橫線通勤", "added": "2026-11-23", "ended": ""}


class TestFields:
    def test_credentials_have_a_start_an_expiry_and_a_note(self):
        spec = PERSON_HISTORY_FIELDS["Credentials"]
        assert spec.model is CredentialEntry and not spec.split and not spec.single
        assert HISTORY_PERIOD_KEYS["Credentials"] == ("since", "until") and "Credentials" in NOTE_FIELDS

    def test_an_expiry_does_not_strike_the_entry_out(self):
        # 到期叫 until 不叫 end：is_active 把有 end 的當成已劃掉
        assert is_active(VISA)
        assert history_period("Credentials", VISA) == "～2028-03"

    def test_expiry_before_start_is_rejected(self):
        with pytest.raises(ValidationError, match="到期"):
            CredentialEntry.model_validate({"value": "駕照", "since": "2024", "until": "2020"})

    def test_daily_life_is_one_sentence_per_entry(self):
        spec = PERSON_HISTORY_FIELDS["Daily_Life"]
        assert spec.model is HistoryNote and not spec.split and not spec.single

    def test_create_form_places_them_next_to_residence_and_languages(self):
        names = list(PersonCreateParams.model_fields)
        assert names.index("Daily_Life") == names.index("Residence") + 1
        assert names.index("Credentials") == names.index("Languages") + 1

    def test_web_search_buttons(self):
        fields = {f.key: f.fields for f in PersonProfileHandler.SEARCH_FIELDS}
        assert fields["daily"] == ("Daily_Life",) and fields["credentials"] == ("Credentials",)


class TestTg:
    def test_list_codes_section_and_full_view(self):
        assert view.CODE_OF_LIST["Credentials"] == "CR" and view.CODE_OF_LIST["Daily_Life"] == "DY"
        assert {"Credentials", "Daily_Life"} <= set(view.SECTION_FIELDS["context"])
        grouped = {name for _, names in view.DISPLAY_GROUPS for name in names}
        assert {"Credentials", "Daily_Life"} <= grouped

    def test_add_a_credential_asks_start_expiry_and_note(self):
        state, note_service, card_service = _fsm(), _note_service(), _card_service()
        message, _ = _message()
        _run(person_profile.start_add(message, state, 5, "Credentials"))
        for answer, question in (("工作簽證", "什麼時候取得"), ("2024/4", "什麼時候到期"), ("2028-03", "補充說明")):
            typed, _ = _message(answer)
            _run(person_profile.process_add_text(typed, state))
            assert question in _sent(typed.answer)[-1]
        note, _ = _message("效期 3 年")
        _run(person_profile.process_add_text(note, state))
        assert "工作簽證（2024-04～2028-03）" in _sent(note.answer)[-1]
        _tap("pp:save", state, note_service, card_service)
        note_service.run_action.assert_awaited_once_with(5, "add_history", {"field": "Credentials", "item": {
            "value": "工作簽證", "since": "2024-04", "until": "2028-03", "note": "效期 3 年",
        }})

    def test_expiry_before_start_is_asked_again(self):
        state = _fsm()
        message, _ = _message()
        _run(person_profile.start_add(message, state, 5, "Credentials"))
        for answer in ("駕照", "2024"):
            typed, _ = _message(answer)
            _run(person_profile.process_add_text(typed, state))
        early, _ = _message("2020")
        _run(person_profile.process_add_text(early, state))
        assert "到期" in _sent(early.answer)[-1] and "早於" in _sent(early.answer)[-1]


class TestCard:
    def test_shown_in_the_context_section(self):
        source = FRONT.read_text(encoding="utf-8")
        assert '<div id="f-Credentials">{{Credentials}}</div>' in source
        assert '<div id="f-Daily_Life">{{Daily_Life}}</div>' in source
        assert "historyRow('身分與證照', 'Credentials')" in source and "historyRow('生活圈', 'Daily_Life')" in source


class TestHandler:
    def test_add_with_start_expiry_and_note(self):
        anki = PersonAnki([person(1, Display_Name="陳さん")])
        _act(anki, 1, action="add_history", field="Credentials",
             item={"value": "工作簽證", "since": "2024/4", "until": "2028-03", "note": "效期 3 年"})
        (entry,) = _list(anki, 1, "Credentials")
        assert (entry["since"], entry["until"], entry["note"]) == ("2024-04", "2028-03", "效期 3 年")
        assert is_active(entry)

    def test_strike_out_and_restore_keep_the_expiry(self):
        # 到期叫 until 的理由：劃掉與恢復都不能碰它（恢復時會清掉 end）
        anki = PersonAnki([person(1, Display_Name="陳さん", Credentials=json.dumps([VISA]))])
        for ended in (True, False):
            (entry,) = _list(anki, 1, "Credentials")
            _act(anki, 1, action="set_history_status", field="Credentials", index=0,
                 fingerprint=item_fingerprint(entry), ended=ended)
            (entry,) = _list(anki, 1, "Credentials")
            assert entry["until"] == "2028-03" and is_active(entry) is not ended

    def test_same_credential_with_a_new_note_merges_it(self):
        anki = PersonAnki([person(1, Display_Name="陳さん", Credentials=json.dumps([VISA]))])
        _act(anki, 1, action="add_history", field="Credentials", item={"value": "工作簽證", "note": "公司幫忙辦"})
        (entry,) = _list(anki, 1, "Credentials")
        assert entry["note"] == "公司幫忙辦" and entry["until"] == "2028-03"


class TestGuides:
    # 說明涵蓋的欄位由 person_profile_ingest 在 import 時 assert（少了、多了都 import 失敗），這裡測的是用在提示詞裡

    def test_prompt_routing_table_comes_from_the_guides(self):
        for mode in ("update", "create"):
            prompt = ingest.build_prompt({"Display_Name": "x"}, {}, "t", TODAY, mode)
            for name in (*PERSON_HISTORY_FIELDS, *ingest.UPDATABLE_FIELDS):
                assert f"- `{name}`（" in prompt and FIELD_GUIDES[name] in prompt, name
            for name, hints in FIELD_TIME_HINTS.items():
                for slot, hint in zip(("time_start", "time_end"), hints, strict=False):
                    assert f"`{slot}`：{hint}" in prompt, (name, slot)
            for name, hint in FIELD_NOTE_HINTS.items():
                assert f"`note`：{hint}" in prompt, name
            assert "備註（`Notes`）" in prompt

    def test_create_mode_does_not_teach_striking_out(self):
        prompt = ingest.build_prompt({}, {}, "t", TODAY, "create")
        assert '用 `op: "end"` 劃掉它' not in prompt and "已知的現職只是補上" not in prompt

    def test_prompt_lists_known_credentials_and_daily_life(self):
        prompt = ingest.build_prompt(
            {"Display_Name": "x"}, {"Credentials": [VISA], "Daily_Life": [COMMUTE]}, "t", TODAY
        )
        assert "工作簽證（～2028-03）" in prompt and "每天搭東急東橫線通勤" in prompt

    def test_dialog_gets_the_key_names_from_the_models(self):
        labels = ingest.item_labels()
        assert labels["Credentials"] == {"value": "內容", "since": "取得", "until": "到期", "note": "說明"}
        assert labels["Education"]["start"] == "入學" and labels["Daily_Life"] == {"value": "內容"}
        # 鍵與順序就是輸入框（同 ITEM_EDIT_KEYS）；每個鍵都有中文名稱（拿鍵名當名稱時前端不會退回後備，會露出英文）
        assert {name: list(keys) for name, keys in labels.items()} == {
            name: list(keys) for name, keys in ITEM_EDIT_KEYS.items()
        }
        assert all(label != key for keys in labels.values() for key, label in keys.items())


class TestAi:
    def test_ingest_proposes_a_credential_with_its_expiry_and_note(self):
        result = _result(followups=[], field_updates=[], list_updates=[{
            "field": "Credentials", "op": "add", "value": "工作簽證", "time_end": "2028-03", "note": "效期 3 年",
            "reason": "工作簽證到 2028 年 3 月",
        }])
        (proposal,) = [p for p in ingest.proposals_from(result, {}, {}, TODAY) if p.kind == "history_add"]
        assert proposal.data["field"] == "Credentials"
        item = proposal.data["item"]
        assert {key: item.get(key) for key in ("value", "since", "until", "note")} == {
            "value": "工作簽證", "since": None, "until": "2028-03", "note": "效期 3 年",
        }

    def test_renewal_strikes_out_the_old_entry_and_adds_the_new_one(self):
        # 續簽：劃掉舊的＋新增同名、新到期日的一筆；不論 LLM 先寫哪一個，兩項都在，而且劃掉的排在前面（先寫入）
        end = {"field": "Credentials", "op": "end", "entry": 1, "reason": "簽證續了"}
        add = {"field": "Credentials", "op": "add", "value": "工作簽證", "time_end": "2031-03", "reason": "續到 2031 年 3 月"}
        for order in ([end, add], [add, end]):
            result = _result(followups=[], field_updates=[], list_updates=order)
            proposals = [p for p in ingest.proposals_from(result, {}, {"Credentials": [VISA]}, TODAY)
                         if p.kind.startswith("history")]
            assert [p.kind for p in proposals] == ["history_end", "history_add"]
            assert proposals[1].data["item"]["until"] == "2031-03"

    def test_only_a_new_expiry_without_striking_out_is_dropped_and_logged(self, caplog):
        result = _result(followups=[], field_updates=[], list_updates=[
            {"field": "Credentials", "op": "add", "value": "工作簽證", "time_end": "2031-03", "reason": "續到 2031"},
        ])
        assert not [p for p in ingest.proposals_from(result, {}, {"Credentials": [VISA]}, TODAY)
                    if p.kind == "history_add"]
        assert "續簽、換新要先劃掉舊的" in caplog.text

    def test_striking_out_and_re_adding_the_same_thing_is_not_proposed(self):
        # 劃掉又新增一模一樣的（沒有新的時間或說明）：兩個建議互相矛盾，新增那項略過
        result = _result(followups=[], field_updates=[], list_updates=[
            {"field": "Daily_Life", "op": "end", "entry": 1, "reason": "不搭了"},
            {"field": "Daily_Life", "op": "add", "value": COMMUTE["value"], "reason": "還是搭"},
        ])
        proposals = ingest.proposals_from(result, {}, {"Daily_Life": [COMMUTE]}, TODAY)
        assert [p.kind for p in proposals if p.kind.startswith("history")] == ["history_end"]

    def test_ingest_proposes_daily_life(self):
        result = _result(followups=[], field_updates=[], list_updates=[
            {"field": "Daily_Life", "op": "add", "value": "下班走到大江戶線搭車", "reason": "下班要走到江戶線坐車"},
        ])
        (proposal,) = [p for p in ingest.proposals_from(result, {}, {}, TODAY) if p.kind == "history_add"]
        assert (proposal.data["field"], proposal.data["item"]["value"]) == ("Daily_Life", "下班走到大江戶線搭車")

    def test_topics_read_both_fields(self):
        assert "工作簽證" in topics.history_text("Credentials", [VISA])
        assert "東橫線" in topics.history_text("Daily_Life", [COMMUTE])


class TestMigration:
    def test_only_the_two_new_fields_are_added(self):
        from scripts.local_anki.Person_Profile_Dark import migrate_single_card as migration

        fields = [name for name in migration.target_fields() if name not in ("Credentials", "Daily_Life")]
        migration, client = _migration_client(fields, ["人脈卡"])
        plan = _run(migration.make_plan(client))
        assert (sorted(plan.add_fields), plan.rename, plan.remove, plan.problems) == (
            ["Credentials", "Daily_Life"], False, False, [],
        )
