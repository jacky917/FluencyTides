"""人脈卡 handler 的測試（計畫書 docs/wip/person_profile_card_FEAT_2026-10-23.md §3.11）。

Tests for the person profile handler.
"""

import asyncio
import json
import re
from datetime import date

import pytest
from pydantic import ValidationError

from app.core.exceptions import FieldNotEditableError, InvalidNoteFieldsError, JsonItemChangedError
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager
from app.schemas.anki import AnkiNoteInfo
from app.schemas.person_profile import PERSON_NOTE_TYPE, PersonCreateParams, item_fingerprint
from app.services.task_handlers.person_profile_handler import PersonProfileHandler, new_card_id
from app.services.task_handlers.registry import handler_registry
from tests.test_handler_base import FakeAnki, _card_service, _relations

PERSON_FIELDS = json.load(open("app/anki_models/Person_Profile_Dark.json", encoding="utf-8"))["inOrderFields"]
JSON_FIELDS = ["Interactions_JSON", "Followups_JSON", "Family_JSON", "Gifts_JSON", "Key_Dates_JSON", "Contacts_JSON"]


def _run(coro):
    return asyncio.run(coro)


def person(note_id: int, **values: str) -> AnkiNoteInfo:
    """一筆人脈卡筆記，沒給的欄位為空。A person note with empty defaults."""
    return AnkiNoteInfo(
        noteId=note_id,
        modelName=PERSON_NOTE_TYPE,
        tags=["PersonProfile"],
        fields={name: {"value": values.get(name, ""), "order": i} for i, name in enumerate(PERSON_FIELDS)},
        cards=[note_id + 1],
    )


class PersonAnki(FakeAnki):
    """記憶體裡的 AnkiConnect，認得人脈卡，寫入會套用到筆記上（之後讀得到）。"""

    def __init__(self, notes=()) -> None:
        super().__init__(notes)
        self.models[PERSON_NOTE_TYPE] = PERSON_FIELDS

    async def update_note_fields(self, note_id: int, fields: dict[str, str]) -> None:
        await super().update_note_fields(note_id, fields)
        note = self.notes[note_id]
        for name, value in fields.items():
            note.fields[name]["value"] = value

    def value(self, note_id: int, name: str) -> str:
        return self.notes[note_id].fields[name]["value"]


def _create(anki: PersonAnki, **params) -> dict[str, str]:
    """經 CREATE_PARAMS 驗證後建立，回傳寫入的欄位。"""
    parameters = PersonCreateParams.model_validate(params).model_dump()
    parameters["tg_bot"] = "FluencyTidesBot"
    _run(PersonProfileHandler().execute_create(_card_service(anki), _relations(), "人脈", PERSON_NOTE_TYPE, parameters))
    (note,) = anki.added
    return note.fields


class TestDeclarations:
    def test_registered_with_capabilities(self):
        handler = handler_registry.get_handler("person_profile")
        caps = handler.CAPABILITIES.to_dict()
        assert caps["create"]["supported"] and caps["create"]["channels"] == ["web", "tg"]
        assert caps["create"]["note_type"] == PERSON_NOTE_TYPE
        assert caps["update_fields"]["supported"] and caps["delete"]["supported"]
        assert handler_registry.handler_for_note_type(PERSON_NOTE_TYPE) is handler

    def test_json_and_photo_fields_are_locked(self):
        handler = PersonProfileHandler()
        assert list(handler.JSON_FIELDS) == JSON_FIELDS
        assert handler.MEDIA_FIELDS == ("Photo",)

    def test_create_form_has_no_system_or_locked_fields(self):
        properties = PersonCreateParams.model_json_schema()["properties"]
        for name in [*JSON_FIELDS, "Photo", "Card_ID", "TG_Bot", "Updated_At"]:
            assert name not in properties
        # 其餘欄位都在表單上，順序與筆記類型相同
        expected = [n for n in PERSON_FIELDS if n not in {*JSON_FIELDS, "Photo", "Card_ID", "TG_Bot", "Updated_At"}]
        assert list(properties) == expected


class TestCreateParams:
    def test_only_display_name_is_required(self):
        assert PersonCreateParams.model_validate({"Display_Name": " 田中さん "}).Display_Name == "田中さん"
        with pytest.raises(ValidationError):
            PersonCreateParams.model_validate({"Display_Name": "  "})

    @pytest.mark.parametrize(
        ("field", "value"),
        [("Birthday", "11/04"), ("Met_Date", "2026-6-7"), ("Closeness", "6"), ("Contact_Cadence", "0"),
         ("Introducer_Note_ID", "abc")],
    )
    def test_rejects_malformed_values(self, field, value):
        with pytest.raises(ValidationError):
            PersonCreateParams.model_validate({"Display_Name": "田中さん", field: value})

    @pytest.mark.parametrize("birthday", ["", "1994-03-15", "--03-15"])
    def test_birthday_formats(self, birthday):
        assert PersonCreateParams.model_validate({"Display_Name": "田中さん", "Birthday": birthday}).Birthday == birthday

    def test_unknown_or_locked_fields_rejected(self):
        for field in ("Interactions_JSON", "Photo", "Card_ID", "Nickname"):
            with pytest.raises(ValidationError):
                PersonCreateParams.model_validate({"Display_Name": "田中さん", field: "x"})


class TestCreate:
    def test_fills_system_fields(self):
        anki = PersonAnki()
        fields = _create(anki, Display_Name="田中さん", Company="Geekly", Interests="登山, 精釀啤酒")
        assert fields["Display_Name"] == "田中さん"
        assert fields["Company"] == "Geekly"
        assert re.fullmatch(r"PP_\d{8}_\d{6}_[0-9a-f]{4}", fields["Card_ID"])
        assert fields["TG_Bot"] == "FluencyTidesBot"
        assert fields["Updated_At"] == date.today().isoformat()
        for name in JSON_FIELDS:
            assert fields[name] == "[]"
        # 沒填的欄位留空
        assert fields["Name_EN"] == ""
        assert anki.added[0].tags == ["PersonProfile", "HandlerGenerated"]

    def test_allows_duplicate_display_names(self):
        """不同的人可以都叫「田中さん」：建立時不擋首欄位重複。"""
        anki = PersonAnki()
        _create(anki, Display_Name="田中さん")
        assert anki.added[0].options.allowDuplicate is True

    def test_card_ids_differ_within_the_same_second(self):
        assert len({new_card_id() for _ in range(50)}) == 50


class TestUpdate:
    def test_updates_fields_and_stamps_updated_at(self):
        anki = PersonAnki([person(1, Display_Name="田中さん", Card_ID="PP_x", Updated_At="2026-01-01")])
        _run(PersonProfileHandler().execute_update(
            _card_service(anki), _relations(), 1, {"action": "update_fields", "fields": {"Company": "Geekly"}}
        ))
        assert anki.value(1, "Company") == "Geekly"
        assert anki.value(1, "Updated_At") == date.today().isoformat()

    def test_no_change_writes_nothing(self):
        anki = PersonAnki([person(1, Display_Name="田中さん", Company="Geekly", Updated_At="2026-01-01")])
        _run(PersonProfileHandler().execute_update(
            _card_service(anki), _relations(), 1, {"action": "update_fields", "fields": {"Company": "Geekly"}}
        ))
        assert anki.updated == []
        assert anki.value(1, "Updated_At") == "2026-01-01"

    @pytest.mark.parametrize("field", ["Card_ID", "Photo", "Interactions_JSON"])
    def test_locked_fields(self, field):
        anki = PersonAnki([person(1, Display_Name="田中さん", Card_ID="PP_x")])
        with pytest.raises(FieldNotEditableError):
            _run(PersonProfileHandler().execute_update(
                _card_service(anki), _relations(), 1, {"action": "update_fields", "fields": {field: "x"}}
            ))
        assert anki.updated == []


class TestDelete:
    def test_standard_delete(self):
        anki = PersonAnki([person(1, Display_Name="田中さん")])
        _run(PersonProfileHandler().execute_delete(_card_service(anki), _relations(), 1))
        assert anki.deleted == [1]


# =============================================================================
# 具名動作（§3.11）
# =============================================================================


def _act(anki: PersonAnki, note_id: int, **parameters) -> None:
    _run(PersonProfileHandler().execute_update(_card_service(anki), _relations(), note_id, parameters))


def _list(anki: PersonAnki, note_id: int, field: str) -> list:
    return AnkiJsonFieldManager.parse_field_string(anki.value(note_id, field))


FOLLOWUPS = [
    {"added": "2026-10-01", "item": "N1 考得如何？", "type": "ask", "due": "2026-12-07", "status": "open"},
    {"added": "2026-10-01", "item": "介紹 Ken 給他", "type": "my_promise", "status": "open"},
]


class TestFingerprint:
    def test_matches_the_card_template(self):
        """模板的 itemFingerprint 在瀏覽器算出的值（P0 範例資料）。"""
        assert item_fingerprint(FOLLOWUPS[0]) == "14vynru"
        assert item_fingerprint(FOLLOWUPS[1]) == "r1tbnf"

    def test_non_dict_items_do_not_crash(self):
        assert item_fingerprint("x") == item_fingerprint({})


class TestActions:
    def test_add_interaction_moves_last_contact_forward_only(self):
        anki = PersonAnki([person(1, Display_Name="田中さん", Interactions_JSON="[]", Last_Contact="2026-09-01")])
        _act(anki, 1, action="add_interaction", item={"date": "2026-10-01", "topics": "轉職", "place": ""})
        assert _list(anki, 1, "Interactions_JSON") == [{"date": "2026-10-01", "topics": "轉職"}]
        assert anki.value(1, "Last_Contact") == "2026-10-01"
        assert anki.value(1, "Updated_At") == date.today().isoformat()

        _act(anki, 1, action="add_interaction", item={"date": "2026-08-01", "topics": "以前的事"})
        assert anki.value(1, "Last_Contact") == "2026-10-01"
        assert len(_list(anki, 1, "Interactions_JSON")) == 2

    def test_add_interaction_validates(self):
        anki = PersonAnki([person(1, Display_Name="田中さん", Interactions_JSON="[]")])
        with pytest.raises(InvalidNoteFieldsError):
            _act(anki, 1, action="add_interaction", item={"date": "10/01", "topics": "x"})
        with pytest.raises(InvalidNoteFieldsError):
            _act(anki, 1, action="add_interaction", item={"date": "2026-10-01"}, extra="x")
        assert anki.updated == []

    def test_add_followup_defaults(self):
        anki = PersonAnki([person(1, Display_Name="田中さん", Followups_JSON="[]")])
        _act(anki, 1, action="add_followup", item={"item": "N1 考得如何？"})
        assert _list(anki, 1, "Followups_JSON") == [
            {"item": "N1 考得如何？", "type": "ask", "status": "open", "added": date.today().isoformat()}
        ]

    def test_set_followup_status_by_fingerprint(self):
        anki = PersonAnki([person(1, Display_Name="田中さん", Followups_JSON=json.dumps(FOLLOWUPS))])
        _act(anki, 1, action="set_followup_status", index=1, fingerprint="r1tbnf", status="done")
        assert [f["status"] for f in _list(anki, 1, "Followups_JSON")] == ["open", "done"]

    def test_stale_index_found_by_fingerprint(self):
        """卡片上的序號過時（前面的被刪掉了）：依指紋找到同一筆。"""
        anki = PersonAnki([person(1, Display_Name="田中さん", Followups_JSON=json.dumps(FOLLOWUPS[1:]))])
        _act(anki, 1, action="set_followup_status", index=1, fingerprint="r1tbnf", status="done")
        assert _list(anki, 1, "Followups_JSON")[0]["status"] == "done"

    def test_changed_item_is_not_touched(self):
        anki = PersonAnki([person(1, Display_Name="田中さん", Followups_JSON=json.dumps(FOLLOWUPS))])
        with pytest.raises(JsonItemChangedError):
            _act(anki, 1, action="remove_item", field="Followups_JSON", index=0, fingerprint="zzzz")
        assert anki.updated == []

    def test_remove_item(self):
        anki = PersonAnki([person(1, Display_Name="田中さん", Followups_JSON=json.dumps(FOLLOWUPS))])
        _act(anki, 1, action="remove_item", field="Followups_JSON", index=0, fingerprint="14vynru")
        assert [f["item"] for f in _list(anki, 1, "Followups_JSON")] == ["介紹 Ken 給他"]

    def test_add_item_to_generic_lists_only(self):
        anki = PersonAnki([person(1, Display_Name="田中さん", Gifts_JSON="[]")])
        _act(anki, 1, action="add_item", field="Gifts_JSON", item={"item": "鳳梨酥", "date": "2026-09-20"})
        assert _list(anki, 1, "Gifts_JSON") == [{"item": "鳳梨酥", "direction": "given", "date": "2026-09-20"}]
        with pytest.raises(InvalidNoteFieldsError):
            _act(anki, 1, action="add_item", field="Gifts_JSON", item={"date": "2026-09-20"})
        with pytest.raises(InvalidNoteFieldsError):
            _act(anki, 1, action="add_item", field="Interactions_JSON", item={"date": "2026-09-20"})

    def test_set_photo(self):
        anki = PersonAnki([person(1, Display_Name="田中さん")])
        _act(anki, 1, action="set_photo", filename="person_PP_x_20261024.jpg")
        assert anki.value(1, "Photo") == '<img src="person_PP_x_20261024.jpg">'
        with pytest.raises(InvalidNoteFieldsError):
            _act(anki, 1, action="set_photo", filename='x.jpg" onerror="alert(1)')
        _act(anki, 1, action="set_photo", filename="")
        assert anki.value(1, "Photo") == ""

    def test_declared_actions(self):
        assert set(PersonProfileHandler.CAPABILITIES.actions) == {
            "add_interaction", "add_followup", "set_followup_status", "add_item", "remove_item", "set_photo",
        }
