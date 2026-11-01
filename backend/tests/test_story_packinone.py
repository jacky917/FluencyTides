"""PackinOne 存檔編解碼與寫入器的測試（劇情流程圖 D13）。

Tests for the KiriKiri PackinOne codec and save writer: hash vectors, LZ4
and cipher round trips, the JPEG container, and rewriting scene/point/user.
A real template round trip runs only when the local calibration file exists.
"""

import random
import struct
from pathlib import Path

import pytest

from app.infrastructure.krkr import packinone as P

REAL_TEMPLATE = Path(r"C:\Users\forip\Desktop\WorkSpace\game\SW_calib\quick_template.jpg")


def _jpeg(payload: bytes = b"\x12\x34\x56") -> bytes:
    """最小的 JPEG：SOI、APP0、SOS、影像資料、EOI。A minimal JPEG."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
    sos = b"\xff\xda" + struct.pack(">H", 8) + b"\x01\x01\x00\x00\x3f\x00"
    return b"\xff\xd8" + app0 + sos + payload + b"\xff\xd9"


def _header(seed: int = 0xA9BBD962, cryptmode: int = 1, compress: bytes = b"4") -> P.PackHeader:
    iv = bytes(range(16))
    raw = b"TJS/" + compress + b"s0\x00" + struct.pack("<IHH", seed, cryptmode, len(iv)) + iv
    return P.parse_header(raw)


def _template(root: dict, **header) -> bytes:
    h = _header(**header)
    return P.embed_in_jpeg(_jpeg(), P.pack_data(root, h))


SAMPLE = {
    "core": {"scenePlayer": {"point": 20, "scene": "001.共通.ks*chapter11"}, "chSpeed": 25.0,
             "storeTime": 1792766511000, "bookMarkInfo": {"lastText": "舊的", "textlang": "jp"},
             "list": [1, 2.5, None, "文字", {"a": -3}]},
    "user": {},
    "id": "{A293B3BB}",
}


def test_xxh32_known_vectors():
    assert P.xxh32(b"", 0) == 0x02CC5D05
    assert P.xxh32(b"abc", 0) == 0x32D153FF


def test_lz4_round_trip_random_and_repetitive():
    rnd = random.Random(7)
    for raw in (bytes(rnd.randrange(256) for _ in range(9000)), b"ab" * 5000, b"", b"x" * 13):
        assert P.lz4_stream_decode(P.lz4_stream_encode(raw)) == raw


@pytest.mark.parametrize("mode", sorted(P.CRYPT_MODES))
def test_crypt_is_symmetric(mode):
    data = bytes(range(256)) * 9
    enc = P.crypt(data, 1234, b"iv-bytes", mode)
    assert enc != data
    assert P.crypt(enc, 1234, b"iv-bytes", mode) == data


def test_serialize_keeps_int_float_and_void():
    raw = P.serialize(SAMPLE, 99)
    assert P.deserialize(raw, 99) == SAMPLE
    back = P.deserialize(raw, 99)
    assert isinstance(back["core"]["chSpeed"], float)
    assert isinstance(back["core"]["storeTime"], int)


def test_checker_detects_corruption():
    raw = bytearray(P.serialize(SAMPLE, 99))
    raw[1] ^= 0xFF
    with pytest.raises(P.PackError):
        P.deserialize(bytes(raw), 99)


@pytest.mark.parametrize("compress,cryptmode", [(b"4", 1), (b"n", 0), (b"4", 6)])
def test_container_round_trip(compress, cryptmode):
    data = _template(SAMPLE, compress=compress, cryptmode=cryptmode)
    root, h = P.load(data)
    assert root == SAMPLE
    assert h.cryptmode == cryptmode
    pack, rebuild, image = P.read_container(data)
    assert image == _jpeg()
    assert rebuild(pack) == data


def test_writer_rewrites_position_user_text_time_and_thumb():
    template = _template(SAMPLE)
    thumb = _jpeg(b"\x99\x88")
    out = P.PackinOneSaveWriter().write(
        template, {"scene": "009.共通－休日デート.ks*009_01A", "point": 5},
        {"sel_flag": 0, "meg_flag": 2}, P.SaveMeta(last_text="新しい台詞", thumb=thumb, store_time_ms=1800000000000))
    root, _ = P.load(out)
    assert root["core"]["scenePlayer"] == {"point": 5, "scene": "009.共通－休日デート.ks*009_01A"}
    assert root["user"] == {"sel_flag": 0, "meg_flag": 2}
    assert root["core"]["bookMarkInfo"] == {"lastText": "新しい台詞", "textlang": "jp"}
    assert root["core"]["storeTime"] == 1800000000000
    assert root["core"]["chSpeed"] == 25.0 and isinstance(root["core"]["chSpeed"], float)
    _, _, image = P.read_container(out)
    assert image == thumb


def test_writer_without_thumb_keeps_template_image():
    template = _template(SAMPLE)
    out = P.PackinOneSaveWriter().write(template, {"scene": "a.ks*b", "point": 1}, {}, P.SaveMeta())
    assert P.read_container(out)[2] == _jpeg()
    assert P.load(out)[0]["core"]["bookMarkInfo"]["lastText"] == "舊的"


def test_rejects_non_save():
    with pytest.raises(P.PackError):
        P.load(b"not a save")
    with pytest.raises(P.PackError):
        P.load(_jpeg())


@pytest.mark.skipif(not REAL_TEMPLATE.exists(), reason="本機沒有校準用的快速存檔樣板")
def test_real_template_round_trip_is_byte_identical():
    data = REAL_TEMPLATE.read_bytes()
    pack, rebuild, _ = P.read_container(data)
    root, h = P.unpack(pack)
    assert "scenePlayer" in root["core"]
    again = rebuild(P.pack_data(root, h))
    assert P.load(again)[0] == root
