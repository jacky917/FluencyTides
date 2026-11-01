"""語料庫台詞與遊戲設定的共用讀取層（docs/wip/corpus_db_rebuild_FEAT_2026-10-31.md D9 的 A）。

Shared read layer for corpus lines and game settings (item A of D9 in the
corpus rebuild plan).

建卡管線、讀音判定、詞彙索引、ES 同步與刪卡工具都改從這裡讀台詞，不再各自寫
SQL。舊表 ``scripts`` 的欄位在新庫的對應（D2 的檢視表對照）：

Every reader of the old ``scripts`` table goes through this module. Old
column → new source:

========================  ============================================
舊 ``scripts`` 欄位          新來源
========================  ============================================
``id``                    ``game_lines.id``（沿用舊 id）
``dialogue``              ``COALESCE(ruby_ja_jp, text_ja_jp)``
``role_name``             ``game_speakers.name_ja_jp``（旁白為 NULL）
``chapter``               ``game_lines.chapter_label``
``audio_file``            ``game_lines.voice_file``
``source='サノバウィッチ'``   ``game_id='SabbatOfTheWitch'``
``status='1'``            ``status='active' AND in_corpus=1 AND kind IN ('line','bgv')``
========================  ============================================

「前後文」依 ``story_order``（劇情順序）取，不再依 id；還原的台詞會出現，選項畫面不會。
Context lines follow ``story_order`` rather than id.

每句台詞也帶它播放的 BGM（``CorpusLine.bgm``，依 ``ord`` 排列）：讀台詞的查詢之後，再用一次批次查詢
取 ``game_line_bgm`` join ``game_bgm_tracks``（不是每句一次）；``iter_lines`` 在串流前先查好整個範圍。
Each line also carries its BGM tracks (``CorpusLine.bgm``), loaded with one
extra batched query per call (``iter_lines`` prefetches its whole scope
before streaming).

遊戲設定（名稱、語音與頭像資料夾）從 ``game_profiles`` 讀，資料夾相對於 ``.env`` 的
``GAME_ASSETS_ROOT``（D9 的 H）。
Game settings come from ``game_profiles``; directories are relative to
``GAME_ASSETS_ROOT``.
"""

from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.infrastructure.database.corpus_tables import (
    CORPUS_LINE_KINDS,
    GAME_BGM_TRACKS,
    GAME_LINE_BGM,
    GAME_LINES,
    GAME_PROFILES,
    GAME_SPEAKERS,
)

# 預設遊戲：建卡管線的 ``--game`` 預設值。Default game for the pipelines' ``--game``.
DEFAULT_GAME_ID = "SabbatOfTheWitch"

_KINDS_SQL = ", ".join(f"'{kind}'" for kind in CORPUS_LINE_KINDS)

# 進建卡與搜尋的台詞（D2、D6）。Lines that belong to the corpus.
CORPUS_FILTER = f"l.status = 'active' AND l.in_corpus = 1 AND l.kind IN ({_KINDS_SQL})"

_LINE_COLUMNS = (
    "l.id, l.game_id, l.chapter_label, sp.name_ja_jp, "
    "COALESCE(l.ruby_ja_jp, l.text_ja_jp), l.text_ja_jp, l.voice_file, sp.avatar_file, "
    "l.story_order, l.kind"
)
_LINE_FROM = f"FROM {GAME_LINES} l LEFT JOIN {GAME_SPEAKERS} sp ON sp.speaker_id = l.speaker_id"
_BGM_SELECT = (
    "SELECT lb.line_id, lb.ord, t.code, t.title_ja_jp, t.title_zh_hant "
    f"FROM {GAME_LINE_BGM} lb JOIN {GAME_BGM_TRACKS} t ON t.track_id = lb.track_id"
)


@dataclass(frozen=True)
class CorpusLineBgm:
    """一句台詞播放的一首 BGM（``game_line_bgm`` 一列 join ``game_bgm_tracks``）。One BGM track of a line.

    Attributes:
        code: 曲目代號（例 ``BGM02``）。Track code.
        ord: 同一句的第幾首（0 起算；播放中切換曲子的台詞有 0 和 1）。Order within the line.
        title_ja_jp: 日文曲名；不明時為 None。Japanese title.
        title_zh_hant: 繁中曲名；不明時為 None。Traditional Chinese title.
    """

    code: str
    ord: int
    title_ja_jp: str | None = None
    title_zh_hant: str | None = None


@dataclass(frozen=True)
class CorpusLine:
    """一句台詞（舊 ``scripts`` 一列在新庫的樣子）。One corpus line.

    Attributes:
        id: 台詞 ID（``game_lines.id``，沿用舊 ``scripts.id``）。Line id.
        game_id: 遊戲代號。Game id.
        chapter: 章節名（``chapter_label``，舊 ``chapter``）；可能為 None。Chapter label.
        speaker: 說話者日文名（舊 ``role_name``）；旁白為 None。Speaker, None for narration.
        dialogue: 帶振假名標記的日文（舊 ``dialogue``）。Japanese text with ruby marks.
        text: 遊戲實際顯示的日文（無振假名標記）。Displayed Japanese text.
        voice_file: 語音檔名（不含副檔名，舊 ``audio_file``）。Voice file name.
        avatar_file: 說話者的頭像檔名；None 時用 ``<說話者>.jpg``。Speaker avatar file.
        story_order: 全作的劇情順序。Story order within the game.
        kind: ``line`` 或 ``bgv``（背景語音）。Line kind.
        bgm: 這句播放的 BGM，依 ``ord`` 排列；沒有 BGM 時為空 tuple。BGM tracks in ``ord``
            order; empty when none.
    """

    id: int
    game_id: str
    chapter: str | None
    speaker: str | None
    dialogue: str
    text: str
    voice_file: str | None
    avatar_file: str | None
    story_order: int
    kind: str
    bgm: tuple[CorpusLineBgm, ...] = ()

    @property
    def bgm_codes(self) -> tuple[str, ...]:
        """BGM 代號（依 ``ord``）。BGM codes in ``ord`` order."""
        return tuple(track.code for track in self.bgm)

    @property
    def avatar_name(self) -> str | None:
        """頭像檔名：``game_speakers.avatar_file``，沒有時用 ``<說話者>.jpg``；旁白為 None。

        Avatar file name, falling back to ``<speaker>.jpg``; None for narration.
        """
        if not self.speaker:
            return None
        return self.avatar_file or f"{self.speaker}.jpg"


def _line(row) -> CorpusLine:
    """把查詢列轉成 :class:`CorpusLine`。Map a result row to a CorpusLine."""
    return CorpusLine(
        id=int(row[0]),
        game_id=row[1],
        chapter=row[2],
        speaker=row[3],
        dialogue=row[4] or "",
        text=row[5] or "",
        voice_file=row[6],
        avatar_file=row[7],
        story_order=int(row[8]),
        kind=row[9],
    )


def _bgm_map(rows) -> dict[int, tuple[CorpusLineBgm, ...]]:
    """把 ``_BGM_SELECT`` 的結果（依 line_id、ord 排序）整理成 ``{line_id: (曲目, …)}``。

    Group ``_BGM_SELECT`` rows by line. Identical tracks share one object, so
    prefetching a whole game stays small.
    """
    shared: dict[tuple, CorpusLineBgm] = {}
    grouped: dict[int, list[CorpusLineBgm]] = {}
    for line_id, ord_, code, title_ja, title_zh in rows:
        key = (code, int(ord_), title_ja, title_zh)
        track = shared.get(key)
        if track is None:
            track = shared[key] = CorpusLineBgm(code, int(ord_), title_ja, title_zh)
        grouped.setdefault(int(line_id), []).append(track)
    return {line_id: tuple(tracks) for line_id, tracks in grouped.items()}


async def _fetch_bgm(session: AsyncSession, ids: Iterable[int]) -> dict[int, tuple[CorpusLineBgm, ...]]:
    """一次查出多句台詞的 BGM。Fetch the BGM of several lines in one query."""
    id_list = sorted({int(i) for i in ids})
    if not id_list:
        return {}
    query = text(f"{_BGM_SELECT} WHERE lb.line_id IN :ids ORDER BY lb.line_id, lb.ord").bindparams(
        bindparam("ids", expanding=True)
    )
    return _bgm_map((await session.execute(query, {"ids": id_list})).fetchall())


async def _with_bgm(session: AsyncSession, lines: list[CorpusLine]) -> list[CorpusLine]:
    """替台詞補上 BGM（一次批次查詢）。Attach BGM to lines with one batched query."""
    bgm = await _fetch_bgm(session, (line.id for line in lines))
    return [replace(line, bgm=bgm[line.id]) if line.id in bgm else line for line in lines]


async def _select_lines(session: AsyncSession, ids: Iterable[int], corpus_only: bool) -> list[CorpusLine]:
    """依 ID 取台詞（不含 BGM）。Fetch lines by id, without BGM."""
    id_list = sorted({int(i) for i in ids})
    if not id_list:
        return []
    where = "l.id IN :ids" + (f" AND {CORPUS_FILTER}" if corpus_only else "")
    query = text(f"SELECT {_LINE_COLUMNS} {_LINE_FROM} WHERE {where}").bindparams(
        bindparam("ids", expanding=True)
    )
    result = await session.execute(query, {"ids": id_list})
    return [_line(row) for row in result.fetchall()]


async def get_lines(
    session: AsyncSession, ids: Iterable[int], *, corpus_only: bool = True,
) -> dict[int, CorpusLine]:
    """依 ID 取多句台詞。Fetch lines by id.

    Args:
        session: 語料庫 async session。Corpus async session.
        ids: 台詞 ID。Line ids.
        corpus_only: True（預設）時只回傳進語料庫的台詞，和舊表 ``scripts`` 的範圍相同。
            Only corpus lines (the old ``scripts`` scope) when True.

    Returns:
        dict[int, CorpusLine]: ``{id: line}``（含 BGM）；查無的 ID 不在結果中。Lines with
        their BGM; missing ids are absent.
    """
    lines = await _with_bgm(session, await _select_lines(session, ids, corpus_only))
    return {line.id: line for line in lines}


async def get_line_metadata(session: AsyncSession, ids: Iterable[int]) -> dict[int, dict]:
    """取多句台詞的章節與說話者（選句漏斗的 ``metadata_fetcher`` 格式）。

    Chapter and speaker of each line, in the funnel's metadata format.

    Args:
        session: 語料庫 async session。Corpus async session.
        ids: 台詞 ID。Line ids.

    Returns:
        dict[int, dict]: ``{id: {"chapter": str, "speaker": str}}``，NULL 以空字串表示。
        NULLs are represented as empty strings.
    """
    lines = await _select_lines(session, ids, corpus_only=True)  # 不需要 BGM，少一次查詢
    return {line.id: {"chapter": line.chapter or "", "speaker": line.speaker or ""} for line in lines}


async def get_context(
    session: AsyncSession, line_id: int, prev: int, next: int,  # noqa: A002 - 與計畫書介面同名
) -> list[CorpusLine]:
    """取一句台詞與它前後的台詞（同一遊戲、同一章節，依劇情順序）。

    Fetch a line with the lines before and after it (same game and chapter,
    ordered by story order).

    取代舊的「``id BETWEEN id-prev AND id+next AND chapter = …`` 依 id 排序」：改依
    ``story_order``，所以還原的台詞會出現在前後文，選項畫面不會；前後各取到 ``prev``／``next``
    句（章節邊界為止），不再因為 id 空號而變少。
    Replaces the old id-window query; lines are counted, not id-ranged.

    Args:
        session: 語料庫 async session。Corpus async session.
        line_id: 目標台詞 ID。Target line id.
        prev: 往前取幾句。Lines before the target.
        next: 往後取幾句。Lines after the target.

    Returns:
        list[CorpusLine]: 依劇情順序排列、含目標句（都帶 BGM，整段只多一次查詢）；目標句不在
        語料庫中時為空清單。Ordered lines including the target, with BGM loaded in
        one extra query; empty when the target is not a corpus line.
    """
    found = await _select_lines(session, [line_id], corpus_only=True)
    if not found:
        return []
    target = found[0]
    same_scope = (
        f"l.game_id = :game_id AND {CORPUS_FILTER} "
        "AND (l.chapter_label = :chapter OR (l.chapter_label IS NULL AND :chapter IS NULL))"
    )
    params = {"game_id": target.game_id, "chapter": target.chapter, "order": target.story_order}
    before = await session.execute(text(
        f"SELECT {_LINE_COLUMNS} {_LINE_FROM} WHERE {same_scope} AND l.story_order < :order "
        f"ORDER BY l.story_order DESC LIMIT {max(int(prev), 0)}"
    ), params)
    after = await session.execute(text(
        f"SELECT {_LINE_COLUMNS} {_LINE_FROM} WHERE {same_scope} AND l.story_order > :order "
        f"ORDER BY l.story_order ASC LIMIT {max(int(next), 0)}"
    ), params)
    earlier = [_line(row) for row in before.fetchall()]
    earlier.reverse()
    return await _with_bgm(session, earlier + [target] + [_line(row) for row in after.fetchall()])


async def iter_lines(
    session: AsyncSession, game_id: str, chapter: str | None = None,
) -> AsyncIterator[CorpusLine]:
    """依劇情順序逐句讀出一款遊戲（可限定章節）的語料台詞。

    Stream a game's corpus lines in story order, optionally for one chapter.

    以伺服器端游標串流，不一次載入全部台詞；串流期間同一個 session 不能執行其他查詢。
    串流期間不能再查 BGM，所以開始串流前先用一次查詢取好整個範圍的 BGM（一款遊戲約 5 萬列，
    相同的曲目共用同一個物件）。
    Streams through a server-side cursor; do not run other statements on the
    same session while iterating. The scope's BGM is prefetched in one query
    before streaming starts.

    Args:
        session: 語料庫 async session。Corpus async session.
        game_id: 遊戲代號。Game id.
        chapter: 只取這個章節（``chapter_label``）；None 時取全部。Optional chapter.

    Yields:
        CorpusLine: 一句台詞。One line.
    """
    where = f"l.game_id = :game_id AND {CORPUS_FILTER}"
    params: dict = {"game_id": game_id}
    if chapter is not None:
        where += " AND l.chapter_label = :chapter"
        params["chapter"] = chapter
    bgm = _bgm_map((await session.execute(text(
        f"{_BGM_SELECT} JOIN {GAME_LINES} l ON l.id = lb.line_id WHERE {where} ORDER BY lb.line_id, lb.ord"
    ), params)).fetchall())
    result = await session.stream(
        text(f"SELECT {_LINE_COLUMNS} {_LINE_FROM} WHERE {where} ORDER BY l.story_order"), params
    )
    async for row in result:
        line = _line(row)
        yield replace(line, bgm=bgm[line.id]) if line.id in bgm else line


async def list_chapters(session: AsyncSession, game_id: str) -> list[str]:
    """列出一款遊戲有語料台詞的章節，依章節第一句的劇情順序排列。

    List a game's chapters that hold corpus lines, in story order.

    Args:
        session: 語料庫 async session。Corpus async session.
        game_id: 遊戲代號。Game id.

    Returns:
        list[str]: 章節名（``chapter_label``，不含 NULL）。Chapter labels.
    """
    result = await session.execute(text(
        f"SELECT l.chapter_label FROM {GAME_LINES} l "
        f"WHERE l.game_id = :game_id AND {CORPUS_FILTER} AND l.chapter_label IS NOT NULL "
        "GROUP BY l.chapter_label ORDER BY MIN(l.story_order)"
    ), {"game_id": game_id})
    return [row[0] for row in result.fetchall()]


def _normalize_sql(column: str) -> str:
    """去掉換行與全半形空白的 SQL 運算式。SQL expression stripping newlines and spaces."""
    return (
        f"REPLACE(REPLACE(REPLACE(REPLACE({column}, '\\r', ''), '\\n', ''), ' ', ''), '　', '')"
    )


async def find_line_by_text(
    session: AsyncSession, normalized_text: str, game_id: str | None = None,
) -> CorpusLine | None:
    """以去掉空白與換行後的日文找一句語料台詞（刪卡工具修復孤兒卡用）。

    Find a corpus line by its whitespace-stripped Japanese text.

    比對遊戲顯示的文字（``text_ja_jp``，沒有振假名標記），卡片上的例句也是去掉標記的。
    Compares against the displayed text, which carries no ruby marks.

    Args:
        session: 語料庫 async session。Corpus async session.
        normalized_text: 已去掉空白與換行的台詞。Whitespace-stripped text.
        game_id: 限定遊戲；None 時不限定。Optional game filter.

    Returns:
        CorpusLine | None: 第一句符合的台詞（依劇情順序）。The first match, or None.
    """
    where = f"{CORPUS_FILTER} AND {_normalize_sql('l.text_ja_jp')} = :text"
    params: dict = {"text": normalized_text}
    if game_id:
        where += " AND l.game_id = :game_id"
        params["game_id"] = game_id
    return await _first_line(session, where, params)


async def _first_line(session: AsyncSession, where: str, params: dict) -> CorpusLine | None:
    """依劇情順序取第一句符合的語料台詞（含 BGM）。The first matching line, with BGM."""
    result = await session.execute(text(
        f"SELECT {_LINE_COLUMNS} {_LINE_FROM} WHERE {where} ORDER BY l.story_order LIMIT 1"
    ), params)
    row = result.fetchone()
    return (await _with_bgm(session, [_line(row)]))[0] if row else None


async def find_line_by_voice(
    session: AsyncSession, voice_file: str, game_id: str | None = None,
) -> CorpusLine | None:
    """以語音檔名（不含副檔名）找一句語料台詞。Find a corpus line by its voice file.

    Args:
        session: 語料庫 async session。Corpus async session.
        voice_file: 語音檔名，例 ``hid002_040``。Voice file name.
        game_id: 限定遊戲；None 時不限定。Optional game filter.

    Returns:
        CorpusLine | None: 第一句符合的台詞。The first match, or None.
    """
    where = f"{CORPUS_FILTER} AND l.voice_file = :voice"
    params: dict = {"voice": voice_file}
    if game_id:
        where += " AND l.game_id = :game_id"
        params["game_id"] = game_id
    return await _first_line(session, where, params)


async def find_excluded_line(
    session: AsyncSession, *, normalized_text: str | None = None, voice_file: str | None = None,
    game_id: str | None = None,
) -> tuple[int, str, int, str] | None:
    """在 ``game_lines`` 找一句「存在但不進語料庫」的台詞（修復孤兒卡失敗時說明原因用）。

    Look up a line that exists in ``game_lines`` but is outside the corpus
    (``in_corpus=0``, not ``active``, or a ``choice``), so repair failures
    can tell "missing" from "excluded".

    :func:`find_line_by_text` / :func:`find_line_by_voice` 只找語料台詞；它們找不到時，用這個區分
    「``game_lines`` 根本沒有」與「有，但被排除在語料庫外」。
    Use after the corpus-only finders return None.

    Args:
        session: 語料庫 async session。Corpus async session.
        normalized_text: 已去掉空白與換行的台詞；None 時不以台詞找。Whitespace-stripped text.
        voice_file: 語音檔名；None 時不以語音找。Voice file name.
        game_id: 限定遊戲；None 時不限定。Optional game filter.

    Returns:
        tuple[int, str, int, str] | None: ``(id, status, in_corpus, kind)``；兩種方式都找不到時為
        None。The first match, or None.
    """
    matches = []
    params: dict = {}
    if normalized_text:
        matches.append(f"{_normalize_sql('l.text_ja_jp')} = :text")
        params["text"] = normalized_text
    if voice_file:
        matches.append("l.voice_file = :voice")
        params["voice"] = voice_file
    if not matches:
        return None
    where = f"({' OR '.join(matches)}) AND NOT ({CORPUS_FILTER})"
    if game_id:
        where += " AND l.game_id = :game_id"
        params["game_id"] = game_id
    row = (await session.execute(text(
        f"SELECT l.id, l.status, l.in_corpus, l.kind FROM {GAME_LINES} l WHERE {where} "
        "ORDER BY l.story_order LIMIT 1"
    ), params)).fetchone()
    return (int(row[0]), row[1], int(row[2]), row[3]) if row else None


# ============================================================ game_profiles


@dataclass(frozen=True)
class GameProfile:
    """一款遊戲的設定（``game_profiles`` 一列）。One row of ``game_profiles``.

    Attributes:
        game_id: 遊戲代號；也是 Anki 媒體檔名的前綴（``<game_id>_<檔名>``）。Game id,
            also the Anki media prefix.
        title_ja_jp: 日文標題；卡片上與提示詞中的遊戲名稱（原 ``JP_*_GAME_NAME_JP``）。
            Japanese title shown on cards.
        voice_dir: 語音資料夾，相對於 ``GAME_ASSETS_ROOT``。Voice directory (relative).
        avatar_dir: 頭像資料夾，相對於 ``GAME_ASSETS_ROOT``。Avatar directory (relative).
        legacy_source: 舊 ``scripts.source`` 的值。The old ``scripts.source`` value.
    """

    game_id: str
    title_ja_jp: str
    voice_dir: str | None
    avatar_dir: str | None
    legacy_source: str | None

    @property
    def media_prefix(self) -> str:
        """Anki 媒體檔名前綴（``SabbatOfTheWitch_``）。The Anki media file prefix."""
        return f"{self.game_id}_"

    @property
    def es_source(self) -> str:
        """ES 文件 ``source`` 欄的值：舊 ``scripts.source``，沒有時用日文標題。

        The ES ``source`` value: the legacy source, else the Japanese title.

        ES 的文件格式不變（D9 的 D），搜尋依這個值過濾遊戲，所以同步與搜尋都用它。
        Both the sync and the search filter use this value.
        """
        return self.legacy_source or self.title_ja_jp

    def voice_path(self) -> Path:
        """語音資料夾的絕對路徑。Absolute voice directory."""
        return assets_dir(self.voice_dir, what=f"{self.game_id} 的語音資料夾")

    def avatar_path(self) -> Path:
        """頭像資料夾的絕對路徑。Absolute avatar directory."""
        return assets_dir(self.avatar_dir, what=f"{self.game_id} 的頭像資料夾")


def assets_dir(relative: str | None, *, what: str = "素材資料夾") -> Path:
    """把 ``game_profiles`` 的相對資料夾接上 ``GAME_ASSETS_ROOT``。

    Join a relative ``game_profiles`` directory onto ``GAME_ASSETS_ROOT``.

    Args:
        relative: 相對路徑（例 ``voice/yuzusoft/SabbatOfTheWitch``）。Relative path.
        what: 錯誤訊息中的名稱。Name used in error messages.

    Returns:
        Path: 絕對路徑。The absolute path.

    Raises:
        ValueError: 沒有設定 ``GAME_ASSETS_ROOT``，或資料夾欄位為空。When the root or the
            directory is not configured.
    """
    root = (settings.GAME_ASSETS_ROOT or "").strip()
    if not root:
        raise ValueError("尚未設定 GAME_ASSETS_ROOT（.env 的素材根目錄），無法找到" + what)
    if not relative:
        raise ValueError(f"game_profiles 沒有設定{what}")
    return Path(root) / relative


def require_media_dirs(game: GameProfile) -> tuple[Path, Path]:
    """取一款遊戲的語音與頭像資料夾，任一不存在就報錯（建卡管線啟動時呼叫）。

    Resolve a game's voice and avatar directories, failing when either does
    not exist (called by the card pipelines at startup).

    資料夾設錯時管線仍會跑完，只是每張卡都沒有音檔與頭像；所以一開始就擋下。
    A wrong folder would otherwise silently produce cards without audio or
    avatars, so the pipelines abort up front.

    Args:
        game: 遊戲設定。The game profile.

    Returns:
        tuple[Path, Path]: ``(語音資料夾, 頭像資料夾)``。``(voice_dir, avatar_dir)``.

    Raises:
        ValueError: 沒有設定 ``GAME_ASSETS_ROOT`` 或資料夾欄位（見 :func:`assets_dir`）。
            The root or a directory is not configured.
        FileNotFoundError: 資料夾不存在；訊息含遊戲代號、解析出的路徑與檢查方向。A
            directory is missing; the message names the game, path and what to check.
    """
    voice_dir = game.voice_path()
    avatar_dir = game.avatar_path()
    for what, path in (("語音", voice_dir), ("頭像", avatar_dir)):
        if not path.is_dir():
            raise FileNotFoundError(
                f"遊戲 {game.game_id} 的{what}資料夾不存在：{path}"
                "（請檢查 .env 的 GAME_ASSETS_ROOT 與 game_profiles 的 voice_dir / avatar_dir）"
            )
    return voice_dir, avatar_dir


_PROFILE_COLUMNS = "game_id, title_ja_jp, voice_dir, avatar_dir, legacy_source"


async def get_game_profile(session: AsyncSession, game_id: str) -> GameProfile:
    """讀一款遊戲的設定。Read one game's settings.

    Args:
        session: 語料庫 async session。Corpus async session.
        game_id: 遊戲代號。Game id.

    Returns:
        GameProfile: 遊戲設定。The game profile.

    Raises:
        LookupError: ``game_profiles`` 沒有這款遊戲；訊息列出可用的代號。When the game is
            unknown; the message lists the available ids.
    """
    result = await session.execute(text(
        f"SELECT {_PROFILE_COLUMNS} FROM {GAME_PROFILES} WHERE game_id = :game_id"
    ), {"game_id": game_id})
    row = result.fetchone()
    if row is None:
        available = await list_game_ids(session)
        raise LookupError(f"game_profiles 沒有遊戲 {game_id!r}（可用：{available}）")
    return GameProfile(*row)


async def list_game_ids(session: AsyncSession) -> list[str]:
    """列出 ``game_profiles`` 裡全部遊戲的代號（媒體清理用來判斷前綴）。

    List every game id in ``game_profiles`` (media cleanup checks each prefix).

    Args:
        session: 語料庫 async session。Corpus async session.

    Returns:
        list[str]: 遊戲代號，依代號排序。Game ids, sorted.
    """
    result = await session.execute(text(f"SELECT game_id FROM {GAME_PROFILES} ORDER BY game_id"))
    return [row[0] for row in result.fetchall()]
