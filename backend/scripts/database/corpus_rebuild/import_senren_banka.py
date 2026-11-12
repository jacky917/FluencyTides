"""把《千恋＊万花》加進語料庫 fluencytides_corpus（docs/wip/senren_banka_corpus_import_FEAT_2026-11-08.md）。

Add Senren*Banka to the corpus database without touching other games.

和 ``rebuild.py`` 不同：不刪表、不讀舊庫、不對齊舊 id；只寫 ``game_id='SenrenBanka'`` 的內容表列。
來源全部在素材資料夾（``Y:\\material\\yuzusoft\\SenrenBanka``）：

- ``calib/SB_point_table.json``：錨點表（每列一句，遊戲實測）
- ``calib/SB_scene_links.json``：場景連接（校準 flowchart.py 輸出）
- ``calib/SB_bgm_titles.json``：BGM 代號與曲名
- ``text/speakers.json``：說話者（繁中名、語音前綴、頭像檔）
- ``text/script_lines.jsonl``：解包劇本（只取含注音的日文）

可以重跑：先刪掉這款遊戲的內容表列再寫入。這款遊戲的台詞已被卡片紀錄、詞彙索引、讀音判定或
``rel_links`` 引用時拒絕執行（紀錄表不能被清掉）；有流程圖建置時要加 ``--drop-story-builds``。

Re-runnable: deletes this game's content rows first; refuses when card logs,
NLP rows or relations reference its lines.

用法 / usage（在 backend/ 底下）::

    python -m scripts.database.corpus_rebuild.import_senren_banka --dry-run
    python -m scripts.database.corpus_rebuild.import_senren_banka
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

_backend_dir = Path(__file__).resolve().parents[3]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

GAME_ID = "SenrenBanka"
GAME_PROFILE = {
    "game_id": GAME_ID,
    "title_ja_jp": "千恋＊万花",
    "title_zh_hant": "千戀＊萬花",
    "engine": "kirikiri_savestruct",
    "languages": ["ja-JP", "zh-Hant"],
    "voice_dir": "yuzusoft/SenrenBanka/voice",
    "avatar_dir": "yuzusoft/SenrenBanka/avatar",
    "legacy_source": None,
}
DEFAULT_MATERIAL = Path("//192.168.50.250/sn640/material/yuzusoft/SenrenBanka")
# hist_matched（校準 README）→ anchor_method
ANCHOR_METHOD = {1: "backlog", 2: "backlog_other", 4: "fill"}
_ATTR_KEYS = ("segment", "cg_unlock", "replay_unlock", "bgm_unlock", "flags_new", "flags_after_line",
              "fc_node", "fc_flags")
_VOICE_PREFIX = re.compile(r"([a-z]+)\d")
_BRANCH = re.compile(r'SetBranchFlags\(\s*"([^"]+)"\s*,\s*(-?\d+)\s*\)')
_RUBY = re.compile(r"\[[^\]]*\]")
_STYLE = re.compile(r"%\d*;")


@dataclass
class Content:
    """組好的內容表資料（id 從 0 起的區域編號，寫入時再加上位移）。Assembled rows."""

    files: list[dict] = field(default_factory=list)
    scenes: list[dict] = field(default_factory=list)
    speakers: list[dict] = field(default_factory=list)
    lines: list[dict] = field(default_factory=list)
    tracks: list[dict] = field(default_factory=list)
    line_bgm: list[tuple[int, int, int]] = field(default_factory=list)
    links: list[dict] = field(default_factory=list)
    report: dict = field(default_factory=dict)


def parse_branch_effects(exp: str | None) -> list[dict]:
    """``SetBranchFlags("s001*com_part_1_sel",1)`` → ``[{"var": "s001*com_part_1_sel", "op": "set", "value": 1}]``。

    Parse Senren*Banka's flowchart branch-flag expression into variable operations.
    """
    return [{"var": m.group(1), "op": "set", "value": int(m.group(2))} for m in _BRANCH.finditer(exp or "")]


def _norm(text: str | None) -> str:
    text = _STYLE.sub("", _RUBY.sub("", (text or "").replace("\\n", "")))
    return unicodedata.normalize("NFKC", text).replace(" ", "").replace("\u3000", "").strip()


def file_no(file_name: str) -> str:
    """``001・アーサー王ver1.07.ks`` → ``001``；``508a・…`` → ``508a``。"""
    m = re.match(r"(\d+[ab]?)", file_name)
    return m.group(1) if m else file_name


def file_title(file_name: str) -> str:
    """檔名中段（例 ``アーサー王``），比照魔女的夜宴用檔名。The title part of the file name."""
    t = re.sub(r"(ver[\d.]+)?\.ks$", "", file_name)
    return re.sub(r"^\d+[ab]?・?", "", t)


def _voices(voice: str | None) -> list[str]:
    return [re.sub(r"\.(ogg|opus|flac|wav)$", "", v) for v in (voice or "").split("|") if v]


def _read_json(path: Path, key: str | None = None):
    """讀一個 JSON 檔（可取頂層的某個鍵）；檔案不在、格式錯或缺鍵時附上檔名結束。Read one JSON file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise SystemExit(f"❌ 素材檔不存在：{path}") from e
    except json.JSONDecodeError as e:
        raise SystemExit(f"❌ {path} 不是合法的 JSON：{e}") from e
    if key is None:
        return data
    if not isinstance(data, dict) or key not in data:
        raise SystemExit(f"❌ {path} 缺少頂層的 {key!r}")
    return data[key]


def _read_jsonl(path: Path) -> list[dict]:
    """逐行讀 JSONL；壞掉的行附上行號結束。Read JSONL, reporting the bad line number."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise SystemExit(f"❌ 素材檔不存在：{path}") from e
    out = []
    for n, x in enumerate(text.splitlines(), start=1):
        if x.strip():
            try:
                out.append(json.loads(x))
            except json.JSONDecodeError as e:
                raise SystemExit(f"❌ {path}:{n} 不是合法的 JSON：{e}") from e
    return out


def load_material(material: Path) -> dict:
    """讀素材資料夾的五個來源。Read the five source files."""
    return {
        "rows": _read_json(material / "calib/SB_point_table.json", "rows"),
        "links": _read_json(material / "calib/SB_scene_links.json", "links"),
        "titles": _read_json(material / "calib/SB_bgm_titles.json"),
        "speakers": _read_json(material / "text/speakers.json"),
        "script": _read_jsonl(material / "text/script_lines.jsonl"),
    }


def build(src: dict) -> Content:
    """組出全部內容表（區域 id 從 1 起）。Build all content tables with local ids starting at 1."""
    c = Content()
    rows = sorted(src["rows"], key=lambda r: r["story_order"])

    # ---------- 檔案、場景（依劇情順序第一次出現）/ files and scenes
    file_ids: dict[str, int] = {}
    scene_ids: dict[str, int] = {}
    per_file: collections.Counter = collections.Counter()

    def ensure_scene(scene: str | None) -> int | None:
        if not scene or "*" not in scene:
            return None
        f, label = scene.split("*", 1)
        if f not in file_ids:
            file_ids[f] = len(file_ids) + 1
            c.files.append({"file_id": file_ids[f], "game_id": GAME_ID, "file_name": f, "file_no": file_no(f),
                            "title_ja_jp": file_title(f), "title_zh_hant": None, "file_order": file_ids[f]})
        if scene not in scene_ids:
            scene_ids[scene] = len(scene_ids) + 1
            per_file[f] += 1
            c.scenes.append({"scene_id": scene_ids[scene], "file_id": file_ids[f], "label": label,
                             "scene_order": per_file[f]})
        return scene_ids[scene]

    bad_scene = [(r.get("story_order"), r.get("scene")) for r in rows if not r.get("scene") or "*" not in r["scene"]]
    if bad_scene:
        raise SystemExit(f"❌ 錨點表有 {len(bad_scene)} 列的 scene 不是 <檔名>*<標籤>：{bad_scene[:5]}")
    for r in rows:
        ensure_scene(r["scene"])

    # ---------- 說話者 / speakers（依台詞數）
    speaker_ids: dict[str, int] = {}
    for n, s in enumerate(sorted(src["speakers"], key=lambda x: -x["line_count"]), start=1):
        speaker_ids[s["name_ja_jp"]] = n
        c.speakers.append({"speaker_id": n, "game_id": GAME_ID, "name_ja_jp": s["name_ja_jp"],
                           "name_zh_hant": s.get("name_zh_hant"), "name_zh_hant_src": s.get("name_zh_hant_src"),
                           "voice_prefix": s.get("voice_prefix"), "avatar_file": s.get("avatar_file"),
                           "sort_order": n})

    # ---------- BGM 曲目 / tracks（依 bgm.xp3 序號）
    track_ids: dict[str, int] = {}
    for t in sorted(src["titles"], key=lambda t: t["file_index"]):
        track_ids[t["code"]] = len(track_ids) + 1
        c.tracks.append({"track_id": track_ids[t["code"]], "game_id": GAME_ID, "code": t["code"],
                         "archive_index": t["file_index"], "gallery_no": t.get("music_room_no"),
                         "title_ja_jp": t.get("title_jp") or None, "title_zh_hant": t.get("title_tw") or None})

    # ---------- 注音：只有含注音的句子 / ruby, only for lines that have it
    ruby: dict[tuple[str, str, str], str] = {}
    for x in src["script"]:
        if "[" in (x.get("ruby_ja_jp") or ""):
            ruby[(x["file"], x["label"], _norm(x["text_ja_jp"]))] = _STYLE.sub("", x["ruby_ja_jp"])

    # ---------- 台詞 / lines
    seq: collections.Counter = collections.Counter()
    missing_bgm: collections.Counter = collections.Counter()
    unknown_hist: collections.Counter = collections.Counter()
    last_chapter = None
    for n, r in enumerate(rows):
        is_choice = r["kind"] == "choice"
        sid = scene_ids[r["scene"]]
        if is_choice:
            text = "選択肢：" + " / ".join(o["jp"] for o in r["choice_options"] or [])
        else:
            text = r["text_jp"]
        voices = _voices(r.get("voice"))
        attrs = {k: r[k] for k in _ATTR_KEYS if r.get(k)}
        if len(voices) > 1:
            attrs["voices"] = voices
        if is_choice:
            attrs["choice_options"] = r["choice_options"]
        chapter = r.get("chapter") or last_chapter
        last_chapter = chapter
        line = {
            "id": n + 1, "game_id": GAME_ID, "scene_id": sid, "seq": seq[sid], "story_order": r["story_order"],
            "kind": "choice" if is_choice else "line", "point": r["point"], "chapter_label": chapter,
            "speaker_id": speaker_ids.get(r.get("speaker_jp")) if r.get("speaker_jp") else None,
            "text_ja_jp": text, "text_zh_hant": r.get("text_tw") or None,
            "ruby_ja_jp": None if is_choice else ruby.get((r["file"], r["label"], _norm(text))),
            "voice_file": voices[0] if voices else None,
            "is_h": 1 if r.get("is_h") else 0, "h_label": r.get("h_label") or None, "thumb": r.get("thumb") or None,
            "bgm_detect": None, "bgm_may_change": 0, "attrs": attrs or None, "status": "active",
            "in_corpus": 0 if is_choice else 1, "origin": "game_only",
            "anchor_method": ANCHOR_METHOD.get(r.get("hist_matched")), "anchor_verified_at": None,
        }
        if r["kind"] == "line" and line["anchor_method"] is None:
            unknown_hist[r.get("hist_matched")] += 1
        seq[sid] += 1
        c.lines.append(line)
        for o, code in enumerate(r.get("bgm_codes") or []):
            if code in track_ids:
                c.line_bgm.append((line["id"], o, track_ids[code]))
            else:
                missing_bgm[code] += 1

    # ---------- 場景連接 / scene links
    seen: set[tuple] = set()
    skipped: collections.Counter = collections.Counter()
    for x in src["links"]:
        frm, to = ensure_scene(x.get("from_scene")), ensure_scene(x.get("to_scene"))
        key = (frm, x["kind"], x.get("option_index"), to)
        # 和 rebuild.py 相同：起點不合法的不收；同一條（起點、種類、選項、終點）只收一次；
        # 自己接自己的不收（流程圖不畫自迴圈，選項回到同一場景的情況在遊戲裡沒有）
        if frm is None:
            skipped["bad_from"] += 1
            continue
        if key in seen:
            skipped["duplicate"] += 1
            continue
        if frm == to:
            skipped["self_loop"] += 1
            continue
        seen.add(key)
        c.links.append({
            "link_id": len(c.links) + 1, "game_id": GAME_ID, "from_scene_id": frm, "to_scene_id": to,
            "to_point": x.get("to_point"), "kind": x["kind"], "option_index": x.get("option_index"),
            "option_text_ja_jp": x.get("option_text_ja_jp"), "option_text_zh_hant": x.get("option_text_zh_hant"),
            "condition_text_ja_jp": None, "condition_text_zh_hant": x.get("condition_text_zh_hant"),
            "condition_vars": None, "effects": parse_branch_effects(x.get("raw_expression")) or None,
            "raw_expression": x.get("raw_expression"), "via": x.get("via"), "evidence": x["evidence"],
            "note": x.get("note"),
        })

    c.report = {
        "lines": len(c.lines), "kinds": collections.Counter(x["kind"] for x in c.lines),
        "files": len(c.files), "scenes": len(c.scenes), "speakers": len(c.speakers), "tracks": len(c.tracks),
        "line_bgm": len(c.line_bgm), "links": collections.Counter(x["kind"] for x in c.links),
        "ruby": sum(1 for x in c.lines if x["ruby_ja_jp"]),
        "anchor_method": collections.Counter(x["anchor_method"] for x in c.lines),
        "unknown_hist_matched": dict(unknown_hist),
        "missing_bgm": dict(missing_bgm),
        "links_skipped": dict(skipped),
        "no_speaker_match": sorted({r["speaker_jp"] for r in rows if r.get("speaker_jp") and r["speaker_jp"] not in speaker_ids}),
    }
    return c


def check(c: Content, src: dict) -> list[str]:
    """寫入前檢查；回傳問題清單。Pre-write checks."""
    problems = []
    orders = [x["story_order"] for x in c.lines]
    if orders != list(range(len(orders))):
        problems.append("story_order 不是 0 起連號")
    pts = collections.Counter((x["scene_id"], x["point"]) for x in c.lines if x["point"] is not None)
    if any(v > 1 for v in pts.values()):
        problems.append("同場景的存檔點重複")
    if c.report["no_speaker_match"]:
        problems.append(f"說話者不在 speakers.json：{c.report['no_speaker_match']}")
    if c.report["missing_bgm"]:
        problems.append(f"BGM 代號不在 SB_bgm_titles.json：{c.report['missing_bgm']}")
    if c.report["links_skipped"].get("bad_from"):
        problems.append(f"場景連接的起點不是 <檔名>*<標籤>：{c.report['links_skipped']['bad_from']} 條")
    if c.report["unknown_hist_matched"]:
        problems.append(f"台詞的 hist_matched 不認得（anchor_method 會是空的）：{c.report['unknown_hist_matched']}")
    want_ruby = sum(1 for x in src["script"] if "[" in (x.get("ruby_ja_jp") or ""))
    if c.report["ruby"] != want_ruby:
        problems.append(f"注音句數 {c.report['ruby']} ≠ 劇本 {want_ruby}")
    for col, n in (("text_ja_jp", 1024), ("text_zh_hant", 1024), ("voice_file", 64), ("chapter_label", 64)):
        if any(x[col] and len(x[col]) > n for x in c.lines):
            problems.append(f"{col} 超過 {n} 字")
    return problems


# ---------------------------------------------------------------------------- 寫入 / write

def connect(database: str):
    import pymysql

    import scripts.common.env  # noqa: F401  載入 .env / load .env
    from app.core.config import settings

    conn = pymysql.connect(host=settings.MYSQL_HOST, port=int(settings.MYSQL_PORT), user=settings.MYSQL_USER,
                           password=settings.MYSQL_PASSWORD, database=database, charset="utf8mb4", autocommit=False)
    with conn.cursor() as cur:
        # 超長或不合型別的值一律報錯並 rollback，不讓伺服器默默截斷
        cur.execute("SET SESSION sql_mode = CONCAT(@@sql_mode, ',STRICT_TRANS_TABLES')")
    return conn


def _json(v):
    return None if v is None else json.dumps(v, ensure_ascii=False)


def _insert(cur, table: str, rows: list[dict], json_cols: tuple[str, ...] = ()) -> None:
    if not rows:
        return
    cols = list(rows[0])
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})"
    data = [tuple(_json(r[k]) if k in json_cols else r[k] for k in cols) for r in rows]
    for i in range(0, len(data), 2000):
        cur.executemany(sql, data[i:i + 2000])


def references(cur) -> dict[str, int]:
    """這款遊戲的台詞被紀錄表引用的筆數。Rows in record tables that reference this game's content."""
    ids = f"SELECT id FROM game_lines WHERE game_id = '{GAME_ID}'"
    out = {}
    for name, sql in (
        ("card_gen_log", f"SELECT COUNT(*) FROM card_gen_log WHERE game_id = '{GAME_ID}'"),
        ("nlp_line_terms", f"SELECT COUNT(*) FROM nlp_line_terms WHERE game_id = '{GAME_ID}'"),
        ("nlp_line_readings", f"SELECT COUNT(*) FROM nlp_line_readings WHERE line_id IN ({ids})"),
        ("rel_links", "SELECT COUNT(*) FROM rel_links r WHERE "
                      f"(r.src_type = 'script_line' AND r.src_id IN (SELECT CAST(id AS CHAR) FROM game_lines WHERE game_id = '{GAME_ID}')) OR "
                      f"(r.dst_type = 'script_line' AND r.dst_id IN (SELECT CAST(id AS CHAR) FROM game_lines WHERE game_id = '{GAME_ID}'))"),
    ):
        cur.execute(sql)
        out[name] = cur.fetchone()[0]
    return out


def delete_game(cur, drop_story_builds: bool) -> None:
    """依外鍵順序刪掉這款遊戲的內容表列。Delete this game's content rows in FK order."""
    cur.execute("SELECT COUNT(*) FROM story_builds WHERE game_id = %s", (GAME_ID,))
    if cur.fetchone()[0]:
        if not drop_story_builds:
            raise SystemExit("❌ 已有這款遊戲的流程圖建置（story_builds），重匯會改變台詞 id；加 --drop-story-builds 一起刪掉")
        cur.execute("DELETE FROM story_builds WHERE game_id = %s", (GAME_ID,))  # story_node_lines 會連帶刪除
    cur.execute("DELETE r FROM game_line_revisions r JOIN game_lines l ON l.id = r.line_id WHERE l.game_id = %s", (GAME_ID,))
    cur.execute("DELETE b FROM game_line_bgm b JOIN game_lines l ON l.id = b.line_id WHERE l.game_id = %s", (GAME_ID,))
    cur.execute("DELETE FROM game_lines WHERE game_id = %s", (GAME_ID,))
    cur.execute("DELETE FROM game_scene_links WHERE game_id = %s", (GAME_ID,))
    cur.execute("DELETE FROM game_speakers WHERE game_id = %s", (GAME_ID,))
    cur.execute("DELETE FROM game_bgm_tracks WHERE game_id = %s", (GAME_ID,))
    cur.execute("DELETE s FROM game_scenes s JOIN game_script_files f ON f.file_id = s.file_id WHERE f.game_id = %s", (GAME_ID,))
    cur.execute("DELETE FROM game_script_files WHERE game_id = %s", (GAME_ID,))


def _next_id(cur, table: str, col: str) -> int:
    cur.execute(f"SELECT COALESCE(MAX({col}), 0) FROM {table}")
    return cur.fetchone()[0]


def write(c: Content, database: str, drop_story_builds: bool) -> dict:
    """寫入（單一交易）。Write everything in one transaction."""
    conn = connect(database)
    try:
        with conn.cursor() as cur:
            refs = references(cur)
            if any(refs.values()):
                raise SystemExit(f"❌ 這款遊戲的台詞已被紀錄表引用，不能重匯：{refs}")
            delete_game(cur, drop_story_builds)
            cur.execute(
                "INSERT INTO game_profiles (game_id, title_ja_jp, title_zh_hant, engine, languages, voice_dir, avatar_dir, legacy_source) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE title_ja_jp=VALUES(title_ja_jp), "
                "title_zh_hant=VALUES(title_zh_hant), engine=VALUES(engine), languages=VALUES(languages), "
                "voice_dir=VALUES(voice_dir), avatar_dir=VALUES(avatar_dir), legacy_source=VALUES(legacy_source)",
                tuple(_json(v) if k == "languages" else v for k, v in GAME_PROFILE.items()))
            off = {t: _next_id(cur, t, col) for t, col in (
                ("game_script_files", "file_id"), ("game_scenes", "scene_id"), ("game_speakers", "speaker_id"),
                ("game_bgm_tracks", "track_id"), ("game_scene_links", "link_id"), ("game_lines", "id"))}
            fo, so, po, to, lo, io = (off[k] for k in ("game_script_files", "game_scenes", "game_speakers",
                                                         "game_bgm_tracks", "game_scene_links", "game_lines"))
            _insert(cur, "game_script_files", [{**x, "file_id": x["file_id"] + fo} for x in c.files])
            _insert(cur, "game_scenes", [{**x, "scene_id": x["scene_id"] + so, "file_id": x["file_id"] + fo} for x in c.scenes])
            _insert(cur, "game_speakers", [{**x, "speaker_id": x["speaker_id"] + po} for x in c.speakers])
            _insert(cur, "game_bgm_tracks", [{**x, "track_id": x["track_id"] + to} for x in c.tracks])
            _insert(cur, "game_scene_links", [{
                **x, "link_id": x["link_id"] + lo, "from_scene_id": x["from_scene_id"] + so,
                "to_scene_id": None if x["to_scene_id"] is None else x["to_scene_id"] + so} for x in c.links],
                ("condition_vars", "effects"))
            _insert(cur, "game_lines", [{
                **x, "id": x["id"] + io, "scene_id": x["scene_id"] + so,
                "speaker_id": None if x["speaker_id"] is None else x["speaker_id"] + po} for x in c.lines], ("attrs",))
            cur.executemany("INSERT INTO game_line_bgm (line_id, ord, track_id) VALUES (%s, %s, %s)",
                            [(lid + io, o, tid + to) for lid, o, tid in c.line_bgm])
            cur.execute("SELECT COUNT(*), MIN(id), MAX(id) FROM game_lines WHERE game_id = %s", (GAME_ID,))
            n, lo_id, hi_id = cur.fetchone()
            if n != len(c.lines):
                raise RuntimeError(f"寫入後台詞數 {n} ≠ {len(c.lines)}")
        conn.commit()
        return {"lines": n, "id_range": [lo_id, hi_id]}
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--material", type=Path, default=DEFAULT_MATERIAL)
    ap.add_argument("--database", default="fluencytides_corpus")
    ap.add_argument("--dry-run", action="store_true", help="只輸出報表，不寫資料庫 / report only")
    ap.add_argument("--drop-story-builds", action="store_true", help="一併刪掉這款遊戲的流程圖建置")
    args = ap.parse_args()
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    src = load_material(args.material)
    c = build(src)
    print(json.dumps(c.report, ensure_ascii=False, indent=1, default=dict))
    problems = check(c, src)
    if problems:
        sys.exit(f"❌ {problems}")
    if args.dry_run:
        print("只檢查（--dry-run），未寫入。")
        return
    print(f"✅ 已寫入 {args.database}：{write(c, args.database, args.drop_story_builds)}")


if __name__ == "__main__":
    main()
