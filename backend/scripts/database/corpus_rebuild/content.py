"""由舊劇本與錨點檔案組出內容表的資料（不碰資料庫寫入）。

Build the content-table rows (game_* tables) in memory from the legacy
script rows and the anchor / calibration files. No DB writes here; see
``rebuild.py`` for loading.

計畫書：docs/wip/corpus_db_rebuild_FEAT_2026-10-31.md D2–D5。
"""

from __future__ import annotations

import collections
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from scripts.database.corpus_rebuild.align import (
    Anchor,
    OldLine,
    align,
    file_key,
    norm_text,
    strip_ruby,
)

GAME_ID = "SabbatOfTheWitch"
GAME_PROFILE = {
    "game_id": GAME_ID,
    "title_ja_jp": "サノバウィッチ",
    "title_zh_hant": "魔女的夜宴",
    "engine": "kirikiri_packinone",
    "languages": ["ja-JP", "zh-Hant"],
    "voice_dir": "voice/yuzusoft/SabbatOfTheWitch",
    "avatar_dir": "avatar/yuzusoft/SabbatOfTheWitch",
    "legacy_source": "サノバウィッチ",
}
# F1：舊表刪掉、要用原 id 還原的 6 句 / ids deleted from the legacy table
RESTORE_IDS = (1722, 8330, 18023, 36635, 43959, 45037)
TYPO_ID = 9516  # F3
BGV_ID = 1043  # F4
BGV_PLAYS_AT = {"scene": "002.共通－欠片吸収.ks*chapter14", "point": 670}
NEW_ID_START = 53197
_ATTR_KEYS = ("segment", "replay_unlock", "cg_unlock", "cg_unlock_vars", "vars", "flags_new")
_VOICE_PREFIX = re.compile(r"([a-z]+)\d")
_EFFECT = re.compile(r"f\.(\w+)\s*(\+\+|--|\+=|-=|=)\s*(-?\d+)?")


@dataclass
class LegacyRow:
    """舊 scripts 一列中遷移會用到的欄位。Fields used from a legacy row."""

    id: int
    filename: str
    chapter: str
    role: str
    dialogue: str
    audio_file: str


@dataclass
class Content:
    """組好的內容表資料。Assembled content-table rows."""

    files: list[dict] = field(default_factory=list)
    scenes: list[dict] = field(default_factory=list)  # scene_id 由 1 起依序
    speakers: list[dict] = field(default_factory=list)
    lines: list[dict] = field(default_factory=list)
    tracks: list[dict] = field(default_factory=list)
    line_bgm: list[tuple[int, int, int]] = field(default_factory=list)
    links: list[dict] = field(default_factory=list)
    revisions: list[dict] = field(default_factory=list)
    report: dict = field(default_factory=dict)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def parse_effects(exp: str | None) -> list[dict]:
    """把 ``f.sel_flag = 0 , f.wak_flag ++`` 解析成變數操作清單。

    Parse a KAG expression into variable operations.
    """
    out = []
    for m in _EFFECT.finditer(exp or ""):
        var, op, val = m.group(1), m.group(2), m.group(3)
        if op == "++":
            out.append({"var": var, "op": "add", "value": 1})
        elif op == "--":
            out.append({"var": var, "op": "add", "value": -1})
        elif op in ("+=", "-="):
            out.append({"var": var, "op": "add", "value": int(val or 0) * (1 if op == "+=" else -1)})
        else:
            out.append({"var": var, "op": "set", "value": int(val or 0)})
    return out


def build(anchor_rows: list[dict], legacy: list[LegacyRow], calib_dir: Path,
          titles: dict) -> Content:
    """組出全部內容表。Build all content tables."""
    c = Content()
    by_old = {r.id: r for r in legacy}

    # ---------- 對齊 / alignment
    anchors = [Anchor(i, r["scene"], r["text_jp"], r.get("voice") or "", r.get("role") or "")
               for i, r in enumerate(anchor_rows)]
    olds = [OldLine(r.id, file_key(r.filename), r.chapter, r.role or "", r.dialogue, r.audio_file or "")
            for r in legacy]
    res = align(anchors, olds)
    match = dict(res.match)

    # 錨點這邊沒對到的：選項畫面、6 句還原、9516 / unmatched anchors
    by_label: dict[str, list[int]] = collections.OrderedDict()
    for i, r in enumerate(anchor_rows):
        by_label.setdefault(r["scene"], []).append(i)
    origin: dict[int, str] = {i: "legacy" for i in match}
    restored, typo = [], None
    free = set(RESTORE_IDS) | {TYPO_ID}
    for s, idxs in by_label.items():
        for k, i in enumerate(idxs):
            if i in match or anchor_rows[i]["text_jp"].startswith("選択肢"):
                continue
            prev = next((match[idxs[j]] for j in range(k - 1, -1, -1) if idxs[j] in match), 0)
            nxt = next((match[idxs[j]] for j in range(k + 1, len(idxs)) if idxs[j] in match), 1 << 62)
            cand = [x for x in free if prev < x < nxt]
            if len(cand) == 1:
                x = cand[0]
                free.discard(x)
                match[i] = x
                if x == TYPO_ID:
                    origin[i], typo = "legacy", i
                else:
                    origin[i] = "restored"
                    restored.append(i)
    next_id = NEW_ID_START
    for i, r in enumerate(anchor_rows):
        if i not in match:
            match[i] = next_id
            origin[i] = "game_only" if r["text_jp"].startswith("選択肢") else "restored"
            next_id += 1

    # ---------- 場景檔、場景 / files and scenes（依錨點出現順序）
    file_ids: dict[str, int] = {}
    scene_ids: dict[str, int] = {}
    scene_count: collections.Counter = collections.Counter()

    def ensure_scene(scene: str) -> int | None:
        if not scene or "*" not in scene:
            return None
        f, label = scene.split("*", 1)
        if f not in file_ids:
            file_ids[f] = len(file_ids) + 1
            no, _, rest = f.partition(".")
            c.files.append({
                "file_id": file_ids[f], "game_id": GAME_ID, "file_name": f, "file_no": no,
                "title_ja_jp": rest[:-3] if rest.endswith(".ks") else rest,
                "title_zh_hant": None, "file_order": file_ids[f],
            })
        if scene not in scene_ids:
            scene_ids[scene] = len(scene_ids) + 1
            scene_count[f] += 1
            c.scenes.append({"scene_id": scene_ids[scene], "file_id": file_ids[f],
                             "label": label, "scene_order": scene_count[f]})
        return scene_ids[scene]

    for r in anchor_rows:
        ensure_scene(r["scene"])

    # ---------- 台詞順序（1043 插在 1042 之後）/ story order
    order: list[tuple[str, int]] = [("a", i) for i in range(len(anchor_rows))]
    inv = {v: k for k, v in match.items()}
    if BGV_ID in by_old and BGV_ID not in inv and (BGV_ID - 1) in inv:
        pos = order.index(("a", inv[BGV_ID - 1]))
        order.insert(pos + 1, ("bgv", BGV_ID))

    # ---------- 說話者 / speakers
    def speaker_of(kind: str, i: int) -> str:
        if kind == "bgv":
            return by_old[BGV_ID].role or ""
        oid = match[i]
        if origin[i] == "legacy" and oid in by_old:
            return by_old[oid].role or anchor_rows[i].get("role") or ""
        return anchor_rows[i].get("role") or ""

    def voice_of(kind: str, i: int) -> str:
        if kind == "bgv":
            return by_old[BGV_ID].audio_file
        oid = match[i]
        # 舊台詞只用舊表的語音：錨點在無聲台詞上會沿用前一句的語音
        # Legacy rows keep the legacy voice: anchors carry the previous voice over silent lines.
        if origin[i] == "legacy" and oid in by_old:
            return by_old[oid].audio_file or ""
        return anchor_rows[i].get("voice") or ""

    names = collections.Counter(speaker_of(k, i) for k, i in order)
    names.pop("", None)
    prefixes: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for k, i in order:
        m = _VOICE_PREFIX.match(voice_of(k, i) or "")
        if m:
            prefixes[speaker_of(k, i)][m.group(1)] += 1
    # F11：在繁中模式讀檔看名字欄得到的繁中名（有這個檔案才用）
    # F11: zh-Hant names read from the in-game name box, if available.
    tw_names: dict[str, str] = {}
    tw_file = calib_dir / "speaker_names_tw.json"
    if tw_file.exists():
        for x in json.loads(tw_file.read_text(encoding="utf-8")):
            # 低信心的是場景裡的暫時稱呼（例「電影」「男生」），不當成名字
            # Low-confidence entries are situational labels, not names.
            if x.get("name_zh_hant") and x.get("confidence") == "high":
                tw_names[x["name_ja_jp"]] = x["name_zh_hant"]
    speaker_ids: dict[str, int] = {}
    for n, (name, _cnt) in enumerate(names.most_common(), start=1):
        speaker_ids[name] = n
        pre = prefixes[name].most_common(1)
        c.speakers.append({"speaker_id": n, "game_id": GAME_ID, "name_ja_jp": name,
                           "name_zh_hant": tw_names.get(name),
                           "name_zh_hant_src": "game" if name in tw_names else None,
                           "voice_prefix": pre[0][0] if pre else None,
                           "avatar_file": None, "sort_order": n})

    # ---------- 校準紀錄 / anchor method
    verified: dict[tuple[str, int], str] = {}
    for r in _read_jsonl(calib_dir / "calib_results_tw.jsonl"):
        if r.get("scene") is not None and r.get("point") is not None:
            verified[(r["scene"], r["point"])] = r["t"]
    filled: set[tuple[str, int]] = set()
    for r in _read_jsonl(calib_dir / "calib_fill.jsonl"):
        for loc in r.get("locs") or []:
            filled.add((loc[0], loc[1]))

    # ---------- BGM 曲目 / tracks
    track_ids: dict[str, int] = {}
    for k, v in sorted(((k, v) for k, v in titles.items() if not k.startswith("_")), key=lambda kv: int(kv[0])):
        track_ids[v["bgm"]] = len(track_ids) + 1
        c.tracks.append({"track_id": track_ids[v["bgm"]], "game_id": GAME_ID, "code": v["bgm"],
                         "archive_index": int(k), "gallery_no": v.get("gallery"),
                         "title_ja_jp": v.get("title") or None,
                         "title_zh_hant": v.get("title_zh_hant") or None})

    # ---------- 台詞 / lines
    seq_in_scene: collections.Counter = collections.Counter()
    last_chapter: str | None = None
    for n, (kind, i) in enumerate(order):
        if kind == "bgv":
            old = by_old[BGV_ID]
            scene = BGV_PLAYS_AT["scene"]
            sid = scene_ids[scene]
            line = {
                "id": BGV_ID, "kind": "bgv", "point": None, "scene_id": sid,
                "chapter_label": old.chapter, "text_ja_jp": strip_ruby(old.dialogue),
                "text_zh_hant": None, "ruby_ja_jp": old.dialogue if "[" in old.dialogue else None,
                "voice_file": old.audio_file or None, "is_h": 0, "h_label": None, "thumb": None,
                "bgm_detect": None, "bgm_may_change": 0,
                "attrs": {"plays_at": BGV_PLAYS_AT}, "origin": "legacy", "in_corpus": 1,
                "anchor_method": None, "anchor_verified_at": None,
            }
            c.revisions.append({"line_id": BGV_ID, "field": "kind", "old_value": "line", "new_value": "bgv",
                                "reason": "背景語音：不顯示在對話框，只在 point 670 的旁白背後播放（F4）",
                                "evidence": "voicewatch：讀檔到 667 時開啟 voice.xp3 第 392 個檔案"})
        else:
            r = anchor_rows[i]
            lid = match[i]
            sid = scene_ids[r["scene"]]
            old = by_old.get(lid) if origin[i] == "legacy" else None
            is_choice = r["text_jp"].startswith("選択肢")
            if old is not None and norm_text(old.dialogue) == norm_text(r["text_jp"]):
                text = strip_ruby(old.dialogue)
                ruby = old.dialogue if "[" in old.dialogue else None
            else:
                text, ruby = r["text_jp"], None
                if old is not None:
                    c.revisions.append({"line_id": lid, "field": "text_ja_jp", "old_value": old.dialogue,
                                        "new_value": text, "reason": "舊資料庫錯字，依遊戲顯示修正（F3）",
                                        "evidence": f"{r['scene']}@{r['point']}"})
            attrs = {k: r[k] for k in _ATTR_KEYS if r.get(k)}
            key = (r["scene"], r["point"])
            line = {
                "id": lid, "kind": "choice" if is_choice else "line", "point": r["point"],
                "scene_id": sid, "chapter_label": old.chapter if old is not None else None,
                "text_ja_jp": text, "text_zh_hant": r.get("text_tw") or None, "ruby_ja_jp": ruby,
                "voice_file": voice_of(kind, i) or None, "is_h": 1 if r.get("is_h") else 0,
                "h_label": r.get("h_label") or None, "thumb": r.get("thumb") or None,
                "bgm_detect": r.get("bgm_source") or None, "bgm_may_change": 1 if r.get("bgm_may_change") else 0,
                "attrs": attrs or None, "origin": origin[i], "in_corpus": 0 if is_choice else 1,
                "anchor_method": "fill" if key in filled else ("probe" if key in verified else None),
                "anchor_verified_at": verified.get(key),
            }
            if origin[i] in ("restored", "game_only"):
                c.revisions.append({"line_id": lid, "field": "row", "old_value": None, "new_value": text,
                                    "reason": ("舊資料庫刪掉的台詞，依遊戲還原（F1）" if origin[i] == "restored"
                                               else "選項畫面，舊資料庫沒有（F2）"),
                                    "evidence": f"{r['scene']}@{r['point']}"})
            for o, code in enumerate(r.get("bgm") or []):
                if code in track_ids:
                    c.line_bgm.append((lid, o, track_ids[code]))
        # 沒有舊章節的台詞沿用前一句 / inherit the previous chapter
        if line["chapter_label"] is None:
            line["chapter_label"] = last_chapter
        last_chapter = line["chapter_label"]
        line["speaker_id"] = speaker_ids.get(speaker_of(kind, i)) if speaker_of(kind, i) else None
        line["seq"] = seq_in_scene[line["scene_id"]]
        seq_in_scene[line["scene_id"]] += 1
        line["story_order"] = n
        line["game_id"] = GAME_ID
        line["status"] = "active"
        c.lines.append(line)

    # ---------- 場景連接 / scene links
    _build_links(c, anchor_rows, calib_dir, ensure_scene, scene_ids)

    low = [{"line_id": match[i], "scene": anchor_rows[i]["scene"], "point": anchor_rows[i]["point"],
            "text": anchor_rows[i]["text_jp"]} for i in res.low_confidence]
    c.report = {
        "anchors": len(anchor_rows), "legacy": len(legacy),
        "stages": collections.Counter(res.stage.values()),
        "origin": collections.Counter(origin.values()),
        "restored_ids": sorted(match[i] for i in restored),
        "typo_line": match.get(typo) if typo is not None else None,
        "unmatched_legacy": res.unmatched_old({r.id for r in legacy}),
        "low_confidence": low,
        "speaker_conflicts": sum(1 for i, o in res.match.items()
                                 if anchor_rows[i].get("role") and o in by_old and by_old[o].role
                                 and anchor_rows[i]["role"] != by_old[o].role),
        "links": collections.Counter(x["kind"] for x in c.links),
    }
    return c


def _build_links(c: Content, anchor_rows: list[dict], calib_dir: Path, ensure_scene, scene_ids) -> None:
    """由校準紀錄組出 game_scene_links。Build scene links from calibration logs."""
    seen: set[tuple] = set()
    choice_tw: dict[str, list[str]] = {}
    for r in anchor_rows:
        if r["text_jp"].startswith("選択肢") and r.get("text_tw"):
            body = r["text_tw"].split("：", 1)[-1]
            choice_tw[r["scene"]] = [x.strip() for x in body.split(" / ")]

    def add(**kw) -> None:
        key = (kw["from_scene_id"], kw["kind"], kw.get("option_index"), kw.get("to_scene_id"))
        if kw["from_scene_id"] is None or key in seen:
            return
        seen.add(key)
        base = {"game_id": GAME_ID, "to_point": None, "option_index": None, "option_text_ja_jp": None,
                "option_text_zh_hant": None, "condition_text_ja_jp": None, "condition_text_zh_hant": None,
                "condition_vars": None, "effects": None, "raw_expression": None, "via": None, "note": None}
        base.update(kw)
        c.links.append(base)

    # 自然接續 / natural continuation（calib_state）
    state = json.loads((calib_dir / "calib_state.json").read_text(encoding="utf-8"))
    for v in state.get("scenes", {}).values():
        frm = ensure_scene(v.get("scene"))
        if v.get("next_scene"):
            add(from_scene_id=frm, to_scene_id=ensure_scene(v["next_scene"]), to_point=v.get("next_point"),
                kind="next", via=v.get("via"), evidence="observed")
        elif v.get("result") == "end" and frm is not None:
            add(from_scene_id=frm, to_scene_id=None, kind="next", evidence="observed",
                note="場景結束後沒有接續")

    # 選項 / choices（calib_choices ＋ calib_choice_vars 的運算式）
    exps: dict[tuple[str, int], str] = {}
    for r in _read_jsonl(calib_dir / "calib_choice_vars.jsonl"):
        for o in r.get("options") or []:
            exps[(r["choice_scene"], o.get("selidx"))] = o.get("exp")
    for r in _read_jsonl(calib_dir / "calib_choices.jsonl"):
        cs, idx = r["choice_scene"], r["index"]
        tw = choice_tw.get(cs) or []
        exp = exps.get((cs, idx))
        add(from_scene_id=ensure_scene(cs), to_scene_id=ensure_scene(r.get("to_scene")),
            to_point=r.get("to_point"), kind="choice", option_index=idx,
            option_text_ja_jp=r.get("option"), option_text_zh_hant=tw[idx] if idx < len(tw) else None,
            effects=parse_effects(exp) or None, raw_expression=exp, evidence="observed")

    # 帶變數重跑看到的場景轉移 / transitions observed while replaying with vars
    for r in _read_jsonl(calib_dir / "calib_results_vars.jsonl"):
        a, b = r.get("scene"), r.get("saved_scene")
        if not a or not b or a == b or "dummyselect" in a:
            continue
        kind = "jump" if a.split("*", 1)[1].startswith("dummy") else "next"
        add(from_scene_id=ensure_scene(a), to_scene_id=ensure_scene(b), to_point=r.get("saved_point"),
            kind=kind, condition_vars=r.get("vars") if kind == "jump" else None,
            evidence="probed" if kind == "jump" else "observed")
