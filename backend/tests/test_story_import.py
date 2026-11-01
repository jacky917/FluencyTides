"""建置包寫出、讀入與匯入流程的測試（劇情流程圖 D2 版本生命週期）。

Tests for the build package and the import flow against an in-memory
store: validation failures never create a build, atomic activation,
rollback, duplicate-content skip, pruning, local asset upload (templates,
thumbnails, voices), ``--assets-only`` for the active build, config
validation with the game adapter's model, and loud cleanup failures.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import story_fixtures as F  # noqa: E402

from app.schemas.story import BuildManifest  # noqa: E402
from scripts.story.common.package import read_package, validate_package, write_package  # noqa: E402
from scripts.story.common.scene_graph import build_story_graph  # noqa: E402
from scripts.story.import_story_build import (  # noqa: E402
    LocalAssetSink,
    MinioAssetSink,
    diff_graphs,
    import_package,
    prune_retired,
    refresh_assets,
    rollback,
    upload_assets,
)

GAME = "SabbatOfTheWitch"
CONFIG = {
    "languages": [{"code": "ja-JP", "label": "日本語"}],
    "annotations": {"cards": {"card_types": [{"card_type": "jp_core_verb", "label": "核心動詞"}]}},
    "saves": {"template": f"story/{GAME}/templates/quick.jpg",
              "slots": [{"id": "quick", "label": "快速存檔", "filename": "data_quick_01.jpg"}]},
    "route_presets": {"nene": {"nen_flag": 4}},
}


class MemoryStore:
    """記憶體裡的 story_builds／story_node_lines。In-memory build store."""

    def __init__(self, line_ids, thumbs=(), voices=()):
        self.lines = set(line_ids)
        self.thumbs = set(thumbs)
        self.voices = set(voices)
        self.builds: dict[int, dict] = {}
        self.node_lines: dict[int, list] = {}
        self.fail_insert = False
        self.fail_delete = False
        self.next_id = 1

    def game_exists(self, game_id):
        return game_id == GAME

    def game_line_ids(self, game_id):
        return set(self.lines)

    def game_thumbs(self, game_id):
        return set(self.thumbs)

    def game_voices(self, game_id):
        return set(self.voices)

    def find_by_hash(self, game_id, source_hash):
        for b in self.builds.values():
            if b["source_hash"] == source_hash:
                return {"build_id": b["build_id"], "status": b["status"]}
        return None

    def active_build(self, game_id):
        for b in self.builds.values():
            if b["status"] == "active":
                return {"build_id": b["build_id"], "source_hash": b["source_hash"], "graph": b["graph"]}
        return None

    def list_builds(self, game_id):
        return [dict(b) for b in sorted(self.builds.values(), key=lambda b: b["build_id"])]

    def insert_build(self, row):
        build_id = self.next_id
        self.next_id += 1
        self.builds[build_id] = {**row, "build_id": build_id, "status": "importing"}
        return build_id

    def insert_node_lines(self, build_id, rows):
        if self.fail_insert:
            raise RuntimeError("disk full")
        self.node_lines[build_id] = list(rows)

    def count_node_lines(self, build_id):
        return len(self.node_lines.get(build_id, []))

    def count_foreign_lines(self, build_id, game_id):
        return sum(1 for _, _, lid in self.node_lines.get(build_id, []) if lid not in self.lines)

    def set_status(self, build_id, status):
        self.builds[build_id]["status"] = status

    def delete_build(self, build_id):
        if self.fail_delete:
            raise RuntimeError("connection lost")
        self.builds.pop(build_id, None)
        self.node_lines.pop(build_id, None)

    def activate(self, game_id, build_id):
        for b in self.builds.values():
            if b["status"] == "active" and b["build_id"] != build_id:
                b["status"] = "retired"
        self.builds[build_id]["status"] = "active"

    def status(self):
        return {k: v["status"] for k, v in self.builds.items()}


def _package(tmp_path: Path, name="pkg", titles=None, thumbs_dir=None, with_template=True, config=None,
             voices_dir=None):
    result = build_story_graph(*F.rows(), F.rules(titles=titles or {}))
    template = tmp_path / "quick.jpg"
    template.write_bytes(b"\xff\xd8template\xff\xd9")
    config = dict(config or CONFIG)
    if not with_template:
        config.pop("saves")
    manifest = BuildManifest(game_id=GAME, extractor="test@1", config=config, stats=result.stats,
                             assets={"thumbs_dir": str(thumbs_dir) if thumbs_dir else None,
                                     "thumbs_prefix": f"story/{GAME}/thumbs/",
                                     "voices_dir": str(voices_dir) if voices_dir else None,
                                     "voices_prefix": f"story/{GAME}/voices/"})
    write_package(tmp_path / name, manifest, result.graph, result.rows,
                  templates={CONFIG["saves"]["template"]: template} if with_template else None)
    return read_package(tmp_path / name)


def _all_lines():
    return [line.id for line in F.rows()[2]]


def test_package_round_trip_and_hash_stable(tmp_path):
    a = _package(tmp_path, "a")
    b = _package(tmp_path, "b")
    assert validate_package(a) == []
    assert a.source_hash == b.source_hash
    c = _package(tmp_path, "c", titles={"sw:001": "別名"})
    assert c.source_hash != a.source_hash


def test_validate_package_catches_missing_template_and_bad_presets(tmp_path):
    pkg = _package(tmp_path)
    (pkg.root / "assets/templates/quick.jpg").unlink()
    pkg.manifest.config["route_presets"] = {"nope": {"x": 1}}
    errors = validate_package(pkg)
    assert any("樣板檔不存在" in e for e in errors)
    assert any("nope" in e for e in errors)


def test_config_is_validated_with_the_game_adapter_model(tmp_path):
    """基底 StoryConfig 接受字串旗標，但 SabbatConfig（上線後 API 用的）不接受：匯入時就要擋下。"""
    pkg = _package(tmp_path, config={**CONFIG, "route_presets": {"nene": {"nen_flag": "4"}}})
    assert any("設定格式錯誤" in e for e in validate_package(pkg))
    store = MemoryStore(_all_lines())
    result = import_package(pkg, store, log=lambda *_: None)
    assert result.status == "invalid" and store.builds == {}


def test_failed_cleanup_is_reported(tmp_path):
    store = MemoryStore(_all_lines())
    store.fail_insert = store.fail_delete = True
    logs = []
    result = import_package(_package(tmp_path), store, log=logs.append)
    assert result.status == "failed"
    assert any("清理也失敗" in e and "importing" in e for e in result.errors)
    assert store.status() == {1: "importing"}  # 孤兒列確實還在，訊息才有意義
    assert any("Traceback" in m for m in logs)


def test_minio_sink_refuses_missing_settings(monkeypatch, tmp_path):
    from scripts.story.common import corpus_db

    monkeypatch.setattr(corpus_db, "BACKEND_DIR", tmp_path)  # 沒有 .env
    for key in MinioAssetSink.REQUIRED:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(SystemExit, match="MINIO_HOST"):
        MinioAssetSink()


def test_import_activates_and_skips_duplicates(tmp_path):
    store = MemoryStore(_all_lines())
    pkg = _package(tmp_path)
    result = import_package(pkg, store, log=lambda *_: None)
    assert result.status == "imported"
    assert store.status() == {1: "active"}
    assert len(store.node_lines[1]) == len(pkg.rows)
    again = import_package(pkg, store, log=lambda *_: None)
    assert again.status == "skipped" and again.build_id == 1
    assert store.status() == {1: "active"}


def test_dry_run_writes_nothing(tmp_path):
    store = MemoryStore(_all_lines())
    result = import_package(_package(tmp_path), store, dry_run=True, log=lambda *_: None)
    assert result.status == "dry_run" and store.builds == {}


def test_invalid_package_never_creates_a_build(tmp_path):
    store = MemoryStore(_all_lines()[:-1])  # 有一句不屬於這款遊戲
    result = import_package(_package(tmp_path), store, log=lambda *_: None)
    assert result.status == "invalid" and store.builds == {}


def test_failed_write_is_removed_and_active_unchanged(tmp_path):
    store = MemoryStore(_all_lines())
    import_package(_package(tmp_path, "a"), store, log=lambda *_: None)
    store.fail_insert = True
    result = import_package(_package(tmp_path, "b", titles={"sw:001": "x"}), store, log=lambda *_: None)
    assert result.status == "failed"
    assert store.status() == {1: "active"}


def test_new_build_retires_old_and_rollback_restores(tmp_path):
    store = MemoryStore(_all_lines())
    import_package(_package(tmp_path, "a"), store, log=lambda *_: None)
    import_package(_package(tmp_path, "b", titles={"sw:001": "x"}), store, log=lambda *_: None)
    assert store.status() == {1: "retired", 2: "active"}
    rollback(store, GAME, 1, log=lambda *_: None)
    assert store.status() == {1: "active", 2: "retired"}
    with pytest.raises(SystemExit):
        rollback(store, GAME, 1, log=lambda *_: None)


def test_no_activate_leaves_ready(tmp_path):
    store = MemoryStore(_all_lines())
    import_package(_package(tmp_path), store, activate=False, log=lambda *_: None)
    assert store.status() == {1: "ready"}


def test_prune_keeps_newest_retired(tmp_path):
    store = MemoryStore(_all_lines())
    for i in range(6):
        import_package(_package(tmp_path, f"p{i}", titles={"sw:001": f"t{i}"}), store, log=lambda *_: None)
    statuses = store.status()
    assert list(statuses.values()).count("active") == 1
    assert sorted(k for k, v in statuses.items() if v == "retired") == [3, 4, 5]
    assert prune_retired(store, GAME, keep=1, log=lambda *_: None) == [3, 4]


def test_diff_summary(tmp_path):
    pkg = _package(tmp_path)
    old = json.loads(json.dumps(pkg.graph.to_json_dict()))
    old["nodes"] = [n for n in old["nodes"] if n["key"] != "sw:101"] + [{"key": "sw:gone", "line_count": 0}]
    next(n for n in old["nodes"] if n["key"] == "sw:001")["title"] = "舊標題"
    diff = diff_graphs(old, pkg.graph)
    assert diff.added == ["sw:101"] and diff.removed == ["sw:gone"]
    assert diff.renamed[0][0] == "sw:001"


def test_local_assets_upload_templates_archive_and_missing_thumbs(tmp_path):
    thumbs = tmp_path / "thumbs"
    thumbs.mkdir()
    (thumbs / "aaaaaaaaaaaaaaaa.jpg").write_bytes(b"thumb")
    store = MemoryStore(_all_lines(), thumbs={"aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"})
    sink = LocalAssetSink(tmp_path / "assets")
    logs = []
    result = import_package(_package(tmp_path, thumbs_dir=thumbs), store, sink, log=logs.append)
    assert result.status == "imported"
    assert any(m.startswith("⚠") and "缺 1 張" in m and "bbbbbbbbbbbbbbbb" in m for m in logs)
    root = tmp_path / "assets" / "story" / GAME
    assert (root / "templates" / "quick.jpg").read_bytes().startswith(b"\xff\xd8")
    assert (root / "thumbs" / "aaaaaaaaaaaaaaaa.jpg").read_bytes() == b"thumb"
    assert (root / "builds" / "1" / "package.zip").exists()


def _voices(tmp_path: Path, *names: str) -> Path:
    folder = tmp_path / "voices"
    folder.mkdir(exist_ok=True)
    for name in names:
        (folder / f"{name}.mp3").write_bytes(b"ID3" + name.encode())
    return folder


def test_upload_voices_only_referenced_skips_existing_and_reports_missing(tmp_path):
    voices = _voices(tmp_path, "nen002_039", "nen002_040", "unused_001", "evil")
    (tmp_path / "evil.mp3").write_bytes(b"x")
    pkg = _package(tmp_path, voices_dir=voices)
    root = tmp_path / "assets"
    sink = LocalAssetSink(root)
    vdir = root / "story" / GAME / "voices"
    vdir.mkdir(parents=True)
    (vdir / "nen002_040.mp3").write_bytes(b"already")
    logs = []
    stats = upload_assets(pkg, sink, set(), {"nen002_039", "nen002_040", "nen999_001", "../evil", "a.b"},
                          log=logs.append)
    assert stats["voices_uploaded"] == 1 and stats["voices_skipped"] == 1 and stats["voices_missing"] == 1
    assert stats["voices_invalid"] == 2 and stats["voices_bytes"] == len(b"ID3nen002_039")
    assert (vdir / "nen002_039.mp3").read_bytes() == b"ID3nen002_039"
    assert (vdir / "nen002_040.mp3").read_bytes() == b"already"  # 已存在不覆寫
    assert sorted(p.name for p in vdir.iterdir()) == ["nen002_039.mp3", "nen002_040.mp3"]  # 沒用到的不上傳
    assert not (root / "story" / GAME / "evil.mp3").exists()
    assert any(m.startswith("⚠") and "語音資料夾" in m and "nen999_001" in m for m in logs)
    assert any(m.startswith("⚠") and "格式不對" in m for m in logs)


def test_no_voices_dir_uploads_no_voices(tmp_path):
    stats = upload_assets(_package(tmp_path), LocalAssetSink(tmp_path / "assets"), set(), {"nen002_039"},
                          log=lambda *_: None)
    assert "voices_uploaded" not in stats and not (tmp_path / "assets" / "story" / GAME / "voices").exists()


def test_import_uploads_voices_referenced_by_game_lines(tmp_path):
    voices = _voices(tmp_path, "nen002_039")
    store = MemoryStore(_all_lines(), voices={"nen002_039"})
    result = import_package(_package(tmp_path, voices_dir=voices), store, LocalAssetSink(tmp_path / "assets"),
                            log=lambda *_: None)
    assert result.status == "imported"
    assert (tmp_path / "assets" / "story" / GAME / "voices" / "nen002_039.mp3").exists()


def test_assets_only_uploads_for_active_build_without_new_build(tmp_path):
    """語音資料夾不算進 source_hash：同內容重跑會被略過，要靠 --assets-only 補上傳。"""
    store = MemoryStore(_all_lines(), voices={"nen002_039"})
    sink = LocalAssetSink(tmp_path / "assets")
    assert import_package(_package(tmp_path, "a"), store, sink, log=lambda *_: None).status == "imported"
    with_voices = _package(tmp_path, "b", voices_dir=_voices(tmp_path, "nen002_039"))
    logs = []
    assert import_package(with_voices, store, sink, log=logs.append).status == "skipped"
    assert any("--assets-only" in m for m in logs)
    voice = tmp_path / "assets" / "story" / GAME / "voices" / "nen002_039.mp3"
    assert not voice.exists()

    result = refresh_assets(with_voices, store, sink, log=lambda *_: None)
    assert result.status == "assets" and result.build_id == 1
    assert voice.exists() and store.status() == {1: "active"}
    again = refresh_assets(with_voices, store, sink, log=lambda *_: None)  # 重跑：已存在就跳過
    assert again.status == "assets" and "voices_skipped" in again.messages[0]


def test_assets_only_refuses_other_content_or_no_active_build(tmp_path):
    store = MemoryStore(_all_lines())
    sink = LocalAssetSink(tmp_path / "assets")
    result = refresh_assets(_package(tmp_path, "a"), store, sink, log=lambda *_: None)
    assert result.status == "invalid" and "沒有上線版本" in result.errors[0]
    import_package(_package(tmp_path, "a"), store, sink, log=lambda *_: None)
    other = _package(tmp_path, "b", titles={"sw:001": "別名"})
    result = refresh_assets(other, store, sink, log=lambda *_: None)
    assert result.status == "invalid" and "不同" in result.errors[0]
    assert store.status() == {1: "active"}
