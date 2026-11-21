"""Codec for KiriKiri PackinOne ``saveDataPack`` saves (TJS/4s0).

吉里吉里 PackinOne「saveDataPack」存檔（TJS/4s0）的編解碼。

Ported from the offline tool ``krkr_savepack.py`` (docs/wip/story_flowchart_FEAT_2026-10-31.md D13).

移植自離線工具 ``krkr_savepack.py``（docs/wip/story_flowchart_FEAT_2026-10-31.md D13）。

For games that use PackinOne.dll, such as YUZUSOFT's Sabbat of the Witch: a slot save
``data_*.jpg`` is a thumbnail, and the pack is hidden after the image data in an ``FF EB``
segment (0xFF is stuffed as ``FF 00``), followed finally by ``FF D9``.

適用於 ゆずソフト《魔女的夜宴》等使用 PackinOne.dll 的遊戲：欄位存檔 ``data_*.jpg`` 是一張縮圖，
封包藏在影像資料之後的 ``FF EB`` 區段（0xFF 以 ``FF 00`` 填充），最後是 ``FF D9``。

Pack format:
    header       ``TJS/`` + compression (``n`` none / ``4`` LZ4) + ``s0\\0`` + seed(u32) + cryptmode(u16)
                 + ivlen(u16) + iv
    encryption   key = BLAKE2s-256(key=seed, data=iv); nonce = XXH32(iv, seed), seed;
                 each key-stream segment = one ChaCha block (64 bytes), extended by xorshift32 to
                 "block count × 64" bytes; the segment size depends on cryptmode: 1 → 1024, 2 → 512,
                 3 → 256, 4–6 → 64 (plain ChaCha, not extended)
    compression  repeated [u16 length][LZ4 block], each block 4096 bytes after decompression
    content      a tree serialization of [type][check byte][data], with a 4-byte checksum at the end

封包格式：
    檔頭  ``TJS/`` + 壓縮（``n`` 無／``4`` LZ4）+ ``s0\\0`` + seed(u32) + cryptmode(u16) + ivlen(u16) + iv
    加密  key = BLAKE2s-256(key=seed, data=iv)；nonce = XXH32(iv, seed), seed；
          每個金鑰串流區段 = 1 次 ChaCha（64 位元組）再以 xorshift32 延伸到「區塊數 × 64」位元組，
          區段大小依 cryptmode：1 → 1024、2 → 512、3 → 256、4–6 → 64（純 ChaCha，不延伸）
    壓縮  反覆的 [u16 長度][LZ4 區塊]，每塊解壓後 4096 位元組
    內容  [型別][檢查位元組][資料] 的樹狀序列化，最後 4 位元組為校驗碼

Integers and floats are distinct TJS types (25 vs 25.0); ``None`` maps to void.

整數與小數是不同的 TJS 型別（25 與 25.0）；``None`` 對應 void。
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from typing import Any, Callable

M32 = 0xFFFFFFFF
CHUNK = 4096
#: cryptmode → (ChaCha rounds, 64-byte blocks per segment); when there is more than one block, the blocks
#: from the second on are extended by xorshift32.
#: cryptmode → (ChaCha 輪數, 每段的 64 位元組區塊數)；區塊數 > 1 時第 2 塊起以 xorshift32 延伸。
CRYPT_MODES = {1: (8, 16), 2: (12, 8), 3: (20, 4), 4: (8, 1), 5: (12, 1), 6: (20, 1)}
T_VOID, T_STRING, T_INT, T_REAL, T_ARRAY, T_DICT = 0x00, 0x02, 0x04, 0x05, 0x81, 0xC1


class PackError(ValueError):
    """An error for malformed save data.

    存檔格式錯誤。
    """


def _rotl(x: int, r: int) -> int:
    return ((x << r) | (x >> (32 - r))) & M32


# ---------- 雜湊與加密 ----------


def xxh32(data: bytes, seed: int) -> int:
    """Return the XXH32 hash of ``data``.

    回傳 ``data`` 的 XXH32 雜湊。

    Args:
        data: the bytes to hash
            要雜湊的位元組
        seed: the 32-bit seed
            32 位元的種子

    Returns:
        the 32-bit hash
        32 位元雜湊值
    """
    p1, p2, p3, p4, p5 = 0x9E3779B1, 0x85EBCA77, 0xC2B2AE3D, 0x27D4EB2F, 0x165667B1
    n, i = len(data), 0
    if n >= 16:
        v = [(seed + p1 + p2) & M32, (seed + p2) & M32, seed, (seed - p1) & M32]
        while i <= n - 16:
            for j in range(4):
                w = struct.unpack_from("<I", data, i + j * 4)[0]
                v[j] = (_rotl((v[j] + w * p2) & M32, 13) * p1) & M32
            i += 16
        h = (_rotl(v[0], 1) + _rotl(v[1], 7) + _rotl(v[2], 12) + _rotl(v[3], 18)) & M32
    else:
        h = (seed + p5) & M32
    h = (h + n) & M32
    while i + 4 <= n:
        h = (_rotl((h + struct.unpack_from("<I", data, i)[0] * p3) & M32, 17) * p4) & M32
        i += 4
    while i < n:
        h = (_rotl((h + data[i] * p5) & M32, 11) * p1) & M32
        i += 1
    h ^= h >> 15
    h = (h * p2) & M32
    h ^= h >> 13
    h = (h * p3) & M32
    return h ^ (h >> 16)


def _chacha_block(state: list[int], rounds: int) -> list[int]:
    x = list(state)

    def qr(a: int, b: int, c: int, d: int) -> None:
        x[a] = (x[a] + x[b]) & M32
        x[d] = _rotl(x[d] ^ x[a], 16)
        x[c] = (x[c] + x[d]) & M32
        x[b] = _rotl(x[b] ^ x[c], 12)
        x[a] = (x[a] + x[b]) & M32
        x[d] = _rotl(x[d] ^ x[a], 8)
        x[c] = (x[c] + x[d]) & M32
        x[b] = _rotl(x[b] ^ x[c], 7)

    for _ in range(rounds // 2):
        qr(0, 4, 8, 12)
        qr(1, 5, 9, 13)
        qr(2, 6, 10, 14)
        qr(3, 7, 11, 15)
        qr(0, 5, 10, 15)
        qr(1, 6, 11, 12)
        qr(2, 7, 8, 13)
        qr(3, 4, 9, 14)
    return [(x[i] + state[i]) & M32 for i in range(16)]


def crypt(data: bytes, seed: int, iv: bytes, cryptmode: int) -> bytes:
    """Encrypt or decrypt ``data``; both are the same operation (XOR with the key stream).

    加密或解密 ``data``；兩者相同（XOR 金鑰串流）。

    Args:
        data: the bytes to encrypt or decrypt
            要加密或解密的位元組
        seed: the pack header's seed
            封包檔頭的 seed
        iv: the pack header's iv
            封包檔頭的 iv
        cryptmode: the pack header's cryptmode (1–6)
            封包檔頭的 cryptmode（1–6）

    Returns:
        the result, the same length as ``data``
        結果，與 ``data`` 等長

    Raises:
        PackError: if ``cryptmode`` is unknown
            若 ``cryptmode`` 未知
    """
    if cryptmode not in CRYPT_MODES:
        raise PackError(f"未知的加密模式 {cryptmode}")
    rounds, blocks = CRYPT_MODES[cryptmode]
    key = hashlib.blake2s(iv, key=struct.pack("<I", seed), digest_size=32).digest()
    h = xxh32(iv, seed)
    fallback = (seed ^ h) if blocks > 1 and (seed ^ h) else seed
    base = list(struct.unpack("<4I", b"expand 32-byte k")) + list(struct.unpack("<8I", key)) + [0, 0, h, seed]
    stream = bytearray()
    counter = 0
    while len(stream) < len(data):
        state = base[:]
        state[12], state[13] = counter & M32, counter >> 32
        counter += 1
        words = _chacha_block(state, rounds)
        for i in range(16, blocks * 16):
            v = words[i - 16]
            v ^= (v << 13) & M32
            v ^= v >> 17
            v ^= (v << 5) & M32
            words.append(v if v else fallback)
        stream += struct.pack(f"<{len(words)}I", *words)
    n = len(data)
    return (int.from_bytes(data, "little") ^ int.from_bytes(bytes(stream[:n]), "little")).to_bytes(n, "little")


# ---------- LZ4（區塊格式） ----------


def _lz4_decompress_into(src: bytes, out: bytearray) -> None:
    i, n = 0, len(src)
    while i < n:
        token = src[i]
        i += 1
        lit = token >> 4
        if lit == 15:
            while True:
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        out += src[i:i + lit]
        i += lit
        if i >= n:
            break
        offset = src[i] | src[i + 1] << 8
        i += 2
        if offset == 0 or offset > len(out):
            raise PackError("LZ4 資料損毀（offset 超出範圍）")
        ml = token & 15
        if ml == 15:
            while True:
                b = src[i]
                i += 1
                ml += b
                if b != 255:
                    break
        ml += 4
        start = len(out) - offset
        for k in range(ml):  # 可能與自身重疊，逐位元組複製
            out.append(out[start + k])


def _lz4_len(out: bytearray, v: int) -> None:
    while v >= 255:
        out.append(255)
        v -= 255
    out.append(v)


def lz4_compress_block(src: bytes) -> bytes:
    """Compress ``src`` into one independent LZ4 block with a simple greedy matcher.

    簡單的貪婪 LZ4 壓縮（獨立區塊）。

    Args:
        src: the data to compress
            要壓縮的資料

    Returns:
        the LZ4 block
        LZ4 區塊
    """
    n = len(src)
    out = bytearray()
    table: dict[bytes, int] = {}
    anchor = i = 0
    match_limit = n - 12
    end_limit = n - 5
    while i < match_limit:
        key = src[i:i + 4]
        cand = table.get(key)
        table[key] = i
        if cand is not None and i - cand <= 0xFFFF:
            m = 4
            while i + m < end_limit and src[cand + m] == src[i + m]:
                m += 1
            lit, ml = i - anchor, m - 4
            out.append(min(lit, 15) << 4 | min(ml, 15))
            if lit >= 15:
                _lz4_len(out, lit - 15)
            out += src[anchor:i]
            out += struct.pack("<H", i - cand)
            if ml >= 15:
                _lz4_len(out, ml - 15)
            i += m
            anchor = i
        else:
            i += 1
    lit = n - anchor
    out.append(min(lit, 15) << 4)
    if lit >= 15:
        _lz4_len(out, lit - 15)
    out += src[anchor:]
    return bytes(out)


def lz4_stream_decode(body: bytes) -> bytes:
    """Decompress a pack body made of repeated [u16 length][LZ4 block] records.

    解壓由反覆的 [u16 長度][LZ4 區塊] 組成的封包本體。

    Args:
        body: the compressed body
            壓縮後的本體

    Returns:
        the decompressed bytes
        解壓後的位元組

    Raises:
        PackError: if an LZ4 block is corrupt (offset out of range)
            若 LZ4 區塊損毀（offset 超出範圍）
    """
    out = bytearray()
    i = 0
    while i < len(body):
        size = struct.unpack_from("<H", body, i)[0]
        _lz4_decompress_into(body[i + 2:i + 2 + size], out)
        i += 2 + size
    return bytes(out)


def lz4_stream_encode(raw: bytes) -> bytes:
    """Compress ``raw`` in 4096-byte chunks into repeated [u16 length][LZ4 block] records.

    把 ``raw`` 每 4096 位元組壓成一塊，組成反覆的 [u16 長度][LZ4 區塊]。

    Args:
        raw: the uncompressed bytes
            未壓縮的位元組

    Returns:
        the compressed body
        壓縮後的本體
    """
    out = bytearray()
    for i in range(0, len(raw), CHUNK):
        block = lz4_compress_block(raw[i:i + CHUNK])
        out += struct.pack("<H", len(block)) + block
    return bytes(out)


# ---------- 樹狀序列化 ----------


class _Checker:
    """A generator of the check bytes and the trailing checksum (a 3-byte xorshift).

    檢查位元組與結尾校驗碼的產生器（3 位元組 xorshift）。
    """

    def __init__(self, seed: int) -> None:
        self.s = [((seed >> 24) ^ seed) & 0xFF, (seed >> 8) & 0xFF, (seed >> 16) & 0xFF]

    def next(self, type_byte: int) -> int:
        if not type_byte:  # void 不推進狀態
            return self.s[2]
        a, b, c = self.s
        t = (a ^ (a << 1)) & 0xFF
        n = ((((t >> 2) ^ c) >> 3) ^ c ^ t) & 0xFF
        self.s = [b, c, n]
        return n

    def trailer(self) -> int:
        for _ in range(3):
            self.next(1)
        return self.s[0] << 16 | self.s[1] << 8 | self.s[2]


def deserialize(raw: bytes, seed: int) -> Any:
    """Decode the serialized tree of a pack's content into a Python value.

    封包內容 → Python 值。

    Args:
        raw: the decrypted and decompressed content
            已解密、解壓的封包內容
        seed: the pack header's seed, which drives the check bytes
            封包檔頭的 seed，決定檢查位元組

    Returns:
        the root value: a dict, list, str, int, float or ``None`` (void)
        根值：字典、串列、字串、整數、小數或 ``None``（void）

    Raises:
        PackError: if a check byte or the checksum does not match, a type is unsupported, or the
            content is truncated
            若檢查位元組或校驗碼不符、型別不支援，或內容被截斷
    """
    checker = _Checker(seed)

    def read_str(i: int) -> tuple[str, int]:
        n = struct.unpack_from("<I", raw, i)[0]
        return raw[i + 4:i + 4 + n * 2].decode("utf-16le", "surrogatepass"), i + 4 + n * 2

    def read(i: int) -> tuple[Any, int]:
        t, check = raw[i], raw[i + 1]
        if checker.next(t) != check:
            raise PackError(f"檢查位元組不符（位置 {i}）")
        i += 2
        if t == T_VOID:
            return None, i
        if t == T_STRING:
            return read_str(i)
        if t == T_INT:
            return struct.unpack_from("<q", raw, i)[0], i + 8
        if t == T_REAL:
            return struct.unpack_from("<d", raw, i)[0], i + 8
        if t == T_ARRAY:
            n = struct.unpack_from("<I", raw, i)[0]
            i += 4
            arr = []
            for _ in range(n):
                v, i = read(i)
                arr.append(v)
            return arr, i
        if t == T_DICT:
            n = struct.unpack_from("<I", raw, i)[0]
            i += 4
            dic = {}
            for _ in range(n):
                k, i = read_str(i)
                dic[k], i = read(i)
            return dic, i
        raise PackError(f"不支援的型別 0x{t:02x}（位置 {i - 2}）")

    try:
        root, end = read(0)
        if struct.unpack_from("<I", raw, end)[0] != checker.trailer() or end + 4 != len(raw):
            raise PackError("結尾校驗碼不符")
    except (struct.error, IndexError) as exc:
        raise PackError(f"封包內容截斷：{exc}") from exc
    return root


def serialize(root: Any, seed: int) -> bytes:
    """Encode a Python value into a pack's serialized tree.

    Python 值 → 封包內容。

    ``bool`` is written as an integer.

    ``bool`` 寫成整數。

    Args:
        root: the root value: a dict, list, str, int, bool, float or ``None``
            根值：字典、串列、字串、整數、布林、小數或 ``None``
        seed: the pack header's seed, which drives the check bytes
            封包檔頭的 seed，決定檢查位元組

    Returns:
        the serialized content, ending with the 4-byte checksum
        序列化後的內容，結尾為 4 位元組校驗碼

    Raises:
        TypeError: if a value has a type that cannot be serialized
            若有無法序列化的型別
    """
    checker = _Checker(seed)
    out = bytearray()

    def write_str(s: str) -> None:
        b = s.encode("utf-16le", "surrogatepass")
        out.extend(struct.pack("<I", len(b) // 2) + b)

    def write(v: Any) -> None:
        if v is None:
            t = T_VOID
        elif isinstance(v, (bool, int)):
            t = T_INT
        elif isinstance(v, float):
            t = T_REAL
        elif isinstance(v, str):
            t = T_STRING
        elif isinstance(v, list):
            t = T_ARRAY
        elif isinstance(v, dict):
            t = T_DICT
        else:
            raise TypeError(f"無法序列化的型別：{type(v).__name__}")
        out.extend((t, checker.next(t)))
        if t == T_STRING:
            write_str(v)
        elif t == T_INT:
            out.extend(struct.pack("<q", int(v)))
        elif t == T_REAL:
            out.extend(struct.pack("<d", v))
        elif t == T_ARRAY:
            out.extend(struct.pack("<I", len(v)))
            for x in v:
                write(x)
        elif t == T_DICT:
            out.extend(struct.pack("<I", len(v)))
            for k, x in v.items():
                write_str(k)
                write(x)

    write(root)
    out.extend(struct.pack("<I", checker.trailer()))
    return bytes(out)


# ---------- 封包與容器 ----------


@dataclass(frozen=True)
class PackHeader:
    """The pack header.

    封包檔頭。

    Attributes:
        compress: the compression byte, ``b"n"`` for none or ``b"4"`` for LZ4
            壓縮方式位元組，``b"n"`` 為無、``b"4"`` 為 LZ4
        seed: the seed used by encryption and the check bytes
            加密與檢查位元組使用的 seed
        cryptmode: the encryption mode; 0 means not encrypted
            加密模式；0 表示未加密
        iv: the initialization vector
            初始化向量
        raw: the raw header bytes, including the iv
            檔頭原始位元組（含 iv）
    """

    compress: bytes
    seed: int
    cryptmode: int
    iv: bytes
    raw: bytes


def parse_header(pack: bytes) -> PackHeader:
    """Parse the header of a TJS/4s0 pack.

    解析 TJS/4s0 封包的檔頭。

    Args:
        pack: the pack bytes
            封包位元組

    Returns:
        the parsed header
        解析後的檔頭

    Raises:
        PackError: if ``pack`` is not a TJS/4s0 pack
            若 ``pack`` 不是 TJS/4s0 封包
    """
    if pack[:4] != b"TJS/" or pack[5:8] != b"s0\x00":
        raise PackError("不是 TJS/4s0 封包")
    seed, cryptmode, ivlen = struct.unpack_from("<IHH", pack, 8)
    header_len = 16 + ivlen
    return PackHeader(pack[4:5], seed, cryptmode, pack[16:header_len], pack[:header_len])


def unpack(pack: bytes) -> tuple[Any, PackHeader]:
    """Decode a pack into (content, header).

    封包 → (內容, 檔頭)。

    Args:
        pack: the pack bytes
            封包位元組

    Returns:
        the decoded root value and the pack header
        解碼後的根值與封包檔頭

    Raises:
        PackError: if the pack is malformed, or its encryption or compression is unknown
            若封包格式錯誤，或加密、壓縮方式未知
    """
    h = parse_header(pack)
    body = pack[len(h.raw):]
    if h.cryptmode:
        body = crypt(body, h.seed, h.iv, h.cryptmode)
    if h.compress == b"4":
        body = lz4_stream_decode(body)
    elif h.compress != b"n":
        raise PackError(f"未知的壓縮方式 {h.compress!r}")
    return deserialize(body, h.seed), h


def pack_data(root: Any, h: PackHeader) -> bytes:
    """Re-encode a value into a pack with the same header (seed, iv).

    以同一個檔頭（seed、iv）重新編碼。

    Args:
        root: the root value to encode
            要編碼的根值
        h: the header to reuse
            沿用的檔頭

    Returns:
        the pack bytes
        封包位元組

    Raises:
        TypeError: if a value has a type that cannot be serialized
            若有無法序列化的型別
    """
    body = serialize(root, h.seed)
    if h.compress == b"4":
        body = lz4_stream_encode(body)
    if h.cryptmode:
        body = crypt(body, h.seed, h.iv, h.cryptmode)
    return h.raw + body


def _jpeg_pack_offset(b: bytes) -> int:
    """Return the offset of the ``FF EB`` segment that holds the pack, searched after the JPEG headers.

    回傳裝有封包的 ``FF EB`` 區段位置（略過 JPEG 檔頭各區段之後才找）。

    Raises:
        PackError: if the JPEG has no embedded save data
            若這張 JPEG 沒有嵌入存檔資料
    """
    i = 2
    while b[i] == 0xFF and b[i + 1] != 0xDA:
        i += 2 + struct.unpack_from(">H", b, i + 2)[0]
    pos = b.find(b"\xff\xeb", i)
    if pos < 0:
        raise PackError("這張 JPEG 沒有嵌入存檔資料")
    return pos


def embed_in_jpeg(image: bytes, pack: bytes) -> bytes:
    """Append the pack after the JPEG image data (drop the original EOI, add an FF EB segment, re-add EOI).

    把封包接在 JPEG 影像資料之後（去掉原本的 EOI，接 FF EB 區段，再補 EOI）。

    ``image`` must be a complete JPEG (ending with ``FF D9``) that has no pack embedded itself.

    ``image`` 必須是完整 JPEG（結尾 ``FF D9``）且本身沒有嵌入封包。

    Args:
        image: the bare JPEG image
            單純的 JPEG 影像
        pack: the pack bytes
            封包位元組

    Returns:
        the JPEG with the pack embedded
        嵌入封包後的 JPEG

    Raises:
        PackError: if ``image`` is not a complete JPEG (no EOI at the end)
            若 ``image`` 不是完整的 JPEG（結尾沒有 EOI）
    """
    if not image.endswith(b"\xff\xd9"):
        raise PackError("縮圖不是完整的 JPEG（結尾沒有 EOI）")
    stuffed = pack.replace(b"\xff", b"\xff\x00")
    # 長度欄位＝其後到檔尾的位元組數（遊戲讀取時不檢查，超過 16 位元時直接截斷）
    return image[:-2] + b"\xff\xeb" + struct.pack(">H", (len(stuffed) + 2) & 0xFFFF) + stuffed + b"\xff\xd9"


def read_container(data: bytes) -> tuple[bytes, Callable[[bytes], bytes], bytes | None]:
    """Split a save file into its pack, a container rebuild function and the original image.

    拆出存檔的封包、重建容器的函式與原影像。

    Args:
        data: the save file bytes, either a raw ``TJS/`` pack (``.ksd``) or a JPEG with an
            embedded pack
            存檔位元組：單純的 ``TJS/`` 封包（``.ksd``），或嵌有封包的 JPEG

    Returns:
        (pack, function that rebuilds the container from a new pack using the original image,
        original JPEG image or ``None`` for a raw pack)
        (封包, 以原影像重建容器的函式, 原影像 JPEG；單純封包時為 ``None``)

    Raises:
        PackError: if the file is neither a TJS/ pack nor a JPEG, or the JPEG has no embedded save
            若檔案不是 TJS/ 封包也不是 JPEG，或 JPEG 沒有嵌入存檔
    """
    if data[:4] == b"TJS/":
        return data, lambda pack: pack, None
    if data[:2] == b"\xff\xd8":
        pos = _jpeg_pack_offset(data)
        end = data.rfind(b"\xff\xd9")
        pack = data[pos + 4:end].replace(b"\xff\x00", b"\xff")
        image = data[:pos] + b"\xff\xd9"
        return pack, lambda new_pack: embed_in_jpeg(image, new_pack), image
    raise PackError("無法辨識的檔案：不是 TJS/ 封包也不是 JPEG")


def load(data: bytes) -> tuple[Any, PackHeader]:
    """Decode save file bytes into (content, header).

    存檔位元組 → (內容, 檔頭)。

    Args:
        data: the save file bytes (``.ksd`` pack or JPEG slot save)
            存檔位元組（``.ksd`` 封包或 JPEG 欄位存檔）

    Returns:
        the decoded root value and the pack header
        解碼後的根值與封包檔頭

    Raises:
        PackError: if the file is not a recognizable or valid save
            若檔案無法辨識或格式錯誤
    """
    pack, _, _ = read_container(data)
    return unpack(pack)


# ---------- 存檔寫入器（D13） ----------


@dataclass(frozen=True)
class SaveMeta:
    """Extra information written into a save.

    寫進存檔的附帶資訊。

    Attributes:
        last_text: the line shown in the save list (``core.bookMarkInfo.lastText``); ``None``
            leaves it unchanged
            存檔清單顯示的台詞（``core.bookMarkInfo.lastText``）；``None`` 時不改
        thumb: the thumbnail of that line, in the writer's ``thumb_format``; ``None`` keeps the
            template's thumbnail
            該句的縮圖（寫入器 ``thumb_format`` 的格式）；``None`` 時沿用樣板的縮圖
        store_time_ms: the save time (epoch milliseconds, ``core.storeTime``); ``None`` leaves it
            unchanged
            存檔時間（epoch 毫秒，``core.storeTime``）；``None`` 時不改
    """

    last_text: str | None = None
    thumb: bytes | None = None
    store_time_ms: int | None = None


class PackinOneSaveWriter:
    """The save writer for the ``kirikiri_packinone`` engine.

    ``kirikiri_packinone`` 引擎的存檔寫入器。
    """

    engine = "kirikiri_packinone"
    #: The thumbnail format that can be embedded in a save.
    #: 能嵌入存檔的縮圖格式。
    thumb_format = "jpeg"

    def write(self, template: bytes, position: dict[str, Any], variables: dict[str, Any], meta: SaveMeta) -> bytes:
        """Write a slot save from the template.

        以樣板產生一個欄位存檔。

        ``root["user"]`` (the game's ``f.*``) is replaced wholesale by ``variables`` and the
        template's own variables are not kept: a save's variables come only from the selected path
        and the route defaults, never mixed with flags from the play-through used to make the
        template. In ``core`` only the position, the line text and the time change; the time is set
        only when the template already has ``storeTime``. ``meta.thumb`` replaces the thumbnail only
        when the template is a JPEG; ``bool`` variables are stored as integers.

        ``root["user"]``（遊戲的 ``f.*``）整個換成 ``variables``，樣板原本的變數不保留：存檔的變數
        只來自選取路徑與路線預設，不會混到做樣板時那次遊玩的旗標。``core`` 只改位置、台詞與時間；
        樣板本來就有 ``storeTime`` 時才改時間。樣板是 JPEG 時 ``meta.thumb`` 才會換掉縮圖；``bool``
        變數存成整數。

        Args:
            template: the template save (``data_quick_01.jpg`` and so on)
                樣板存檔（``data_quick_01.jpg`` 等）
            position: the story position, ``{"scene": "<file name>.ks*<label>", "point": N}``
                故事位置，``{"scene": "<檔名>.ks*<標籤>", "point": N}``
            variables: the game variables (stored in ``user``, that is ``f.*``)
                遊戲變數（存進 ``user``，即 ``f.*``）
            meta: the line text, thumbnail and time
                台詞、縮圖、時間

        Returns:
            the new save bytes
            新的存檔位元組

        Raises:
            PackError: if the template is malformed, the thumbnail is not a complete JPEG, or the
                written save does not decode back to the same content
                若樣板格式錯誤、縮圖不是完整的 JPEG，或寫出的存檔解不回相同內容
        """
        pack, rebuild, _ = read_container(template)
        root, header = unpack(pack)
        core = root.setdefault("core", {})
        core["scenePlayer"] = {"point": int(position["point"]), "scene": str(position["scene"])}
        root["user"] = {k: (int(v) if isinstance(v, bool) else v) for k, v in variables.items()}
        if meta.last_text is not None:
            info = core.get("bookMarkInfo") if isinstance(core.get("bookMarkInfo"), dict) else {}
            core["bookMarkInfo"] = {**info, "lastText": meta.last_text}
        if meta.store_time_ms is not None and "storeTime" in core:
            core["storeTime"] = int(meta.store_time_ms)
        new_pack = pack_data(root, header)
        if meta.thumb is not None and template[:2] == b"\xff\xd8":
            data = embed_in_jpeg(meta.thumb, new_pack)
        else:
            data = rebuild(new_pack)
        # 回傳前完整解回來比對，確保遊戲讀得到同樣的內容
        if load(data)[0] != root:
            raise PackError("重新解碼的內容與寫入的不一致")
        return data
