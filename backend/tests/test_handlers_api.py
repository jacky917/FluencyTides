"""Handler 與筆記 REST API 的測試（handlers 計畫書 §9 第 5 步、§4、D6）。

Tests for the handler and note REST API: status codes, error codes, the
422 ``loc`` of invalid create parameters, and the removed old paths.

用真實的 app（不進入 lifespan），只把 Anki 與關聯服務換成假的，所以路由、依賴、服務層、handler、
``CardService`` 都是正式的程式。

The real app without its lifespan; only Anki and the relation service are
fakes, so routing, dependencies, the service layer and the handlers are
the production code.
"""

from unittest import mock

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.dependencies import get_anki_client, get_relation_service
from tests.test_handler_base import V2, _note, _relations
from tests.test_note_service import SyncingAnki, _model_note


class ApiAnki(SyncingAnki):
    """再加上牌組相關方法（建卡時 ``ensure_deck_exists`` 會查牌組、不存在就建立）。"""

    def __init__(self, notes=(), **kwargs):
        super().__init__(notes, **kwargs)
        self.decks = {"Default": 1, "English::TOEIC": 2}

    async def get_deck_names(self) -> list[str]:
        return list(self.decks)

    async def create_deck(self, deck: str) -> int:
        self.decks[deck] = len(self.decks) + 1
        return self.decks[deck]

    async def get_deck_names_and_ids(self) -> dict[str, int]:
        return dict(self.decks)


class DuplicateAnki(ApiAnki):
    """Anki 以首欄位重複拒絕新增。"""

    async def add_notes(self, notes):
        return [None]


@pytest.fixture
def anki():
    return ApiAnki([
        _note(1, Expression="anyway", Target_Translation="無論如何"),
        _model_note(5, "Speaking_Coach_Dark", Prompt="hi"),
        _model_note(7, "JP_Context_Dark"),
        _model_note(3, "Notion_SRS_Dark"),
    ])


@pytest.fixture
def relations():
    return _relations()


@pytest.fixture
def client(anki, relations):
    from app.main import app

    holder = {"anki": anki}
    app.dependency_overrides[get_anki_client] = lambda: holder["anki"]
    app.dependency_overrides[get_relation_service] = lambda: relations
    try:
        with mock.patch.object(settings, "API_SECRET_KEY", ""):
            test_client = TestClient(app)
            test_client.holder = holder  # 測試可換掉 Anki
            yield test_client
    finally:
        app.dependency_overrides.pop(get_anki_client, None)
        app.dependency_overrides.pop(get_relation_service, None)


def _error(response) -> tuple[int, str]:
    return response.status_code, response.json().get("error_code")


# =============================================================================
# GET /handlers
# =============================================================================


def test_list_handlers(client):
    response = client.get("/api/v1/handlers")
    assert response.status_code == 200
    handlers = {h["name"]: h for h in response.json()}
    assert list(handlers) == [
        "vocabulary_mining", "expression_correction", "speaking_coach",
        "speaking_trilingual", "jp_verb_pair", "jp_core_verb", "person_profile",
    ]
    vocab = handlers["vocabulary_mining"]
    assert vocab["capabilities"]["create"]["channels"] == ["web", "tg"]
    assert vocab["create_schema"]["required"] == ["Expression"]
    assert vocab["json_fields"] == ["Word_Family_JSON", "Synonyms_JSON", "Collocations_JSON"]
    assert "input_schema" not in vocab
    assert handlers["jp_verb_pair"]["create_schema"] is None


def test_trailing_slash_still_works(client):
    """GET /handlers/ 由 FastAPI 的 redirect_slashes 轉到 /handlers，不會變 404。"""
    assert client.get("/api/v1/handlers/").status_code == 200


# =============================================================================
# POST /handlers/{name}/notes
# =============================================================================


def test_create_standard_note(client, anki):
    response = client.post("/api/v1/handlers/vocabulary_mining/notes", json={
        "deck": "English::TOEIC", "note_type": V2, "params": {"Expression": "allocate", "Target_Translation": "分配"},
    })
    assert response.status_code == 201
    assert response.json() == {"note_ids": [9001], "deck": "English::TOEIC", "sync_warning": None}
    assert anki.syncs == [{"raise_errors": True, "force": True}]


def test_create_in_a_new_deck(client, anki):
    """牌組不存在時 CardService 自動建立（§4.2），不回錯誤。"""
    response = client.post("/api/v1/handlers/vocabulary_mining/notes", json={
        "deck": "English::New", "note_type": V2, "params": {"Expression": "defer"},
    })
    assert response.status_code == 201
    assert "English::New" in anki.decks


def test_invalid_params_are_422_with_field_loc(client, anki):
    response = client.post("/api/v1/handlers/vocabulary_mining/notes", json={
        "deck": "D", "note_type": V2, "params": {"Expression": "  ", "Target_POS": 3},
    })
    assert response.status_code == 422
    locs = sorted(tuple(error["loc"]) for error in response.json()["detail"])
    assert locs == [("body", "params", "Expression"), ("body", "params", "Target_POS")]
    assert anki.added == []


@pytest.mark.parametrize(
    ("handler", "body", "expected"),
    [
        ("vocabulary_mining", {"deck": "D", "note_type": "TOEIC_Coach_Dark", "params": {"Expression": "x"}},
         (422, "NOTE_TYPE_NOT_CREATABLE")),
        ("jp_verb_pair", {"deck": "D", "note_type": "JP_VerbPair_Master_Dark", "params": {}},
         (400, "HANDLER_OPERATION_UNSUPPORTED")),
        ("speaking_coach", {"deck": "D", "note_type": "Speaking_Coach_Dark", "params": {"prompt": "x"}},
         (400, "HANDLER_OPERATION_UNSUPPORTED")),
        ("nope", {"deck": "D", "note_type": V2, "params": {}}, (404, "HANDLER_NOT_FOUND")),
        ("vocabulary_mining", {"deck": "D", "note_type": V2, "params": {"Expression": "x", "Meaning": "y"}},
         (400, "FIELD_MISMATCH")),
        ("vocabulary_mining", {"deck": "D", "note_type": V2, "params": {"Expression": "x", "Word_Audio": "a.mp3"}},
         (400, "FIELD_NOT_EDITABLE")),
    ],
    ids=["wrong-note-type", "script-only", "closed", "unknown-handler", "unknown-field", "media-field"],
)
def test_create_errors(client, anki, handler, body, expected):
    response = client.post(f"/api/v1/handlers/{handler}/notes", json=body)
    assert _error(response) == expected
    assert anki.added == []


def test_create_refusal_message_is_the_declared_reason(client):
    """§10：對 jp_verb_pair 建立回 400 並帶原因，不再回「建立成功」。"""
    response = client.post("/api/v1/handlers/jp_verb_pair/notes", json={
        "deck": "D", "note_type": "JP_VerbPair_Master_Dark", "params": {},
    })
    assert response.json()["message"] == "子卡由本機批次腳本 generate_child_cards.py 產生"


def test_duplicate_first_field(client):
    client.holder["anki"] = DuplicateAnki()
    response = client.post("/api/v1/handlers/vocabulary_mining/notes", json={
        "deck": "D", "note_type": V2, "params": {"Expression": "anyway"},
    })
    assert _error(response) == (409, "DUPLICATE_CARD")


# =============================================================================
# GET / PATCH / DELETE /handlers/{name}/notes/{note_id}
# =============================================================================


def test_handler_read(client):
    response = client.get("/api/v1/handlers/vocabulary_mining/notes/1")
    assert response.status_code == 200
    body = response.json()
    assert body["note_type"] == V2 and body["fields"][0] == {"name": "Expression", "value": "anyway"}


def test_jp_context_readable_from_both_handlers(client):
    for name in ("jp_verb_pair", "jp_core_verb"):
        assert client.get(f"/api/v1/handlers/{name}/notes/7").status_code == 200


@pytest.mark.parametrize(
    ("method", "url", "json", "expected"),
    [
        ("get", "/api/v1/handlers/vocabulary_mining/notes/5", None, (400, "NOTE_NOT_OWNED_BY_HANDLER")),
        ("get", "/api/v1/handlers/vocabulary_mining/notes/404", None, (404, "NOTE_NOT_FOUND")),
        ("patch", "/api/v1/handlers/vocabulary_mining/notes/5", {"fields": {"Prompt": "x"}},
         (400, "NOTE_NOT_OWNED_BY_HANDLER")),
        ("patch", "/api/v1/handlers/speaking_coach/notes/5", {"fields": {"Prompt": "x"}},
         (400, "HANDLER_OPERATION_UNSUPPORTED")),
        ("patch", "/api/v1/handlers/vocabulary_mining/notes/1", {"fields": {"Synonyms_JSON": "[]"}},
         (400, "FIELD_NOT_EDITABLE")),
        ("patch", "/api/v1/handlers/vocabulary_mining/notes/1", {"fields": {"Meaning": "x"}},
         (400, "FIELD_MISMATCH")),
        ("delete", "/api/v1/handlers/speaking_coach/notes/5", None, (400, "HANDLER_OPERATION_UNSUPPORTED")),
        ("delete", "/api/v1/handlers/jp_verb_pair/notes/7", None, (400, "HANDLER_OPERATION_UNSUPPORTED")),
        ("delete", "/api/v1/handlers/vocabulary_mining/notes/404", None, (404, "NOTE_NOT_FOUND")),
    ],
)
def test_note_errors_touch_nothing(client, anki, relations, method, url, json, expected):
    response = client.request(method.upper(), url, json=json)
    assert _error(response) == expected
    assert anki.updated == [] and anki.deleted == [] and anki.syncs == []
    relations.delete_relations_for_note.assert_not_awaited()


def test_patch_returns_updated_note_and_warning(client, anki):
    response = client.patch("/api/v1/handlers/vocabulary_mining/notes/1", json={"fields": {"Target_POS": "adv."}})
    assert response.status_code == 200
    body = response.json()
    assert body["note_id"] == 1 and body["sync_warning"] is None and "fields" in body
    assert anki.updated == [(1, {"Target_POS": "adv."})]


def test_delete(client, anki, relations):
    response = client.delete("/api/v1/handlers/vocabulary_mining/notes/1")
    assert response.status_code == 200
    assert response.json() == {"sync_warning": None}
    assert anki.deleted == [1]
    relations.delete_relations_for_note.assert_awaited_once_with(1)


# =============================================================================
# GET /handlers/{name}/graph
# =============================================================================


def test_graph(client):
    response = client.get("/api/v1/handlers/vocabulary_mining/graph", params={"deck": "English"})
    assert response.status_code == 200
    body = response.json()
    assert body["label_field"] == "Expression" and body["label_fields"] == []
    assert body["nodes"][0]["translation"] == "無論如何"


@pytest.mark.parametrize(
    ("url", "params", "expected"),
    [
        ("/api/v1/handlers/speaking_coach/graph", {}, (400, "HANDLER_OPERATION_UNSUPPORTED")),
        ("/api/v1/handlers/vocabulary_mining/graph", {"note_type": "Speaking_Coach_Dark"}, (422, "INVALID_NOTE_QUERY")),
        ("/api/v1/handlers/vocabulary_mining/graph", {"label_field": "Target_POS"}, (422, "INVALID_NOTE_QUERY")),
        ("/api/v1/handlers/nope/graph", {}, (404, "HANDLER_NOT_FOUND")),
    ],
)
def test_graph_errors(client, url, params, expected):
    assert _error(client.get(url, params=params)) == expected


# =============================================================================
# GET /notes/{note_id}、GET /decks
# =============================================================================


def test_generic_read_permissions(client):
    vocab = client.get("/api/v1/notes/1").json()
    assert vocab["handler"] == "vocabulary_mining"
    assert vocab["permissions"]["update_fields"] == {"allowed": True, "reason": None}

    orphan = client.get("/api/v1/notes/3").json()
    assert orphan["handler"] is None
    assert orphan["permissions"]["delete"] == {"allowed": False, "reason": "沒有 handler 負責 Notion_SRS_Dark"}

    assert _error(client.get("/api/v1/notes/404")) == (404, "NOTE_NOT_FOUND")


def test_decks(client):
    response = client.get("/api/v1/decks")
    assert response.json() == [{"deck_name": "Default", "deck_id": 1}, {"deck_name": "English::TOEIC", "deck_id": 2}]


def test_new_routes_require_api_key(client):
    with mock.patch.object(settings, "API_SECRET_KEY", "s3cret"):
        for url in ("/api/v1/handlers", "/api/v1/notes/1", "/api/v1/decks", "/api/v1/handlers/vocabulary_mining/notes/1"):
            assert client.get(url).status_code == 401, url
            assert client.get(url, headers={"X-API-Key": "s3cret"}).status_code == 200, url


# =============================================================================
# 舊路徑已移除（D5）
# =============================================================================


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("POST", "/api/v1/handlers/vocabulary_mining/create"),
        ("GET", "/api/v1/handlers/cards/1"),
        ("GET", "/api/v1/handlers/decks"),
        ("GET", "/api/v1/handlers/vocabulary_mining/cards"),
        ("PUT", "/api/v1/handlers/vocabulary_mining/cards/1"),
        ("DELETE", "/api/v1/handlers/vocabulary_mining/cards/1"),
    ],
)
def test_old_paths_are_gone(client, method, url):
    assert client.request(method, url, json={}).status_code in (404, 405)
