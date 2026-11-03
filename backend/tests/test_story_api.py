"""劇情流程圖 API 與服務的測試（劇情流程圖 D11–D13、D17）。

HTTP-level tests for ``/api/v1/story`` against an in-memory repository:
manifest and capabilities, graph ETag, annotation stats and verb index,
line pages with card annotations, locate (direct and via a master note's
children), save generation (filename, MIME, variables along the selected
path, route presets, background voices), thumbnails, voice playback, the
first-class BGM fields of a line, the reserved BGM audio endpoint (gated by
``config.bgm_audio``), and error codes.
Also covers the path rule shared with the frontend, asset-key safety
(Windows ``..\\`` traversal), MinIO error mapping, config validation and
effect typing.
"""

import asyncio
import struct
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))

import story_fixtures as F  # noqa: E402

from app.api.story import get_story_service, router  # noqa: E402
from app.core.exceptions import FluencyTidesError  # noqa: E402
from app.infrastructure.krkr import packinone as P  # noqa: E402
from app.schemas.story import EdgeEffect, SavesConfig, StoryEdge, StoryGraph, StoryNode, StoryTab  # noqa: E402
from app.services.story.assets import StoryAssetStore, safe_key_parts  # noqa: E402
from app.services.story.errors import StoryAssetUnavailableError  # noqa: E402
from app.services.story.graph_index import GraphIndex  # noqa: E402
from app.services.story.repository import BuildRecord, CardLink, LineBgm, LineRecord  # noqa: E402
from app.services.story.service import StoryService  # noqa: E402
from scripts.story.common.scene_graph import build_story_graph  # noqa: E402

GAME = "SabbatOfTheWitch"
TEMPLATE_KEY = f"story/{GAME}/templates/quick.jpg"
THUMB = "0123456789abcdef"
VOICE = "meg009_001"
MP3 = b"ID3\x03\x00fake-mp3"
OGG = b"OggS\x00fake-ogg"
#: 2700 播放中從 BGM02 切到 BGM51（標題畫面的曲子，兩種語言的曲名都不明）。Line 2700 switches tracks.
BGM_2700 = [LineBgm("BGM02", 0, "気の合う仲間たち", "意氣相投的朋友們", gallery_no=4, archive_index=3),
            LineBgm("BGM51", 1, None, None, gallery_no=None, archive_index=85)]

CONFIG = {
    "languages": [{"code": "ja-JP", "label": "日本語"}, {"code": "zh-Hant", "label": "繁中"}],
    "default_theme": "sabbat-night",
    "annotations": {"cards": {"card_types": [
        {"card_type": "jp_core_verb", "label": "核心動詞", "note_types": ["JP_Context_Dark"]},
        {"card_type": "jp_verb_pair", "label": "自他動詞"}]}},
    "saves": {"template": TEMPLATE_KEY, "install_hint": "放到 savedata",
              "slots": [{"id": "quick", "label": "快速存檔", "filename": "data_quick_01.jpg"},
                        {"id": "1", "label": "第 1 格", "filename": "data_0001_01.jpg"}]},
    "route_presets": {"nene": {"nen_flag": 4}},
}


def _jpeg(payload=b"\x01\x02"):
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
    sos = b"\xff\xda" + struct.pack(">H", 8) + b"\x01\x01\x00\x00\x3f\x00"
    return b"\xff\xd8" + app0 + sos + payload + b"\xff\xd9"


def _template():
    iv = bytes(16)
    h = P.parse_header(b"TJS/4s0\x00" + struct.pack("<IHH", 77, 1, 16) + iv)
    root = {"core": {"scenePlayer": {"point": 1, "scene": "x.ks*y"}, "storeTime": 0,
                     "bookMarkInfo": {"lastText": "", "textlang": "jp"}}, "user": {}}
    return P.embed_in_jpeg(_jpeg(), P.pack_data(root, h))


class FakeRepo:
    """以 fixture 劇情圖為資料的記憶體 repository。In-memory repository."""

    def __init__(self):
        files, scenes, lines, links = F.rows()
        result = build_story_graph(files, scenes, lines, links, F.rules())
        self.graph_json = result.graph.to_json_dict()
        scene_by_id = {s.scene_id: s for s in scenes}
        file_by_id = {f.file_id: f for f in files}
        line_by_id = {x.id: x for x in lines}
        self.rows = result.rows
        self.records: dict[int, tuple[str, LineRecord]] = {}
        for row in result.rows:
            line = line_by_id[row.line_id]
            scene = scene_by_id[line.scene_id]
            point = None if row.line_id == 1001 else row.seq + 3  # 1001 當作背景語音
            self.records[row.line_id] = (row.node_key, LineRecord(
                line_id=row.line_id, seq=row.seq, kind="bgv" if row.line_id == 1001 else line.kind,
                point=point, file_name=file_by_id[scene.file_id].file_name, label=scene.label,
                speaker_ja="めぐる", speaker_zh=None, text_ja=f"台詞{row.line_id}\n続き", text_zh=f"繁中{row.line_id}",
                voice=VOICE if row.line_id == 2700 else None, thumb=THUMB, is_h=False, h_label=None, chapter_label=line.chapter_label,
                attrs={"plays_at": {"scene": "001.共通－始まり.ks*chapter11", "point": 9}} if row.line_id == 1001 else {},
                bgm=list(BGM_2700) if row.line_id == 2700 else [],
                bgm_detect="實測" if row.line_id == 2700 else None, bgm_may_change=row.line_id == 2700))
        # 2700（009_02B 的第 1 句）有兩張核心動詞卡；筆記 900 是母卡，經由子卡 901 找到 2700
        self.cards = [CardLink(2700, 901, "jp_core_verb", "見る"), CardLink(2700, 902, "jp_core_verb", "見る"),
                      CardLink(1000, 903, "jp_verb_pair", "開く"), CardLink(1000, 904, "other_type", "x")]
        self.note_children = {900: [901]}
        self.active = True
        self.config = CONFIG

    def _record(self):
        return BuildRecord(build_id=7, game_id=GAME, status="active", config=self.config, stats={"nodes": 1},
                           engine="kirikiri_packinone", titles={"ja-JP": "サノバウィッチ", "zh-Hant": "魔女的夜宴"})

    async def active_builds(self):
        return [self._record()] if self.active else []

    async def active_build(self, game_id):
        return self._record() if self.active and game_id == GAME else None

    async def build(self, game_id, build_id):
        return self._record() if build_id == 7 else None

    async def graph(self, build_id):
        return self.graph_json

    async def node_lines(self, build_id, node_key, offset, limit):
        recs = sorted((r for k, r in self.records.values() if k == node_key), key=lambda r: r.seq)
        return len(recs), recs[offset:offset + limit]

    async def line_in_build(self, build_id, line_id):
        return self.records.get(line_id)

    async def card_links(self, line_ids):
        return [c for c in self.cards if c.line_id in line_ids]

    async def next_annotated_seq(self, build_id, node_key, after):
        seqs = [r.seq for k, r in self.records.values() if k == node_key and r.seq > after
                and any(c.line_id == r.line_id for c in self.cards)]
        return min(seqs) if seqs else None

    async def annotation_counts(self, build_id):
        out: dict[tuple[str, str], set[int]] = {}
        for c in self.cards:
            out.setdefault((self.records[c.line_id][0], c.card_type), set()).add(c.line_id)
        return [(k, t, len(v)) for (k, t), v in out.items()]

    async def verb_index(self, build_id):
        return [(self.records[c.line_id][0], c.card_type, c.verb) for c in self.cards]

    async def lines_of_note(self, note_id):
        direct = [c.line_id for c in self.cards if c.note_id == note_id]
        if direct:
            return direct
        return [c.line_id for child in self.note_children.get(note_id, []) for c in self.cards if c.note_id == child]


def _app(service):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    @app.exception_handler(FluencyTidesError)
    async def _handler(request, exc):
        return JSONResponse(status_code=exc.status_code, content={"error_code": exc.error_code, "message": exc.message})

    app.dependency_overrides[get_story_service] = lambda: service
    return TestClient(app)


@pytest.fixture
def setup(tmp_path):
    # 素材根目錄是 tmp_path/assets：tmp_path 底下、根目錄之外的檔案用來測路徑跳脫
    assets = tmp_path / "assets"
    (assets / TEMPLATE_KEY).parent.mkdir(parents=True)
    (assets / TEMPLATE_KEY).write_bytes(_template())
    thumb = assets / f"story/{GAME}/thumbs/{THUMB}.jpg"
    thumb.parent.mkdir(parents=True)
    thumb.write_bytes(_jpeg(b"\x07\x07"))
    voice = assets / f"story/{GAME}/voices/{VOICE}.mp3"
    voice.parent.mkdir(parents=True)
    voice.write_bytes(MP3)
    repo = FakeRepo()
    service = StoryService(repo, StoryAssetStore(local_dir=str(assets)))
    return _app(service), repo, service


BASE = f"/api/v1/story/games/{GAME}"


def test_games_and_manifest(setup):
    client, _, _ = setup
    games = client.get("/api/v1/story/games").json()
    assert games[0]["game_id"] == GAME and games[0]["title"] == "魔女的夜宴"
    assert games[0]["capabilities"] == {"saves": True, "thumbs": True, "voices": True, "bgm": False,
                                        "annotations": ["cards"], "route_presets": True,
                                        "languages": ["ja-JP", "zh-Hant"]}
    assert games[0]["card_types"][0]["note_types"] == ["JP_Context_Dark"]
    manifest = client.get(BASE).json()
    assert manifest["build_id"] == 7
    assert [s["id"] for s in manifest["save_slots"]] == ["quick", "1"]
    assert manifest["route_presets"] == ["nene"] and manifest["default_theme"] == "sabbat-night"


def test_unknown_game_is_404(setup):
    client, _, _ = setup
    r = client.get("/api/v1/story/games/Nope")
    assert r.status_code == 404 and r.json()["error_code"] == "STORY_GAME_NOT_FOUND"


def test_graph_etag(setup):
    client, _, _ = setup
    r = client.get(f"{BASE}/graph")
    assert r.status_code == 200 and r.json()["build_id"] == 7 and r.json()["tabs"]
    etag = r.headers["etag"]
    assert client.get(f"{BASE}/graph", headers={"If-None-Match": etag}).status_code == 304
    assert "immutable" in client.get(f"{BASE}/graph?build=7").headers["cache-control"]
    assert client.get(f"{BASE}/graph?build=99").json()["error_code"] == "STORY_BUILD_NOT_FOUND"


def test_annotations_counts_lines_and_ignores_unknown_types(setup):
    client, _, _ = setup
    body = client.get(f"{BASE}/annotations?kinds=cards").json()
    assert body["nodes"]["sw:009_02B"] == {"cards": {"jp_core_verb": 1}}
    assert body["nodes"]["sw:001"] == {"cards": {"jp_verb_pair": 1}}
    assert body["index"]["cards"]["verbs"] == {"見る": ["sw:009_02B"], "開く": ["sw:001"]}
    assert body["index"]["cards"]["verbs:jp_core_verb"] == {"見る": ["sw:009_02B"]}


def test_lines_page_with_annotations(setup):
    client, _, _ = setup
    body = client.get(f"{BASE}/nodes/sw:009_02B/lines?limit=1&annotated_after=-1").json()
    assert body["total"] == 2 and body["offset"] == 0 and len(body["lines"]) == 1
    line = body["lines"][0]
    assert line["texts"] == {"ja-JP": "台詞2700\n続き", "zh-Hant": "繁中2700"}
    assert line["speakers"] == {"ja-JP": "めぐる", "zh-Hant": "めぐる"}
    assert line["annotations"]["cards"] == [{"card_type": "jp_core_verb", "label": "核心動詞", "verb": "見る",
                                             "note_ids": [901, 902]}]
    assert line["voice"] == VOICE
    assert line["can_save"] is True and line["anchor_key"].startswith("009.共通－休日デート.ks*009_02B@")
    assert body["next_annotated_seq"] == 0
    assert client.get(f"{BASE}/nodes/sw:nope/lines").json()["error_code"] == "STORY_NODE_NOT_FOUND"


def test_line_bgm_is_a_first_class_field(setup):
    """BGM 是 StoryLine 的正式欄位：依 ord 排列、曲名以語言代碼為鍵且省略不明的語言；attrs 不再有 BGM。"""
    client, _, _ = setup
    lines = client.get(f"{BASE}/nodes/sw:009_02B/lines").json()["lines"]
    two_tracks = next(line for line in lines if line["line_id"] == 2700)
    assert two_tracks["bgm"] == [
        {"code": "BGM02", "ord": 0, "titles": {"ja-JP": "気の合う仲間たち", "zh-Hant": "意氣相投的朋友們"}, "gallery_no": 4},
        {"code": "BGM51", "ord": 1, "titles": {}, "gallery_no": None}]
    assert two_tracks["bgm_detect"] == "實測" and two_tracks["bgm_may_change"] is True
    no_bgm = next(line for line in lines if line["line_id"] != 2700)
    assert no_bgm["bgm"] == [] and no_bgm["bgm_detect"] is None and no_bgm["bgm_may_change"] is False
    for line in lines:
        assert "bgm" not in line["attrs"] and "bgm_title" not in line["attrs"]


def _bgm_files(service, *names: tuple[str, bytes]) -> Path:
    root = service.assets.local_root / "story" / GAME / "bgm"
    root.mkdir(parents=True, exist_ok=True)
    for name, data in names:
        (root / name).write_bytes(data)
    return root


def test_bgm_audio_is_off_by_default(setup):
    """設定沒打開 bgm_audio：capabilities.bgm 為 false，端點即使檔案在也回 404。"""
    client, _, service = setup
    _bgm_files(service, ("BGM02.ogg", OGG))
    assert client.get(BASE).json()["capabilities"]["bgm"] is False
    r = client.get(f"{BASE}/bgm/BGM02")
    assert r.status_code == 404 and r.json()["error_code"] == "STORY_ASSET_NOT_FOUND" and OGG not in r.content


def test_bgm_audio_enabled(setup):
    """打開 bgm_audio：.ogg 優先、沒有再找 .mp3，Content-Type 跟著檔案；不進記憶體快取。"""
    client, repo, service = setup
    repo.config = {**CONFIG, "bgm_audio": True}
    _bgm_files(service, ("BGM02.ogg", OGG), ("BGM02.mp3", MP3), ("BGM05.mp3", MP3))
    assert client.get(BASE).json()["capabilities"]["bgm"] is True
    assert client.get("/api/v1/story/games").json()[0]["capabilities"]["bgm"] is True
    r = client.get(f"{BASE}/bgm/BGM02")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/ogg" and r.content == OGG
    assert "immutable" in r.headers["cache-control"] and r.headers["x-content-type-options"] == "nosniff"
    r = client.get(f"{BASE}/bgm/BGM05")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg" and r.content == MP3
    assert service.assets._cache.get(StoryService.bgm_key(GAME, "BGM02", ".ogg")) is None
    r = client.get(f"{BASE}/bgm/BGM99")
    assert r.status_code == 404 and r.json()["error_code"] == "STORY_ASSET_NOT_FOUND"
    for bad in ("BGM02.ogg", "a-b", "%2315", "%E6%9B%B2", "x" * 33, "a%20b"):
        r = client.get(f"{BASE}/bgm/{bad}")
        assert r.status_code == 404 and r.json()["error_code"] == "STORY_ASSET_NOT_FOUND", bad
    assert client.get("/api/v1/story/games/Nope/bgm/BGM02").status_code == 404


def test_bgm_traversal_is_rejected(setup, tmp_path):
    client, repo, _ = setup
    repo.config = {**CONFIG, "bgm_audio": True}
    (tmp_path / "evil.ogg").write_bytes(b"SECRET")  # 素材根目錄（tmp_path/assets）之外
    for path in (f"{BASE}/bgm/..%5C..%5C..%5C..%5Cevil", f"{BASE}/bgm/..%2F..%2F..%2F..%2Fevil",
                 "/api/v1/story/games/..%5C..%5C..%5C/bgm/evil"):
        r = client.get(path)
        assert r.status_code == 404 and b"SECRET" not in r.content, path


def test_bgm_needs_an_asset_store_and_maps_store_errors_to_503():
    repo = FakeRepo()
    repo.config = {**CONFIG, "bgm_audio": True}
    caps = _app(StoryService(repo, StoryAssetStore(local_dir=""))).get(BASE).json()["capabilities"]
    assert caps["bgm"] is False  # 設定打開了，但沒有素材來源
    broken = StoryAssetStore(minio_client=_FakeMinio(ConnectionError("refused")), bucket="b", local_dir="")
    r = _app(StoryService(repo, broken)).get(f"{BASE}/bgm/BGM02")
    assert r.status_code == 503 and r.json()["error_code"] == "STORY_ASSET_UNAVAILABLE"


def test_locate_line_and_note(setup):
    client, _, _ = setup
    assert client.get(f"{BASE}/locate?line_id=2700").json() == {
        "game_id": GAME, "build_id": 7, "tab": "common", "node_key": "sw:009_02B", "line_id": 2700, "seq": 0}
    assert client.get(f"{BASE}/locate?note_id=900").json()["line_id"] == 2700  # 母卡經由子卡
    assert client.get(f"{BASE}/locate?note_id=12345").json()["error_code"] == "STORY_LINE_NOT_FOUND"
    assert client.get(f"{BASE}/locate").json()["error_code"] == "STORY_LOCATE_PARAM"
    assert client.get(f"{BASE}/locate?note_id=1&line_id=2").status_code == 400


def test_save_filename_mime_and_path_variables(setup):
    client, repo, _ = setup
    r = client.post(f"{BASE}/saves", json={"line_id": 2700, "slot": "1"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert 'filename="data_0001_01.jpg"' in r.headers["content-disposition"]
    root, _ = P.load(r.content)
    node_key, rec = repo.records[2700]
    assert root["core"]["scenePlayer"] == {"point": rec.point, "scene": "009.共通－休日デート.ks*009_02B"}
    # 路徑：009 的第 0 個選項（sel_flag=0）→ … → 依 sel_flag 的菱形 → 009_02B；變數只來自選項的 effects
    assert root["user"] == {"sel_flag": 0}
    assert root["core"]["bookMarkInfo"]["lastText"] == "台詞2700続き"
    assert root["core"]["storeTime"] > 0
    assert P.read_container(r.content)[2] == _jpeg(b"\x07\x07")  # 換成該句的縮圖


def test_save_option_branch_and_route_preset(setup):
    client, _, _ = setup
    line_b = next(lid for lid, (k, _) in setup[1].records.items() if k == "sw:009_01B")
    root, _ = P.load(client.post(f"{BASE}/saves", json={"line_id": line_b}).content)
    assert root["user"] == {"sel_flag": 1, "meg_flag": 1}
    line_nene = next(lid for lid, (k, _) in setup[1].records.items() if k == "sw:101")
    root, _ = P.load(client.post(f"{BASE}/saves", json={"line_id": line_nene, "route_preset": "nene"}).content)
    assert root["user"] == {"nen_flag": 4}


def test_save_background_voice_uses_plays_at(setup):
    client, _, _ = setup
    root, _ = P.load(client.post(f"{BASE}/saves", json={"line_id": 1001}).content)
    assert root["core"]["scenePlayer"] == {"point": 9, "scene": "001.共通－始まり.ks*chapter11"}


def test_save_errors(setup):
    client, _, _ = setup
    assert client.post(f"{BASE}/saves", json={"line_id": 2700, "slot": "zz"}).json()["error_code"] == "STORY_SAVE_SLOT"
    assert client.post(f"{BASE}/saves", json={"line_id": 2700, "route_preset": "zz"}).status_code == 400
    assert client.post(f"{BASE}/saves", json={"line_id": 1}).json()["error_code"] == "STORY_LINE_NOT_FOUND"


def test_thumbs(setup):
    client, _, _ = setup
    r = client.get(f"{BASE}/thumbs/{THUMB}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert "immutable" in r.headers["cache-control"]
    assert client.get(f"{BASE}/thumbs/ffffffffffffffff").status_code == 404
    assert client.get(f"{BASE}/thumbs/not-a-hash").status_code == 404


def test_thumb_game_id_traversal_is_rejected(setup, tmp_path):
    """``%5C`` 解碼成反斜線，路由仍會配到 ``{game_id}``；Windows 上 ``..\\`` 會跳出素材資料夾。"""
    client, _, service = setup
    evil = tmp_path / "evil" / "thumbs" / f"{THUMB}.jpg"  # 素材根目錄（tmp_path/assets）之外
    evil.parent.mkdir(parents=True)
    evil.write_bytes(b"SECRET")
    r = client.get(f"/api/v1/story/games/..%5C..%5Cevil/thumbs/{THUMB}")
    assert r.status_code == 404 and r.json()["error_code"] == "STORY_GAME_NOT_FOUND"
    assert b"SECRET" not in r.content
    # 繞過 game_id 白名單、直接給素材層一樣讀不到
    assert asyncio.run(service.assets.get(f"story/..\\..\\evil/thumbs/{THUMB}.jpg")) is None
    assert asyncio.run(service.assets.get(str(evil))) is None
    for path in (f"{BASE}/../x", "/api/v1/story/games/a.b", "/api/v1/story/games/..%5Cx/graph"):
        assert client.get(path).status_code == 404


def test_voices(setup):
    client, _, service = setup
    for path in (f"{BASE}/voices/{VOICE}", f"{BASE}/voices/{VOICE}.mp3"):
        r = client.get(path)
        assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg" and r.content == MP3
        assert "immutable" in r.headers["cache-control"] and r.headers["x-content-type-options"] == "nosniff"
    # 語音不進記憶體快取（縮圖會）
    assert service.assets._cache.get(StoryService.voice_key(GAME, VOICE)) is None
    r = client.get(f"{BASE}/voices/nen999_999")
    assert r.status_code == 404 and r.json()["error_code"] == "STORY_ASSET_NOT_FOUND"
    for bad in ("a-b", "a.b", "%E8%AA%9E", "x" * 65, "a%20b"):
        r = client.get(f"{BASE}/voices/{bad}")
        assert r.status_code == 404 and r.json()["error_code"] == "STORY_ASSET_NOT_FOUND", bad
    assert client.get("/api/v1/story/games/Nope/voices/nen002_039").status_code == 404


def test_voice_traversal_is_rejected(setup, tmp_path):
    """``%5C``／``%2F`` 解碼後的路徑字元進不了物件鍵；``game_id`` 也一樣擋。"""
    client, _, _ = setup
    evil = tmp_path / "evil.mp3"  # 素材根目錄（tmp_path/assets）之外
    evil.write_bytes(b"SECRET")
    for path in (f"{BASE}/voices/..%5C..%5C..%5C..%5Cevil", f"{BASE}/voices/..%2F..%2F..%2F..%2Fevil",
                 f"/api/v1/story/games/..%5C..%5C..%5C/voices/evil"):
        r = client.get(path)
        assert r.status_code == 404 and b"SECRET" not in r.content, path


def test_voice_store_error_is_503():
    broken = StoryAssetStore(minio_client=_FakeMinio(ConnectionError("refused")), bucket="b", local_dir="")
    client = _app(StoryService(FakeRepo(), broken))
    r = client.get(f"{BASE}/voices/{VOICE}")
    assert r.status_code == 503 and r.json()["error_code"] == "STORY_ASSET_UNAVAILABLE"


def test_no_asset_store_disables_voices_capability():
    client = _app(StoryService(FakeRepo(), StoryAssetStore(local_dir="")))
    caps = client.get(BASE).json()["capabilities"]
    assert caps["voices"] is False and caps["thumbs"] is False


def test_safe_key_parts():
    assert safe_key_parts("story/G/thumbs/a.jpg") == ["story", "G", "thumbs", "a.jpg"]
    for bad in ("", "/etc/passwd", "\\\\host\\share", "C:/x", "C:x", "a/../b", "a\\..\\b", "a//b", "./a",
                "a/b:stream"):
        assert safe_key_parts(bad) is None, bad


def test_saves_config_rejects_unsafe_template_and_filename():
    slots = [{"id": "q", "label": "Q", "filename": "data_quick_01.jpg"}]
    assert SavesConfig(template="story/G/templates/quick.jpg", slots=slots)
    for template in ("/abs/quick.jpg", "story/../x.jpg", "story\\x.jpg", "C:/x.jpg", "story//x.jpg"):
        with pytest.raises(ValidationError):
            SavesConfig(template=template, slots=slots)
    for filename in ("../data.jpg", "a/b.jpg", "a\\b.jpg", '"x".jpg', ".hidden"):
        with pytest.raises(ValidationError):
            SavesConfig(template="story/G/q.jpg", slots=[{"id": "q", "label": "Q", "filename": filename}])


class _S3Like(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class _FakeMinio:
    def __init__(self, exc):
        self.exc = exc

    async def download_file(self, bucket, key, path):
        # MinioClient 會把 S3Error 包成 MinioStorageError，原錯誤放在 __cause__
        try:
            raise self.exc
        except Exception as inner:
            raise RuntimeError("MinIO 下載失敗") from inner


def test_minio_missing_object_is_none_other_errors_are_503(tmp_path):
    missing = StoryAssetStore(minio_client=_FakeMinio(_S3Like("NoSuchKey")), bucket="b", local_dir="")
    assert asyncio.run(missing.get("story/G/thumbs/x.jpg")) is None
    broken = StoryAssetStore(minio_client=_FakeMinio(ConnectionError("refused")), bucket="b", local_dir="")
    with pytest.raises(StoryAssetUnavailableError):
        asyncio.run(broken.get("story/G/thumbs/x.jpg"))
    assert StoryAssetStore(minio_client=_FakeMinio(None), bucket=None, local_dir="").available is False
    assert StoryAssetStore(minio_client=_FakeMinio(None), bucket="b", local_dir="").available is True

    repo = FakeRepo()
    client = _app(StoryService(repo, broken))
    r = client.get(f"{BASE}/thumbs/{THUMB}")
    assert r.status_code == 503 and r.json()["error_code"] == "STORY_ASSET_UNAVAILABLE"


def test_invalid_stored_config_is_reported_and_skipped_in_list(setup):
    client, repo, _ = setup
    repo.config = {**CONFIG, "route_presets": {"nene": {"nen_flag": "4"}}}  # SabbatConfig 要求整數
    assert client.get("/api/v1/story/games").json() == []
    r = client.get(BASE)
    assert r.status_code == 500 and r.json()["error_code"] == "STORY_CONFIG_INVALID"


def test_annotation_cache(setup):
    client, repo, _ = setup
    client.get(f"{BASE}/annotations")
    repo.cards = []
    assert client.get(f"{BASE}/annotations").json()["nodes"]  # 60 秒內沿用快取


# ---------- 路徑規則（與前端 core/path.ts 相同） ----------


def test_path_rule_prefers_lowest_order_and_accumulates():
    graph = build_story_graph(*F.rows(), F.rules()).graph
    index = GraphIndex(graph)
    path = index.path_nodes("sw:019_01D")
    assert path[0] == "sw:001" and path[-1] == "sw:019_01D"
    assert "sw:009_01A" in path and "sw:009_01B" not in path
    assert index.path_variables("sw:019_01D") == {"sel_flag": 3}
    assert index.path_variables("sw:009_01B") == {"sel_flag": 1, "meg_flag": 1}
    assert index.path_nodes("sw:001") == ["sw:001"]
    assert index.path_edges("sw:nope") == []


def test_effect_add_requires_number():
    assert EdgeEffect(var="f", op="add", value=2).value == 2
    assert EdgeEffect(var="f", op="set", value="x").value == "x"
    for bad in ("4", True, None):
        with pytest.raises(ValidationError):
            EdgeEffect(var="f", op="add", value=bad)


def _tiny_graph(effects):
    return StoryGraph(
        tabs=[StoryTab(key="t", title="T", order=1, start="a")],
        nodes=[StoryNode(key=k, tab="t", type="scene") for k in ("a", "b", "c")],
        edges=[StoryEdge(**{"from": "a"}, to="b", effects=effects[:1]),
               StoryEdge(**{"from": "b"}, to="c", effects=effects[1:]),
               StoryEdge(**{"from": "b"}, to="gone")])  # 指向不存在的節點：索引要能容忍


def test_graph_index_tolerates_dangling_edges_and_skips_non_numeric_adds():
    index = GraphIndex(_tiny_graph([EdgeEffect(var="v", op="set", value="s"), EdgeEffect(var="v", op="add", value=1)]))
    assert index.reachable("t") == {"a", "b", "c"}
    assert index.path_variables("c") == {"v": "s"}  # 字串不累加，和前端 pathVariables 相同
    index = GraphIndex(_tiny_graph([EdgeEffect(var="v", op="add", value=1), EdgeEffect(var="v", op="add", value=2)]))
    assert index.path_variables("c") == {"v": 3}


def test_verb_uses_the_shared_strict_term_parser():
    from app.services.story.repository import _verb

    assert _verb("ja-JP:見る") == "見る"
    assert _verb(None) is None
    assert _verb("見る") is None  # 沒有語言代碼：交給 corpus_tables 的嚴格解析，不把整個鍵當原形
    assert _verb("zh-Hant:看") is None
