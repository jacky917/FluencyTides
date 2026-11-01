"""通用的劇情流程圖匯入：驗證 → 差異摘要 → 寫入（importing → ready）→ 原子切換 active。

Generic story-build importer: validate, diff summary, write
(importing → ready), then atomically activate
(docs/wip/story_flowchart_FEAT_2026-10-31.md D2 版本生命週期、§7).

可以重跑：同一份內容（``source_hash`` 相同）已經匯入過就直接略過；語料庫重建（drop 再建表）後
重跑也會重新匯入。素材（存檔樣板、縮圖）上傳到 MinIO，或用 ``--assets local:<資料夾>`` 寫到本機
資料夾（後端以 ``STORY_ASSETS_DIR`` 指向同一個資料夾即可讀到）。

Re-runnable: an identical package is skipped. Assets go to MinIO or, with
``--assets local:<dir>``, to a local folder served via ``STORY_ASSETS_DIR``.

用法 / Usage::

    python backend/scripts/story/import_story_build.py build/sabbat_of_the_witch --dry-run
    python backend/scripts/story/import_story_build.py build/sabbat_of_the_witch
    python backend/scripts/story/import_story_build.py build/sabbat_of_the_witch --assets local:D:/story-assets
    python backend/scripts/story/import_story_build.py --game SabbatOfTheWitch --list
    python backend/scripts/story/import_story_build.py --game SabbatOfTheWitch --rollback 3

資料庫預設是 ``backend/.env`` 的 ``MYSQL_DATABASE``（和後端相同）；語料庫切換前要對新庫跑時加
``--database fluencytides_corpus``。
The database defaults to ``MYSQL_DATABASE``; use ``--database`` before cutover.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sys
import traceback
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Protocol

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.story.common import corpus_db  # noqa: E402
from scripts.story.common.package import BuildPackage, read_package, validate_package  # noqa: E402

from app.schemas.story import StoryGraph  # noqa: E402

#: 每款遊戲保留幾個 retired 版本（加上 1 個 active）。Retired builds kept per game.
KEEP_RETIRED = 3
INSERT_CHUNK = 2000
#: 縮圖缺檔時列出幾個雜湊當例子。How many missing thumbnail hashes to print.
MISSING_THUMB_EXAMPLES = 10


# ============================================================================
# 資料庫
# ============================================================================


class BuildStore(Protocol):
    """匯入需要的資料庫操作（測試用記憶體版本替換）。DB operations used by the importer."""

    def game_exists(self, game_id: str) -> bool: ...
    def game_line_ids(self, game_id: str) -> set[int]: ...
    def game_thumbs(self, game_id: str) -> set[str]: ...
    def find_by_hash(self, game_id: str, source_hash: str) -> dict | None: ...
    def active_build(self, game_id: str) -> dict | None: ...
    def list_builds(self, game_id: str) -> list[dict]: ...
    def insert_build(self, row: dict) -> int: ...
    def insert_node_lines(self, build_id: int, rows: list[tuple[str, int, int]]) -> None: ...
    def count_node_lines(self, build_id: int) -> int: ...
    def count_foreign_lines(self, build_id: int, game_id: str) -> int: ...
    def set_status(self, build_id: int, status: str) -> None: ...
    def delete_build(self, build_id: int) -> None: ...
    def activate(self, game_id: str, build_id: int) -> None: ...


class MySQLBuildStore:
    """以 pymysql 操作 ``story_builds``／``story_node_lines``。pymysql implementation."""

    def __init__(self, conn) -> None:
        self.conn = conn

    def _one(self, sql: str, args: Iterable[Any] = ()) -> dict | None:
        with self.conn.cursor() as cur:
            cur.execute(sql, tuple(args))
            return cur.fetchone()

    def _all(self, sql: str, args: Iterable[Any] = ()) -> list[dict]:
        with self.conn.cursor() as cur:
            cur.execute(sql, tuple(args))
            return list(cur.fetchall())

    def game_exists(self, game_id: str) -> bool:
        return self._one("SELECT 1 AS ok FROM game_profiles WHERE game_id=%s", (game_id,)) is not None

    def game_line_ids(self, game_id: str) -> set[int]:
        rows = self._all("SELECT id FROM game_lines WHERE game_id=%s AND status='active'", (game_id,))
        return {int(r["id"]) for r in rows}

    def game_thumbs(self, game_id: str) -> set[str]:
        rows = self._all("SELECT DISTINCT thumb FROM game_lines WHERE game_id=%s AND thumb IS NOT NULL", (game_id,))
        return {r["thumb"] for r in rows}

    def find_by_hash(self, game_id: str, source_hash: str) -> dict | None:
        return self._one("SELECT build_id, status FROM story_builds WHERE game_id=%s AND source_hash=%s",
                         (game_id, source_hash))

    def active_build(self, game_id: str) -> dict | None:
        row = self._one("SELECT build_id, graph FROM story_builds WHERE game_id=%s AND status='active'", (game_id,))
        if row and isinstance(row["graph"], (str, bytes)):
            row["graph"] = json.loads(row["graph"])
        return row

    def list_builds(self, game_id: str) -> list[dict]:
        return self._all("SELECT build_id, status, extractor, source_hash, note, created_at, activated_at, "
                         "JSON_EXTRACT(stats, '$.nodes') AS nodes, JSON_EXTRACT(stats, '$.lines') AS line_count "
                         "FROM story_builds WHERE game_id=%s ORDER BY build_id", (game_id,))

    def insert_build(self, row: dict) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO story_builds (game_id, status, schema_version, extractor, source_hash, config, graph, "
                "stats, note) VALUES (%s, 'importing', %s, %s, %s, %s, %s, %s, %s)",
                (row["game_id"], row["schema_version"], row["extractor"], row["source_hash"],
                 json.dumps(row["config"], ensure_ascii=False), json.dumps(row["graph"], ensure_ascii=False),
                 json.dumps(row["stats"], ensure_ascii=False), row.get("note")))
            build_id = cur.lastrowid
        self.conn.commit()
        return int(build_id)

    def insert_node_lines(self, build_id: int, rows: list[tuple[str, int, int]]) -> None:
        with self.conn.cursor() as cur:
            for i in range(0, len(rows), INSERT_CHUNK):
                cur.executemany(
                    "INSERT INTO story_node_lines (build_id, node_key, seq, line_id) VALUES (%s, %s, %s, %s)",
                    [(build_id, k, s, lid) for k, s, lid in rows[i:i + INSERT_CHUNK]])
        self.conn.commit()

    def count_node_lines(self, build_id: int) -> int:
        return int(self._one("SELECT COUNT(*) AS n FROM story_node_lines WHERE build_id=%s", (build_id,))["n"])

    def count_foreign_lines(self, build_id: int, game_id: str) -> int:
        row = self._one("SELECT COUNT(*) AS n FROM story_node_lines n JOIN game_lines l ON l.id = n.line_id "
                        "WHERE n.build_id=%s AND l.game_id <> %s", (build_id, game_id))
        return int(row["n"])

    def set_status(self, build_id: int, status: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute("UPDATE story_builds SET status=%s WHERE build_id=%s", (status, build_id))
        self.conn.commit()

    def delete_build(self, build_id: int) -> None:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM story_node_lines WHERE build_id=%s", (build_id,))
            cur.execute("DELETE FROM story_builds WHERE build_id=%s", (build_id,))
        self.conn.commit()

    def activate(self, game_id: str, build_id: int) -> None:
        """一個交易內：舊 active → retired，新的 → active。Swap active builds in one transaction."""
        try:
            with self.conn.cursor() as cur:
                cur.execute("SELECT build_id, status FROM story_builds WHERE game_id=%s FOR UPDATE", (game_id,))
                rows = {r["build_id"]: r["status"] for r in cur.fetchall()}
                if rows.get(build_id) not in ("ready", "retired", "active"):
                    raise RuntimeError(f"版本 {build_id} 的狀態是 {rows.get(build_id)}，不能上線")
                cur.execute("UPDATE story_builds SET status='retired' WHERE game_id=%s AND status='active' "
                            "AND build_id<>%s", (game_id, build_id))
                cur.execute("UPDATE story_builds SET status='active', activated_at=NOW() WHERE build_id=%s",
                            (build_id,))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise


# ============================================================================
# 素材
# ============================================================================


class AssetSink(Protocol):
    """素材的寫入目的地。Where assets are written."""

    def existing(self, prefix: str) -> set[str]: ...
    def put(self, key: str, src: Path) -> None: ...
    def put_bytes(self, key: str, data: bytes, content_type: str) -> None: ...


class LocalAssetSink:
    """寫到本機資料夾（物件鍵即相對路徑）；縮圖優先用硬連結。Local-folder sink."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def existing(self, prefix: str) -> set[str]:
        base = self.root / prefix
        if not base.exists():
            return set()
        return {prefix + p.name for p in base.iterdir() if p.is_file()}

    def put(self, key: str, src: Path) -> None:
        dst = self.root / key
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            dst.unlink()
        try:
            os.link(src, dst)
        except OSError:
            shutil.copyfile(src, dst)

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        dst = self.root / key
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)


class MinioAssetSink:
    """寫到 MinIO（設定取自 ``backend/.env`` 的 ``MINIO_*``，環境變數優先）。MinIO sink.

    缺任何一個必要設定就直接結束，不退回 ``127.0.0.1:9000``／``minioadmin``：寫到錯的 MinIO 比
    報錯更難發現。
    Missing settings abort instead of falling back to local defaults.
    """

    REQUIRED = ("MINIO_HOST", "MINIO_PORT", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MINIO_DEFAULT_BUCKET")

    def __init__(self) -> None:
        from dotenv import dotenv_values
        from minio import Minio

        env = {**dotenv_values(corpus_db.BACKEND_DIR / ".env"), **os.environ}
        missing = [k for k in self.REQUIRED if not env.get(k)]
        if missing:
            raise SystemExit(f"--assets minio 需要 backend/.env 或環境變數設定 {', '.join(missing)}；"
                             "本機測試可改用 --assets local:<資料夾>")
        self.bucket = env["MINIO_DEFAULT_BUCKET"]
        self.client = Minio(f"{env['MINIO_HOST']}:{env['MINIO_PORT']}",
                            access_key=env["MINIO_ACCESS_KEY"], secret_key=env["MINIO_SECRET_KEY"],
                            secure=str(env.get("MINIO_SECURE") or "False").lower() == "true")
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)

    def existing(self, prefix: str) -> set[str]:
        return {o.object_name for o in self.client.list_objects(self.bucket, prefix=prefix, recursive=True)}

    def put(self, key: str, src: Path) -> None:
        content_type = "image/jpeg" if src.suffix.lower() in (".jpg", ".jpeg") else "application/octet-stream"
        self.client.fput_object(self.bucket, key, str(src), content_type=content_type)

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(self.bucket, key, io.BytesIO(data), len(data), content_type=content_type)


def make_sink(spec: str) -> AssetSink | None:
    """``minio``、``local:<資料夾>`` 或 ``none``。Build a sink from the CLI spec."""
    if spec == "none":
        return None
    if spec == "minio":
        return MinioAssetSink()
    if spec.startswith("local:"):
        return LocalAssetSink(Path(spec[len("local:"):]))
    raise SystemExit(f"不認得的 --assets：{spec}")


def upload_assets(pkg: BuildPackage, sink: AssetSink, thumbs: set[str], log=print) -> dict[str, int]:
    """上傳樣板（一律覆寫）與縮圖（已存在就跳過）。Upload templates and missing thumbs.

    縮圖資料夾裡找不到的雜湊算進 ``thumbs_missing``，並印出警告與前幾個例子。
    Thumbnails absent from the source folder are counted and reported.
    """
    stats = {"templates": 0, "thumbs_uploaded": 0, "thumbs_missing": 0, "thumbs_skipped": 0}
    missing: list[str] = []
    for key, path in pkg.template_files().items():
        sink.put(key, path)
        stats["templates"] += 1
    thumbs_dir = pkg.thumbs_dir()
    prefix = pkg.manifest.assets.get("thumbs_prefix") or f"story/{pkg.manifest.game_id}/thumbs/"
    if thumbs_dir and thumbs:
        have = sink.existing(prefix)
        for i, h in enumerate(sorted(thumbs)):
            key = f"{prefix}{h}.jpg"
            if key in have:
                stats["thumbs_skipped"] += 1
                continue
            src = thumbs_dir / f"{h}.jpg"
            if not src.exists():
                stats["thumbs_missing"] += 1
                missing.append(h)
                continue
            sink.put(key, src)
            stats["thumbs_uploaded"] += 1
            if stats["thumbs_uploaded"] % 2000 == 0:
                log(f"  縮圖已上傳 {stats['thumbs_uploaded']}（{i + 1}/{len(thumbs)}）")
    if missing:
        log(f"⚠ 縮圖資料夾 {thumbs_dir} 缺 {len(missing)} 張（抽屜與存檔會沒有縮圖），例："
            f"{', '.join(missing[:MISSING_THUMB_EXAMPLES])}")
    return stats


def package_archive(pkg: BuildPackage) -> bytes:
    """把建置包壓成 zip（保存原檔用）。Zip the package for safekeeping."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(pkg.root.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(pkg.root).as_posix())
    return buf.getvalue()


# ============================================================================
# 差異摘要
# ============================================================================


@dataclass
class DiffSummary:
    """與目前上線版本的差異。Differences against the active build."""

    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    renamed: list[tuple[str, str | None, str | None]] = field(default_factory=list)
    line_count_changed: list[tuple[str, int, int]] = field(default_factory=list)

    def lines(self) -> list[str]:
        out = [f"新增節點 {len(self.added)}、刪除 {len(self.removed)}、改名 {len(self.renamed)}、"
               f"句數改變 {len(self.line_count_changed)}"]
        out += [f"  + {k}" for k in self.added[:50]]
        out += [f"  - {k}" for k in self.removed[:50]]
        out += [f"  ~ {k}：{a} → {b}" for k, a, b in self.renamed[:50]]
        out += [f"  # {k}：{a} → {b} 句" for k, a, b in self.line_count_changed[:50]]
        return out


def diff_graphs(old: dict | None, new: StoryGraph) -> DiffSummary:
    """比較兩份劇情圖的節點。Compare node sets of two graphs."""
    summary = DiffSummary()
    new_nodes = {n.key: n for n in new.nodes}
    if old is None:
        summary.added = list(new_nodes)
        return summary
    old_nodes = {n["key"]: n for n in old.get("nodes", [])}
    summary.added = [k for k in new_nodes if k not in old_nodes]
    summary.removed = [k for k in old_nodes if k not in new_nodes]
    for k, n in new_nodes.items():
        o = old_nodes.get(k)
        if o is None:
            continue
        if o.get("title") != n.title:
            summary.renamed.append((k, o.get("title"), n.title))
        if int(o.get("line_count", 0)) != n.line_count:
            summary.line_count_changed.append((k, int(o.get("line_count", 0)), n.line_count))
    return summary


# ============================================================================
# 匯入
# ============================================================================


@dataclass
class ImportResult:
    """匯入結果。Import outcome."""

    status: str  # invalid / skipped / dry_run / failed / imported
    build_id: int | None = None
    errors: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)


def import_package(
    pkg: BuildPackage,
    store: BuildStore,
    sink: AssetSink | None = None,
    *,
    dry_run: bool = False,
    activate: bool = True,
    note: str | None = None,
    log=print,
) -> ImportResult:
    """驗證並匯入一個建置包。Validate and import one build package.

    Args:
        pkg: 建置包。The package.
        store: 資料庫操作。DB operations.
        sink: 素材目的地；None 不上傳。Asset sink, or None.
        dry_run: 只驗證與輸出差異摘要。Validate and diff only.
        activate: 寫入後切換上線。Activate after writing.
        note: 版本備註（覆寫 manifest 的 note）。Build note.
        log: 輸出函式。Logger.
    """
    game_id = pkg.manifest.game_id
    errors = validate_package(pkg)
    if not store.game_exists(game_id):
        errors.append(f"game_profiles 沒有 {game_id}")
    if errors:
        return ImportResult("invalid", errors=errors)

    game_lines = store.game_line_ids(game_id)
    foreign = [r.line_id for r in pkg.rows if r.line_id not in game_lines]
    if foreign:
        return ImportResult("invalid", errors=[f"{len(foreign)} 句不屬於 {game_id}（或已停用），例：{foreign[:10]}"])
    assigned = {r.line_id for r in pkg.rows}
    unassigned = sorted(game_lines - assigned)
    messages = [f"建置包：{len(pkg.graph.nodes)} 節點、{len(pkg.graph.edges)} 邊、{len(pkg.rows)} 句；"
                f"source_hash={pkg.source_hash[:12]}…"]
    if unassigned:
        messages.append(f"沒有歸屬的台詞 {len(unassigned)} 句，例：{unassigned[:20]}")

    existing = store.find_by_hash(game_id, pkg.source_hash)
    active = store.active_build(game_id)
    messages += diff_graphs(active["graph"] if active else None, pkg.graph).lines()
    for m in messages:
        log(m)
    if existing:
        msg = f"同一份內容已匯入為版本 {existing['build_id']}（{existing['status']}），略過"
        if existing["status"] != "active":
            msg += f"；要改用它請執行 --rollback {existing['build_id']}"
        log(msg)
        return ImportResult("skipped", build_id=existing["build_id"], messages=messages + [msg])
    if dry_run:
        log("--dry-run：不寫入")
        return ImportResult("dry_run", messages=messages)

    if sink is not None:
        stats = upload_assets(pkg, sink, store.game_thumbs(game_id) if pkg.thumbs_dir() else set(), log)
        log(f"素材：{stats}")

    build_id = store.insert_build({
        "game_id": game_id, "schema_version": pkg.manifest.schema_version, "extractor": pkg.manifest.extractor,
        "source_hash": pkg.source_hash, "config": pkg.manifest.config, "graph": pkg.graph.to_json_dict(),
        "stats": pkg.manifest.stats or {}, "note": note or pkg.manifest.note,
    })
    try:
        store.insert_node_lines(build_id, [(r.node_key, r.seq, r.line_id) for r in pkg.rows])
        post_errors = []
        if store.count_node_lines(build_id) != len(pkg.rows):
            post_errors.append("寫入的列數和建置包不一致")
        if store.count_foreign_lines(build_id, game_id):
            post_errors.append("有台詞不屬於這款遊戲")
        if post_errors:
            return ImportResult("failed", errors=post_errors + _cleanup(store, build_id, log), messages=messages)
    except Exception as exc:
        log(f"✗ 版本 {build_id} 寫入失敗：\n{traceback.format_exc()}")
        return ImportResult("failed", errors=[f"寫入失敗：{exc}", *_cleanup(store, build_id, log)],
                            messages=messages)
    store.set_status(build_id, "ready")
    log(f"版本 {build_id} 寫入完成（ready）")

    if sink is not None:
        try:
            sink.put_bytes(f"story/{game_id}/builds/{build_id}/package.zip", package_archive(pkg), "application/zip")
        except Exception as exc:  # noqa: BLE001 - 保存原檔失敗不影響上線
            log(f"⚠ 保存建置包原檔失敗：{exc}\n{traceback.format_exc()}")
    if activate:
        store.activate(game_id, build_id)
        log(f"版本 {build_id} 已上線（active）")
        prune_retired(store, game_id, log=log)
    return ImportResult("imported", build_id=build_id, messages=messages)


def _cleanup(store: BuildStore, build_id: int, log=print) -> list[str]:
    """刪掉寫到一半的版本；刪除也失敗時回傳錯誤訊息（留下 ``importing`` 的孤兒列）。

    Remove a half-written build. If that fails too, report it loudly: an
    orphan ``importing`` row is left behind and must be deleted by hand.
    """
    try:
        store.delete_build(build_id)
        return []
    except Exception as exc:  # noqa: BLE001 - 清理失敗要明講，不能蓋掉原本的錯誤
        log(f"✗ 清理版本 {build_id} 也失敗：\n{traceback.format_exc()}")
        return [f"清理也失敗（{exc}）：story_builds 留下 build_id={build_id} 的 importing 列與它的 "
                f"story_node_lines，請手動刪除（DELETE FROM story_node_lines WHERE build_id={build_id}; "
                f"DELETE FROM story_builds WHERE build_id={build_id};）"]


def prune_retired(store: BuildStore, game_id: str, keep: int = KEEP_RETIRED, log=print) -> list[int]:
    """只保留最新的 ``keep`` 個 retired 版本。Keep only the newest retired builds."""
    retired = [b["build_id"] for b in store.list_builds(game_id) if b["status"] == "retired"]
    drop = sorted(retired)[:-keep] if keep else sorted(retired)
    for build_id in drop:
        store.delete_build(build_id)
        log(f"刪除舊版本 {build_id}")
    return drop


def rollback(store: BuildStore, game_id: str, build_id: int, log=print) -> None:
    """把某個 retired（或 ready）版本重新上線。Re-activate a retired or ready build."""
    builds = {b["build_id"]: b for b in store.list_builds(game_id)}
    if build_id not in builds:
        raise SystemExit(f"{game_id} 沒有版本 {build_id}")
    if builds[build_id]["status"] not in ("retired", "ready"):
        raise SystemExit(f"版本 {build_id} 的狀態是 {builds[build_id]['status']}，只能回滾到 retired／ready")
    store.activate(game_id, build_id)
    log(f"版本 {build_id} 已重新上線")


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="劇情流程圖建置包匯入（驗證 → 寫入 → 原子切換）")
    parser.add_argument("package", nargs="?", type=Path, help="建置包資料夾")
    parser.add_argument("--dry-run", action="store_true", help="只驗證並輸出差異摘要")
    parser.add_argument("--no-activate", action="store_true", help="寫入成 ready，不切換上線")
    parser.add_argument("--note", help="版本備註")
    parser.add_argument("--assets", default="minio", help="素材目的地：minio（預設）、local:<資料夾>、none")
    parser.add_argument("--database", help="語料庫名稱（預設 backend/.env 的 MYSQL_DATABASE；"
                                           "語料庫切換前可指定 fluencytides_corpus）")
    parser.add_argument("--game", help="--list／--rollback 用的 game_id")
    parser.add_argument("--list", action="store_true", help="列出版本")
    parser.add_argument("--rollback", type=int, metavar="BUILD_ID",
                        help="把 retired 或 ready（--no-activate 寫入的）版本上線")
    args = parser.parse_args()

    conn = corpus_db.connect(args.database)
    store = MySQLBuildStore(conn)
    try:
        if args.list or args.rollback:
            game = args.game or (read_package(args.package).manifest.game_id if args.package else None)
            if not game:
                raise SystemExit("需要 --game 或建置包")
            if args.rollback:
                rollback(store, game, args.rollback)
            for b in store.list_builds(game):
                print(f"  {b['build_id']:>4} {b['status']:<9} {b['extractor']} {b['source_hash'][:12]} "
                      f"nodes={b['nodes']} lines={b['line_count']} created={b['created_at']} "
                      f"activated={b['activated_at']} {b['note'] or ''}")
            return
        if not args.package:
            raise SystemExit("需要建置包資料夾")
        pkg = read_package(args.package)
        sink = None if args.dry_run else make_sink(args.assets)
        result = import_package(pkg, store, sink, dry_run=args.dry_run, activate=not args.no_activate,
                                note=args.note)
        for e in result.errors:
            print("✗ " + e)
        print(f"結果：{result.status}" + (f"（版本 {result.build_id}）" if result.build_id else ""))
        if result.status in ("invalid", "failed"):
            sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
