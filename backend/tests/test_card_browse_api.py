"""卡片瀏覽與 Anki 媒體 API 的測試（網頁版計畫階段 A）。

HTTP-level tests for ``/api/v1/cards`` and ``/api/v1/anki-media`` against a
fake AnkiConnect client: deck-term escaping (wildcards literal), paging
newest-first, field ordering, search errors, Anki play tags turned into play
buttons, media filename validation and response types, API-key enforcement
on the real application, and the note-detail shape the web modal relies on.
"""

import base64
import inspect
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.cards import router
from app.core.config import settings
from app.core.dependencies import get_anki_client
from app.core.exceptions import FluencyTidesError
from app.infrastructure.anki.client import AnkiActionError, AnkiClient, AnkiConnectError
from app.schemas.anki import AnkiNoteInfo
from app.services.card_browse_service import (
    build_query,
    deck_search_term,
    media_type_for,
    replace_av_tags,
    template_sounds,
)


def _info(card_id: int, deck: str = "日本語::外語糾錯::母卡片", **overrides) -> dict:
    info = {
        "cardId": card_id,
        "note": card_id + 1000,
        "deckName": deck,
        "modelName": "Expression_Master_Dark",
        "ord": 0,
        # 刻意打亂順序：回傳時要依 order 排
        "fields": {"Context": {"value": "[]", "order": 1}, "My_Original": {"value": "私わ", "order": 0}},
        "question": f"<style>.card{{}}</style><div>Q{card_id}</div>",
        "answer": f"<style>.card{{}}</style><div>A{card_id}</div>",
        "interval": 3,  # cardsInfo 還有很多用不到的鍵，應被忽略
    }
    info.update(overrides)
    return info


class FakeAnki:
    """記下收到的呼叫，回傳固定資料。Records calls, returns canned data."""

    def __init__(self) -> None:
        self.queries: list[str] = []
        self.info_requests: list[list[int]] = []
        self.media_requests: list[str] = []
        self.template_requests: list[str] = []
        self.media: dict[str, str | bool] = {"rec 1.ogg": base64.b64encode(b"OggS-data").decode()}
        self.infos: dict[int, dict] = {card: _info(card) for card in (101, 105, 103)}
        self.templates: dict[str, dict[str, str]] = {}
        self.fail_with: Exception | None = None

    async def find_cards(self, query: str) -> list[int]:
        if self.fail_with:
            raise self.fail_with
        self.queries.append(query)
        return list(self.infos)

    async def get_cards_info(self, cards: list[int]) -> list[dict]:
        self.info_requests.append(list(cards))
        return [self.infos.get(card, {}) for card in cards]

    async def get_model_templates(self, model_name: str) -> dict[str, dict[str, str]]:
        self.template_requests.append(model_name)
        return self.templates

    async def retrieve_media_file(self, filename: str) -> str | bool:
        self.media_requests.append(filename)
        return self.media.get(filename, False)

    async def get_notes_info(self, notes: list[int] | None = None, query: str | None = None) -> list[AnkiNoteInfo]:
        return [
            AnkiNoteInfo(
                noteId=note,
                modelName="Speaking_Coach_Dark",
                tags=["t"],
                fields={"Prompt": {"value": "やあ", "order": 0}},
                cards=[note * 10],
            )
            for note in notes or []
        ]


def test_fake_matches_the_real_client_signatures():
    """假客戶端的方法與真的 AnkiClient 簽名一致；真的改名或改參數時這裡會先失敗。"""
    for name in ("find_cards", "get_cards_info", "get_model_templates", "retrieve_media_file", "get_notes_info"):
        real = inspect.signature(getattr(AnkiClient, name))
        fake = inspect.signature(getattr(FakeAnki, name))
        assert list(real.parameters) == list(fake.parameters), name


@pytest.fixture
def anki() -> FakeAnki:
    return FakeAnki()


@pytest.fixture
def client(anki):
    """只掛本 router 與全域錯誤 handler 的小 app。A small app with this router."""
    from app.main import fluencytides_error_handler

    app = FastAPI()
    app.add_exception_handler(FluencyTidesError, fluencytides_error_handler)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_anki_client] = lambda: anki
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def real_app_client(anki):
    """真實的 app.main.app（不進入 lifespan），Anki 換成假的。The real app with a fake Anki."""
    from app.main import app

    app.dependency_overrides[get_anki_client] = lambda: anki
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_anki_client, None)


# ---------------------------------------------------------------------------
# 搜尋條件（Search terms）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("deck", "expected"),
    [
        ("日本語::外語糾錯", 'deck:"日本語\\:\\:外語糾錯"'),
        ("A_B*C", 'deck:"A\\_B\\*C"'),
        ('say "hi"\\', 'deck:"say \\"hi\\"\\\\"'),
    ],
)
def test_deck_search_term_escapes_wildcards_and_quotes(deck, expected):
    """牌組名中的 _ * " \\ 照字面比對，不當萬用字元。"""
    assert deck_search_term(deck) == expected


def test_build_query_without_deck_and_with_search():
    """沒有牌組時查全部；額外的搜尋語句以括號接上。"""
    assert build_query(None, None) == "deck:*"
    assert build_query("", "  ") == "deck:*"
    assert build_query("日本語", "note:A OR note:B") == 'deck:"日本語" (note:A OR note:B)'


# ---------------------------------------------------------------------------
# 卡片清單（Card list）
# ---------------------------------------------------------------------------


def test_list_cards_pages_newest_first_with_ordered_fields(client, anki):
    """依卡片 ID 由新到舊分頁，欄位依筆記類型的順序，清單不含卡面。"""
    response = client.get("/api/v1/cards", params={"deck": "日本語", "page": 1, "page_size": 2})

    assert response.status_code == 200
    body = response.json()
    assert anki.queries == ['deck:"日本語"']
    assert anki.info_requests == [[105, 103]]
    assert (body["total"], body["page"], body["page_size"]) == (3, 1, 2)
    first = body["cards"][0]
    assert first["card_id"] == 105 and first["note_id"] == 1105
    assert first["deck_name"] == "日本語::外語糾錯::母卡片"
    assert list(first["fields"]) == ["My_Original", "Context"]
    assert "question" not in first


def test_list_cards_last_partial_page(client, anki):
    """最後一頁不滿一頁時，只讀剩下的卡。"""
    body = client.get("/api/v1/cards", params={"page": 2, "page_size": 2}).json()

    assert [card["card_id"] for card in body["cards"]] == [101]
    assert anki.info_requests == [[101]]


def test_list_cards_past_the_last_page_is_empty(client, anki):
    """超過最後一頁回傳空清單，不呼叫 cardsInfo。"""
    body = client.get("/api/v1/cards", params={"page": 5}).json()

    assert body["cards"] == [] and body["total"] == 3
    assert anki.info_requests == []


def test_list_cards_skips_cards_deleted_between_search_and_read(client, anki):
    """搜尋後、讀取前被刪除的卡（cardsInfo 回空物件）不列出。"""
    original = anki.get_cards_info

    async def partly_missing(cards):
        return [info if info.get("cardId") != 103 else {} for info in await original(cards)]

    anki.get_cards_info = partly_missing
    body = client.get("/api/v1/cards").json()

    assert [card["card_id"] for card in body["cards"]] == [105, 101]


def test_rejected_user_search_is_a_400(client, anki):
    """Anki 拒絕使用者的搜尋語句時回 400 與 Anki 的原訊息，不當成服務故障。"""
    anki.fail_with = AnkiActionError("invalid search: deck:(")

    response = client.get("/api/v1/cards", params={"q": "deck:("})

    assert response.status_code == 400
    assert response.json()["error_code"] == "INVALID_ANKI_SEARCH"
    assert "invalid search" in response.json()["message"]


def test_anki_errors_without_user_search_stay_502(client, anki):
    """沒有使用者語句時，Anki 回報的錯誤與連不上一樣是 502。"""
    for error in (AnkiActionError("collection is not available"), AnkiConnectError("無法連接到 AnkiConnect")):
        anki.fail_with = error

        response = client.get("/api/v1/cards", params={"deck": "日本語"})

        assert response.status_code == 502
        assert response.json()["error_code"] == "ANKI_SERVICE_ERROR"


def test_list_cards_validates_paging(client):
    """page_size 上限 200、page 從 1 起。"""
    assert client.get("/api/v1/cards", params={"page_size": 201}).status_code == 422
    assert client.get("/api/v1/cards", params={"page": 0}).status_code == 422


# ---------------------------------------------------------------------------
# 單張卡與播放標記（Card faces and play tags）
# ---------------------------------------------------------------------------


def test_read_card_returns_rendered_faces_without_fetching_templates(client, anki):
    """沒有播放標記的卡面原樣回傳，不多查模板。"""
    body = client.get("/api/v1/cards/105").json()

    assert body["question"].endswith("<div>Q105</div>")
    assert body["answer"].endswith("<div>A105</div>")
    assert body["model_name"] == "Expression_Master_Dark"
    assert anki.template_requests == []


def test_play_tags_become_buttons_in_template_order(client, anki):
    """[anki:play:q|a:N] 依模板中欄位的順序對回檔名：q 算正面、a 只算背面自己的部分。"""
    anki.infos[105] = _info(
        105,
        modelName="TOEIC_Coach_Dark_v2",
        fields={
            "Expression": {"value": "postpone", "order": 0},
            "Word_Audio": {"value": "[sound:01-01.mp3]", "order": 1},
            "Example_Audio": {"value": "[sound:01-02.mp3]", "order": 2},
        },
        question="<div>postpone [anki:play:q:0]</div>",
        answer="<div>postpone [anki:play:q:0]</div><hr>[anki:play:a:0] [anki:play:a:1] [anki:play:a:2]",
    )
    anki.templates = {
        "Card 1": {
            "Front": "{{Expression}} {{Word_Audio}}",
            "Back": "{{FrontSide}}<hr>{{#Word_Audio}}{{Word_Audio}}{{/Word_Audio}} {{text:Example_Audio}}",
        }
    }

    body = client.get("/api/v1/cards/105").json()

    assert anki.template_requests == ["TOEIC_Coach_Dark_v2"]
    assert body["question"] == '<div>postpone <button type="button" class="replay-button soundLink" data-anki-sound="01-01.mp3" title="01-01.mp3">▶</button></div>'
    assert body["answer"].count('data-anki-sound="01-01.mp3"') == 2
    assert 'data-anki-sound="01-02.mp3"' in body["answer"]
    # 背面只有兩個音檔，第三個標記對不到
    assert body["answer"].endswith('<span class="replay-button-missing" title="找不到這個播放標記對應的音檔">🔇</span>')
    assert "[anki:play" not in body["question"] + body["answer"]


def test_template_sounds_ignores_sections_frontside_and_tts():
    """區塊標記、FrontSide、tts 不算欄位；過濾器（furigana: 等）取最後的欄位名。"""
    fields = {"A": "[sound:a.mp3]x[sound:b.mp3]", "B": "[sound:c.mp3]", "C": "no sound"}

    sounds = template_sounds("{{#A}}{{A}}{{/A}}{{FrontSide}}{{furigana:B}}{{tts en_US:C}}{{^C}}{{/C}}{{Missing}}", fields)

    assert sounds == ["a.mp3", "b.mp3", "c.mp3"]


def test_play_button_escapes_the_file_name():
    """檔名放進 HTML 屬性前要跳脫，避免檔名中的引號跳出屬性。"""
    face = replace_av_tags("[anki:play:q:0]", ['x" onclick="alert(1).mp3'], [])

    assert 'onclick="alert' not in face
    assert "&quot;" in face


def test_read_missing_card_is_404(client):
    """不存在的卡回 404 CARD_NOT_FOUND。"""
    response = client.get("/api/v1/cards/999")

    assert response.status_code == 404
    assert response.json()["error_code"] == "CARD_NOT_FOUND"


# ---------------------------------------------------------------------------
# 媒體檔（Media）
# ---------------------------------------------------------------------------


def test_media_is_served_with_safe_headers(client):
    """媒體檔解碼後回傳，類型依副檔名；檔名含空白與 URL 編碼也可以；帶 nosniff 與 sandbox CSP。"""
    response = client.get("/api/v1/anki-media/rec%201.ogg")

    assert response.status_code == 200
    assert response.content == b"OggS-data"
    assert response.headers["content-type"] == "audio/ogg"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"].startswith("sandbox")
    assert "max-age" in response.headers["cache-control"]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("a.mp3", "audio/mpeg"),
        ("A.JPG", "image/jpeg"),
        ("clip.m4a", "audio/mp4"),
        ("face.webp", "image/webp"),
        ("page.html", "application/octet-stream"),
        ("icon.svg", "application/octet-stream"),
        ("script.js", "application/octet-stream"),
        ("data.xml", "application/octet-stream"),
        ("no_extension", "application/octet-stream"),
    ],
)
def test_media_types_are_limited_to_audio_video_and_images(name, expected):
    """只有音訊、影片與不能執行腳本的圖片照類型回應；HTML、SVG 等一律 octet-stream。"""
    assert media_type_for(name) == expected


def test_scriptable_media_is_not_served_as_html(client, anki):
    """媒體資料夾中的 HTML 不會以 text/html 回應（同源，否則能在網頁的 origin 執行）。"""
    anki.media = {"x.html": base64.b64encode(b"<script>alert(1)</script>").decode()}

    response = client.get("/api/v1/anki-media/x.html")

    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_empty_media_file_is_served(client, anki):
    """0 位元組的檔案照常回傳，不當成不存在。"""
    anki.media = {"empty.mp3": ""}

    response = client.get("/api/v1/anki-media/empty.mp3")

    assert response.status_code == 200
    assert response.content == b""


def test_missing_media_is_404(client):
    """媒體資料夾中沒有的檔案回 404。"""
    response = client.get("/api/v1/anki-media/none.mp3")

    assert response.status_code == 404
    assert response.json()["error_code"] == "MEDIA_FILE_NOT_FOUND"


@pytest.mark.parametrize("name", ["..%2Fcollection.anki2", "sub/file.mp3", "a%5Cb.mp3", "%2E%2E", "a%00.mp3"])
def test_media_paths_are_rejected_before_reaching_anki(client, anki, name):
    """含路徑或 NUL 的檔名一律 400，不會送到 AnkiConnect。"""
    response = client.get(f"/api/v1/anki-media/{name}")

    assert response.status_code == 400
    assert response.json()["error_code"] == "INVALID_MEDIA_FILENAME"
    assert anki.media_requests == []


def test_undecodable_media_is_a_502(client, anki):
    """AnkiConnect 回傳的內容不是 base64 時回 502，而不是送出亂碼。"""
    anki.media = {"bad.mp3": "not base64!!"}

    response = client.get("/api/v1/anki-media/bad.mp3")

    assert response.status_code == 502
    assert response.json()["error_code"] == "ANKI_SERVICE_ERROR"


# ---------------------------------------------------------------------------
# 真實 app（Real application）
# ---------------------------------------------------------------------------


def test_real_app_requires_api_key_for_cards_and_media(real_app_client):
    """設定了 API_SECRET_KEY 時，卡片與媒體端點都要金鑰；同時證明路由掛在真實 app 上。"""
    with mock.patch.object(settings, "API_SECRET_KEY", "s3cret"):
        for url in ("/api/v1/cards", "/api/v1/cards/105", "/api/v1/anki-media/rec%201.ogg"):
            missing = real_app_client.get(url)
            wrong = real_app_client.get(url, headers={"X-API-Key": "nope"})
            ok = real_app_client.get(url, headers={"X-API-Key": "s3cret"})

            assert (missing.status_code, wrong.status_code, ok.status_code) == (401, 401, 200), url


def test_note_detail_keeps_the_notes_info_shape(real_app_client):
    """網頁的筆記欄位彈窗依賴 /handlers/cards/{note_id} 回傳 notesInfo 的 camelCase 形狀。"""
    with mock.patch.object(settings, "API_SECRET_KEY", ""):
        body = real_app_client.get("/api/v1/handlers/cards/7").json()

    assert set(body) == {"noteId", "modelName", "tags", "fields", "cards"}
    assert body["noteId"] == 7
    assert body["fields"]["Prompt"] == {"value": "やあ", "order": 0}
