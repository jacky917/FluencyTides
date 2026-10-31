"""卡片頁欄位搜尋的測試：能力宣告、欄位按鈕、組 Anki 查詢與 API（先開放給人脈卡）。

Tests for the card page field search: the capability, the field buttons,
the query building and the API.
"""

import asyncio
import json

import pytest
from pydantic import BaseModel, Field

from app.core.exceptions import HandlerOperationUnsupportedError, InvalidNoteFieldsError
from app.infrastructure.anki.search import contains_term
from app.services.task_handlers.capabilities import HandlerCapabilities, JsonFieldSpec, Operation, SearchField
from app.services.task_handlers.registry import HandlerRegistry
from tests.test_handler_base import make_handler
from tests.test_handlers_api import _error, anki, client, relations  # noqa: F401 - pytest fixtures
from tests.test_note_service import SyncingAnki, _service

PERSON_QUERY = '(note:"Person\\_Profile\\_Dark")'


def _run(coro):
    return asyncio.run(coro)


class TestContainsTerm:
    def test_field_and_any_field(self):
        assert contains_term("Interactions_JSON", "餐廳") == '"Interactions_JSON:*餐廳*"'
        assert contains_term(None, "餐廳") == '"餐廳"'

    def test_value_is_literal(self):
        """萬用字元、引號、冒號照字面比對，不能變成 Anki 語法。"""
        assert contains_term("Notes", 'a*b_"c":re:x') == '"Notes:*a\\*b\\_\\"c\\"\\:re\\:x*"'


# =============================================================================
# 欄位按鈕
# =============================================================================


class Params(BaseModel):
    Name: str = Field(title="名字")
    Photo: str = Field("", title="照片")


class Item(BaseModel):
    text: str


class TestSearchFields:
    def test_derived_from_create_params_and_json_specs(self):
        handler = make_handler(
            "derived",
            CREATE_PARAMS=Params,
            MEDIA_FIELDS=("Photo",),
            JSON_FIELDS=("Notes_JSON",),
            JSON_FIELD_SPECS={"Notes_JSON": JsonFieldSpec(item=Item, title="筆記")},
        )
        assert handler.search_fields() == (
            SearchField("Name", "名字", ("Name",)),
            SearchField("Notes_JSON", "筆記", ("Notes_JSON",)),
        )

    def test_declared_fields_win(self):
        declared = (SearchField("n", "名字", ("A", "B")),)
        assert make_handler("declared", CREATE_PARAMS=Params, SEARCH_FIELDS=declared).search_fields() == declared

    @pytest.mark.parametrize(
        ("attrs", "message"),
        [
            ({}, "沒有可搜尋的欄位"),
            ({"SEARCH_FIELDS": (SearchField("a", "A", ("X",)), SearchField("a", "B", ("Y",)))}, "鍵重複"),
            ({"SEARCH_FIELDS": (SearchField("*", "全部", ("X",)),)}, "保留"),
            ({"SEARCH_FIELDS": (SearchField("a", "A", ()),)}, "沒有對應的 Anki 欄位"),
        ],
    )
    def test_registry_checks(self, attrs, message):
        handler = make_handler("bad", CAPABILITIES=HandlerCapabilities(search=Operation(supported=True)), **attrs)
        with pytest.raises(RuntimeError, match=message):
            HandlerRegistry().register(handler)

    def test_person_buttons_use_real_fields(self):
        """人脈卡的按鈕比對的都是筆記類型真的有的欄位，名字合併各語言的姓名。"""
        model_fields = json.load(open("app/anki_models/Person_Profile_Dark.json", encoding="utf-8"))["inOrderFields"]
        from app.services.task_handlers.registry import handler_registry

        person = handler_registry.get_handler("person_profile")
        for button in person.search_fields():
            assert set(button.fields) <= set(model_fields), button
        name = next(b for b in person.search_fields() if b.key == "name")
        assert set(name.fields) == {"Display_Name", "Name_ZH", "Name_EN", "Name_JA", "Name_JA_Kana", "Name_Romaji"}

    def test_list_handlers(self):
        handlers = {h["name"]: h for h in _run(_service(SyncingAnki()).list_handlers())}
        person = handlers["person_profile"]
        assert person["capabilities"]["search"] == {"supported": True, "reason": None}
        assert {"key": "interactions", "title": "互動"} in person["search_fields"]
        assert handlers["vocabulary_mining"]["search_fields"] == []


# =============================================================================
# 組查詢
# =============================================================================


class TestSearchQuery:
    def _query(self, conditions, handler="person_profile"):
        return _service(SyncingAnki()).search_query(handler, conditions)

    def test_conditions_are_anded(self):
        query = self._query([("relationship", "客戶"), ("interactions", "餐廳"), ("relationship", "前同事")])
        assert query == (
            f'{PERSON_QUERY} "Relationship:*客戶*" "Interactions_JSON:*餐廳*" "Relationship:*前同事*"'
        )

    def test_multi_field_button_is_ored(self):
        assert self._query([("food", "拉麵")]) == f'{PERSON_QUERY} ("Food_Drink:*拉麵*" OR "Dietary:*拉麵*")'

    def test_any_field_and_blank_values(self):
        assert self._query([("*", " Geekly "), ("name", "  ")]) == f'{PERSON_QUERY} "Geekly"'
        assert self._query([]) == PERSON_QUERY

    def test_html_escaped_variant(self):
        """JSON 欄位與 Anki 編輯器存的是 HTML 轉義後的文字：含 & 時兩種寫法都比對。"""
        assert self._query([("notes", "A&B")]) == f'{PERSON_QUERY} ("Notes:*A&B*" OR "Notes:*A&amp;B*")'

    def test_unknown_field(self):
        with pytest.raises(InvalidNoteFieldsError, match="不能搜尋這個欄位"):
            self._query([("Card_ID", "x")])

    def test_not_offered(self):
        with pytest.raises(HandlerOperationUnsupportedError):
            self._query([], handler="vocabulary_mining")


# =============================================================================
# API
# =============================================================================


def test_api_builds_query(client):
    response = client.post(
        "/api/v1/handlers/person_profile/search-query",
        json={"conditions": [{"field": "relationship", "value": "客戶"}, {"field": "interactions", "value": "餐廳"}]},
    )
    assert response.status_code == 200
    assert response.json() == {"query": f'{PERSON_QUERY} "Relationship:*客戶*" "Interactions_JSON:*餐廳*"'}


@pytest.mark.parametrize(
    ("handler", "conditions", "expected"),
    [
        ("vocabulary_mining", [], (400, "HANDLER_OPERATION_UNSUPPORTED")),
        ("person_profile", [{"field": "Card_ID", "value": "x"}], (422, "INVALID_NOTE_FIELDS")),
    ],
)
def test_api_refusals(client, handler, conditions, expected):
    response = client.post(f"/api/v1/handlers/{handler}/search-query", json={"conditions": conditions})
    assert _error(response) == expected
