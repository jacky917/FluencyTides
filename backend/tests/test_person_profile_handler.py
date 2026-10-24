"""人脈卡 handler 的測試（計畫書 docs/wip/person_profile_card_FEAT_2026-10-23.md §3.11）。

Tests for the person profile handler.
"""

import asyncio
import json
import re
from datetime import date

import pytest
from pydantic import ValidationError

from app.core.exceptions import FieldNotEditableError
from app.schemas.anki import AnkiNoteInfo
from app.schemas.person_profile import PERSON_NOTE_TYPE, PersonCreateParams
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
