"""The game-agnostic story-flowchart service behind ``/api/v1/story``.

劇情流程圖的通用服務。

It covers the manifest, graph, annotations, lines, locating, saves, thumbnails, voices, and BGM (D11–D13).

涵蓋 manifest、劇情圖、註記、台詞、定位、存檔、縮圖、語音、BGM（D11–D13）。

Caching:

- Graph indexes are keyed by ``build_id`` (the content does not change while the build stays the same).
- Annotation stats and search indexes are cached in memory for 60 seconds (card creation or deletion shows up
  within 60 seconds at the latest).

快取：

- 劇情圖索引以 ``build_id`` 為鍵（版本不變內容就不變）。
- 註記統計與搜尋索引記憶體快取 60 秒（建卡、刪卡後最慢 60 秒反映）。
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
    StoryLineBgm,
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
    StorySaveTemplateError,
    StorySaveUnavailableError,
)
from app.services.story.graph_index import GraphIndex
from app.services.story.registry import get_story_game
from app.services.story.repository import BuildRecord, LineBgm, LineRecord, StoryRepository
from app.services.story.saves import SaveFormatError, SaveMeta, save_writer_for

logger = logging.getLogger(__name__)

ANNOTATION_TTL_SECONDS = 60.0
#: The maximum lines per page; must match the API's ``limit`` validation and ``MAX_LINES_PAGE`` in the frontend
#: ``useStoryLines``.
#: 一頁台詞的上限；API 的 ``limit`` 驗證與前端 ``useStoryLines`` 的 ``MAX_LINES_PAGE`` 要一致。
MAX_LINES_PAGE = 2000
_THUMB_HASH = re.compile(r"^[0-9a-f]{16}$")
#: Voice file stems (``game_lines.voice_file``, for example ``nen002_039``): letters, digits, and underscores only,
#: with no path characters or extension.
#: 語音檔名主幹（``game_lines.voice_file``，例 ``nen002_039``）：只收英數與底線，不能帶路徑字元或副檔名。
_VOICE = re.compile(r"^[A-Za-z0-9_]{1,64}$")
#: The ``game_id`` whitelist: the id goes into object keys (``story/{game_id}/...``), so it must not carry path
#: characters.
#: ``game_id`` 白名單：它會進物件鍵（``story/{game_id}/...``），不能帶路徑字元。
_GAME_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
#: BGM codes (``game_bgm_tracks.code``, for example ``BGM02``, ``BGM20I``): like voices, letters, digits, and
#: underscores only, up to the column length (32). Codes that do not match (for example another game's ``#15``) get
#: no audio file, and the upload script does not upload them either.
#: BGM 代號（``game_bgm_tracks.code``，例 ``BGM02``、``BGM20I``）：和語音一樣只收英數與底線，
#: 長度上限同欄位（32）。不合的代號（例：其他遊戲的 ``#15``）不提供音檔，上傳腳本也不會上傳。
_BGM_CODE = re.compile(r"^[A-Za-z0-9_]{1,32}$")
#: The extensions tried for a BGM file, in order, with their Content-Type: ``.ogg`` wins when a code has both. The
#: game's original files are mostly Ogg Vorbis; to support browsers that cannot play Ogg, upload ``.mp3`` instead
#: (the upload script looks for files in the same order).
#: BGM 音檔依序嘗試的副檔名與 Content-Type：同一個代號兩種都有時 ``.ogg`` 優先。遊戲原檔多半是
#: Ogg Vorbis；要支援不能播 Ogg 的瀏覽器時改上傳 ``.mp3``（上傳腳本找檔案用同一個順序）。
BGM_FORMATS: tuple[tuple[str, str], ...] = ((".ogg", "audio/ogg"), (".mp3", "audio/mpeg"))
#: Database column languages as BCP 47 tags.
#: 資料庫欄位語言 → BCP 47。
LANG_JA, LANG_ZH = "ja-JP", "zh-Hant"


class SaveFile:
    """A generated save file.

    產生的存檔。
    """

    def __init__(self, data: bytes, filename: str, media_type: str) -> None:
        """Create a save file.

        建立存檔。

        Args:
            data: the save content
                存檔內容
            filename: the download file name
                下載的檔名
            media_type: the Content-Type
                Content-Type
        """
        self.data, self.filename, self.media_type = data, filename, media_type


class StoryService:
    """The game-agnostic story-flowchart service.

    通用的劇情流程圖服務。
    """

    def __init__(self, repo: StoryRepository, assets: StoryAssetStore | None = None) -> None:
        """Create the service.

        建立服務。

        Args:
            repo: the story repository
                劇情資料庫存取物件
            assets: the asset store; ``None`` creates a default ``StoryAssetStore``
                素材讀取器；``None`` 時建立預設的 ``StoryAssetStore``
        """
        self.repo = repo
        self.assets = assets or StoryAssetStore()
        self._indexes: dict[int, GraphIndex] = {}
        self._annotations: dict[tuple[int, tuple[str, ...]], tuple[float, StoryAnnotations]] = {}

    # ---------- 版本與設定 ----------

    @staticmethod
    def _check_game_id(game_id: str) -> None:
        """Treat a ``game_id`` outside the whitelist as not found.

        不合白名單的 ``game_id`` 一律當作不存在。
        """
        if not _GAME_ID.match(game_id):
            raise StoryGameNotFoundError("遊戲代號格式不對")

    async def _active(self, game_id: str) -> BuildRecord:
        self._check_game_id(game_id)
        build = await self.repo.active_build(game_id)
        if build is None:
            raise StoryGameNotFoundError(f"遊戲 {game_id} 沒有上線中的劇情流程圖")
        return build

    def _config(self, build: BuildRecord) -> StoryConfig:
        """Parse the build config with the game adapter's config model.

        以遊戲適配器的設定模型解析。

        A config that fails validation is turned into ``STORY_CONFIG_INVALID``.

        不合格時轉成 ``STORY_CONFIG_INVALID``。
        """
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
            voices=self.assets.available,
            bgm=config.bgm_audio and self.assets.available,
            annotations=[kind for kind, p in PROVIDERS.items() if p.enabled(config)],
            route_presets=bool(config.route_presets),
            languages=langs,
        )

    async def list_games(self) -> list[StoryGameSummary]:
        """Return the games with an active build.

        回傳上線中的遊戲。

        A game whose config is invalid is skipped and logged as ERROR, so it cannot fail the whole list.

        設定不合格的遊戲會略過並記 ERROR，不讓整份清單失敗。

        Returns:
            the game summaries, ordered by game id
            遊戲摘要，依遊戲代號排序
        """
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
        """Return the manifest of a game's active build.

        回傳一款遊戲上線版本的 manifest。

        Args:
            game_id: the game id
                遊戲代號

        Returns:
            the manifest
            manifest

        Raises:
            StoryConfigInvalidError: if the active build's config is invalid
                若上線版本的設定不合格
            StoryGameNotFoundError: if ``game_id`` is not whitelisted or the game has no active build
                若 ``game_id`` 不合白名單或沒有上線版本
        """
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
        """Return the whole graph of a build.

        回傳整份劇情圖。

        Args:
            game_id: the game id
                遊戲代號
            build_id: the build to read (``active``, ``retired``, or ``ready``); ``None`` means the active build
                要讀的版本（``active``、``retired`` 或 ``ready``）；``None`` 表示上線版本

        Returns:
            the build id and the graph JSON
            版本 ID 與劇情圖 JSON

        Raises:
            StoryBuildNotFoundError: if ``build_id`` is given but not readable for this game
                若指定的 ``build_id`` 在這款遊戲中不能讀
            StoryGameNotFoundError: if ``game_id`` is not whitelisted, or ``build_id`` is ``None`` and the game has
                no active build
                若 ``game_id`` 不合白名單，或 ``build_id`` 為 ``None`` 而遊戲沒有上線版本
        """
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
        """Return each node's annotation stats and the search index (cached for 60 seconds).

        回傳各節點的註記統計與搜尋索引（快取 60 秒）。

        Args:
            game_id: the game id
                遊戲代號
            kinds: the annotation kinds wanted; ``None`` or empty means every kind. Unknown kinds and kinds the
                game does not enable are ignored.
                要的註記種類；``None`` 或空串列表示全部。不認得的種類與遊戲沒啟用的種類會忽略。

        Returns:
            the annotations of the active build
            上線版本的註記

        Raises:
            StoryConfigInvalidError: if the active build's config is invalid
                若上線版本的設定不合格
            StoryGameNotFoundError: if ``game_id`` is not whitelisted or the game has no active build
                若 ``game_id`` 不合白名單或沒有上線版本
        """
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
        if rec.attrs.get("plays_at"):
            attrs["plays_at"] = rec.attrs["plays_at"]
        label = rec.label.lstrip("*")
        return StoryLine(
            line_id=rec.line_id, seq=rec.seq, kind=rec.kind,
            anchor_key=f"{rec.file_name}*{label}@{rec.point}" if rec.point is not None else None,
            point=rec.point, speaker=rec.speaker_ja, speakers=speakers, texts=texts, voice=rec.voice,
            thumb=rec.thumb, chapter_label=rec.chapter_label, attrs=attrs, annotations=annotations,
            can_save=can_save, bgm=[self._to_bgm(b) for b in rec.bgm], bgm_detect=rec.bgm_detect,
            bgm_may_change=rec.bgm_may_change)

    @staticmethod
    def _to_bgm(track: LineBgm) -> StoryLineBgm:
        """Convert one BGM track to the API format.

        一首 BGM 轉成 API 格式。

        Titles are keyed by language code; languages without a title are left out.

        曲名以語言代碼為鍵，沒有的語言省略。
        """
        titles = {k: v for k, v in ((LANG_JA, track.title_ja), (LANG_ZH, track.title_zh)) if v}
        return StoryLineBgm(code=track.code, ord=track.ord, titles=titles, gallery_no=track.gallery_no)

    async def lines(self, game_id: str, node_key: str, offset: int, limit: int,
                    annotated_after: int | None = None) -> StoryLinesPage:
        """Return one page of a node's lines, in ``seq`` order.

        回傳一個節點的一頁台詞（依 ``seq``）。

        Args:
            game_id: the game id
                遊戲代號
            node_key: the node in the active build
                上線版本中的節點
            offset: the first ``seq`` of the page; negative values count as 0
                這一頁的第一個 ``seq``；負數當作 0
            limit: the page size, clamped to 1 to ``MAX_LINES_PAGE``
                每頁筆數，限制在 1 到 ``MAX_LINES_PAGE``
            annotated_after: if given, also look up the next ``seq`` after it whose line has a card
                有給時，另外找它之後下一句有卡片的 ``seq``

        Returns:
            the page, with ``next_annotated_seq`` set only when ``annotated_after`` is given
            這一頁；只有給了 ``annotated_after`` 時才有 ``next_annotated_seq``

        Raises:
            StoryConfigInvalidError: if the active build's config is invalid
                若上線版本的設定不合格
            StoryGameNotFoundError: if ``game_id`` is not whitelisted or the game has no active build
                若 ``game_id`` 不合白名單或沒有上線版本
            StoryNodeNotFoundError: if ``node_key`` is not in the active build
                若 ``node_key`` 不在上線版本中
        """
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
        """Return where a line or note is on the active build's graph.

        回傳台詞或筆記在上線版本圖上的位置。

        For a note, its lines are tried in order and the first one on the graph wins.

        筆記會依序嘗試它的台詞，取第一句在圖上的。

        Args:
            game_id: the game id
                遊戲代號
            note_id: the note to locate; give exactly one of ``note_id`` and ``line_id``
                要定位的筆記；``note_id`` 與 ``line_id`` 剛好給一個
            line_id: the line to locate
                要定位的台詞

        Returns:
            the location
            位置

        Raises:
            StoryGameNotFoundError: if ``game_id`` is not whitelisted or the game has no active build
                若 ``game_id`` 不合白名單或沒有上線版本
            StoryLineNotFoundError: if the line or note is not on the graph
                若台詞或筆記不在流程圖上
            StoryLocateParamError: if not exactly one of ``note_id`` and ``line_id`` is given
                若 ``note_id`` 與 ``line_id`` 不是剛好給一個
        """
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

    # ---------- 存檔、縮圖、語音與 BGM ----------

    async def make_save(self, game_id: str, line_id: int, slot_id: str, route_preset: str | None) -> SaveFile:
        """Generate a slot save at a line.

        產生某句台詞的欄位存檔。

        The thumbnail is embedded only when the writer uses JPEG thumbnails; if it cannot be read, the template's
        thumbnail is kept and the save is still produced.

        只有寫入器用 JPEG 縮圖時才嵌入縮圖；讀不到時沿用樣板的縮圖，存檔照樣產生。

        Args:
            game_id: the game id
                遊戲代號
            line_id: the line to save at
                要存檔的台詞
            slot_id: the save slot in the game config
                遊戲設定中的存檔欄位
            route_preset: the route preset to apply; ``None`` or empty applies none
                要套用的路線預設；``None`` 或空字串表示不套用

        Returns:
            the save file; its media type is ``image/jpeg`` if the data starts with a JPEG header, otherwise
            ``application/octet-stream``
            存檔；資料以 JPEG 標頭開頭時 media type 為 ``image/jpeg``，否則為 ``application/octet-stream``

        Raises:
            StoryAssetNotFoundError: if the save template cannot be found
                若讀不到存檔樣板
            StoryAssetUnavailableError: if the asset source fails while reading the template
                若讀取樣板時素材來源出錯
            StoryConfigInvalidError: if the active build's config is invalid
                若上線版本的設定不合格
            StoryGameNotFoundError: if ``game_id`` is not whitelisted or the game has no active build
                若 ``game_id`` 不合白名單或沒有上線版本
            StoryLineNotFoundError: if the line is not on the graph
                若台詞不在流程圖上
            StorySaveSlotError: if the slot or route preset is unknown
                若存檔欄位或路線預設不認得
            StorySaveTemplateError: if the template is malformed
                若樣板格式不對
            StorySaveUnavailableError: if the game offers no saves or the line has no save position
                若遊戲不提供存檔或台詞沒有存檔位置
        """
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
        # 只讀寫入器能用的縮圖格式：縮圖物件是 JPEG，用 BMP 縮圖的寫入器（千戀＊萬花）讀了也用不上
        if line.thumb and getattr(writer, "thumb_format", "jpeg") == "jpeg":
            try:
                thumb = await self.assets.get(self.thumb_key(game_id, line.thumb))
            except StoryAssetUnavailableError:
                # 縮圖只是存檔清單的預覽：讀不到（已記 WARNING）就沿用樣板的縮圖，存檔照樣產生
                thumb = None
        meta = SaveMeta(last_text=game.last_text(line), thumb=thumb,
                        store_time_ms=int(datetime.now(timezone.utc).timestamp() * 1000))
        try:
            data = writer.write(template, position, variables, meta)
        except SaveFormatError as exc:
            logger.exception("產生存檔失敗：game=%s line=%s template=%s", game_id, line_id, config.saves.template)
            raise StorySaveTemplateError(
                f"存檔樣板 {config.saves.template} 格式不對，無法產生存檔（{exc}）；請用正確的樣板重新擷取並匯入"
            ) from exc
        media = "image/jpeg" if data[:2] == b"\xff\xd8" else "application/octet-stream"
        return SaveFile(data, slot.filename, media)

    @staticmethod
    def thumb_key(game_id: str, thumb: str) -> str:
        """Return the object key of a thumbnail.

        回傳縮圖的物件鍵。

        Args:
            game_id: the game id
                遊戲代號
            thumb: the thumbnail hash
                縮圖雜湊

        Returns:
            ``story/<game_id>/thumbs/<thumb>.jpg``
            ``story/<game_id>/thumbs/<thumb>.jpg``
        """
        return f"story/{game_id}/thumbs/{thumb}.jpg"

    async def thumb(self, game_id: str, thumb: str) -> bytes:
        """Return a thumbnail JPEG.

        回傳縮圖 JPEG。

        Args:
            game_id: the game id
                遊戲代號
            thumb: the thumbnail hash (16 lowercase hex digits)
                縮圖雜湊（16 位小寫十六進位）

        Returns:
            the JPEG content
            JPEG 內容

        Raises:
            StoryAssetNotFoundError: if the hash is malformed or the thumbnail does not exist
                若雜湊格式不對或縮圖不存在
            StoryAssetUnavailableError: if the asset source fails
                若素材來源出錯
            StoryGameNotFoundError: if ``game_id`` is not whitelisted
                若 ``game_id`` 不合白名單
        """
        self._check_game_id(game_id)
        if not _THUMB_HASH.match(thumb):
            raise StoryAssetNotFoundError("縮圖雜湊格式不對")
        data = await self.assets.get(self.thumb_key(game_id, thumb))
        if data is None:
            raise StoryAssetNotFoundError(f"縮圖 {thumb} 不存在")
        return data

    @staticmethod
    def voice_key(game_id: str, voice: str) -> str:
        """Return the object key of a voice file.

        回傳語音的物件鍵。

        Args:
            game_id: the game id
                遊戲代號
            voice: the voice file stem
                語音檔名主幹

        Returns:
            ``story/<game_id>/voices/<voice>.mp3``
            ``story/<game_id>/voices/<voice>.mp3``
        """
        return f"story/{game_id}/voices/{voice}.mp3"

    async def voice(self, game_id: str, voice: str) -> bytes:
        """Return a line's voice MP3 (not kept in the memory cache).

        回傳一句台詞的語音 MP3（不進記憶體快取）。

        Args:
            game_id: the game id
                遊戲代號
            voice: the voice file stem
                語音檔名主幹

        Returns:
            the MP3 content
            MP3 內容

        Raises:
            StoryAssetNotFoundError: if the name is malformed or the file does not exist
                若名稱格式不對或檔案不存在
            StoryAssetUnavailableError: if the asset source fails
                若素材來源出錯
            StoryGameNotFoundError: if ``game_id`` is not whitelisted
                若 ``game_id`` 不合白名單
        """
        self._check_game_id(game_id)
        if not _VOICE.match(voice):
            raise StoryAssetNotFoundError("語音名稱格式不對")
        data = await self.assets.get(self.voice_key(game_id, voice), cache=False)
        if data is None:
            raise StoryAssetNotFoundError(f"語音 {voice} 不存在")
        return data

    @staticmethod
    def bgm_key(game_id: str, code: str, ext: str) -> str:
        """Return the object key of a BGM file.

        回傳 BGM 音檔的物件鍵。

        Args:
            game_id: the game id
                遊戲代號
            code: the BGM code
                BGM 代號
            ext: the extension including the dot, for example ``.ogg``
                副檔名（含點），例 ``.ogg``

        Returns:
            ``story/<game_id>/bgm/<code><ext>``
            ``story/<game_id>/bgm/<code><ext>``
        """
        return f"story/{game_id}/bgm/{code}{ext}"

    async def bgm(self, game_id: str, code: str) -> tuple[bytes, str]:
        """Return a BGM file and its Content-Type (not kept in the memory cache).

        回傳一首 BGM 的音檔與 Content-Type（不進記憶體快取）。

        It is served only when the config of this game's active build enables ``bgm_audio`` (the same switch as
        ``capabilities.bgm``); otherwise it is always 404, even if the asset folder happens to have the file.
        Extensions are tried in :data:`BGM_FORMATS` order.

        只有這款遊戲上線版本的設定打開 ``bgm_audio`` 時才提供（和 ``capabilities.bgm`` 同一個開關）；
        沒打開時一律 404，即使素材資料夾裡剛好有檔案。副檔名依 ``BGM_FORMATS`` 的順序找。

        Args:
            game_id: the game id
                遊戲代號
            code: the BGM code
                BGM 代號

        Returns:
            the audio content and its Content-Type
            音檔內容與 Content-Type

        Raises:
            StoryAssetNotFoundError: if the code is malformed, BGM is not offered, or the file does not exist
                若代號格式不對、沒有提供 BGM 或檔案不存在
            StoryAssetUnavailableError: if the asset source fails
                若素材來源出錯
            StoryConfigInvalidError: if the active build's config is invalid
                若上線版本的設定不合格
            StoryGameNotFoundError: if ``game_id`` is not whitelisted or the game has no active build
                若 ``game_id`` 不合白名單或沒有上線版本
        """
        self._check_game_id(game_id)
        if not _BGM_CODE.match(code):
            raise StoryAssetNotFoundError("BGM 代號格式不對")
        build = await self._active(game_id)
        if not self._config(build).bgm_audio:
            raise StoryAssetNotFoundError(f"{game_id} 沒有提供 BGM 音檔")
        for ext, media_type in BGM_FORMATS:
            data = await self.assets.get(self.bgm_key(game_id, code, ext), cache=False)
            if data is not None:
                return data, media_type
        raise StoryAssetNotFoundError(f"BGM {code} 不存在")
