"""劇情流程圖的通用服務：manifest、劇情圖、註記、台詞、定位、存檔、縮圖（D11–D13）。

Game-agnostic story service behind ``/api/v1/story``.

快取：
- 劇情圖索引以 ``build_id`` 為鍵（版本不變內容就不變）。
- 註記統計與搜尋索引記憶體快取 60 秒（建卡、刪卡後最慢 60 秒反映）。
Caching: graph indexes per ``build_id``; annotation stats for 60 seconds.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from app.schemas.story import (
    SaveSlot,
    StoryAnnotations,
    StoryCapabilities,
    StoryConfig,
    StoryGameSummary,
    StoryLine,
    StoryLinesPage,
    StoryLocation,
    StoryManifest,
)
from app.services.story.annotations import PROVIDERS
from app.services.story.assets import StoryAssetStore
from app.services.story.errors import (
    StoryAssetNotFoundError,
    StoryAssetUnavailableError,
    StoryBuildNotFoundError,
    StoryConfigInvalidError,
    StoryGameNotFoundError,
    StoryLineNotFoundError,
    StoryLocateParamError,
    StoryNodeNotFoundError,
    StorySaveSlotError,
    StorySaveUnavailableError,
)
from app.services.story.graph_index import GraphIndex
from app.services.story.registry import get_story_game
from app.services.story.repository import BuildRecord, LineRecord, StoryRepository
from app.services.story.saves import SaveMeta, save_writer_for

logger = logging.getLogger(__name__)

ANNOTATION_TTL_SECONDS = 60.0
#: 一頁台詞的上限；API 的 ``limit`` 驗證與前端 ``useStoryLines`` 的 ``MAX_LINES_PAGE`` 要一致。
#: Max lines per page; keep in sync with the API and the frontend hook.
MAX_LINES_PAGE = 2000
_THUMB_HASH = re.compile(r"^[0-9a-f]{16}$")
#: ``game_id`` 白名單：它會進物件鍵（``story/{game_id}/...``），不能帶路徑字元。
#: Game ids go into object keys, so only these characters are accepted.
_GAME_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
#: 資料庫欄位語言 → BCP 47。Column language suffix to BCP 47.
LANG_JA, LANG_ZH = "ja-JP", "zh-Hant"


class SaveFile:
    """產生的存檔。A generated save file."""

    def __init__(self, data: bytes, filename: str, media_type: str) -> None:
        self.data, self.filename, self.media_type = data, filename, media_type


class StoryService:
    """通用的劇情流程圖服務。The story service."""

    def __init__(self, repo: StoryRepository, assets: StoryAssetStore | None = None) -> None:
        self.repo = repo
        self.assets = assets or StoryAssetStore()
        self._indexes: dict[int, GraphIndex] = {}
        self._annotations: dict[tuple[int, tuple[str, ...]], tuple[float, StoryAnnotations]] = {}

    # ---------- 版本與設定 ----------

    @staticmethod
    def _check_game_id(game_id: str) -> None:
        """不合白名單的 ``game_id`` 一律當作不存在。Reject ids outside the whitelist."""
        if not _GAME_ID.match(game_id):
            raise StoryGameNotFoundError("遊戲代號格式不對")

    async def _active(self, game_id: str) -> BuildRecord:
        self._check_game_id(game_id)
        build = await self.repo.active_build(game_id)
        if build is None:
            raise StoryGameNotFoundError(f"遊戲 {game_id} 沒有上線中的劇情流程圖")
        return build

    def _config(self, build: BuildRecord) -> StoryConfig:
        """以遊戲適配器的設定模型解析；不合格時轉成 ``STORY_CONFIG_INVALID``。Parse the build config."""
        try:
            return get_story_game(build.game_id).parse_config(build.config)
        except ValidationError as exc:
            raise StoryConfigInvalidError(
                f"{build.game_id} 版本 {build.build_id} 的設定不合格：{exc.error_count()} 個錯誤") from exc

    async def _index(self, build_id: int) -> GraphIndex:
        if build_id not in self._indexes:
            self._indexes[build_id] = GraphIndex.from_json(await self.repo.graph(build_id))
            if len(self._indexes) > 8:
                self._indexes.pop(next(iter(self._indexes)))
        return self._indexes[build_id]

    def _capabilities(self, build: BuildRecord, config: StoryConfig) -> StoryCapabilities:
        langs = [lang.code for lang in config.languages] or list(build.languages)
        return StoryCapabilities(
            saves=bool(config.saves) and save_writer_for(build.engine) is not None,
            thumbs=self.assets.available,
            annotations=[kind for kind, p in PROVIDERS.items() if p.enabled(config)],
            route_presets=bool(config.route_presets),
            languages=langs,
        )

    async def list_games(self) -> list[StoryGameSummary]:
        """上線中的遊戲。Games with an active build."""
        out = []
        for build in await self.repo.active_builds():
            try:
                config = self._config(build)
            except StoryConfigInvalidError:
                # 一款遊戲的設定壞掉不能讓整份清單失敗；略過它並記 ERROR
                logger.error("略過設定不合格的上線版本 %s/%s", build.game_id, build.build_id, exc_info=True)
                continue
            out.append(StoryGameSummary(
                game_id=build.game_id, title=build.titles.get(LANG_ZH) or build.titles.get(LANG_JA) or build.game_id,
                titles=build.titles, build_id=build.build_id, capabilities=self._capabilities(build, config),
                card_types=config.annotations.cards.card_types if config.annotations.cards else []))
        return out

    async def manifest(self, game_id: str) -> StoryManifest:
        """一款遊戲的 manifest。The game manifest."""
        build = await self._active(game_id)
        config = self._config(build)
        caps = self._capabilities(build, config)
        return StoryManifest(
            game_id=game_id, titles=build.titles, build_id=build.build_id, capabilities=caps,
            languages=config.languages, line_attrs=config.line_attrs, annotation_kinds=caps.annotations,
            card_types=config.annotations.cards.card_types if config.annotations.cards else [],
            save_slots=config.saves.slots if config.saves and caps.saves else [],
            save_install_hint=config.saves.install_hint if config.saves else None,
            route_presets=list(config.route_presets), default_theme=config.default_theme, stats=build.stats)

    async def graph(self, game_id: str, build_id: int | None) -> tuple[int, dict[str, Any]]:
        """整份劇情圖；``build_id`` 省略時為上線版本。The whole graph."""
        if build_id is None:
            build = await self._active(game_id)
        else:
            self._check_game_id(game_id)
            build = await self.repo.build(game_id, build_id)
            if build is None:
                raise StoryBuildNotFoundError(f"{game_id} 沒有版本 {build_id}")
        index = await self._index(build.build_id)
        return build.build_id, index.graph.to_json_dict()

    # ---------- 註記 ----------

    async def annotations(self, game_id: str, kinds: list[str] | None) -> StoryAnnotations:
        """各節點的註記統計與搜尋索引（快取 60 秒）。Annotation stats and index."""
        build = await self._active(game_id)
        config = self._config(build)
        wanted = tuple(sorted(k for k in (kinds or list(PROVIDERS)) if k in PROVIDERS and PROVIDERS[k].enabled(config)))
        key = (build.build_id, wanted)
        cached = self._annotations.get(key)
        if cached and time.monotonic() - cached[0] < ANNOTATION_TTL_SECONDS:
            return cached[1]
        nodes: dict[str, dict[str, dict[str, int]]] = {}
        index: dict[str, dict[str, dict[str, list[str]]]] = {}
        for kind in wanted:
            provider = PROVIDERS[kind]
            for node_key, stats in (await provider.node_stats(self.repo, build.build_id, config)).items():
                nodes.setdefault(node_key, {})[kind] = stats
            index[kind] = await provider.search_index(self.repo, build.build_id, config)
        result = StoryAnnotations(build_id=build.build_id, nodes=nodes, index=index)
        self._annotations[key] = (time.monotonic(), result)
        return result

    # ---------- 台詞 ----------

    def _to_line(self, rec: LineRecord, config: StoryConfig, annotations: dict[str, list], can_save: bool) -> StoryLine:
        texts = {LANG_JA: rec.text_ja}
        if rec.text_zh:
            texts[LANG_ZH] = rec.text_zh
        speakers = {k: v for k, v in ((LANG_JA, rec.speaker_ja), (LANG_ZH, rec.speaker_zh or rec.speaker_ja)) if v}
        attrs: dict[str, Any] = {"is_h": rec.is_h}
        if rec.h_label:
            attrs["h_label"] = rec.h_label
        if rec.bgm:
            first = rec.bgm[0]
            attrs["bgm_title"] = first.get("title_zh") or first.get("title_ja") or first.get("code")
            attrs["bgm"] = rec.bgm
        if rec.attrs.get("plays_at"):
            attrs["plays_at"] = rec.attrs["plays_at"]
        label = rec.label.lstrip("*")
        return StoryLine(
            line_id=rec.line_id, seq=rec.seq, kind=rec.kind,
            anchor_key=f"{rec.file_name}*{label}@{rec.point}" if rec.point is not None else None,
            point=rec.point, speaker=rec.speaker_ja, speakers=speakers, texts=texts, voice=rec.voice,
            thumb=rec.thumb, chapter_label=rec.chapter_label, attrs=attrs, annotations=annotations,
            can_save=can_save)

    async def lines(self, game_id: str, node_key: str, offset: int, limit: int,
                    annotated_after: int | None = None) -> StoryLinesPage:
        """一個節點的一頁台詞（依 ``seq``）。A page of a node's lines."""
        build = await self._active(game_id)
        config = self._config(build)
        index = await self._index(build.build_id)
        if node_key not in index.nodes:
            raise StoryNodeNotFoundError(f"節點 {node_key} 不在版本 {build.build_id}")
        limit = max(1, min(limit, MAX_LINES_PAGE))
        total, records = await self.repo.node_lines(build.build_id, node_key, max(0, offset), limit)
        ids = [r.line_id for r in records]
        per_line: dict[int, dict[str, list]] = {}
        for kind, provider in PROVIDERS.items():
            if not provider.enabled(config):
                continue
            for line_id, items in (await provider.line_annotations(self.repo, ids, config)).items():
                per_line.setdefault(line_id, {})[kind] = items
        game = get_story_game(game_id)
        saves_ok = self._capabilities(build, config).saves
        lines = [self._to_line(r, config, per_line.get(r.line_id, {}),
                               saves_ok and game.save_position(r) is not None) for r in records]
        next_seq = None
        if annotated_after is not None:
            next_seq = await self.repo.next_annotated_seq(build.build_id, node_key, annotated_after)
        return StoryLinesPage(node_key=node_key, build_id=build.build_id, total=total, offset=max(0, offset),
                              lines=lines, next_annotated_seq=next_seq)

    async def locate(self, game_id: str, note_id: int | None, line_id: int | None) -> StoryLocation:
        """台詞或筆記在圖上的位置。Where a line or note is."""
        if (note_id is None) == (line_id is None):
            raise StoryLocateParamError("請指定 note_id 或 line_id 其中之一")
        build = await self._active(game_id)
        index = await self._index(build.build_id)
        candidates = [line_id] if line_id is not None else await self.repo.lines_of_note(int(note_id))
        for candidate in candidates:
            found = await self.repo.line_in_build(build.build_id, int(candidate))
            if found:
                node_key, rec = found
                tab = index.tab_of(node_key)
                if tab:
                    return StoryLocation(game_id=game_id, build_id=build.build_id, tab=tab, node_key=node_key,
                                         line_id=rec.line_id, seq=rec.seq)
        what = f"筆記 {note_id}" if note_id is not None else f"台詞 {line_id}"
        raise StoryLineNotFoundError(f"{what} 不在 {game_id} 的流程圖上")

    # ---------- 存檔與縮圖 ----------

    async def make_save(self, game_id: str, line_id: int, slot_id: str, route_preset: str | None) -> SaveFile:
        """產生某句台詞的欄位存檔。Generate a slot save for a line."""
        build = await self._active(game_id)
        config = self._config(build)
        writer = save_writer_for(build.engine)
        if not config.saves or writer is None:
            raise StorySaveUnavailableError(f"{game_id} 不提供存檔")
        slot: SaveSlot | None = next((s for s in config.saves.slots if s.id == slot_id), None)
        if slot is None:
            raise StorySaveSlotError(f"不認得的存檔欄位 {slot_id}")
        if route_preset and route_preset not in config.route_presets:
            raise StorySaveSlotError(f"不認得的路線預設 {route_preset}")
        found = await self.repo.line_in_build(build.build_id, line_id)
        if found is None:
            raise StoryLineNotFoundError(f"台詞 {line_id} 不在 {game_id} 的流程圖上")
        node_key, line = found
        game = get_story_game(game_id)
        position = game.save_position(line)
        if position is None:
            raise StorySaveUnavailableError(f"台詞 {line_id} 沒有存檔位置（point）")
        template = await self.assets.get(config.saves.template)
        if template is None:
            raise StoryAssetNotFoundError(f"讀不到存檔樣板 {config.saves.template}")
        index = await self._index(build.build_id)
        variables = game.save_variables(index, node_key, config, route_preset)
        thumb = None
        if line.thumb:
            try:
                thumb = await self.assets.get(self.thumb_key(game_id, line.thumb))
            except StoryAssetUnavailableError:
                # 縮圖只是存檔清單的預覽：讀不到（已記 WARNING）就沿用樣板的縮圖，存檔照樣產生
                thumb = None
        meta = SaveMeta(last_text=game.last_text(line), thumb=thumb,
                        store_time_ms=int(datetime.now(timezone.utc).timestamp() * 1000))
        data = writer.write(template, position, variables, meta)
        media = "image/jpeg" if data[:2] == b"\xff\xd8" else "application/octet-stream"
        return SaveFile(data, slot.filename, media)

    @staticmethod
    def thumb_key(game_id: str, thumb: str) -> str:
        """縮圖的物件鍵。Object key of a thumbnail."""
        return f"story/{game_id}/thumbs/{thumb}.jpg"

    async def thumb(self, game_id: str, thumb: str) -> bytes:
        """縮圖 JPEG。A thumbnail."""
        self._check_game_id(game_id)
        if not _THUMB_HASH.match(thumb):
            raise StoryAssetNotFoundError("縮圖雜湊格式不對")
        data = await self.assets.get(self.thumb_key(game_id, thumb))
        if data is None:
            raise StoryAssetNotFoundError(f"縮圖 {thumb} 不存在")
        return data
