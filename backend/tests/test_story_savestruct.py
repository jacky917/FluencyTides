"""吉里吉里 saveStruct 存檔（千戀＊萬花）編解碼與寫入器的測試。

Tests for the KiriKiri saveStruct codec and save writer (Senren*Banka):
TJS text round trips in the game's exact layout, a golden snapshot of the
writer's full output, the BMP container, every malformed-input path
(always :class:`SaveStructError`), and rewriting position / user / text /
time. A real template round trip runs only when ``SENREN_MATERIAL`` points
at the material folder.
"""

import os
import struct
from pathlib import Path

import pytest

from app.infrastructure.krkr import savestruct
from app.infrastructure.krkr.packinone import SaveMeta
from app.services.story.saves import save_writer_for

MATERIAL = os.environ.get("SENREN_MATERIAL")  # 例 Y:/material/yuzusoft/SenrenBanka；沒設就跳過真實樣板測試

# 遊戲寫出的樣子：(const) 前綴、一層縮排一個空白、CRLF、十六進位小數附註解、空容器也換行
GAME_TEXT = "\r\n".join([
    '(const) %[',
    ' "core" => (const) %[',
    '  "scenePlayer" => (const) %[',
    '   "scene" => "001・アーサー王ver1.07.ks*001_01com",',
    '   "point" => 613,',
    '   "histories" => (const) [',
    '    (const) %[',
    '     "flags" => (const) %[',
    '      "currentChartPos" => "s001*com_part_1",',
    '      "s001*com_part_1_sel" => 1',
    '     ]',
    '    ]',
    '   ]',
    '  ],',
    '  "chSpeed" => 0x1.9000000000000p4 /* 25 */,',
    '  "storeTime" => 1794042359000,',
    '  "bookMarkInfo" => (const) %[',
    '   "textlang" => "jp",',
    '   "lastText" => "「舊的\\"台詞\\"」"',
    '  ],',
    '  "historyData" => (const) %[',
    '   "data" => (const) [',
    '    "a",',
    '    void',
    '   ]',
    '  ],',
    '  "empty" => (const) %[',
    '  ]',
    ' ],',
    ' "user" => (const) %[',
    '  "selectDoneFlag" => 1,',
    '  "s001*com_part_1_sel" => 1,',
    '  "selectTotalCount" => 2,',
    '  "sub" => 1',
    ' ],',
    ' "id" => "{AA3F539A}"',
    ']',
])

#: 寫入器對 GAME_TEXT 的完整輸出（golden）：空陣列、新字典、user 的鍵順序都在這裡固定下來。
#: 這個格式改了，遊戲就會報「栞を読み込めない（Syntax error）」，所以一字不差地比對。
WRITER_GOLDEN = "\r\n".join([
    '(const) %[',
    ' "core" => (const) %[',
    '  "scenePlayer" => (const) %[',
    '   "scene" => "101・芳乃－弁当ver1.02.ks*yos_part_1:2",',
    '   "point" => 577,',
    '   "histories" => (const) [',
    '    (const) %[',
    '     "flags" => (const) %[',
    '      "currentChartPos" => "s001*com_part_1",',
    '      "s010*com_part_10_sel" => 2',
    '     ]',
    '    ]',
    '   ]',
    '  ],',
    '  "chSpeed" => 0x1.9000000000000p4 /* 25 */,',
    '  "storeTime" => 1,',
    '  "bookMarkInfo" => (const) %[',
    '   "textlang" => "jp",',
    '   "lastText" => "「新的」"',
    '  ],',
    '  "historyData" => (const) %[',
    '   "data" => (const) [',
    '   ]',
    '  ],',
    '  "empty" => (const) %[',
    '  ]',
    ' ],',
    ' "user" => (const) %[',
    '  "selectDoneFlag" => 1,',
    '  "selectTotalCount" => 2,',
    '  "s010*com_part_10_sel" => 2',
    ' ],',
    ' "id" => "{AA3F539A}"',
    ']',
])

POSITION = {"scene": "101・芳乃－弁当ver1.02.ks*yos_part_1:2", "point": 577}


def _bmp(payload: bytes = b"\x00" * 12) -> bytes:
    """最小的 BMP：檔頭的檔案大小涵蓋整張圖。A minimal BMP whose header size covers the image."""
    size = 14 + 40 + len(payload)
    return b"BM" + struct.pack("<IHHI", size, 0, 0, 54) + struct.pack("<IiiHHIIiiII", 40, 2, 2, 1, 24, 0,
                                                                      len(payload), 0, 0, 0, 0) + payload


def _template(text: str = GAME_TEXT) -> bytes:
    return _bmp() + savestruct.pack_text(text)


def _write(template: bytes | None = None, variables: dict | None = None, meta: SaveMeta | None = None) -> bytes:
    writer = save_writer_for("kirikiri_savestruct")
    return writer.write(template or _template(), POSITION, variables if variables is not None else {},
                        meta or SaveMeta())


def _text_of(save: bytes) -> str:
    return savestruct.unpack_text(savestruct.split_container(save)[1])


# ---------- TJS 文字 ----------


def test_dump_reproduces_game_text_exactly():
    assert savestruct.dump(savestruct.parse(GAME_TEXT)) == GAME_TEXT


def test_parse_keeps_ints_reals_void_and_escapes():
    core = savestruct.parse(GAME_TEXT)["core"]
    assert core["scenePlayer"]["point"] == 613 and isinstance(core["scenePlayer"]["point"], int)
    assert core["chSpeed"] == 25.0 and isinstance(core["chSpeed"], savestruct.Real)
    assert core["historyData"]["data"] == ["a", None]
    assert core["bookMarkInfo"]["lastText"] == '「舊的"台詞"」'
    assert core["empty"] == {}


def test_new_real_uses_game_format():
    assert savestruct.dump(50000.0) == "0x1.86A0000000000p15 /* 50000 */"
    assert savestruct.dump(-0.5) == "-0x1.0000000000000p-1 /* -0.5 */"


def test_control_characters_round_trip_even_before_hex_letters():
    # \x01 後面緊接 "BC"：寫成 \x0001 才不會被讀成 \x01BC
    text = "a\x01BC\x1fz"
    assert savestruct.parse(savestruct.dump(text)) == text


@pytest.mark.parametrize("text, message", [
    ('(const) %[ "a" => 1', "提早結束"),
    ("(const) [ 1, 2", "提早結束"),
    ('%[ "a" => 1 ] 5 ]', "多餘內容"),
    ('%[ "a" => 1 "b" => 2 ]', "缺少逗號"),
    ("[ 1 2 ]", "缺少逗號"),
    ('%[ "a" 1 ]', "缺少 =>"),
    ("%[ 1 => 2 ]", "鍵必須是字串"),
    ("%[ @ ]", "無法解析"),
])
def test_parse_rejects_malformed_text(text, message):
    with pytest.raises(savestruct.SaveStructError, match=message):
        savestruct.parse(text)


@pytest.mark.parametrize("value, message", [
    (float("nan"), "不能寫入"),
    (float("inf"), "不能寫入"),
    (1 << 63, "64 位元"),
    ({1: "x"}, "鍵必須是字串"),
    (object(), "不能序列化"),
])
def test_dump_rejects_values_the_game_cannot_read(value, message):
    with pytest.raises(savestruct.SaveStructError, match=message):
        savestruct.dump(value)


# ---------- 容器 ----------


def test_container_round_trip_and_header():
    data = _template()
    image, pack = savestruct.split_container(data)
    assert image == _bmp()
    assert pack[:5] == savestruct.MAGIC
    assert savestruct.load(data) == savestruct.parse(GAME_TEXT)


def test_ksd_without_image():
    assert savestruct.split_container(savestruct.pack_text(GAME_TEXT))[0] == b""


def _with_bmp_size(data: bytes, size: int) -> bytes:
    return data[:2] + struct.pack("<I", size) + data[6:]


@pytest.mark.parametrize("data, message", [
    (b"BM\x00", "檔頭不完整"),
    (_with_bmp_size(_template(), 10), "不合理"),
    (_with_bmp_size(_template(), 10 ** 7), "不合理"),
    (_bmp(), "沒有存檔資料"),
    (b"\xff\xd8\xff\xe0 jpeg", "不是 BMP 也不是 .ksd"),
    (_bmp() + savestruct.MAGIC + b"\x00" * 3, "檔頭不完整"),
])
def test_container_rejects_malformed_files(data, message):
    with pytest.raises(savestruct.SaveStructError, match=message):
        savestruct.load(data)


def test_truncated_or_corrupt_pack_is_reported():
    pack = savestruct.pack_text(GAME_TEXT)
    with pytest.raises(savestruct.SaveStructError, match="被截斷"):
        savestruct.unpack_text(pack[:-10])
    body = struct.pack("<QQ", 4, 8) + b"\x00\x01\x02\x03"
    with pytest.raises(savestruct.SaveStructError, match="解壓失敗"):
        savestruct.unpack_text(savestruct.MAGIC + body)
    import zlib
    odd = zlib.compress(b"abc")  # 奇數位元組不是 UTF-16LE
    with pytest.raises(savestruct.SaveStructError, match="UTF-16LE"):
        savestruct.unpack_text(savestruct.MAGIC + struct.pack("<QQ", len(odd), 3) + odd)


def test_first_difference_names_the_path():
    assert savestruct.first_difference({"a": [1, {"b": 2}]}, {"a": [1, {"b": 3}]}) == "root.a[1].b：2 ≠ 3"
    assert savestruct.first_difference({"a": 1}, {"a": 1}) is None
    assert "只有一邊有" in savestruct.first_difference({"a": 1}, {})


# ---------- 寫入器 ----------


def test_writer_output_matches_golden_text():
    out = _write(variables={"s010*com_part_10_sel": 2}, meta=SaveMeta(last_text="「新的」", store_time_ms=1))
    assert _text_of(out) == WRITER_GOLDEN
    assert savestruct.split_container(out)[0] == _bmp()


def test_writer_replaces_story_flags_but_keeps_choice_state():
    root = savestruct.load(_write(variables={"s010*com_part_10_sel": 2, "flag": True}))
    # 樣板的劇情旗標（s001…、sub）去掉，選項畫面狀態保留，換上路徑變數（bool 轉整數）
    assert root["user"] == {"selectDoneFlag": 1, "selectTotalCount": 2, "s010*com_part_10_sel": 2, "flag": 1}
    flags = root["core"]["scenePlayer"]["histories"][0]["flags"]
    assert flags == {"currentChartPos": "s001*com_part_1", "s010*com_part_10_sel": 2, "flag": 1}


def test_path_variables_override_template_choice_state():
    root = savestruct.load(_write(variables={"selectDoneFlag": 0}))
    assert root["user"]["selectDoneFlag"] == 0


def test_writer_adds_book_mark_info_but_not_store_time_when_template_lacks_them():
    text = GAME_TEXT.replace('  "storeTime" => 1794042359000,\r\n', "")
    text = text.replace('  "bookMarkInfo" => (const) %[\r\n   "textlang" => "jp",\r\n'
                        '   "lastText" => "「舊的\\"台詞\\"」"\r\n  ],\r\n', "")
    root = savestruct.load(_write(_template(text), meta=SaveMeta(last_text="x", store_time_ms=5)))
    assert "storeTime" not in root["core"]
    assert root["core"]["bookMarkInfo"] == {"lastText": "x"}


@pytest.mark.parametrize("text", [
    '(const) %[\r\n]',
    '(const) [\r\n]',
    '(const) %[\r\n "core" => (const) %[\r\n  "scenePlayer" => (const) %[\r\n  ]\r\n ]\r\n]',
])
def test_writer_rejects_template_without_required_structure(text):
    with pytest.raises(savestruct.SaveStructError, match="scenePlayer 或 core.historyData"):
        _write(_template(text))


def test_writer_thumbnail_only_replaced_by_a_bare_bmp():
    jpeg = _write(meta=SaveMeta(thumb=b"\xff\xd8jpeg"))
    assert savestruct.split_container(jpeg)[0] == _bmp()
    with_tail = _write(meta=SaveMeta(thumb=_bmp(b"\x02" * 12) + b"trailing"))
    assert savestruct.split_container(with_tail)[0] == _bmp()
    other = _bmp(b"\x01" * 12)
    assert savestruct.split_container(_write(meta=SaveMeta(thumb=other)))[0] == other


def test_writer_declares_bmp_thumbnails():
    assert save_writer_for("kirikiri_savestruct").thumb_format == "bmp"
    assert save_writer_for("kirikiri_packinone").thumb_format == "jpeg"


@pytest.mark.skipif(not MATERIAL, reason="設 SENREN_MATERIAL 指向素材資料夾才跑")
def test_real_template_round_trip_is_text_identical():
    data = (Path(MATERIAL) / "templates" / "quick.bmp").read_bytes()
    text = savestruct.unpack_text(savestruct.split_container(data)[1])
    assert savestruct.dump(savestruct.parse(text)) == text
    savestruct.load(_write(data))  # 寫入器能處理真實樣板
