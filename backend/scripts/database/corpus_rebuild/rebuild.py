"""重建語料庫 fluencytides_corpus（docs/wip/corpus_db_rebuild_FEAT_2026-10-31.md D7）。

Rebuild the corpus database from the legacy ``yuzusoft`` DB plus the game
anchor / calibration files.

- 對 ``yuzusoft`` 只做 SELECT；
- 會刪掉目標資料庫的所有表再從頭建立，所以**只能對空的資料庫執行**。目標資料庫只要有這支腳本
  還原不了的資料就拒絕（``data_stores_AUDIT_2026-11-01.md`` §4「紀錄表任何腳本都不得清空」）：
  紀錄表（``PROTECTED_TABLES``：卡片紀錄、關係、修訂紀錄、詞彙索引、讀音判定、流程圖建置）有任何一列，
  或 ``game_profiles`` 有其他遊戲（例如另外匯入的千戀＊萬花）。檢查的是資料庫本身，不看是哪一台機器、
  哪一份 ``.env``；沒有略過的選項。要在測試用的資料庫重跑，先自己刪掉那個資料庫再建一個空的。
- 另外保留原本的第一道檢查：``.env`` 的 ``MYSQL_DATABASE`` 已是目標資料庫（已切換）時直接拒絕。

Read-only on ``yuzusoft``; drops and recreates every table in the target DB,
so it only runs against an empty database: it refuses whenever the target
holds data it cannot recreate (any row in the record tables, or another
game's profile), whatever machine or ``.env`` it runs from.

用法 / usage（在 backend/ 底下）::

    python -m scripts.database.corpus_rebuild.rebuild --dry-run
    python -m scripts.database.corpus_rebuild.rebuild
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_backend_dir = Path(__file__).resolve().parents[3]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))
import scripts.common.env  # noqa: E402,F401  載入 .env / load .env

import pymysql  # noqa: E402

from app.core.config import settings  # noqa: E402
from scripts.database.corpus_rebuild import logs  # noqa: E402
from scripts.database.corpus_rebuild.content import GAME_PROFILE, Content, LegacyRow, build  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_ANCHORS = Path(r"C:/Users/forip/Desktop/WorkSpace/game/SW_point_table.json")
DEFAULT_CALIB = Path(r"C:/Users/forip/Desktop/WorkSpace/game/SW_calib")
# 依外鍵相反的順序刪表 / drop order (reverse FK order)
DROP_ORDER = (
    "story_node_lines", "story_builds", "rel_links", "rel_types", "rel_entity_types",
    "card_gen_log", "nlp_line_readings", "nlp_line_terms", "game_line_revisions", "game_line_bgm",
    "game_bgm_tracks", "game_lines", "game_speakers", "game_scene_links", "game_scenes",
    "game_script_files", "game_legacy_scripts", "game_profiles",
)


#: 不能被清掉的表：切換後只存在新庫、持續有寫入，或由別的流程產生（data_stores_AUDIT §4）。
#: Tables that must never be wiped once they hold rows.
PROTECTED_TABLES = (
    "card_gen_log", "rel_links", "game_line_revisions", "nlp_line_terms", "nlp_line_readings", "story_builds",
)


def unrecoverable_data(cur, database: str) -> dict[str, int]:
    """目標資料庫裡，重建會刪掉、而這支腳本還原不了的資料：表（或說明）→ 列數；沒有時為空 dict。

    Data in the target DB that a rebuild would destroy and cannot recreate.

    Args:
        cur: 目標資料庫的 cursor。A cursor on the target DB.
        database: 目標資料庫名稱。The target database name.

    Returns:
        ``{表名: 列數}``；另有 ``game_profiles（其他遊戲）``。Table to row count.
    """
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = %s", (database,))
    existing = {row[0] for row in cur.fetchall()}
    found: dict[str, int] = {}
    for table in PROTECTED_TABLES:
        if table not in existing:
            continue
        cur.execute(f"SELECT COUNT(*) FROM `{table}`")
        count = cur.fetchone()[0]
        if count:
            found[table] = count
    if "game_profiles" in existing:
        cur.execute("SELECT COUNT(*) FROM `game_profiles` WHERE game_id <> %s", (GAME_PROFILE["game_id"],))
        count = cur.fetchone()[0]
        if count:
            found["game_profiles（其他遊戲）"] = count
    return found


def refuse_if_unrecoverable(cur, database: str) -> None:
    """目標資料庫有還原不了的資料時結束程式，什麼都不刪。Exit before touching a DB that holds such data.

    Raises:
        SystemExit: 有紀錄表資料或其他遊戲。When the target holds unrecoverable data.
    """
    found = unrecoverable_data(cur, database)
    if found:
        detail = "、".join(f"{name} {count} 列" for name, count in found.items())
        sys.exit(
            f"❌ {database} 有重建會清掉、而且還原不了的資料（{detail}），拒絕執行。"
            "rebuild 只能對空的資料庫執行；要在測試用的資料庫重跑，請先自己刪掉並重建那個資料庫。"
        )


def connect(database: str) -> pymysql.connections.Connection:
    """以 .env 的帳號連線到指定資料庫。Connect with the .env credentials."""
    return pymysql.connect(host=settings.MYSQL_HOST, port=settings.MYSQL_PORT, user=settings.MYSQL_USER,
                           password=settings.MYSQL_PASSWORD, database=database, charset="utf8mb4",
                           autocommit=False)


def load_legacy(old_db: str) -> list[LegacyRow]:
    """讀舊劇本。Read the legacy script rows."""
    conn = connect(old_db)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, filename, chapter, role_name, dialogue, audio_file FROM scripts ORDER BY id")
            return [LegacyRow(*r) for r in cur.fetchall()]
    finally:
        conn.close()


def _json(v):
    return None if v is None else json.dumps(v, ensure_ascii=False)


def _insert(cur, table: str, rows: list[dict], cols: list[str], json_cols: tuple[str, ...] = ()) -> None:
    if not rows:
        return
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})"
    data = [tuple(_json(r[c]) if c in json_cols else r[c] for c in cols) for r in rows]
    for k in range(0, len(data), 2000):
        cur.executemany(sql, data[k:k + 2000])


def write(c: Content, old_db: str, new_db: str) -> None:
    """建表並寫入所有資料。Create tables and load everything."""
    conn = connect(new_db)
    try:
        with conn.cursor() as cur:
            # 刪表前在同一個連線再檢查一次：不論從哪裡呼叫 write，都不會清掉紀錄表
            refuse_if_unrecoverable(cur, new_db)
            cur.execute("SET FOREIGN_KEY_CHECKS=0")
            for t in DROP_ORDER:
                cur.execute(f"DROP TABLE IF EXISTS {t}")
            cur.execute("SET FOREIGN_KEY_CHECKS=1")
            for stmt in _statements((HERE / "schema.sql").read_text(encoding="utf-8")):
                cur.execute(stmt)
            # 舊劇本原樣封存 / verbatim archive
            cur.execute(f"CREATE TABLE game_legacy_scripts LIKE `{old_db}`.scripts")
            cur.execute(f"INSERT INTO game_legacy_scripts SELECT * FROM `{old_db}`.scripts")

            _insert(cur, "game_profiles", [GAME_PROFILE], list(GAME_PROFILE), ("languages",))
            _insert(cur, "game_script_files", c.files, list(c.files[0]))
            _insert(cur, "game_scenes", c.scenes, list(c.scenes[0]))
            _insert(cur, "game_speakers", c.speakers, list(c.speakers[0]))
            link_cols = list(c.links[0])
            _insert(cur, "game_scene_links", c.links, link_cols, ("condition_vars", "effects"))
            line_cols = ["id", "game_id", "scene_id", "seq", "story_order", "kind", "point", "chapter_label",
                         "speaker_id", "text_ja_jp", "text_zh_hant", "ruby_ja_jp", "voice_file", "is_h", "h_label",
                         "thumb", "bgm_detect", "bgm_may_change", "attrs", "status", "in_corpus", "origin",
                         "anchor_method", "anchor_verified_at"]
            _insert(cur, "game_lines", c.lines, line_cols, ("attrs",))
            cur.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM game_lines")
            cur.execute(f"ALTER TABLE game_lines AUTO_INCREMENT = {cur.fetchone()[0]}")
            _insert(cur, "game_bgm_tracks", c.tracks, list(c.tracks[0]))
            cur.executemany("INSERT INTO game_line_bgm (line_id, ord, track_id) VALUES (%s, %s, %s)", c.line_bgm)
            _insert(cur, "game_line_revisions", c.revisions,
                    ["line_id", "field", "old_value", "new_value", "reason", "evidence"])

            logs.migrate(cur, old_db)
        conn.commit()
    except BaseException:
        # 含 SystemExit（拒絕執行）：什麼都沒刪，照樣收掉交易
        conn.rollback()
        raise
    finally:
        conn.close()


def _statements(sql: str) -> list[str]:
    body = "\n".join(x for x in sql.splitlines() if not x.strip().startswith("--"))
    return [s.strip() for s in body.split(";") if s.strip()]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--anchors", type=Path, default=DEFAULT_ANCHORS)
    ap.add_argument("--calib", type=Path, default=DEFAULT_CALIB)
    ap.add_argument("--old", default="yuzusoft")
    ap.add_argument("--new", default="fluencytides_corpus")
    ap.add_argument("--dry-run", action="store_true", help="只輸出報表，不寫資料庫 / report only")
    args = ap.parse_args()
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    if settings.MYSQL_DATABASE == args.new and not args.dry_run:
        sys.exit(f"❌ .env 的 MYSQL_DATABASE 已是 {args.new}（已切換），rebuild 會刪掉卡片紀錄，拒絕執行。")
    if not args.dry_run:
        # 先查目標資料庫再做耗時的對齊：有還原不了的資料就不必往下跑（write 刪表前還會再查一次）
        conn = connect(args.new)
        try:
            with conn.cursor() as cur:
                refuse_if_unrecoverable(cur, args.new)
        finally:
            conn.close()

    t0 = time.time()
    anchors = json.loads(args.anchors.read_text(encoding="utf-8"))
    titles = json.loads((args.calib / "bgm_titles" / "file_titles.json").read_text(encoding="utf-8-sig"))
    legacy = load_legacy(args.old)
    content = build(anchors, legacy, args.calib, titles)

    out = HERE / "report"
    out.mkdir(exist_ok=True)
    (out / "rebuild_report.json").write_text(json.dumps(content.report, ensure_ascii=False, indent=1),
                                             encoding="utf-8")
    rep = {k: v for k, v in content.report.items() if k != "low_confidence"}
    print(json.dumps(rep, ensure_ascii=False, indent=1, default=dict))
    print(f"low_confidence: {len(content.report['low_confidence'])} 句（詳見 report/rebuild_report.json）")
    print(f"lines={len(content.lines)} files={len(content.files)} scenes={len(content.scenes)} "
          f"speakers={len(content.speakers)} links={len(content.links)} line_bgm={len(content.line_bgm)} "
          f"revisions={len(content.revisions)}  ({time.time() - t0:.0f}s)")
    if args.dry_run:
        return
    write(content, args.old, args.new)
    print(f"✅ 已寫入 {args.new}（{time.time() - t0:.0f}s）")


if __name__ == "__main__":
    main()
