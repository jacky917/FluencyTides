"""舊劇本 id 與遊戲錨點的對齊（docs/wip/corpus_db_rebuild_FEAT_2026-10-31.md D4）。

Align legacy ``scripts`` ids with the game anchors (plan D4).

同一個檔案裡有一字不差的重複台詞（例「………」），只比文字會對錯 id。
觀察到的結構（2026-11-01 對真實資料驗證）：

- 每個場景標籤（``file*label``）在舊表裡是一段連續的 id，且只屬於一個舊章節；
- 同一標籤內，舊 id 的順序與錨點的順序一致；但標籤之間的先後在兩邊可能不同。

所以分四步：

1. 以「台詞＋語音」在整個檔案內唯一的配對當錨；
2. 再以「台詞」在檔案內唯一（且語音不衝突）的配對當錨；
3. 由錨定出每個標籤在舊表的 id 範圍，標籤內做加權的序列比對（DP）：
   文字相同才可配對，語音相同、說話者相同的配對分數較高；
4. 剩下的舊列（多半是沒有語音的「………」）配給同檔案、同文字、
   位置最接近的錨點，標成低信心；這一步不檢查語音，因為剩下的多半是
   兩邊語音記錄互相矛盾的列。

Observed structure: each label maps to one contiguous old-id range within one
legacy chapter, and order is consistent inside a label but not across labels.
Hence: unique (text, voice) anchors → unique text anchors → per-label weighted
DP inside the label's id range → nearest-position fallback for leftovers.
"""

from __future__ import annotations

import collections
import re
import unicodedata
from dataclasses import dataclass, field

_RUBY = re.compile(r"\[[^\]]*\]")
_SPACE = re.compile(r"\s")
_VOICE_BASE = re.compile(r"[a-z]+\d+_\d+")


def strip_ruby(text: str) -> str:
    """去掉舊劇本的振假名標記 ``[よみ]漢字`` → ``漢字``。

    Remove legacy furigana markup.
    """
    return _RUBY.sub("", text)


def norm_text(text: str) -> str:
    """比對用的正規化：NFC、去振假名、去所有空白與換行。

    Normalisation for matching: NFC, no furigana, no whitespace.
    """
    return _SPACE.sub("", strip_ruby(unicodedata.normalize("NFC", text or "")))


def voice_base(voice: str | None) -> str:
    """語音檔名去掉變體後綴（``nen001_027_ref`` → ``nen001_027``）。

    Strip voice variant suffixes such as ``_ref`` / ``_bgv``.
    """
    m = _VOICE_BASE.match(voice or "")
    return m.group(0) if m else (voice or "")


def file_key(name: str) -> str:
    """檔名正規化成 NFC、去掉 ``.json`` 等副檔名後的 ``.ks`` 名稱。

    Normalise a legacy filename (NFD, ``….ks.json``) to the NFC ``….ks`` key.
    """
    n = unicodedata.normalize("NFC", name)
    return n[:-5] if n.endswith(".json") else n


@dataclass(frozen=True)
class Anchor:
    """一句錨點台詞。One anchor line."""

    idx: int
    scene: str
    text: str
    voice: str
    role: str

    @property
    def file(self) -> str:
        return self.scene.split("*", 1)[0]


@dataclass(frozen=True)
class OldLine:
    """一列舊劇本。One legacy script row."""

    id: int
    file: str
    chapter: str
    role: str
    text: str
    voice: str


@dataclass
class AlignResult:
    """對齊結果。Alignment result."""

    match: dict[int, int] = field(default_factory=dict)  # anchor idx → old id
    stage: dict[int, str] = field(default_factory=dict)  # anchor idx → 階段
    low_confidence: list[int] = field(default_factory=list)  # anchor idx

    def unmatched_old(self, old_ids: set[int]) -> list[int]:
        return sorted(old_ids - set(self.match.values()))


def _voice_ok(a: Anchor, o: OldLine) -> bool:
    av, ov = voice_base(a.voice), voice_base(o.voice)
    return not (av and ov and av != ov)


def _score(a: Anchor, o: OldLine) -> int:
    s = 0
    if a.voice and voice_base(a.voice) == voice_base(o.voice):
        s += 2
    if a.role and a.role == o.role:
        s += 1
    return s


def _dp(anchors: list[Anchor], olds: list[OldLine]) -> list[tuple[int, int]]:
    """加權的最長共同子序列；回傳 (anchor idx, old id)。Weighted LCS."""
    n, m = len(anchors), len(olds)
    if not n or not m:
        return []
    ta = [norm_text(a.text) for a in anchors]
    tb = [norm_text(o.text) for o in olds]
    prev = [0] * (m + 1)
    back: list[bytearray] = []
    for x in range(n):
        cur = [0] * (m + 1)
        bk = bytearray(m + 1)
        a = anchors[x]
        for y in range(m):
            best, b = prev[y + 1], 1
            if cur[y] > best:
                best, b = cur[y], 2
            if ta[x] == tb[y] and _voice_ok(a, olds[y]):
                w = prev[y] + 100 + _score(a, olds[y])
                if w > best:
                    best, b = w, 3
            cur[y + 1] = best
            bk[y + 1] = b
        back.append(bk)
        prev = cur
    out: list[tuple[int, int]] = []
    x, y = n, m
    while x > 0 and y > 0:
        b = back[x - 1][y]
        if b == 3:
            out.append((anchors[x - 1].idx, olds[y - 1].id))
            x, y = x - 1, y - 1
        elif b == 1:
            x -= 1
        else:
            y -= 1
    return out


def align(anchors: list[Anchor], olds: list[OldLine]) -> AlignResult:
    """把錨點對到舊劇本 id。Align anchors to legacy ids."""
    res = AlignResult()
    used: set[int] = set()

    def take(i: int, o: int, stage: str) -> None:
        res.match[i] = o
        res.stage[i] = stage
        used.add(o)

    by_file_a: dict[str, list[Anchor]] = collections.defaultdict(list)
    for a in anchors:
        by_file_a[a.file].append(a)
    by_file_o: dict[str, list[OldLine]] = collections.defaultdict(list)
    for o in olds:
        by_file_o[o.file].append(o)
    old_by_id = {o.id: o for o in olds}

    for f, al in by_file_a.items():
        ol = by_file_o.get(f, [])
        # 1. 台詞＋語音唯一 / unique (text, voice)
        ka = collections.Counter((norm_text(a.text), voice_base(a.voice)) for a in al if a.voice)
        ko = collections.Counter((norm_text(o.text), voice_base(o.voice)) for o in ol if o.voice)
        byk = {(norm_text(o.text), voice_base(o.voice)): o for o in ol if o.voice}
        for a in al:
            k = (norm_text(a.text), voice_base(a.voice))
            if a.voice and ka[k] == 1 and ko[k] == 1:
                take(a.idx, byk[k].id, "voice")
        # 2. 台詞唯一 / unique text
        ca = collections.Counter(norm_text(a.text) for a in al)
        co = collections.Counter(norm_text(o.text) for o in ol)
        byt = {norm_text(o.text): o for o in ol}
        for a in al:
            t = norm_text(a.text)
            if a.idx in res.match or ca[t] != 1 or co[t] != 1:
                continue
            o = byt[t]
            if o.id not in used and _voice_ok(a, o):
                take(a.idx, o.id, "unique")

    # 3. 標籤內 DP / per-label DP inside the label's id range
    by_label: dict[str, list[Anchor]] = collections.OrderedDict()
    for a in anchors:
        by_label.setdefault(a.scene, []).append(a)
    ranges: dict[str, list[tuple[int, int, str]]] = collections.defaultdict(list)
    for s, al in by_label.items():
        ids = [res.match[a.idx] for a in al if a.idx in res.match]
        if ids:
            ranges[al[0].file].append((min(ids), max(ids), s))
    for f, rs in ranges.items():
        rs.sort()
        ol = by_file_o.get(f, [])
        for k, (lo_id, hi_id, s) in enumerate(rs):
            lo = rs[k - 1][1] if k > 0 else -1
            hi = rs[k + 1][0] if k + 1 < len(rs) else 1 << 62
            pinned = {res.match[a.idx] for a in by_label[s] if a.idx in res.match}
            seg_o = [o for o in ol if lo < o.id < hi and o.id not in used]
            # DP 在整個標籤上做，讓已錨定的配對一起參與排序；只採用新配對
            # Run DP over the whole label so pinned pairs constrain order.
            full_o = sorted(seg_o + [old_by_id[p] for p in pinned], key=lambda o: o.id)
            for i, o in _dp(by_label[s], full_o):
                if i not in res.match and o not in used:
                    take(i, o, "dp")

    # 4. 剩下的舊列 → 同檔案同文字、位置最近的錨點 / nearest leftover fallback
    pos = {a.idx: n for n, a in enumerate(anchors)}
    inv = {o: i for i, o in res.match.items()}
    for o in sorted(set(old_by_id) - used):
        ol = old_by_id[o]
        t = norm_text(ol.text)
        cands = [a for a in by_file_a.get(ol.file, [])
                 if a.idx not in res.match and norm_text(a.text) == t]
        if not cands:
            continue
        near = [inv[x] for x in (o - 1, o + 1, o - 2, o + 2) if x in inv]
        target = pos[near[0]] if near else 0
        best = min(cands, key=lambda a: (-_score(a, ol), abs(pos[a.idx] - target)))
        take(best.idx, o, "fallback")
        inv[o] = best.idx
        res.low_confidence.append(best.idx)
    return res
