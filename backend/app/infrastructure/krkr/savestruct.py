"""吉里吉里內建 saveStruct 存檔（壓縮 TJS 文字）的編解碼與寫入器。

KiriKiri's built-in ``saveStruct`` save format (compressed TJS text) and its
save writer, ported from the calibration tool ``SB_calib/work/sbsave.py``
(docs/wip/senren_banka_corpus_import_FEAT_2026-11-08.md D7).

適用於 ゆずソフト《千恋＊万花》等不用 PackinOne 的遊戲：

    data_*.bmp    BMP 縮圖之後（BMP 檔頭記的檔案大小之後）接一段封包
    *.ksd         整個檔案就是封包

封包：``FE FE 02 FF FE`` ＋ 壓縮後長度 u64 ＋ 原始長度 u64 ＋ zlib(UTF-16LE 的 TJS 文字)。
文字是 TJS 運算式，例 ``(const) %[ "core" => (const) %[ ... ], "user" => %[ ... ] ]``：
字串、整數、小數（``0x1.9p4 /* 25 */`` 十六進位寫法）、``void``、陣列、字典、octet。

**寫回時必須完全照遊戲的寫法**（每個陣列／字典前加 ``(const)``、一層縮排一個空白、空容器也換行、
小數用十六進位並附註解、換行 ``\\r\\n``），否則遊戲報「栞を読み込めないか…(Syntax error)」。

所有格式錯誤一律拋 :class:`SaveStructError`（附位置或長度等脈絡），呼叫端只需要接這一種。

Writes must mimic the game's own serializer exactly (``(const)`` prefixes,
one-space indent, CRLF, hex reals with comments), or the game rejects the
save with a syntax error. Every format problem raises :class:`SaveStructError`.
"""

from __future__ import annotations

import logging
import math
import re
import struct
import zlib
from typing import Any

from app.infrastructure.krkr.packinone import SaveMeta

logger = logging.getLogger(__name__)

MAGIC = b"\xfe\xfe\x02\xff\xfe"
_BMP_HEADER = 54  # BITMAPFILEHEADER (14) + BITMAPINFOHEADER (40)
_INT64 = (-(1 << 63), (1 << 63) - 1)


class SaveStructError(ValueError):
    """存檔格式錯誤。Malformed save data."""


# ---------- 容器 / container ----------


def bmp_size(data: bytes) -> int:
    """BMP 檔頭記的檔案大小；檔頭不完整或大小不合理時拋錯。The BMP header's file size.

    Raises:
        SaveStructError: 不是 BMP、檔頭不完整，或記的大小小於檔頭、大於資料長度。
            Not a BMP, a truncated header, or an impossible size.
    """
    if data[:2] != b"BM":
        raise SaveStructError(f"不是 BMP（開頭 {data[:4]!r}）")
    if len(data) < 6:
        raise SaveStructError(f"BMP 檔頭不完整（{len(data)} 位元組）")
    size = struct.unpack_from("<I", data, 2)[0]
    if not _BMP_HEADER <= size <= len(data):
        raise SaveStructError(f"BMP 檔頭記的大小 {size} 不合理（資料 {len(data)} 位元組）；檔案可能被截斷")
    return size


def split_container(data: bytes) -> tuple[bytes, bytes]:
    """回傳（BMP 縮圖, 封包）；``.ksd`` 時縮圖為空。Return (BMP image, pack).

    Raises:
        SaveStructError: 不是 BMP 存檔也不是 ``.ksd`` 封包，或 BMP 之後沒有封包。
            Neither a BMP save nor a ``.ksd`` pack, or no pack after the BMP.
    """
    if data[:2] == b"BM":
        size = bmp_size(data)
        if size == len(data):
            raise SaveStructError("這張 BMP 之後沒有存檔資料（不是 saveStruct 存檔）")
        return data[:size], data[size:]
    if data[:5] != MAGIC:
        raise SaveStructError(f"無法辨識的存檔：不是 BMP 也不是 .ksd 封包（開頭 {data[:5]!r}）")
    return b"", data


def unpack_text(pack: bytes) -> str:
    """封包 → TJS 文字。Pack → TJS text."""
    if pack[:5] != MAGIC:
        raise SaveStructError(f"不是壓縮 TJS 封包（開頭 {pack[:5]!r}）")
    if len(pack) < 21:
        raise SaveStructError(f"封包檔頭不完整（{len(pack)} 位元組）")
    clen, ulen = struct.unpack_from("<QQ", pack, 5)
    body = pack[21:21 + clen]
    if len(body) != clen:
        raise SaveStructError(f"封包被截斷：記錄壓縮長度 {clen}，實際 {len(body)}")
    try:
        raw = zlib.decompress(body)
    except zlib.error as e:
        raise SaveStructError(f"解壓失敗：{e}") from e
    if len(raw) != ulen:
        raise SaveStructError(f"解壓後長度 {len(raw)} ≠ 記錄 {ulen}")
    try:
        return raw.decode("utf-16le")
    except UnicodeDecodeError as e:
        raise SaveStructError(f"TJS 文字不是合法的 UTF-16LE：{e}") from e


def pack_text(text: str) -> bytes:
    """TJS 文字 → 封包。TJS text → pack."""
    raw = text.encode("utf-16le")
    z = zlib.compress(raw, 6)
    return MAGIC + struct.pack("<QQ", len(z), len(raw)) + z


# ---------- TJS 解析與序列化 / parse and dump ----------


class Real(float):
    """TJS 的實數；``src`` 保留原文（含 ``/* 註解 */``），寫回時一字不差。A TJS real."""

    src: str | None = None


_TOKEN = re.compile(r'''
    (?P<ws>\s+|/\*.*?\*/)
  | (?P<const>\(const\))
  | (?P<dict>%\[)
  | (?P<open>\[)
  | (?P<close>\])
  | (?P<arrow>=>)
  | (?P<comma>,)
  | (?P<str>"(?:[^"\\]|\\.)*")
  | (?P<hexreal>[+-]?0x[0-9A-Fa-f]\.[0-9A-Fa-f]+p[+-]?\d+(?:[ ]/\*[^*]*\*/)?)
  | (?P<real>[+-]?(?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?(?:[ ]/\*[^*]*\*/)?|[+-]?\d+[eE][+-]?\d+)
  | (?P<int>-?0x[0-9A-Fa-f]+|-?\d+)
  | (?P<void>void)
  | (?P<octet><%[0-9A-Fa-f\s]*%>)
''', re.S | re.X)

_ESC = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "'": "'", "a": "\a", "b": "\b", "f": "\f",
        "v": "\v", "0": "\0"}


def _unescape(s: str) -> str:
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        n = s[i + 1]
        if n == "x":
            # 最多 4 位十六進位；寫出時一律寫滿 4 位（見 _quote），來回一致
            m = re.match(r"[0-9A-Fa-f]{1,4}", s[i + 2:])
            if not m:
                raise SaveStructError(f"不正確的 \\x 跳脫：{s[i:i + 6]!r}")
            out.append(chr(int(m.group(0), 16)))
            i += 2 + len(m.group(0))
        else:
            out.append(_ESC.get(n, n))
            i += 2
    return "".join(out)


def _tokens(text: str) -> list[tuple[str, str, int]]:
    pos = 1 if text.startswith("﻿") else 0
    out = []
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            raise SaveStructError(f"無法解析的位置 {pos}：{text[pos:pos + 40]!r}")
        if m.lastgroup not in ("ws", "const"):
            out.append((m.lastgroup, m.group(0), pos))
        pos = m.end()
    return out


def parse(text: str) -> Any:
    """TJS 文字 → Python 值（字典、串列、字串、整數、:class:`Real`、None）。Parse TJS text.

    Raises:
        SaveStructError: 語法錯誤、文字被截斷、缺逗號，或根值之後還有多餘內容。
            Syntax errors, truncation, missing commas or trailing content.
    """
    toks = _tokens(text)
    i = 0

    def peek() -> str:
        if i >= len(toks):
            raise SaveStructError(f"文字提早結束（共 {len(toks)} 個記號），存檔可能被截斷")
        return toks[i][0]

    def after_item(where: str) -> None:
        """元素之後只能是逗號或結尾括號。After an element: a comma or the closing bracket."""
        nonlocal i
        kind = peek()
        if kind == "comma":
            i += 1
        elif kind != "close":
            raise SaveStructError(f"{where}的元素之間缺少逗號（位置 {toks[i][2]}：{toks[i][1]!r}）")

    def value() -> Any:
        nonlocal i
        peek()
        kind, s, pos = toks[i]
        i += 1
        if kind == "str":
            return _unescape(s[1:-1])
        if kind == "int":
            return int(s, 16) if s.lstrip("-").startswith("0x") else int(s)
        if kind in ("hexreal", "real"):
            num = s.split(" /*")[0]
            if kind == "hexreal" and "p-" not in num and "p+" not in num:
                num = num.replace("p", "p+")
            r = Real(float.fromhex(num) if kind == "hexreal" else float(num))
            r.src = s
            return r
        if kind == "void":
            return None
        if kind == "octet":
            return {"__octet__": re.sub(r"\s", "", s[2:-2])}
        if kind == "open":
            arr = []
            while peek() != "close":
                arr.append(value())
                after_item("陣列")
            i += 1
            return arr
        if kind == "dict":
            d = {}
            while peek() != "close":
                k = value()
                if not isinstance(k, str):
                    raise SaveStructError(f"字典的鍵必須是字串（位置 {pos}）：{k!r}")
                if peek() != "arrow":
                    raise SaveStructError(f"字典缺少 =>（位置 {toks[i][2]}：{toks[i][1]!r}）")
                i += 1
                d[k] = value()
                after_item("字典")
            i += 1
            return d
        raise SaveStructError(f"意外的記號 {kind} {s!r}（位置 {pos}）")

    root = value()
    if i != len(toks):
        raise SaveStructError(f"根值之後還有多餘內容（位置 {toks[i][2]}：{toks[i][1]!r}）")
    return root


def _quote(s: str) -> str:
    out = ['"']
    for c in s:
        if c == '"':
            out.append('\\"')
        elif c == "\\":
            out.append("\\\\")
        elif c == "\n":
            out.append("\\n")
        elif c == "\r":
            out.append("\\r")
        elif c == "\t":
            out.append("\\t")
        elif ord(c) < 0x20:
            # 寫滿 4 位：後面緊接十六進位字元時才不會被讀成同一個跳脫（\x01 + "BC" ≠ \x01BC）
            out.append(f"\\x{ord(c):04X}")
        else:
            out.append(c)
    out.append('"')
    return "".join(out)


def _real(v: float) -> str:
    """仿照遊戲的寫法：``0x1.86A0000000000p15 /* 50000 */``。Mimic the game's real format."""
    src = getattr(v, "src", None)
    if src is not None:
        return src
    v = float(v)
    if math.isnan(v) or math.isinf(v):
        raise SaveStructError(f"TJS 存檔不能寫入 {v!r}")
    if v == 0:
        return "0x0.0000000000000p0 /* 0 */"
    h = v.hex()
    sign = "-" if h.startswith("-") else ""
    mant, exp = h.lstrip("-")[2:].split("p")
    comment = str(int(v)) if v == int(v) else repr(v)
    return f"{sign}0x{mant.upper()}p{int(exp)} /* {comment} */"


def dump(v: Any, indent: int = 0) -> str:
    """Python 值 → 遊戲讀得懂的 TJS 文字。Serialize the way the game does.

    Raises:
        SaveStructError: 不能序列化的型別、非字串的字典鍵、超出 64 位元的整數、NaN／無限大。
            Unsupported types, non-string keys, out-of-range ints, NaN/inf.
    """
    pad = " " * indent
    if v is None:
        return "void"
    if isinstance(v, bool):
        return str(int(v))
    if isinstance(v, float):
        return _real(v)
    if isinstance(v, int):
        if not _INT64[0] <= v <= _INT64[1]:
            raise SaveStructError(f"整數 {v} 超出 64 位元範圍")
        return str(v)
    if isinstance(v, str):
        return _quote(v)
    if isinstance(v, dict):
        if "__octet__" in v and len(v) == 1:
            return f'<% {v["__octet__"]} %>'
        if not v:
            return f"(const) %[\r\n{pad}]"
        bad = [k for k in v if not isinstance(k, str)]
        if bad:
            raise SaveStructError(f"字典的鍵必須是字串：{bad[:3]!r}")
        items = [f"{pad} {_quote(k)} => {dump(x, indent + 1)}" for k, x in v.items()]
        return "(const) %[\r\n" + ",\r\n".join(items) + f"\r\n{pad}]"
    if isinstance(v, list):
        if not v:
            return f"(const) [\r\n{pad}]"
        items = [f"{pad} {dump(x, indent + 1)}" for x in v]
        return "(const) [\r\n" + ",\r\n".join(items) + f"\r\n{pad}]"
    raise SaveStructError(f"不能序列化的型別 {type(v).__name__}")


def load(data: bytes) -> Any:
    """存檔位元組 → 根字典。Save bytes → root object."""
    return parse(unpack_text(split_container(data)[1]))


def first_difference(a: Any, b: Any, path: str = "root") -> str | None:
    """兩個值第一個不同的位置（``root.core.scenePlayer.point`` 這種路徑）；相同時回 None。

    Path of the first difference between two values, or None when equal.
    """
    if isinstance(a, dict) and isinstance(b, dict):
        for k in list(a) + [k for k in b if k not in a]:
            if k not in a or k not in b:
                return f"{path}.{k}（只有一邊有）"
            d = first_difference(a[k], b[k], f"{path}.{k}")
            if d:
                return d
        return None
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return f"{path}（長度 {len(a)} ≠ {len(b)}）"
        for n, (x, y) in enumerate(zip(a, b)):
            d = first_difference(x, y, f"{path}[{n}]")
            if d:
                return d
        return None
    return None if a == b else f"{path}：{a!r} ≠ {b!r}"


# ---------- 存檔寫入器 / save writer ----------

def _is_bare_bmp(data: bytes) -> bool:
    """是不是一張完整、後面沒接其他資料的 BMP。Whether ``data`` is exactly one BMP image."""
    try:
        return bmp_size(data) == len(data)
    except SaveStructError:
        return False


#: ``user`` 裡要保留的樣板鍵以外，其他都換成路徑變數；這些是選項畫面的狀態，不是劇情旗標。
#: Template ``user`` keys that describe choice-screen state rather than story flags.
_USER_STATE_KEYS = ("selectDoneFlag", "selectInfos", "selectTotalCount")


class SaveStructSaveWriter:
    """``kirikiri_savestruct`` 引擎的存檔寫入器。Save writer for the saveStruct engine."""

    engine = "kirikiri_savestruct"
    #: 這個寫入器能用的縮圖格式（存檔服務只為它讀這種縮圖）。The thumbnail format it accepts.
    thumb_format = "bmp"

    def write(self, template: bytes, position: dict[str, Any], variables: dict[str, Any], meta: SaveMeta) -> bytes:
        """以樣板產生一個欄位存檔（做法同校準時的 ``advance_run.write_save``）。

        Write a slot save from the template, the way the calibration did.

        - ``core.scenePlayer`` 只改 ``scene``／``point``；回想紀錄 ``core.historyData.data`` 清空。
        - ``user``（遊戲的 ``f.*``）：樣板的劇情旗標全部去掉，換成 ``variables``；選項畫面狀態
          （``selectDoneFlag`` 等）照樣板。``scenePlayer.histories[].flags`` 一起換，否則讀檔後
          倒回時會帶回樣板的旗標。
        - ``bookMarkInfo.lastText``、``storeTime`` 依 ``meta``。
        - 縮圖：``meta.thumb`` 是完整的 BMP 才換；其他格式沿用樣板的縮圖（只影響存檔清單的預覽）。

        Args:
            template: 樣板存檔（``data_quick_01.bmp`` 等）。The template save.
            position: ``{"scene": "<檔名>.ks*<標籤>", "point": N}``。The story position.
            variables: 遊戲變數（``f.*``）。Game variables.
            meta: 台詞、縮圖、時間。Line text, thumbnail and time.

        Returns:
            新的存檔位元組。The new save bytes.

        Raises:
            SaveStructError: 樣板格式不對、缺少 ``core.scenePlayer``／``core.historyData``，或寫回後
                解不回同樣的內容。A malformed template or a failed round trip.
        """
        image, pack = split_container(template)
        root = parse(unpack_text(pack))
        core = root.get("core") if isinstance(root, dict) else None
        player = core.get("scenePlayer") if isinstance(core, dict) else None
        history = core.get("historyData") if isinstance(core, dict) else None
        if not isinstance(player, dict) or not isinstance(history, dict):
            raise SaveStructError("樣板缺少 core.scenePlayer 或 core.historyData，不是千戀＊萬花的欄位存檔")
        player["scene"] = str(position["scene"])
        player["point"] = int(position["point"])
        history["data"] = []
        clean = {k: (int(v) if isinstance(v, bool) else v) for k, v in variables.items()}
        old_user = root.get("user") if isinstance(root.get("user"), dict) else {}
        root["user"] = {**{k: old_user[k] for k in _USER_STATE_KEYS if k in old_user}, **clean}
        dropped = set(old_user) - set(_USER_STATE_KEYS)
        for h in player.get("histories") or []:
            if isinstance(h, dict) and isinstance(h.get("flags"), dict):
                h["flags"] = {**{k: v for k, v in h["flags"].items() if k not in dropped}, **clean}
        if meta.last_text is not None:
            info = core.get("bookMarkInfo") if isinstance(core.get("bookMarkInfo"), dict) else {}
            core["bookMarkInfo"] = {**info, "lastText": meta.last_text}
        if meta.store_time_ms is not None and "storeTime" in core:
            core["storeTime"] = int(meta.store_time_ms)
        if meta.thumb is not None:
            if _is_bare_bmp(meta.thumb):
                image = meta.thumb
            else:
                # 縮圖只是存檔清單的預覽：格式不對時沿用樣板縮圖，存檔照樣產生
                logger.warning("縮圖不是單獨的 BMP（開頭 %r、%d 位元組），沿用樣板縮圖", meta.thumb[:4], len(meta.thumb))
        data = image + pack_text(dump(root))
        # 回傳前完整解回來比對，確保遊戲讀得到同樣的內容
        diff = first_difference(root, load(data))
        if diff:
            raise SaveStructError(f"重新解碼後 {diff}")
        return data
