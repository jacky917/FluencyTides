"""人脈卡 PR 2 的測試：目標、三個新欄位、禮物靈感、名字與照片的隱藏、只留一種卡片的遷移
（工作經歷計畫書 person_career_goals_FEAT_2026-11-14 §6、§7、§12）。

Tests for goals, the new fields, gift ideas, hiding the name and photo,
and the single-card migration.
"""

import json
from datetime import date
from unittest import mock

import pytest
from pydantic import ValidationError

from app.bot.handlers import person_profile
from app.bot.utils import person_profile_view as view
from app.schemas.llm.person_profile import LLMPersonIngestResult
from app.schemas.person_profile import Gift, Goal, item_fingerprint
from app.services import person_profile_ingest as ingest
from app.services import person_profile_topics as topics
from tests.test_bot_replies import _assert_tg_html, _callback, _fsm, _message, _run, _sent
from tests.test_person_career import _start
from tests.test_person_profile_bot import _card_service, _note, _note_service
from tests.test_person_profile_handler import PersonAnki, _act, _list, person

TODAY = date(2026, 11, 15)
N1 = {"kind": "證照", "item": "日本語能力試驗 N1", "target": "2026-12-07", "status": "open", "added": "2026-11-01"}
PR = {"kind": "簽證", "item": "拿到永住", "target": "2028", "status": "done", "added": "", "closed": "2026-10-01"}


def _tap(data, state, note_service=None, card_service=None):
    query = _callback(data)
    _run(person_profile.on_person_button(query, state, note_service or _note_service(), card_service or _card_service()))
    return query


# =============================================================================
# 格式
# =============================================================================


class TestGoalFormat:
    @pytest.mark.parametrize(("typed", "stored"), [("2026/12", "2026-12"), ("2027", "2027"), ("", "")])
    def test_target_is_normalized(self, typed, stored):
        assert Goal(item="N1", target=typed).target == stored

    @pytest.mark.parametrize("value", ["明年", "2026-13", "?"])
    def test_bad_target(self, value):
        with pytest.raises(ValidationError, match="不是可用的時間"):
            Goal(item="N1", target=value)

    def test_status_values(self):
        assert Goal(item="N1").status == "open"
        with pytest.raises(ValidationError):
            Goal(item="N1", status="paused")

    def test_gift_idea(self):
        assert Gift(item="咖啡豆", direction="idea").direction == "idea"


# =============================================================================
# 動作
# =============================================================================


class TestGoalActions:
    def test_add_goal_is_open_with_today(self):
        anki = PersonAnki([person(1, Display_Name="田中さん")])
        with mock.patch("app.services.task_handlers.person_profile_handler.today_iso", return_value="2026-11-15"):
            _act(anki, 1, action="add_item", field="Goals_JSON",
                 item={"kind": "證照", "item": "N1", "target": "2026/12", "status": "done"})
        assert _list(anki, 1, "Goals_JSON") == [
            {"kind": "證照", "item": "N1", "target": "2026-12", "status": "open", "added": "2026-11-15"}
        ]

    def test_done_records_the_day_and_reopen_clears_it(self):
        anki = PersonAnki([person(1, Display_Name="田中さん", Goals_JSON=json.dumps([N1]))])
        fp = item_fingerprint(N1)
        with mock.patch("app.services.task_handlers.person_profile_handler.today_iso", return_value="2026-12-08"):
            _act(anki, 1, action="set_goal_status", index=0, fingerprint=fp, status="done")
        assert _list(anki, 1, "Goals_JSON")[0] == {**N1, "status": "done", "closed": "2026-12-08"}
        _act(anki, 1, action="set_goal_status", index=0, fingerprint=fp, status="open")
        assert _list(anki, 1, "Goals_JSON") == [N1]

    def test_status_change_follows_the_fingerprint(self):
        from app.core.exceptions import JsonItemChangedError

        anki = PersonAnki([person(1, Display_Name="田中さん", Goals_JSON=json.dumps([N1]))])
        with pytest.raises(JsonItemChangedError):
            _act(anki, 1, action="set_goal_status", index=0, fingerprint="zzz", status="done")

    def test_bad_status_is_rejected(self):
        from app.core.exceptions import InvalidNoteFieldsError

        anki = PersonAnki([person(1, Display_Name="田中さん", Goals_JSON=json.dumps([N1]))])
        with pytest.raises(InvalidNoteFieldsError):
            _act(anki, 1, action="set_goal_status", index=0, fingerprint=item_fingerprint(N1), status="paused")


# =============================================================================
# TG
# =============================================================================


class TestGoalTg:
    def test_add_a_goal(self):
        state, note_service, card_service = _fsm(), _note_service(), _card_service()
        message, _ = _message()
        _run(person_profile.start_add(message, state, 5, "Goals_JSON"))
        _tap("pp:ch:0", state, note_service, card_service)  # 建議按鈕：證照
        typed, _ = _message("日本語能力試驗 N1")
        _run(person_profile.process_add_text(typed, state))
        assert "沒有就略過" in _sent(typed.answer)[-1]
        typed, _ = _message("2026/12")
        _run(person_profile.process_add_text(typed, state))
        _tap("pp:save", state, note_service, card_service)
        note_service.run_action.assert_awaited_once_with(5, "add_item", {"field": "Goals_JSON", "item": {
            "kind": "證照", "item": "日本語能力試驗 N1", "target": "2026-12",
        }})

    def test_list_buttons_by_status(self):
        state, note_service = _fsm(), _note_service()
        card_service = _card_service(_note(Goals_JSON=json.dumps([N1, PR])))
        query = _tap("pp:ls:5:GL", state, note_service, card_service)
        (text,) = _sent(query.message.answer)
        _assert_tg_html(text)
        assert "[證照] 日本語能力試驗 N1（目標 2026-12-07）" in text and "<s>[簽證] 拿到永住（目標 2028）</s>（達成 2026-10-01）" in text
        rows = query.message.answer.await_args.kwargs["reply_markup"].inline_keyboard
        fp0, fp1 = item_fingerprint(N1), item_fingerprint(PR)
        assert [(b.text, b.callback_data) for b in rows[0][1:3]] == [
            ("達成 1", f"pp:gs:5:0:{fp0}:d"), ("放棄 1", f"pp:gs:5:0:{fp0}:x"),
        ]
        assert (rows[1][1].text, rows[1][1].callback_data) == ("重開 2", f"pp:gs:5:1:{fp1}:o")

    def test_status_button_and_undo(self):
        state, note_service, card_service = _fsm(), _note_service(), _card_service()
        query = _tap("pp:gs:5:0:abc:d", state, note_service, card_service)
        note_service.run_action.assert_awaited_once_with(
            5, "set_goal_status", {"index": 0, "fingerprint": "abc", "status": "done"}
        )
        assert "已標成達成" in _sent(query.message.answer)[0]
        undo = query.message.answer.await_args.kwargs["reply_markup"].inline_keyboard[0][0]
        assert undo.callback_data == "pp:gs:5:0:abc:o"

    def test_stale_status_code(self):
        state, note_service, card_service = _fsm(), _note_service(), _card_service()
        query = _tap("pp:gs:5:0:abc:z", state, note_service, card_service)
        note_service.run_action.assert_not_awaited()
        assert "按鈕已失效" in _sent(query.message.answer)[0]

    def test_long_press_menu_of_a_goal(self):
        fp = item_fingerprint(N1)
        message, _ = _start(f"pitm-Goals_JSON-0_{fp}-PP_1", _note(Goals_JSON=json.dumps([N1])))
        rows = message.answer.await_args.kwargs["reply_markup"].inline_keyboard
        assert [b.text for b in rows[0]] == ["在卡面隱藏", "達成", "放棄"]

    def test_soon_goal_is_in_the_summary(self):
        later = {**N1, "item": "TOEIC", "target": "2027-06-01"}
        vague = {**N1, "item": "轉職", "target": "2026-12"}
        text = view.summary_text({"Display_Name": "田中さん"}, {"Goals_JSON": [N1, later, vague, PR]}, TODAY)
        assert "日本語能力試驗 N1（12/07，還有 22 天）" in text
        assert "TOEIC" not in text and "轉職" not in text

    def test_gift_idea_line(self):
        assert view.item_line("Gifts_JSON", {"item": "咖啡豆", "direction": "idea"}) == "想要 咖啡豆"


class TestNewFieldsInTg:
    def test_new_fields_have_list_codes_and_sections(self):
        assert {view.CODE_OF_LIST[name] for name in ("Goals_JSON", "Dislikes", "Needs", "Common_Ground")} == {
            "GL", "DL", "ND", "CG"
        }
        assert "Dislikes" in view.SECTION_FIELDS["interests"] and "Needs" in view.SECTION_FIELDS["personality"]

    def test_add_common_ground(self):
        state, note_service, card_service = _fsm(), _note_service(), _card_service()
        message, _ = _message()
        _run(person_profile.start_add(message, state, 5, "Common_Ground"))
        typed, _ = _message("都是新北人")
        _run(person_profile.process_add_text(typed, state))
        _tap("pp:save", state, note_service, card_service)
        note_service.run_action.assert_awaited_once_with(
            5, "add_history", {"field": "Common_Ground", "item": {"value": "都是新北人"}}
        )


# =============================================================================
# 名字與照片的隱藏
# =============================================================================


class TestNameAndPhotoHiding:
    def _anki_card_service(self, note):
        card_service = _card_service(note)
        client = card_service.anki_client
        client.add_tags = mock.AsyncMock()
        client.remove_tags = mock.AsyncMock()
        client.sync = mock.AsyncMock()
        return card_service, client

    def test_photo_long_press_menu(self):
        note = _note(Photo='<img src="a.jpg">')
        note["tags"] = []
        message, _ = _start("pfld-Photo-none-PP_1", note)
        (text,) = _sent(message.answer)
        rows = message.answer.await_args.kwargs["reply_markup"].inline_keyboard
        assert [b.text for b in rows[0]] == ["換照片", "移除照片"]
        assert (rows[1][0].text, rows[1][0].callback_data) == ("在卡面隱藏", "pp:hp:5:h")

    def test_photo_hide_button_adds_the_tag(self):
        card_service, client = self._anki_card_service(_note())
        query = _tap("pp:hp:5:h", _fsm(), _note_service(), card_service)
        client.add_tags.assert_awaited_once_with([5], "人脈卡隱藏::Photo")
        assert "已在卡面隱藏「照片」" in _sent(query.message.answer)[0]
        undo = query.message.answer.await_args.kwargs["reply_markup"].inline_keyboard[0][0]
        assert undo.callback_data == "pp:hp:5:s"

    def test_name_toggle_in_edit_explains_the_scope(self):
        note = _note(Display_Name="田中さん")
        note["tags"] = []
        index = view.EDITABLE_FIELDS.index("Display_Name")
        query = _tap(f"pp:ef:5:{index}", _fsm(), _note_service(), _card_service(note))
        (text,) = _sent(query.message.answer)
        assert "整個名字區" in text
        toggle = query.message.answer.await_args.kwargs["reply_markup"].inline_keyboard[0][0]
        assert toggle.callback_data == f"pp:hf:5:{index}:h"

    def test_fields_the_card_cannot_hide_have_no_toggle(self):
        """其他語言的名字跟著「名字」一起遮，自己沒有開關（按了卡面不會變）。"""
        note = _note(Name_ZH="田中")
        note["tags"] = []
        index = view.EDITABLE_FIELDS.index("Name_ZH")
        query = _tap(f"pp:ef:5:{index}", _fsm(), _note_service(), _card_service(note))
        buttons = query.message.answer.await_args.kwargs["reply_markup"].inline_keyboard[0]
        assert [b.text for b in buttons] == ["取消"]


# =============================================================================
# AI：用一段話記錄、話題
# =============================================================================


def _ingest_result(goals):
    return LLMPersonIngestResult.model_validate({
        "interaction": {"date": "2026-11-15", "topics": "N1"},
        "goals": goals,
    })


class TestGoalProposals:
    def test_goal_is_proposed(self):
        proposals = ingest.proposals_from(_ingest_result([
            {"kind": "證照", "item": "日本語能力試驗 N1", "target": "2026/12/07", "reason": "下個月考"},
        ]), {}, {}, TODAY)
        (goal,) = [p for p in proposals if p.kind == "goal"]
        assert goal.data == {"kind": "證照", "item": "日本語能力試驗 N1", "target": "2026-12-07", "reason": "下個月考"}
        assert person_profile._proposal_action("goal", goal.data) == (
            "add_item", {"field": "Goals_JSON", "item": {"kind": "證照", "item": "日本語能力試驗 N1", "target": "2026-12-07"}}
        )
        line = view.proposal_line("goal", goal.data)
        assert "[證照] 日本語能力試驗 N1（目標 2026-12-07）" in line

    def test_known_or_repeated_goals_are_skipped(self):
        proposals = ingest.proposals_from(_ingest_result([
            {"kind": "證照", "item": "日本語能力試驗 N1", "target": "", "reason": ""},
            {"kind": "夢想", "item": "開咖啡店", "target": "", "reason": ""},
            {"kind": "夢想", "item": "開咖啡店", "target": "", "reason": ""},
        ]), {}, {"Goals_JSON": [N1]}, TODAY)
        assert [p.data["item"] for p in proposals if p.kind == "goal"] == ["開咖啡店"]

    def test_unreadable_target_is_dropped_not_the_goal(self):
        proposals = ingest.proposals_from(_ingest_result([
            {"kind": "職涯", "item": "轉職到外商", "target": "明年左右", "reason": ""},
        ]), {}, {}, TODAY)
        (goal,) = [p for p in proposals if p.kind == "goal"]
        assert "target" not in goal.data

    def test_prompt_lists_known_goals(self):
        prompt = ingest.build_prompt({"Display_Name": "田中さん"}, {"Goals_JSON": [N1, PR]}, "x", TODAY)
        assert "證照：日本語能力試驗 N1（目標 2026-12-07）" in prompt and "（達成）" in prompt
        assert '"goals"' in prompt


class TestTopicsSeeGoalsAndIdeas:
    def test_open_and_done_goals_but_not_dropped(self):
        dropped = {**N1, "item": "放棄的事", "status": "dropped"}
        prompt, _ = topics.build_prompt(
            {"Display_Name": "田中さん"},
            {"Goals_JSON": [N1, PR, dropped], "Gifts_JSON": [{"item": "咖啡豆", "direction": "idea"}]},
            TODAY,
        )
        assert "證照：日本語能力試驗 N1（目標 2026-12-07）" in prompt
        assert "拿到永住" in prompt and "已達成" in prompt
        assert "放棄的事" not in prompt
        assert "想要（還沒送）咖啡豆" in prompt


# =============================================================================
# 遷移腳本（只讀出計畫；實際寫入由使用者執行）
# =============================================================================


def _migration_client(fields, templates, notes=3, kept=3, dropped=2):
    from scripts.local_anki.Person_Profile_Dark import migrate_single_card as migration

    client = mock.MagicMock()
    client.get_model_field_names = mock.AsyncMock(return_value=fields)
    client.get_model_templates = mock.AsyncMock(return_value={name: {} for name in templates})
    client.find_notes = mock.AsyncMock(return_value=list(range(notes)))

    async def find_cards(query):
        return list(range(kept if migration.KEEP_OLD in query else dropped))

    client.find_cards = mock.AsyncMock(side_effect=find_cards)
    return migration, client


class TestMigrationPlan:
    def test_plan_before_the_migration(self):
        from scripts.local_anki.Person_Profile_Dark import migrate_single_card as migration

        old_fields = [name for name in migration.target_fields()
                      if name not in ("Goals_JSON", "Common_Ground", "Needs", "Dislikes")]
        migration, client = _migration_client(old_fields, ["認人", "見面前複習"])
        plan = _run(migration.make_plan(client))
        assert sorted(plan.add_fields) == ["Common_Ground", "Dislikes", "Goals_JSON", "Needs"]
        assert plan.rename and plan.remove and plan.dropped_cards == 2 and not plan.problems
        assert any("會刪掉 2 張認人卡" in line for line in migration.describe(plan))

    def test_nothing_left_after_the_migration(self):
        from scripts.local_anki.Person_Profile_Dark import migrate_single_card as migration

        migration, client = _migration_client(migration.target_fields(), ["人脈卡"])
        plan = _run(migration.make_plan(client))
        assert (plan.add_fields, plan.rename, plan.remove, plan.problems) == ([], False, False, [])

    def test_someone_without_the_kept_card_stops_it(self):
        from scripts.local_anki.Person_Profile_Dark import migrate_single_card as migration

        migration, client = _migration_client(migration.target_fields(), ["認人", "見面前複習"], notes=3, kept=2)
        plan = _run(migration.make_plan(client))
        assert any("有人沒有這張卡" in problem for problem in plan.problems)

    def test_field_order_mismatch_stops_it(self):
        from scripts.local_anki.Person_Profile_Dark import migrate_single_card as migration

        fields = migration.target_fields()
        fields[0], fields[1] = fields[1], fields[0]
        migration, client = _migration_client(fields, ["人脈卡"])
        assert any("順序" in problem for problem in _run(migration.make_plan(client)).problems)

    def test_apply_inserts_fields_at_their_positions_then_renames_and_removes(self):
        from scripts.local_anki.Person_Profile_Dark import migrate_single_card as migration

        wanted = migration.target_fields()
        client = mock.MagicMock()
        client.add_model_field = mock.AsyncMock()
        client.rename_model_template = mock.AsyncMock()
        client.remove_model_template = mock.AsyncMock()
        plan = migration.Plan(add_fields=["Goals_JSON", "Dislikes"], rename=True, remove=True)
        with mock.patch.object(migration, "AnkiSchemaBuilder") as builder:
            builder.return_value.update_model_templates_from_files = mock.AsyncMock()
            _run(migration.apply_plan(client, plan))
        assert [c.args[1] for c in client.add_model_field.await_args_list] == ["Dislikes", "Goals_JSON"]
        assert client.add_model_field.await_args_list[0].kwargs == {"index": wanted.index("Dislikes")}
        client.rename_model_template.assert_awaited_once_with("Person_Profile_Dark", "見面前複習", "人脈卡")
        client.remove_model_template.assert_awaited_once_with("Person_Profile_Dark", "認人")
        builder.return_value.update_model_templates_from_files.assert_awaited_once()
