"""《魔女的夜宴》（サノバウィッチ）的劇情流程圖擷取器：只讀 ``fluencytides_corpus``，產生建置包。

Story-flowchart extractor for Sabbat of the Witch: reads only the corpus
database and writes a build package (docs/wip/story_flowchart_FEAT_2026-10-31.md D5).

遊戲專屬的部分都在這個檔案：分頁、檔案 → 分頁、標題、章節解析、補充連接（資料庫還沒有的
路線分歧、通關後入口），以及流程圖設定（D3）。通用的建圖規則在 ``scripts/story/common/scene_graph.py``。

用法 / Usage::

    python backend/scripts/story/extractors/sabbat_of_the_witch.py --out build/sabbat_of_the_witch \\
        --template "C:/Users/forip/Desktop/WorkSpace/game/SW_calib/quick_template.jpg" \\
        --thumbs "C:/Users/forip/Desktop/WorkSpace/game/SW_calib/thumbs" \\
        --voices "C:/Users/forip/Desktop/WorkSpace/material/voice/yuzusoft/SabbatOfTheWitch"

``--voices`` 是語音資料夾（``<game_lines.voice_file>.mp3``，即 ``GAME_ASSETS_ROOT`` 接上
``game_profiles.voice_dir``）。只記在 manifest，不算進 ``source_hash``；匯入時只上傳台詞用到的檔案。
``--voices`` is the voice folder; only referenced files are uploaded.

BGM（預留，音檔目前沒有）：``--bgm <資料夾>`` 是 BGM 音檔資料夾（``<game_bgm_tracks.code>.ogg`` 或
``.mp3``），和語音一樣只記在 manifest、不算進 ``source_hash``，所以可以先用匯入腳本的 ``--assets-only``
替上線版本預先上傳。``--enable-bgm-audio`` 才把設定的 ``bgm_audio`` 打開（``capabilities.bgm``、前端的
BGM 播放鍵）；它會改變設定、也就是 ``source_hash``，要正常匯入成新版本。
BGM (reserved): ``--bgm`` records the BGM folder (not hashed, so
``--assets-only`` can pre-upload); ``--enable-bgm-audio`` turns on
``config.bgm_audio`` and therefore needs a normal import (new build).
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

from app.schemas.story import BuildManifest  # noqa: E402
from app.services.story.registry import get_story_game  # noqa: E402

GAME_ID = "SabbatOfTheWitch"
EXTRACTOR = "sabbat_of_the_witch@1"
TITLES_FILE = Path(__file__).with_name("sabbat_of_the_witch_titles.json")
TEMPLATE_KEY = f"story/{GAME_ID}/templates/quick.jpg"

TABS = [
    {"key": "common", "title": "共通", "titles": {"ja-JP": "共通", "zh-Hant": "共通"}, "order": 1, "avatar": None},
    {"key": "nene", "title": "寧々", "titles": {"ja-JP": "寧々", "zh-Hant": "寧寧"}, "order": 2, "avatar": "nene"},
    {"key": "meguru", "title": "めぐる", "titles": {"ja-JP": "めぐる", "zh-Hant": "巡"}, "order": 3, "avatar": "meguru"},
    {"key": "tsumugi", "title": "紬", "titles": {"ja-JP": "紬", "zh-Hant": "紬"}, "order": 4, "avatar": "tsumugi"},
    {"key": "touko", "title": "憧子", "titles": {"ja-JP": "憧子", "zh-Hant": "憧子"}, "order": 5, "avatar": "touko"},
    {"key": "wakana", "title": "和奏", "titles": {"ja-JP": "和奏", "zh-Hant": "和奏"}, "order": 6, "avatar": "wakana"},
]
TAB_BY_DIGIT = {"0": "common", "1": "nene", "2": "meguru", "3": "tsumugi", "4": "touko", "5": "wakana"}
RESTART_FILES = {"110", "111", "112", "113", "114", "115", "116"}

#: 資料庫還沒有的連接（2026-11-01 的語料庫：沒有 unlock，也沒有 019 結尾依好感的路線分歧）。
#: 路線分歧的旗標是 calib_route_probe.jsonl 實測時接過去的一組（例，不是門檻）。
#: Links missing from the corpus as of 2026-11-01.
EXTRA_LINKS = [
    ExtraLink(("019", "019_02com"), ("019", "normal_end"), kind="next", evidence="probed",
              condition_text_zh="好感旗標不足"),
    ExtraLink(("019", "019_02com"), ("020.共通－パーティーめぐる", None), condition_vars={"meg_flag": 4}, evidence="probed"),
    ExtraLink(("019", "019_02com"), ("020.共通－パーティー紬", None), condition_vars={"tsu_flag": 2}, evidence="probed"),
    ExtraLink(("019", "019_02com"), ("020.共通－パーティ寧々", None), condition_vars={"nen_flag": 4}, evidence="probed"),
    ExtraLink(("019", "019_02com"), ("020.共通－パーティ憧子", None), condition_vars={"tou_flag": 3}, evidence="probed"),
    # 015 chapter38 結尾依好感接 015_01A／015_01B。calib 把這條記成從 019 的 dummyselect1 出發的自然接續，
    # 遷移腳本已濾掉「選項標籤出發的自然接續」與自迴圈，所以語料庫沒有這條，由這裡補上
    # （不寫 link_id：重建語料庫後 link_id 會變）。
    ExtraLink(("015", "chapter38"), ("015", "015_01B"), condition_vars={"nen_flag": 2}, evidence="probed"),
    # 009 的 dummy1（63 句）是和奏的段落，只有 wak_flag=1 才走得到
    ExtraLink(("009", "009_03B"), ("009", "dummy1"), condition_vars={"wak_flag": 1}, evidence="probed"),
    # 通關後入口
    ExtraLink(("018", "018_01com"), ("500", None), kind="unlock", evidence="inferred",
              condition_text_zh="任一路線通關後", note="和奏入口：條件未實測"),
    ExtraLink(("109_β", None), ("110", None), kind="unlock", evidence="inferred",
              condition_text_zh="寧々通關後 RESTART"),
    *[ExtraLink((src, None), (dst, None), kind="unlock", evidence="observed", flags=("unlock",),
                condition_text_zh="通關後 After Story")
      for src, dst in (("116", "117"), ("218", "219"), ("320", "321"), ("411", "412"), ("507", "508"))],
]

CONFIG = {
    "languages": [{"code": "ja-JP", "label": "日本語"}, {"code": "zh-Hant", "label": "繁中"}],
    "default_theme": "sabbat-night",
    # BGM 不在 line_attrs：它是 StoryLine 的正式欄位（bgm、bgm_detect、bgm_may_change），抽屜直接顯示曲名。
    # BGM is not a line attr: it is the first-class StoryLine.bgm field.
    "line_attrs": [{"key": "is_h", "label": "H", "kind": "flag"}],
    "annotations": {"cards": {"card_types": [
        {"card_type": "jp_core_verb", "label": "核心動詞",
         "note_types": ["JP_CoreVerb_Master_Dark", "JP_Context_Dark", "JP_CoreVerb_Cloze_Dark"]},
        {"card_type": "jp_verb_pair", "label": "自他動詞",
         "note_types": ["JP_VerbPair_Master_Dark", "JP_Context_Dark", "JP_VerbPair_Cloze_Dark"]},
    ]}},
    "saves": {
        "template": TEMPLATE_KEY,
        "slots": [{"id": "quick", "label": "快速存檔", "filename": "data_quick_01.jpg"},
                  {"id": "1", "label": "第 1 格", "filename": "data_0001_01.jpg"}],
        "install_hint": "放到 SabbatOfTheWitch\\savedata\\，遊戲中按 Q.Load（第 1 格則從 LOAD 畫面讀取）",
    },
    "route_presets": {},
}


def tab_of_file(f: FileRow) -> str:
    """檔號第一位決定分頁。The first digit of the file number decides the tab."""
    return TAB_BY_DIGIT.get(f.file_no[:1], "common")


def is_after_file(f: FileRow) -> bool:
    """通關後的 AFTER 檔案。AFTER-story files."""
    return "アフター" in f.file_name


def file_title(f: FileRow) -> tuple[str, str | None]:
    """去掉「共通－」「寧々－」等前綴。Strip the route prefix."""
    def strip(t: str | None) -> str | None:
        return t.split("－", 1)[1] if t and "－" in t else t
    return strip(f.title_ja) or f.file_name, strip(f.title_zh)


_CHAPTER = re.compile(r"chapter[\s\u3000]*(\d+)-(\d+)", re.I)


def parse_chapter(label: str) -> tuple[str, str] | None:
    """``chapter　X-Y`` 取 X；エピローグ、アフターストーリー照原意。Parse a chapter label."""
    m = _CHAPTER.search(label)
    if m:
        return m.group(1), f"CHAPTER {m.group(1)}"
    if "アフター" in label:
        return "after", "After Story"
    if "エピローグ" in label:
        return "epilogue", "Epilogue"
    return None


def chapter_prefix(f: FileRow) -> str:
    """寧々 RESTART（110–116）的章節前綴。Chapter prefix for Nene's RESTART."""
    return "RESTART · " if f.file_no in RESTART_FILES else ""


def rules() -> GameRules:
    """這款遊戲的建圖規則。The build rules for this game."""
    titles = json.loads(TITLES_FILE.read_text(encoding="utf-8")) if TITLES_FILE.exists() else {}
    titles = {k: v for k, v in titles.items() if not k.startswith("_")}
    return GameRules(prefix="sw", main_tab="common", tabs=TABS, tab_of_file=tab_of_file,
                     is_after_file=is_after_file, file_title=file_title, parse_chapter=parse_chapter,
                     chapter_prefix=chapter_prefix, extra_links=EXTRA_LINKS, titles=titles)


def load_rows(conn) -> tuple[list[FileRow], list[SceneRow], list[LineRow], list[LinkRow]]:
    """從語料庫讀出這款遊戲的檔案、場景、台詞與連接。Read the game's rows from the corpus."""
    with conn.cursor() as cur:
        cur.execute("SELECT file_id, file_name, file_no, title_ja_jp, title_zh_hant, file_order "
                    "FROM game_script_files WHERE game_id=%s", (GAME_ID,))
        files = [FileRow(r["file_id"], r["file_name"], r["file_no"], r["title_ja_jp"], r["title_zh_hant"],
                         r["file_order"]) for r in cur.fetchall()]
        cur.execute("SELECT s.scene_id, s.file_id, s.label, s.scene_order FROM game_scenes s "
                    "JOIN game_script_files f ON f.file_id = s.file_id WHERE f.game_id=%s", (GAME_ID,))
        scenes = [SceneRow(r["scene_id"], r["file_id"], r["label"], r["scene_order"]) for r in cur.fetchall()]
        cur.execute("SELECT id, scene_id, story_order, kind, chapter_label FROM game_lines "
                    "WHERE game_id=%s AND status='active'", (GAME_ID,))
        lines = [LineRow(int(r["id"]), r["scene_id"], r["story_order"], r["kind"], r["chapter_label"])
                 for r in cur.fetchall()]
        cur.execute("SELECT * FROM game_scene_links WHERE game_id=%s ORDER BY link_id", (GAME_ID,))
        links = []
        for r in cur.fetchall():
            links.append(LinkRow(
                from_scene_id=r["from_scene_id"], to_scene_id=r["to_scene_id"], kind=r["kind"],
                option_index=r["option_index"], option_text_ja=r["option_text_ja_jp"],
                option_text_zh=r["option_text_zh_hant"], condition_text_ja=r["condition_text_ja_jp"],
                condition_text_zh=r["condition_text_zh_hant"],
                condition_vars=_json(r["condition_vars"]), effects=_json(r["effects"]),
                evidence=r["evidence"], note=r["note"], link_id=r["link_id"]))
    return files, scenes, lines, links


def _json(raw):
    if raw is None or isinstance(raw, (dict, list)):
        return raw
    return json.loads(raw)


def extract(conn, out_dir: Path, template: Path | None, thumbs: Path | None, note: str | None = None,
            voices: Path | None = None, bgm: Path | None = None, bgm_audio: bool = False) -> dict:
    """擷取並寫出建置包；回傳摘要。Extract and write the package; returns a summary.

    ``bgm`` 是 BGM 音檔資料夾（只記在 manifest、不算進 ``source_hash``）；``bgm_audio`` 為 True 時才在
    設定寫入 ``bgm_audio: true`` 打開 BGM 播放（預設不寫這個鍵，等同 False）。
    ``bgm`` is the BGM folder (manifest only, not hashed); ``bgm_audio`` writes
    ``bgm_audio: true`` into the config (the key is omitted by default).
    """
    files, scenes, lines, links = load_rows(conn)
    result = build_story_graph(files, scenes, lines, links, rules())
    config = dict(CONFIG)
    if bgm_audio:
        config["bgm_audio"] = True
    config["route_presets"] = route_presets(result.graph) or {
        "nene": {"nen_flag": 4}, "meguru": {"meg_flag": 4}, "tsumugi": {"tsu_flag": 2}, "touko": {"tou_flag": 3}}
    if template is None:
        config.pop("saves")
    get_story_game(GAME_ID).parse_config(config)  # 和匯入、API 用同一個設定模型（SabbatConfig）
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
    (out_dir / "warnings.txt").write_text("\n".join(result.warnings) + "\n", encoding="utf-8")
    return {"source_hash": source_hash, "stats": result.stats, "warnings": result.warnings}


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="魔女的夜宴：從語料庫產生劇情流程圖建置包")
    parser.add_argument("--out", required=True, type=Path, help="建置包輸出資料夾")
    parser.add_argument("--template", type=Path, help="快速存檔樣板（data_quick_01.jpg）；省略時不提供存檔")
    parser.add_argument("--thumbs", type=Path, help="縮圖資料夾（<雜湊>.jpg），匯入時上傳")
    parser.add_argument("--voices", type=Path, help="語音資料夾（<voice_file>.mp3），匯入時只上傳台詞用到的檔案")
    parser.add_argument("--bgm", type=Path, help="BGM 資料夾（<code>.ogg 或 .mp3），匯入時只上傳 game_bgm_tracks 有的代號")
    parser.add_argument("--enable-bgm-audio", action="store_true",
                        help="在設定打開 BGM 播放（capabilities.bgm）；會改變 source_hash，須正常匯入成新版本")
    parser.add_argument("--database", help="語料庫名稱（預設 backend/.env 的 MYSQL_DATABASE；"
                                           "語料庫切換前可指定 fluencytides_corpus）")
    parser.add_argument("--note", help="這個版本的備註")
    args = parser.parse_args()
    conn = corpus_db.connect(args.database)
    try:
        if args.voices and not args.voices.is_dir():
            raise SystemExit(f"語音資料夾不存在：{args.voices}")
        if args.bgm and not args.bgm.is_dir():
            raise SystemExit(f"BGM 資料夾不存在：{args.bgm}")
        summary = extract(conn, args.out, args.template, args.thumbs, args.note, voices=args.voices,
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
        for w in summary["warnings"][:30]:
            print("  - " + w)


if __name__ == "__main__":
    main()
