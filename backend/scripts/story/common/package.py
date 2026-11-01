"""建置包（build package）的寫出、讀入與驗證。

Write, read and validate a story build package.

建置包是一個資料夾::

    manifest.json        BuildManifest（遊戲、擷取器、設定、統計、素材）
    graph.json           劇情圖（schema v1）
    node_lines.jsonl     每行一筆 {node_key, seq, line_id}
    assets/templates/…   存檔樣板（物件鍵見 manifest.assets.templates）
                         （縮圖、語音與 BGM 不在建置包裡：manifest.assets 的 thumbs_dir／voices_dir／bgm_dir
                         指向來源資料夾）
    graph.mmd.md         Mermaid 版，人工核對用（不算進雜湊）

``source_hash`` 是內容的 SHA-256：圖、成員列、設定與擷取器、樣板檔。同一份內容重複匯入時
匯入腳本直接略過。
``source_hash`` covers the graph, rows, config/extractor and template bytes.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.schemas.story import (
    SCHEMA_VERSION,
    BuildManifest,
    NodeLineRow,
    StoryGraph,
    validate_graph,
    validate_node_lines,
)
from app.services.story.registry import get_story_game

GRAPH_FILE = "graph.json"
ROWS_FILE = "node_lines.jsonl"
MANIFEST_FILE = "manifest.json"
MERMAID_FILE = "graph.mmd.md"


@dataclass
class BuildPackage:
    """讀進記憶體的建置包。A loaded build package."""

    root: Path
    manifest: BuildManifest
    graph: StoryGraph
    rows: list[NodeLineRow]
    source_hash: str

    def template_files(self) -> dict[str, Path]:
        """物件鍵 → 建置包內的樣板檔。Object key to template file."""
        templates = self.manifest.assets.get("templates") or {}
        return {key: self.root / rel for key, rel in templates.items()}

    def thumbs_dir(self) -> Path | None:
        """縮圖來源資料夾（可能沒有）。The thumbnail source folder, if any."""
        raw = self.manifest.assets.get("thumbs_dir")
        return Path(raw) if raw else None

    def voices_dir(self) -> Path | None:
        """語音來源資料夾（``<voice_file>.mp3``，可能沒有）。The voice source folder, if any."""
        raw = self.manifest.assets.get("voices_dir")
        return Path(raw) if raw else None

    def bgm_dir(self) -> Path | None:
        """BGM 來源資料夾（``<game_bgm_tracks.code>.ogg``／``.mp3``，可能沒有）。The BGM source folder, if any."""
        raw = self.manifest.assets.get("bgm_dir")
        return Path(raw) if raw else None


def write_package(
    out_dir: Path,
    manifest: BuildManifest,
    graph: StoryGraph,
    rows: list[NodeLineRow],
    templates: dict[str, Path] | None = None,
    mermaid: str | None = None,
) -> str:
    """寫出建置包並回傳 ``source_hash``。Write the package and return its hash.

    Args:
        out_dir: 輸出資料夾（會建立）。Output folder.
        manifest: manifest；``assets.templates`` 由本函式填。The manifest.
        graph: 劇情圖。The graph.
        rows: 節點成員列。Membership rows.
        templates: 物件鍵 → 本機樣板檔，會複製進 ``assets/templates/``。Templates to copy.
        mermaid: Mermaid 版文字（選填）。Optional Mermaid text.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    tpl_map: dict[str, str] = {}
    for key, src in (templates or {}).items():
        rel = Path("assets") / "templates" / Path(key).name
        (out_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, out_dir / rel)
        tpl_map[key] = rel.as_posix()
    manifest.assets = {**manifest.assets, "templates": tpl_map}

    (out_dir / GRAPH_FILE).write_text(
        json.dumps(graph.to_json_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
    with (out_dir / ROWS_FILE).open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row.model_dump(), ensure_ascii=False) + "\n")
    (out_dir / MANIFEST_FILE).write_text(
        json.dumps(manifest.model_dump(exclude_none=True), ensure_ascii=False, indent=1), encoding="utf-8")
    if mermaid is not None:
        (out_dir / MERMAID_FILE).write_text(mermaid, encoding="utf-8")
    return compute_hash(out_dir, manifest)


def compute_hash(root: Path, manifest: BuildManifest) -> str:
    """建置包內容的 SHA-256。SHA-256 of the package content."""
    h = hashlib.sha256()
    h.update((root / GRAPH_FILE).read_bytes())
    h.update((root / ROWS_FILE).read_bytes())
    h.update(json.dumps({"schema_version": manifest.schema_version, "game_id": manifest.game_id,
                         "extractor": manifest.extractor, "config": manifest.config},
                        ensure_ascii=False, sort_keys=True).encode("utf-8"))
    for key, rel in sorted((manifest.assets.get("templates") or {}).items()):
        h.update(key.encode("utf-8"))
        h.update((root / rel).read_bytes())
    return h.hexdigest()


def read_package(root: Path) -> BuildPackage:
    """讀入建置包（只解析，不驗證）。Read a package without validating it."""
    manifest = BuildManifest.model_validate_json((root / MANIFEST_FILE).read_text(encoding="utf-8"))
    graph = StoryGraph.model_validate_json((root / GRAPH_FILE).read_text(encoding="utf-8"))
    rows = [NodeLineRow.model_validate_json(line)
            for line in (root / ROWS_FILE).read_text(encoding="utf-8").splitlines() if line.strip()]
    return BuildPackage(root=root, manifest=manifest, graph=graph, rows=rows,
                        source_hash=compute_hash(root, manifest))


def validate_package(pkg: BuildPackage) -> list[str]:
    """建置包的 schema 檢查（不碰資料庫）。Schema checks that need no database.

    設定用這款遊戲適配器的設定模型驗證（例 ``SabbatConfig`` 要求路線旗標是整數），和上線後
    API 解析設定用的是同一個模型；只用基底 ``StoryConfig`` 驗證的話，壞設定會上線後才 500。
    The config is validated with the game adapter's model, the same one the
    API uses at runtime.
    """
    errors: list[str] = []
    if pkg.manifest.schema_version != SCHEMA_VERSION:
        errors.append(f"不支援的建置包版本 {pkg.manifest.schema_version}")
    try:
        config = get_story_game(pkg.manifest.game_id).parse_config(pkg.manifest.config)
    except Exception as exc:  # noqa: BLE001 - 收集成錯誤訊息
        errors.append(f"設定格式錯誤：{exc}")
        config = None
    errors += validate_graph(pkg.graph)
    errors += validate_node_lines(pkg.graph, pkg.rows)
    if config is not None:
        tabs = {t.key for t in pkg.graph.tabs}
        errors += [f"route_presets 的分頁 {k} 不存在" for k in config.route_presets if k not in tabs]
        if config.saves:
            files = pkg.template_files()
            if config.saves.template not in files:
                errors.append(f"存檔樣板 {config.saves.template} 不在建置包內")
            elif not files[config.saves.template].exists():
                errors.append(f"存檔樣板檔不存在：{files[config.saves.template]}")
    return errors
