"""標籤：規則、建卡加標籤、標籤清單、草稿的標籤建議、欄位搜尋的標籤條件與三語卡的搜尋按鈕
（計畫書 ``docs/wip/trilingual_search_tags_FEAT_2026-10-30.md`` §7）。

Tests for tags: rules, tags at creation, the tag list, draft tag
suggestions, the tag search condition and the trilingual search buttons.
"""

from unittest import mock

import pytest

from app.core.exceptions import TagInvalidError
from app.infrastructure.anki.client import AnkiConnectError
from app.infrastructure.anki.search import tag_term
from app.schemas.person_profile import TEXT_UNMASKED_TAG
from app.services import speaking_trilingual_generate as generate_service
from app.services.note_service import _suggested_tags
from app.services.tag_rules import MAX_TAGS, is_system_tag, normalize_tags
from app.services.task_handlers.capabilities import HandlerCapabilities, Operation, SearchField
from app.services.task_handlers.registry import validate_handler
from tests.test_handler_base import V2, make_handler
from tests.test_handlers_api import _error, anki, client, relations  # noqa: F401 （pytest fixtures）
from tests.test_note_service import _model_note
from tests.test_trilingual_create import _draft

TRILINGUAL = "Speaking_Trilingual_Dark"


# =============================================================================
# 規則
# =============================================================================


class TestRules:
    @pytest.mark.parametrize(
        "tag",
        ["HandlerGenerated", "handlergenerated", "TelegramBot", "LLM::(claude-code)opus@high", "llm::x",
         "Speaking_Trilingual", "PersonProfile", TEXT_UNMASKED_TAG],
    )
    def test_system_tags(self, tag):
        """固定清單、LLM:: 前綴、各 handler 的 CREATE_TAGS 都算，不分大小寫。"""
        assert is_system_tag(tag)

    @pytest.mark.parametrize("tag", ["Interview", "最終面接", "Game::サノバウィッチ", "Step1"])
    def test_user_tags(self, tag):
        assert not is_system_tag(tag)

    def test_normalize(self):
        assert normalize_tags([" Interview ", "", "interview", "最終面接", "Interview"]) == ["Interview", "最終面接"]

    @pytest.mark.parametrize(
        ("tags", "message"),
        [
            (["Hair Salon"], "空白"),
            (["a\tb"], "空白"),
            (["HandlerGenerated"], "系統標籤"),
            (["LLM::x"], "系統標籤"),
            (["x" * 101], "100"),
            ([f"t{i}" for i in range(MAX_TAGS + 1)], str(MAX_TAGS)),
        ],
    )
    def test_invalid(self, tags, message):
        with pytest.raises(TagInvalidError, match=message):
            normalize_tags(tags)


class TestSuggestedTags:
    def test_marks_existing_and_uses_existing_spelling(self):
        result = _suggested_tags(["interview", "HairSalon", "Hair Salon", "HandlerGenerated", "Interview", 3],
                                 ["Interview", "FlowerShop"])
        assert result == [
            {"tag": "Interview", "existing": True},
            {"tag": "HairSalon", "existing": False},
            {"tag": "3", "existing": False},
        ]

    def test_not_a_list(self):
        assert _suggested_tags(None, ["Interview"]) == []
        assert _suggested_tags("Interview", ["Interview"]) == []


# =============================================================================
# 搜尋
# =============================================================================


class TestSearch:
    def test_tag_term_is_literal(self):
        assert tag_term("Business_Japanese") == r'"tag:Business\_Japanese"'
        assert tag_term("Interview::Final") == r'"tag:Interview\:\:Final"'

    def test_reserved_tag_key_rejected(self):
        handler = make_handler(
            CAPABILITIES=HandlerCapabilities(search=Operation(supported=True)),
            SEARCH_FIELDS=(SearchField("#", "標籤", ("Expression",)),),
        )
        with pytest.raises(RuntimeError, match="保留"):
            validate_handler(handler)

    def test_trilingual_buttons_and_tag_condition(self, client):
        handlers = {h["name"]: h for h in client.get("/api/v1/handlers").json()}
        keys = [f["key"] for f in handlers["speaking_trilingual"]["search_fields"]]
        assert keys == ["prompt", "context", "refs", "zh", "ja", "en"]

        response = client.post("/api/v1/handlers/speaking_trilingual/search-query", json={"conditions": [
            {"field": "refs", "value": "恐れ入りますが"},
            {"field": "#", "value": "Interview"},
        ]})
        assert response.status_code == 200
        query = response.json()["query"]
        assert '"References_ZH:*恐れ入りますが*"' in query and '"References_EN:*恐れ入りますが*"' in query
        assert query.endswith('"tag:Interview"')

    def test_person_profile_gets_tag_condition_too(self, client):
        response = client.post("/api/v1/handlers/person_profile/search-query", json={"conditions": [
            {"field": "#", "value": "客戶"},
        ]})
        assert response.json()["query"].endswith('"tag:客戶"')


# =============================================================================
# 建卡加標籤
# =============================================================================


def _with_add_tags(anki, error: Exception | None = None):
    anki.tag_calls = []

    async def add_tags(notes, tags):
        if error:
            raise error
        anki.tag_calls.append((notes, tags))

    anki.add_tags = add_tags
    return anki


VOCAB_BODY = {"deck": "Default", "note_type": V2, "params": {"Expression": "allocate"}}


class TestCreateWithTags:
    def test_tags_added_after_create_then_synced(self, client, anki):
        _with_add_tags(anki)
        response = client.post("/api/v1/handlers/vocabulary_mining/notes", json={**VOCAB_BODY, "tags": [" TOEIC ", "toeic", "Work"]})
        assert response.status_code == 201
        assert anki.tag_calls == [([9001], "TOEIC Work")]
        assert anki.syncs  # 加完標籤才同步

    def test_no_tags_no_call(self, client, anki):
        _with_add_tags(anki)
        client.post("/api/v1/handlers/vocabulary_mining/notes", json=VOCAB_BODY)
        assert anki.tag_calls == []

    def test_invalid_tag_creates_nothing(self, client, anki):
        _with_add_tags(anki)
        response = client.post("/api/v1/handlers/vocabulary_mining/notes", json={**VOCAB_BODY, "tags": ["Hair Salon"]})
        assert _error(response) == (400, "TAG_INVALID")
        assert anki.added == [] and anki.tag_calls == []

    def test_add_tags_failure_is_a_warning(self, client, anki):
        """卡片已建立：回 201 並提醒，不回錯誤（避免使用者重建一張）。"""
        _with_add_tags(anki, AnkiConnectError("boom"))
        response = client.post("/api/v1/handlers/vocabulary_mining/notes", json={**VOCAB_BODY, "tags": ["TOEIC"]})
        assert response.status_code == 201
        assert "標籤沒加上" in response.json()["sync_warning"]


# =============================================================================
# 標籤清單與草稿的建議
# =============================================================================


def _trilingual_notes(anki):
    tagged = [
        ("Interview", "BusinessJapanese", "TelegramBot", "Speaking_Trilingual"),
        ("Interview", "最終面接", "LLM::x"),
        ("FlowerShop",),
    ]
    for i, tags in enumerate(tagged):
        note = _model_note(100 + i, TRILINGUAL, Prompt=f"p{i}")
        note.tags = list(tags)
        anki.notes[note.noteId] = note


def test_tag_list(client, anki):
    _trilingual_notes(anki)
    response = client.get("/api/v1/handlers/speaking_trilingual/tags")
    assert response.status_code == 200
    assert response.json() == [
        {"tag": "Interview", "count": 2},
        {"tag": "BusinessJapanese", "count": 1},
        {"tag": "FlowerShop", "count": 1},
        {"tag": "最終面接", "count": 1},
    ]


def test_draft_suggests_tags_from_existing(client, anki):
    _trilingual_notes(anki)
    generate = mock.AsyncMock(return_value=_draft(tags=["interview", "HairSalon"]))
    with mock.patch.object(generate_service, "generate", generate):
        response = client.post("/api/v1/handlers/speaking_trilingual/draft", json={"params": {"background": "剪髮"}})
    assert response.status_code == 200
    body = response.json()
    assert "tags" not in body["params"]
    assert body["suggested_tags"] == [{"tag": "Interview", "existing": True}, {"tag": "HairSalon", "existing": False}]
    # 既有標籤依次數排序交給 LLM
    assert generate.await_args.args[3] == ["Interview", "BusinessJapanese", "FlowerShop", "最終面接"]


@pytest.mark.parametrize("existing", [[], ["Interview", "FlowerShop"]])
def test_prompt_renders_tag_section(existing):
    prompt = generate_service.build_prompt("背景", "", None, existing)
    assert "## 標籤（tags）" in prompt
    assert ("Interview、FlowerShop" in prompt) == bool(existing)
    assert "Step1" in prompt  # 排序標籤不要建議


def test_draft_still_works_when_anki_is_down(client, anki):
    """既有標籤只是給 LLM 參考：查不到時照樣生成草稿，只是沒有既有標籤可沿用。"""

    async def down(query):
        raise AnkiConnectError("連不上")

    anki.find_notes = down
    generate = mock.AsyncMock(return_value=_draft(tags=["HairSalon"]))
    with mock.patch.object(generate_service, "generate", generate):
        response = client.post("/api/v1/handlers/speaking_trilingual/draft", json={"params": {"background": "剪髮"}})
    assert response.status_code == 200
    assert generate.await_args.args[3] == []
    assert response.json()["suggested_tags"] == [{"tag": "HairSalon", "existing": False}]
