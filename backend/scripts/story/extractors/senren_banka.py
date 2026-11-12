"""《千恋＊万花》的劇情流程圖擷取器：讀 ``fluencytides_corpus``，產生建置包。

Story-flowchart extractor for Senren*Banka: reads the corpus database and
writes a build package (docs/wip/senren_banka_corpus_import_FEAT_2026-11-08.md D6).

遊戲專屬的部分都在這個檔案：
- 分頁：檔號百位數（0 共通、1 芳乃、2 茉子、3 叢雨、4 蕾娜、5 小春＆蘆花），和遊戲內流程圖的分頁一致；
- 標題：遊戲內官方流程圖的節點標題（素材 ``calib/SB_flowchart_nodes.json``，每句台詞的 ``attrs.fc_node``
  指到它），場景節點取成員第一句的官方節點；
- 章節：台詞的 ``chapter_label``（``chapter　X-Y`` → ``CHAPTER X``、エピローグ、アフター）；
- 補充連接：通關後從標題選單進入的 after story；
- 路線預設：校準 ``route_grid`` 在 015 實測進入各路線的一組選項旗標。

通用的建圖規則在 ``scripts/story/common/scene_graph.py``。

用法 / Usage::

    python backend/scripts/story/extractors/senren_banka.py --out build/senren_banka \\
        --database fluencytides_corpus \\
        --template "Y:/material/yuzusoft/SenrenBanka/templates/quick.bmp" \\
        --thumbs "Y:/material/yuzusoft/SenrenBanka/thumbs" \\
        --voices "Y:/material/yuzusoft/SenrenBanka/voice" \\
        --bgm "Y:/material/yuzusoft/SenrenBanka/bgm" --enable-bgm-audio
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.story.common import corpus_db  # noqa: E402
from scripts.story.common.package import write_package  # noqa: E402
from scripts.story.common.scene_graph import (  # noqa: E402
    BuildResult,
    ExtraLink,
    FileRow,
    GameRules,
    LineRow,
    LinkRow,
    SceneRow,
    build_story_graph,
    route_presets,
    to_mermaid,
)

from app.infrastructure.krkr import savestruct  # noqa: E402
from app.schemas.story import BuildManifest  # noqa: E402
from app.services.story.registry import get_story_game  # noqa: E402

GAME_ID = "SenrenBanka"
EXTRACTOR = "senren_banka@1"
DEFAULT_MATERIAL = Path("//192.168.50.250/sn640/material/yuzusoft/SenrenBanka")
TEMPLATE_KEY = f"story/{GAME_ID}/templates/quick.bmp"

TABS = [
    {"key": "common", "title": "共通", "titles": {"ja-JP": "共通", "zh-Hant": "共通"}, "order": 1, "avatar": None},
    {"key": "yos", "title": "芳乃", "titles": {"ja-JP": "芳乃", "zh-Hant": "芳乃"}, "order": 2, "avatar": None},
    {"key": "mak", "title": "茉子", "titles": {"ja-JP": "茉子", "zh-Hant": "茉子"}, "order": 3, "avatar": None},
    {"key": "mur", "title": "ムラサメ", "titles": {"ja-JP": "ムラサメ", "zh-Hant": "叢雨"}, "order": 4, "avatar": None},
    {"key": "len", "title": "レナ", "titles": {"ja-JP": "レナ", "zh-Hant": "蕾娜"}, "order": 5, "avatar": None},
    {"key": "sub", "title": "小春＆芦花", "titles": {"ja-JP": "小春＆芦花", "zh-Hant": "小春＆蘆花"}, "order": 6,
     "avatar": None},
]
TAB_BY_DIGIT = {"0": "common", "1": "yos", "2": "mak", "3": "mur", "4": "len", "5": "sub"}
#: 通關後的 after story：(結局所在的檔號, after 的檔號, 標題選單進入是否實測過)
AFTER_STORIES = [("114", "115", False), ("210", "211", False), ("316", "317", False),
                 ("428", "429", True), ("512", "513", True), ("522", "523", True)]
AFTER_FILES = {after for _, after, _ in AFTER_STORIES}

EXTRA_LINKS = [
    ExtraLink((end, None), (after, None), kind="unlock", evidence="observed" if seen else "inferred",
              flags=("unlock",), condition_text_ja="クリア後 After Story", condition_text_zh="通關後 After Story",
              note=None if seen else "標題選單入口未實測")
    for end, after, seen in AFTER_STORIES
]

#: 路線預設：校準 calib_route_grid.jsonl 在 015 實測進入各路線、旗標值總和最小的一組（2026-11-07）。
#: レナ在 014 結尾判定，route_grid 從 015 開始量不到，依計數規則推定（010 選 1、010_02A 選 2 → 蕾娜計數 2）。
ROUTE_PRESETS = {
    "yos": {"s001*com_part_1_sel": 1, "s006*com_part_6_sel": 1, "s010*com_part_10_sel": 1, "s010*010_01com_sel": 1,
            "s010*010_02A_sel": 1, "s010*010_02com_sel": 1, "s012*com_part_12_sel": 1, "s014*com_part_14_sel": 1},
    "mak": {"s001*com_part_1_sel": 1, "s006*com_part_6_sel": 2, "s010*com_part_10_sel": 1, "s010*010_01com_sel": 2,
            "s010*010_02A_sel": 2, "s010*010_02com_sel": 1, "s012*com_part_12_sel": 1, "s014*com_part_14_sel": 1},
    "mur": {"s001*com_part_1_sel": 1, "s006*com_part_6_sel": 1, "s010*com_part_10_sel": 1, "s010*010_01com_sel": 3,
            "s010*010_02A_sel": 1, "s010*010_02com_sel": 2, "s012*com_part_12_sel": 1, "s014*com_part_14_sel": 2},
    "len": {"s001*com_part_1_sel": 1, "s006*com_part_6_sel": 1, "s010*com_part_10_sel": 1, "s010*010_01com_sel": 1,
            "s010*010_02A_sel": 2, "s010*010_02com_sel": 1, "s012*com_part_12_sel": 1, "s014*com_part_14_sel": 2},
    "sub": {"s001*com_part_1_sel": 1, "s006*com_part_6_sel": 1, "s010*com_part_10_sel": 1, "s010*010_01com_sel": 1,
            "s010*010_02A_sel": 1, "s010*010_02com_sel": 1, "s012*com_part_12_sel": 2, "s014*com_part_14_sel": 2},
}

CONFIG = {
    "languages": [{"code": "ja-JP", "label": "日本語"}, {"code": "zh-Hant", "label": "繁中"}],
    "default_theme": "senren-hoori",
    "line_attrs": [{"key": "is_h", "label": "H", "kind": "flag"}],
    "annotations": {"cards": {"card_types": [
        {"card_type": "jp_core_verb", "label": "核心動詞",
         "note_types": ["JP_CoreVerb_Master_Dark", "JP_Context_Dark", "JP_CoreVerb_Cloze_Dark"]},
        {"card_type": "jp_verb_pair", "label": "自他動詞",
         "note_types": ["JP_VerbPair_Master_Dark", "JP_Context_Dark", "JP_VerbPair_Cloze_Dark"]},
    ]}},
    "saves": {
        "template": TEMPLATE_KEY,
        "slots": [{"id": "quick", "label": "快速存檔", "filename": "data_quick_01.bmp"},
                  {"id": "continue", "label": "前回のつづきから", "filename": "data_continue.bmp"}],
        "install_hint": "放到遊戲的 savedata\\：快速存檔在遊戲中按 Q.Load（F12）；"
                        "data_continue.bmp 在標題畫面按「前回のつづきから」",
    },
    "route_presets": ROUTE_PRESETS,
}

_CHAPTER = re.compile(r"chapter[\s\u3000]*(\d+)-(\d+)", re.I)


def load_official_nodes(material: Path) -> dict[str, dict]:
    """官方流程圖節點：``{"s001*com_part_1": {title_jp, title_tw, kind, file_code, …}}``。

    Official flowchart nodes keyed by node id.

    Raises:
        SystemExit: 檔案不在、不是 JSON 陣列，或有節點缺少必要的鍵。The file is missing or malformed.
    """
    path = material / "calib" / "SB_flowchart_nodes.json"
    try:
        nodes = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise SystemExit(f"官方流程圖節點檔不存在：{path}") from e
    except json.JSONDecodeError as e:
        raise SystemExit(f"{path} 不是合法的 JSON：{e}") from e
    need = ("node", "kind", "file_code")
    if not isinstance(nodes, list):
        raise SystemExit(f"{path} 應該是節點陣列")
    bad = [n for n in nodes if not isinstance(n, dict) or any(k not in n for k in need)]
    if bad:
        raise SystemExit(f"{path} 的節點格式不對（每個節點要有 {need}）：{str(bad[:2])[:200]}")
    return {n["node"]: n for n in nodes}


def check_template(path: Path) -> None:
    """樣板必須是千戀＊萬花的欄位存檔（BMP＋saveStruct 封包，有 core.scenePlayer／historyData）。

    The template must be a saveStruct slot save; checked here so a bad template
    fails at extraction instead of when a user downloads a save.

    Raises:
        SystemExit: 檔案不在或格式不對。Missing or malformed template.
    """
    if not path.is_file():
        raise SystemExit(f"存檔樣板不存在：{path}")
    try:
        root = savestruct.load(path.read_bytes())
    except savestruct.SaveStructError as e:
        raise SystemExit(f"存檔樣板不是 saveStruct 存檔：{path}（{e}）") from e
    core = root.get("core") if isinstance(root, dict) else None
    if not isinstance(core, dict) or not isinstance(core.get("scenePlayer"), dict)             or not isinstance(core.get("historyData"), dict):
        raise SystemExit(f"存檔樣板缺少 core.scenePlayer 或 core.historyData：{path}")


def tab_of_file(f: FileRow) -> str:
    """檔號百位數決定分頁。The hundreds digit of the file number decides the tab."""
    return TAB_BY_DIGIT.get(f.file_no[:1], "common")


def is_after_file(f: FileRow) -> bool:
    """通關後的 after story 檔案。After-story files."""
    return f.file_no in AFTER_FILES


def parse_chapter(label: str) -> tuple[str, str] | None:
    """``chapter　X-Y`` → ``CHAPTER X``；エピローグ、アフター照原意。Parse a chapter label."""
    m = _CHAPTER.search(label)
    if m:
        return m.group(1), f"CHAPTER {m.group(1)}"
    if "アフター" in label:
        return "after", "After Story"
    if "エピローグ" in label:
        return "epilogue", "Epilogue"
    return None


def rules(official: dict[str, dict], titles: dict | None = None) -> GameRules:
    """這款遊戲的建圖規則。The build rules for this game."""
    first_node = {}
    for n in official.values():
        if n["kind"] == "scene":
            first_node.setdefault(n["file_code"], n)

    def file_title(f: FileRow) -> tuple[str, str | None]:
        n = first_node.get(f.file_no)
        return (n["title_jp"], n["title_tw"]) if n else (f.title_ja or f.file_name, f.title_zh)

    return GameRules(prefix="sb", main_tab="common", tabs=TABS, tab_of_file=tab_of_file,
                     is_after_file=is_after_file, file_title=file_title, parse_chapter=parse_chapter,
                     extra_links=EXTRA_LINKS, titles=titles or {}, default_branch_label="その他")


def load_rows(conn) -> tuple[list[FileRow], list[SceneRow], list[LineRow], list[LinkRow], dict[int, str]]:
    """從語料庫讀出這款遊戲的檔案、場景、台詞、連接，以及每句的官方節點。Read the game's rows."""
    with conn.cursor() as cur:
        cur.execute("SELECT file_id, file_name, file_no, title_ja_jp, title_zh_hant, file_order "
                    "FROM game_script_files WHERE game_id=%s", (GAME_ID,))
        files = [FileRow(r["file_id"], r["file_name"], r["file_no"], r["title_ja_jp"], r["title_zh_hant"],
                         r["file_order"]) for r in cur.fetchall()]
        cur.execute("SELECT s.scene_id, s.file_id, s.label, s.scene_order FROM game_scenes s "
                    "JOIN game_script_files f ON f.file_id = s.file_id WHERE f.game_id=%s", (GAME_ID,))
        scenes = [SceneRow(r["scene_id"], r["file_id"], r["label"], r["scene_order"]) for r in cur.fetchall()]
        cur.execute("SELECT id, scene_id, story_order, kind, chapter_label, "
                    "JSON_UNQUOTE(JSON_EXTRACT(attrs, '$.fc_node')) AS fc_node FROM game_lines "
                    "WHERE game_id=%s AND status='active'", (GAME_ID,))
        lines, fc_of = [], {}
        for r in cur.fetchall():
            lines.append(LineRow(int(r["id"]), r["scene_id"], r["story_order"], r["kind"], r["chapter_label"]))
            if r["fc_node"]:
                fc_of[int(r["id"])] = r["fc_node"]
        cur.execute("SELECT * FROM game_scene_links WHERE game_id=%s ORDER BY link_id", (GAME_ID,))
        links = [LinkRow(
            from_scene_id=r["from_scene_id"], to_scene_id=r["to_scene_id"], kind=r["kind"],
            option_index=r["option_index"], option_text_ja=r["option_text_ja_jp"],
            option_text_zh=r["option_text_zh_hant"], condition_text_ja=r["condition_text_ja_jp"],
            condition_text_zh=r["condition_text_zh_hant"],
            condition_vars=_json(r["condition_vars"], r["link_id"]), effects=_json(r["effects"], r["link_id"]),
            evidence=r["evidence"], note=r["note"], link_id=r["link_id"]) for r in cur.fetchall()]
    return files, scenes, lines, links, fc_of


def _json(raw, link_id: int | None = None):
    if raw is None or isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"game_scene_links {link_id} 的 JSON 欄位格式不對：{e}") from e


def coverage_warnings(files: list[FileRow], fc_of: dict[int, str], official: dict[str, dict]) -> list[str]:
    """官方標題與分頁對不上的地方（寫進 warnings.txt，不中止）。Gaps in tabs and official titles.

    - 檔號第一位不在 0–5：分頁落回「共通」；
    - 檔案沒有官方場景節點：檔名標題落回腳本檔名；
    - 台詞的 ``fc_node`` 不在官方節點表：那些節點的標題落回檔名。
    """
    out = [f"檔案 {f.file_name} 的檔號 {f.file_no} 不在分頁規則內，歸到「共通」"
           for f in files if f.file_no[:1] not in TAB_BY_DIGIT]
    codes = {n["file_code"] for n in official.values() if n["kind"] == "scene"}
    out += [f"檔案 {f.file_name} 沒有官方場景節點，標題用腳本檔名" for f in files if f.file_no not in codes]
    missing = [k for k in fc_of.values() if k not in official]
    if missing:
        out.append(f"{len(missing)} 句台詞的 fc_node 不在官方節點表（例 {sorted(set(missing))[:5]}），這些節點的標題用檔名")
    return out


def official_titles(result: BuildResult, fc_of: dict[int, str], official: dict[str, dict]) -> dict[str, dict]:
    """場景節點 → 官方標題：取節點成員第一句（劇情順序）的官方節點。

    Scene node → official title, from the first member line's official node.
    """
    first_line: dict[str, tuple[int, int]] = {}
    for row in result.rows:
        if row.node_key not in first_line or row.seq < first_line[row.node_key][0]:
            first_line[row.node_key] = (row.seq, row.line_id)
    out = {}
    for node in result.graph.nodes:
        if node.type != "scene" or node.key not in first_line:
            continue
        n = official.get(fc_of.get(first_line[node.key][1], ""))
        if n and (n.get("title_jp") or n.get("title_tw")):
            out[node.key] = {k: v for k, v in (("ja-JP", n.get("title_jp")), ("zh-Hant", n.get("title_tw"))) if v}
    return out


def extract(conn, out_dir: Path, material: Path, template: Path | None, thumbs: Path | None,
            note: str | None = None, voices: Path | None = None, bgm: Path | None = None,
            bgm_audio: bool = False) -> dict:
    """擷取並寫出建置包；回傳摘要。Extract and write the package; returns a summary."""
    files, scenes, lines, links, fc_of = load_rows(conn)
    official = load_official_nodes(material)
    first = build_story_graph(files, scenes, lines, links, rules(official))
    result = build_story_graph(files, scenes, lines, links, rules(official, official_titles(first, fc_of, official)))
    warnings = coverage_warnings(files, fc_of, official) + result.warnings
    config = json.loads(json.dumps(CONFIG))
    if bgm_audio:
        config["bgm_audio"] = True
    # 校準實測的預設優先；圖上自動推出來的只補實測沒有的路線
    config["route_presets"] = {**route_presets(result.graph), **ROUTE_PRESETS}
    if template is None:
        config.pop("saves")
    get_story_game(GAME_ID).parse_config(config)  # 和匯入、API 用同一個設定模型
    manifest = BuildManifest(game_id=GAME_ID, extractor=EXTRACTOR, config=config, stats=result.stats,
                             assets={"thumbs_dir": str(thumbs) if thumbs else None,
                                     "thumbs_prefix": f"story/{GAME_ID}/thumbs/",
                                     "voices_dir": str(voices) if voices else None,
                                     "voices_prefix": f"story/{GAME_ID}/voices/",
                                     "bgm_dir": str(bgm) if bgm else None,
                                     "bgm_prefix": f"story/{GAME_ID}/bgm/"},
                             note=note)
    source_hash = write_package(out_dir, manifest, result.graph, result.rows,
                                templates={TEMPLATE_KEY: template} if template else None,
                                mermaid=to_mermaid(result.graph))
    (out_dir / "warnings.txt").write_text("\n".join(warnings) + "\n", encoding="utf-8")
    return {"source_hash": source_hash, "stats": result.stats, "warnings": warnings}


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="千戀＊萬花：從語料庫產生劇情流程圖建置包")
    parser.add_argument("--out", required=True, type=Path, help="建置包輸出資料夾")
    parser.add_argument("--material", type=Path, default=DEFAULT_MATERIAL,
                        help="素材資料夾（讀 calib/SB_flowchart_nodes.json 的官方標題）")
    parser.add_argument("--template", type=Path, help="快速存檔樣板（quick.bmp）；省略時不提供存檔")
    parser.add_argument("--thumbs", type=Path, help="縮圖資料夾（<雜湊>.jpg），匯入時上傳")
    parser.add_argument("--voices", type=Path, help="語音資料夾（<voice_file>.mp3），匯入時只上傳台詞用到的檔案")
    parser.add_argument("--bgm", type=Path, help="BGM 資料夾（<code>.ogg），匯入時只上傳 game_bgm_tracks 有的代號")
    parser.add_argument("--enable-bgm-audio", action="store_true",
                        help="在設定打開 BGM 播放（capabilities.bgm）；會改變 source_hash，須正常匯入成新版本")
    parser.add_argument("--database", help="語料庫名稱（預設 backend/.env 的 MYSQL_DATABASE；"
                                           "語料庫切換前可指定 fluencytides_corpus）")
    parser.add_argument("--note", help="這個版本的備註")
    args = parser.parse_args()
    for p, what in ((args.voices, "語音"), (args.bgm, "BGM"), (args.thumbs, "縮圖")):
        if p and not p.is_dir():
            raise SystemExit(f"{what}資料夾不存在：{p}")
    if args.template:
        check_template(args.template)
    load_official_nodes(args.material)  # 連資料庫之前先確認素材讀得到
    conn = corpus_db.connect(args.database)
    try:
        summary = extract(conn, args.out, args.material, args.template, args.thumbs, args.note, voices=args.voices,
                          bgm=args.bgm, bgm_audio=args.enable_bgm_audio)
    finally:
        conn.close()
    stats = summary["stats"]
    print(f"已寫出 {args.out}：{stats['nodes']} 個節點、{stats['edges']} 條邊、{stats['lines']} 句")
    print(f"source_hash = {summary['source_hash']}")
    for tab, s in stats["tabs"].items():
        print(f"  {tab}: {s['nodes']} 節點 / {s['lines']} 句")
    print(f"類型：{stats['types']}")
    if summary["warnings"]:
        print(f"警告 {len(summary['warnings'])} 則（見 warnings.txt）：")
        for w in summary["warnings"][:40]:
            print("  - " + w)


if __name__ == "__main__":
    main()
