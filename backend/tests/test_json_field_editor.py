"""JSON 欄位表單編輯的測試（json_field_editor 計畫 D1–D4；先開放給人脈卡）。

Tests for editing JSON fields as forms: declarations, the read with
versions, the whole-field write with the version check and validation.
"""

import asyncio
import html
import json
from datetime import date
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.core.dependencies import get_anki_client, get_relation_service
from app.core.exceptions import FieldNotEditableError, HandlerOperationUnsupportedError, JsonFieldConflictError
from app.services.note_service import json_field_version
from app.services.task_handlers.capabilities import HandlerCapabilities, JsonFieldSpec, Operation
from app.services.task_handlers.registry import HandlerRegistry
from tests.test_handler_base import V2, _note, _relations, make_handler
from tests.test_note_service import SyncingAnki, _model_note, _service

PERSON = "Person_Profile_Dark"
FOLLOWUPS = [{"item": "N1 考得如何？", "type": "ask", "status": "open", "note": "保留的鍵"}]


def _run(coro):
    return asyncio.run(coro)


class ApplyingAnki(SyncingAnki):
    """寫入會套用到筆記上（寫完再讀得到新的值與版本）。"""

    async def update_note_fields(self, note_id: int, fields: dict[str, str]) -> None:
        await super().update_note_fields(note_id, fields)
        for name, value in fields.items():
            self.notes[note_id].fields[name]["value"] = value

    def value(self, note_id: int, name: str) -> str:
        return self.notes[note_id].fields[name]["value"]


def _person(**values: str):
    return _model_note(1, PERSON, Display_Name="田中さん", **values)


class TestDeclarations:
    def test_person_offers_json_editing_for_every_list(self):
        handlers = {h["name"]: h for h in _run(_service(SyncingAnki()).list_handlers())}
        person = handlers["person_profile"]
        assert person["capabilities"]["update_json"] == {"supported": True, "reason": None}
        specs = person["json_field_specs"]
        assert list(specs) == person["json_fields"]
        assert specs["Followups_JSON"]["title"] == "待追蹤"
        assert specs["Followups_JSON"]["item_schema"]["required"] == ["item"]
        assert handlers["vocabulary_mining"]["json_field_specs"] == {}

    def test_registry_requires_specs_when_offered(self):
        handler = make_handler("x", CAPABILITIES=HandlerCapabilities(update_json=Operation(supported=True)))
        with pytest.raises(RuntimeError, match="JSON_FIELD_SPECS"):
            HandlerRegistry().register(handler)

    def test_specs_must_be_declared_json_fields(self):
        class Item(BaseModel):
            word: str

        handler = make_handler(
            "y",
            JSON_FIELDS=("Synonyms_JSON",),
            JSON_FIELD_SPECS={"Collocations_JSON": JsonFieldSpec(item=Item, title="搭配詞")},
        )
        with pytest.raises(RuntimeError, match="不在 JSON_FIELDS 內"):
            HandlerRegistry().register(handler)


class TestRead:
    def test_generic_read_returns_items_and_versions(self):
        raw = html.escape(json.dumps(FOLLOWUPS, ensure_ascii=False))
        anki = SyncingAnki([_person(Followups_JSON=raw)])
        result = _run(_service(anki).get(1))
        assert result["permissions"]["update_json"] == {"allowed": True, "reason": None}
        state = result["json_fields"]["Followups_JSON"]
        assert state == {"items": FOLLOWUPS, "version": json_field_version(raw), "error": None}
        assert result["json_fields"]["Gifts_JSON"]["items"] == []

    def test_corrupted_field_reports_error(self):
        anki = SyncingAnki([_person(Followups_JSON='[{"item": "x"')])
        state = _run(_service(anki).get(1))["json_fields"]["Followups_JSON"]
        assert state["items"] == [] and state["error"]


class TestWrite:
    def _write(self, anki, field, items, version=None):
        raw = anki.value(1, field)
        return _run(_service(anki).update_json(
            "person_profile", 1, field, items, json_field_version(raw) if version is None else version
        ))

    def test_writes_whole_list_and_keeps_unknown_keys(self):
        anki = ApplyingAnki([_person(Followups_JSON=json.dumps(FOLLOWUPS))])
        items = [{**FOLLOWUPS[0], "status": "done"}, {"item": "介紹 Ken", "type": "my_promise"}]
        result = self._write(anki, "Followups_JSON", items)
        stored = json.loads(html.unescape(anki.value(1, "Followups_JSON")))
        assert stored[0]["status"] == "done" and stored[0]["note"] == "保留的鍵"
        assert stored[1]["type"] == "my_promise" and stored[1]["status"] == "open"
        assert result["items"] == stored
        assert result["version"] == json_field_version(anki.value(1, "Followups_JSON"))
        assert result["sync_warning"] is None
        assert anki.value(1, "Updated_At") == date.today().isoformat()

    def test_interactions_move_last_contact(self):
        anki = ApplyingAnki([_person(Interactions_JSON="[]", Last_Contact="2026-01-01")])
        self._write(anki, "Interactions_JSON", [{"date": "2026-10-01", "topics": "轉職"}, {"date": "2026-03-01"}])
        assert anki.value(1, "Last_Contact") == "2026-10-01"

    def test_conflict_writes_nothing(self):
        anki = ApplyingAnki([_person(Followups_JSON=json.dumps(FOLLOWUPS))])
        with pytest.raises(JsonFieldConflictError):
            self._write(anki, "Followups_JSON", [], version="stale")
        assert anki.updated == []

    def test_invalid_item_points_at_the_item(self):
        anki = ApplyingAnki([_person(Followups_JSON="[]")])
        with pytest.raises(ValidationError) as info:
            self._write(anki, "Followups_JSON", [{"item": "ok"}, {"item": "x", "type": "later"}])
        assert info.value.errors()[0]["loc"][:2] == (1, "type")
        assert anki.updated == []

    def test_field_without_spec(self):
        anki = ApplyingAnki([_person()])
        with pytest.raises(FieldNotEditableError):
            self._write(anki, "Display_Name", [])

    def test_handler_without_json_editing(self):
        anki = ApplyingAnki([_note(1, Expression="anyway")])
        with pytest.raises(HandlerOperationUnsupportedError):
            _run(_service(anki).update_json("vocabulary_mining", 1, "Synonyms_JSON", [], json_field_version("")))


@pytest.fixture
def client():
    from app.main import app

    anki = ApplyingAnki([_person(Followups_JSON="[]"), _note(2, Expression="anyway")])
    app.dependency_overrides[get_anki_client] = lambda: anki
    app.dependency_overrides[get_relation_service] = _relations
    try:
        with mock.patch.object(settings, "API_SECRET_KEY", ""):
            test_client = TestClient(app)
            test_client.anki = anki
            yield test_client
    finally:
        app.dependency_overrides.pop(get_anki_client, None)
        app.dependency_overrides.pop(get_relation_service, None)


class TestApi:
    URL = "/api/v1/handlers/person_profile/notes/1/json/Followups_JSON"

    def test_put_and_read_back(self, client):
        version = client.get("/api/v1/notes/1").json()["json_fields"]["Followups_JSON"]["version"]
        response = client.put(self.URL, json={"items": [{"item": "N1"}], "version": version})
        assert response.status_code == 200
        body = response.json()
        assert body["items"][0]["item"] == "N1" and body["version"] != version
        again = client.get("/api/v1/notes/1").json()["json_fields"]["Followups_JSON"]
        assert again["version"] == body["version"]

    def test_conflict_is_409(self, client):
        response = client.put(self.URL, json={"items": [], "version": "stale"})
        assert (response.status_code, response.json()["error_code"]) == (409, "JSON_FIELD_CONFLICT")

    def test_invalid_item_is_422_with_item_location(self, client):
        version = json_field_version("[]")
        response = client.put(self.URL, json={"items": [{"item": ""}], "version": version})
        assert response.status_code == 422
        assert response.json()["detail"][0]["loc"][:4] == ["body", "items", 0, "item"]

    def test_vocabulary_is_refused(self, client):
        response = client.put(
            "/api/v1/handlers/vocabulary_mining/notes/2/json/Synonyms_JSON", json={"items": [], "version": "x"}
        )
        assert response.status_code == 400
