"""saveStruct 引擎（千戀＊萬花）走 API 下載存檔的端到端測試。

End-to-end save downloads through ``/api/v1/story`` for a game on the
``kirikiri_savestruct`` engine: engine dispatch, filename and MIME, path
variables and route presets in ``user``, no JPEG thumbnail read, a broken
template mapped to ``STORY_SAVE_TEMPLATE``, and the Senren*Banka adapter's
save position for ``label:2`` scenes and lines without a save point.
"""

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_story_api as base  # noqa: E402

from app.infrastructure.krkr import savestruct  # noqa: E402
from app.services.story.assets import StoryAssetStore  # noqa: E402
from app.services.story.registry import get_story_game  # noqa: E402
from app.services.story.repository import BuildRecord, LineRecord  # noqa: E402
from app.services.story.service import StoryService  # noqa: E402

TEMPLATE_KEY = f"story/{base.GAME}/templates/quick.bmp"
CONFIG = {**base.CONFIG, "saves": {
    "template": TEMPLATE_KEY, "install_hint": "放到 savedata",
    "slots": [{"id": "quick", "label": "快速存檔", "filename": "data_quick_01.bmp"},
              {"id": "continue", "label": "前回のつづきから", "filename": "data_continue.bmp"}]}}
BMP = b"BM" + struct.pack("<IHHI", 66, 0, 0, 54) + b"\x00" * 52
TEMPLATE_ROOT = {"core": {"scenePlayer": {"scene": "x.ks*y", "point": 1, "histories": []}, "storeTime": 0,
                          "bookMarkInfo": {"lastText": "", "textlang": "jp"}, "historyData": {"data": ["old"]}},
                 "user": {"selectDoneFlag": 1, "old_flag": 3}}


class SaveStructRepo(base.FakeRepo):
    """同一份 fixture 劇情圖，但引擎是 saveStruct。The fixture graph on the saveStruct engine."""

    def __init__(self):
        super().__init__()
        self.config = CONFIG

    def _record(self):
        return BuildRecord(build_id=7, game_id=base.GAME, status="active", config=self.config, stats={"nodes": 1},
                           engine="kirikiri_savestruct", titles={"ja-JP": "テスト", "zh-Hant": "測試"})


class CountingStore(StoryAssetStore):
    """記錄讀過哪些物件。Records which objects were read."""

    def __init__(self, local_dir):
        super().__init__(local_dir=local_dir)
        self.keys: list[str] = []

    async def get(self, key):
        self.keys.append(key)
        return await super().get(key)


@pytest.fixture
def setup(tmp_path):
    assets = tmp_path / "assets"
    (assets / TEMPLATE_KEY).parent.mkdir(parents=True)
    (assets / TEMPLATE_KEY).write_bytes(BMP + savestruct.pack_text(savestruct.dump(TEMPLATE_ROOT)))
    thumb = assets / f"story/{base.GAME}/thumbs/{base.THUMB}.jpg"
    thumb.parent.mkdir(parents=True)
    thumb.write_bytes(base._jpeg(b"\x07\x07"))
    repo = SaveStructRepo()
    store = CountingStore(str(assets))
    return base._app(StoryService(repo, store)), repo, store, assets


def test_save_uses_savestruct_writer(setup):
    client, repo, store, _ = setup
    r = client.post(f"{base.BASE}/saves", json={"line_id": 2700, "slot": "quick"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert 'filename="data_quick_01.bmp"' in r.headers["content-disposition"]
    root = savestruct.load(r.content)
    _, rec = repo.records[2700]
    assert root["core"]["scenePlayer"]["scene"] == "009.共通－休日デート.ks*009_02B"
    assert root["core"]["scenePlayer"]["point"] == rec.point
    assert root["core"]["historyData"]["data"] == []
    # 路徑上的選項 effects；樣板的劇情旗標（old_flag）去掉，選項畫面狀態保留
    assert root["user"] == {"selectDoneFlag": 1, "sel_flag": 0}
    assert root["core"]["bookMarkInfo"]["lastText"] == "台詞2700続き"
    assert savestruct.split_container(r.content)[0] == BMP  # JPEG 縮圖不能嵌入，沿用樣板縮圖
    assert not any("/thumbs/" in k for k in store.keys)  # 用不上的 JPEG 縮圖不去讀


def test_save_route_preset_overlays_path_variables(setup):
    client, repo, _, _ = setup
    line = next(lid for lid, (k, _) in repo.records.items() if k == "sw:101")
    root = savestruct.load(client.post(f"{base.BASE}/saves", json={"line_id": line, "route_preset": "nene"}).content)
    assert root["user"] == {"selectDoneFlag": 1, "nen_flag": 4}


def test_broken_template_is_a_business_error(setup):
    client, _, _, assets = setup
    (assets / TEMPLATE_KEY).write_bytes(BMP + savestruct.pack_text("(const) %[\r\n]"))
    r = client.post(f"{base.BASE}/saves", json={"line_id": 2700})
    assert r.status_code == 500
    assert r.json()["error_code"] == "STORY_SAVE_TEMPLATE"
    assert TEMPLATE_KEY in r.json()["message"]


def _line(label: str, point: int | None) -> LineRecord:
    return LineRecord(line_id=1, seq=0, kind="line", point=point, file_name="508a・サブ－こはるんver1.00.ks",
                      label=label, speaker_ja=None, speaker_zh=None, text_ja="x", text_zh=None, voice=None,
                      thumb=None, is_h=False, h_label=None, chapter_label=None, attrs={}, bgm=[],
                      bgm_detect=None, bgm_may_change=False)


def test_senren_banka_save_position():
    game = get_story_game("SenrenBanka")
    assert game.save_position(_line("sub_part_8a:2", 153)) == {
        "scene": "508a・サブ－こはるんver1.00.ks*sub_part_8a:2", "point": 153}
    assert game.save_position(_line("001_01A", None)) is None  # 沒有存檔點的句子不能存
