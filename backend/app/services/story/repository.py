"""Queries over ``story_builds`` / ``story_node_lines``, joined with the corpus ``game_*`` tables and ``rel_links``.

``story_builds``／``story_node_lines`` 的查詢，以及 join 語料庫的 ``game_*``、``rel_links``。

See D2 「索引對應的查詢」.

見 D2「索引對應的查詢」。

All queries are read-only; writes happen only in the import script. The service layer depends only on the
:class:`StoryRepository` interface, so tests can swap in an in-memory version.

全部是唯讀查詢；寫入只在匯入腳本。服務層只依賴 ``StoryRepository`` 介面，測試用記憶體
版本替換。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy import bindparam, text

from app.infrastructure.database.corpus_tables import (
    ENTITY_NOTE,
    ENTITY_SCRIPT_LINE,
    REL_EXAMPLE_FROM,
    REL_HAS_CLOZE,
    REL_HAS_CONTEXT,
    REL_STATUS_ACTIVE,
    REL_TEACHES,
    lemma_of,
)
from app.infrastructure.database.story_corpus_database import story_session_factory

logger = logging.getLogger(__name__)

EXAMPLE_FROM = REL_EXAMPLE_FROM
TEACHES = REL_TEACHES
CHILD_RELATIONS = (REL_HAS_CONTEXT, REL_HAS_CLOZE)
#: Bind parameters shared by the relation-graph SQL (endpoint types, status), so no strings are hard-coded in SQL.
#: 關係圖 SQL 共用的繫結參數（端點類型、狀態），不在 SQL 裡寫死字串。
_REL_PARAMS = {"note": ENTITY_NOTE, "line": ENTITY_SCRIPT_LINE, "active": REL_STATUS_ACTIVE}


@dataclass
class BuildRecord:
    """One flowchart build, without its graph.

    一個流程圖版本（不含 graph）。
    """

    build_id: int
    game_id: str
    status: str
    config: dict[str, Any]
    stats: dict[str, Any]
    engine: str
    titles: dict[str, str]
    languages: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LineBgm:
    """One BGM track played during a line (``game_line_bgm`` joined with ``game_bgm_tracks``).

    一句台詞播放的一首 BGM（``game_line_bgm`` join ``game_bgm_tracks``）。

    ``archive_index`` is the track's file index inside the game archive (``bgm.xp3``) and is not returned by the API;
    it is kept for matching BGM audio files later.

    ``archive_index`` 是曲子在遊戲封包（``bgm.xp3``）裡的檔案序號，API 不回傳；留著是為了之後
    整理 BGM 音檔時對照。
    """

    code: str
    ord: int
    title_ja: str | None = None
    title_zh: str | None = None
    gallery_no: int | None = None
    archive_index: int | None = None


@dataclass
class LineRecord:
    """One line, for the drawer and saves.

    一句台詞（抽屜與存檔用）。
    """

    line_id: int
    seq: int
    kind: str
    point: int | None
    file_name: str
    label: str
    speaker_ja: str | None
    speaker_zh: str | None
    text_ja: str
    text_zh: str | None
    voice: str | None
    thumb: str | None
    is_h: bool
    h_label: str | None
    chapter_label: str | None
    attrs: dict[str, Any]
    #: The BGM tracks in ``ord`` order; an empty list when there is no BGM.
    #: 依 ``ord`` 排列；沒有 BGM 時是空清單。
    bgm: list[LineBgm] = field(default_factory=list)
    #: How the BGM was detected: ``game_lines.bgm_detect`` (``A``, ``實測``, ``B推定``, or ``None``).
    #: ``game_lines.bgm_detect``（``A``／``實測``／``B推定``／``None``）。
    bgm_detect: str | None = None
    #: Whether the BGM may change during the line: ``game_lines.bgm_may_change``.
    #: ``game_lines.bgm_may_change``。
    bgm_may_change: bool = False


@dataclass
class CardLink:
    """A note whose example comes from a line.

    一張筆記取自一句台詞。
    """

    line_id: int
    note_id: int
    card_type: str | None
    verb: str | None


class StoryRepository(Protocol):
    """The queries the service layer needs.

    服務層需要的查詢。
    """

    async def active_builds(self) -> list[BuildRecord]:
        """Return the active build of every game.

        回傳每款遊戲上線中的版本。

        Returns:
            the active builds, ordered by game id
            上線中的版本，依遊戲代號排序
        """
        ...

    async def active_build(self, game_id: str) -> BuildRecord | None:
        """Return the active build of a game.

        回傳一款遊戲上線中的版本。

        Args:
            game_id: the game id
                遊戲代號

        Returns:
            the active build, or ``None`` if the game has none
            上線中的版本；沒有時為 ``None``
        """
        ...

    async def build(self, game_id: str, build_id: int) -> BuildRecord | None:
        """Return a readable build of a game (``active``, ``retired``, or ``ready``).

        回傳一款遊戲可讀的版本（``active``、``retired`` 或 ``ready``）。

        Args:
            game_id: the game id
                遊戲代號
            build_id: the build id
                版本 ID

        Returns:
            the build, or ``None`` if it does not exist, belongs to another game, or is not readable
            版本；不存在、屬於別款遊戲或不能讀時為 ``None``
        """
        ...

    async def graph(self, build_id: int) -> dict[str, Any]:
        """Return the stored graph JSON of a build.

        回傳版本存下來的劇情圖 JSON。

        Args:
            build_id: the build id; must exist
                版本 ID；必須存在
        """
        ...

    async def node_lines(self, build_id: int, node_key: str, offset: int, limit: int) -> tuple[int, list[LineRecord]]:
        """Return one page of a node's lines, with their BGM.

        回傳一個節點的一頁台詞（含 BGM）。

        Args:
            build_id: the build id
                版本 ID
            node_key: the node
                節點
            offset: the first ``seq`` of the page
                這一頁的第一個 ``seq``
            limit: the page size
                每頁筆數

        Returns:
            the node's total line count and the lines with ``offset <= seq < offset + limit``, in ``seq`` order
            節點的台詞總數，以及 ``offset <= seq < offset + limit`` 的台詞（依 ``seq`` 排序）
        """
        ...

    async def line_in_build(self, build_id: int, line_id: int) -> tuple[str, LineRecord] | None:
        """Find a line in a build.

        在版本中找一句台詞。

        Args:
            build_id: the build id
                版本 ID
            line_id: the line id
                台詞 ID

        Returns:
            the node key and the line (with its BGM), or ``None`` if the line is not in the build
            節點鍵與台詞（含 BGM）；台詞不在版本中時為 ``None``
        """
        ...

    async def card_links(self, line_ids: list[int]) -> list[CardLink]:
        """Return the active notes whose examples come from the given lines.

        回傳例句取自這些台詞、且有效的筆記。

        Args:
            line_ids: the lines
                台詞

        Returns:
            one link per note and taught term; ``verb`` is ``None`` when the term is missing or cannot be parsed;
            empty if ``line_ids`` is empty
            每個筆記與所教的詞各一筆；沒有詞或無法解析時 ``verb`` 為 ``None``；``line_ids`` 為空時為空串列
        """
        ...

    async def next_annotated_seq(self, build_id: int, node_key: str, after: int) -> int | None:
        """Return the next ``seq`` in a node, after ``after``, whose line has a card.

        回傳節點中 ``after`` 之後、下一句有卡片的台詞 ``seq``。

        Args:
            build_id: the build id
                版本 ID
            node_key: the node
                節點
            after: the ``seq`` to search after (exclusive)
                從這個 ``seq`` 之後找（不含）

        Returns:
            the ``seq``, or ``None`` if there is none
            ``seq``；沒有時為 ``None``
        """
        ...

    async def annotation_counts(self, build_id: int) -> list[tuple[str, str | None, int]]:
        """Return, per node and card type, the number of distinct lines carrying cards.

        回傳每個節點、每種卡片類型有卡片的台詞句數（不重複）。

        Args:
            build_id: the build id
                版本 ID

        Returns:
            ``(node_key, card_type, line count)`` tuples
            ``(node_key, card_type, 台詞句數)`` 元組
        """
        ...

    async def verb_index(self, build_id: int) -> list[tuple[str, str | None, str]]:
        """Return the distinct verbs taught by cards in each node.

        回傳每個節點中，卡片所教的不重複動詞。

        Args:
            build_id: the build id
                版本 ID

        Returns:
            ``(node_key, card_type, verb)`` tuples; terms that cannot be parsed are left out
            ``(node_key, card_type, 動詞)`` 元組；無法解析的詞不列入
        """
        ...

    async def lines_of_note(self, note_id: int) -> list[int]:
        """Return the lines a note's examples come from.

        回傳筆記的例句取自哪些台詞。

        When the parent note has no direct example, the lines are found through its child notes (context, cloze).

        母卡沒有直接的例句時，經由子卡（情境、填空）找。

        Args:
            note_id: the note id
                筆記 ID

        Returns:
            the line ids; empty if none
            台詞 ID；沒有時為空串列
        """
        ...


def _json(raw: Any) -> Any:
    if raw is None or isinstance(raw, (dict, list)):
        return raw
    return json.loads(raw)


def _verb(term: str | None) -> str | None:
    """Turn a term key into its lemma (``ja-JP:見る`` → ``見る``).

    詞的鍵 → 原形（``ja-JP:見る`` → ``見る``）。

    Parsing is always left to ``corpus_tables.lemma_of``. A malformed or non-Japanese key is logged as WARNING and
    yields ``None`` (the card then carries no verb), so one bad record cannot break the whole annotation or line API.

    解析一律交給 ``corpus_tables.lemma_of``。格式不對或不是日文的鍵記 WARNING 並回 ``None``（這張卡就不帶動詞），
    不讓一筆壞資料拖垮整個註記或台詞 API。

    Args:
        term: the term key; ``None`` or empty yields ``None``
            詞的鍵；``None`` 或空字串時回 ``None``

    Returns:
        the lemma, or ``None``
        原形，或 ``None``
    """
    if not term:
        return None
    try:
        return lemma_of(term)
    except ValueError:
        logger.warning("rel_links 裡有無法解析的詞的鍵 %r，略過它的動詞", term)
        return None


_LINE_COLUMNS = """
    n.seq, l.id AS line_id, l.kind, l.point, l.text_ja_jp, l.text_zh_hant, l.voice_file, l.thumb,
    l.is_h, l.h_label, l.chapter_label, l.attrs, l.bgm_detect, l.bgm_may_change, s.label, f.file_name,
    sp.name_ja_jp AS speaker_ja, sp.name_zh_hant AS speaker_zh
"""
_LINE_JOINS = """
    FROM story_node_lines n
    JOIN game_lines l ON l.id = n.line_id
    JOIN game_scenes s ON s.scene_id = l.scene_id
    JOIN game_script_files f ON f.file_id = s.file_id
    LEFT JOIN game_speakers sp ON sp.speaker_id = l.speaker_id
"""


def _line_record(r: Any) -> LineRecord:
    return LineRecord(
        line_id=int(r.line_id), seq=int(r.seq), kind=r.kind, point=r.point, file_name=r.file_name,
        label=r.label, speaker_ja=r.speaker_ja, speaker_zh=r.speaker_zh, text_ja=r.text_ja_jp,
        text_zh=r.text_zh_hant, voice=r.voice_file, thumb=r.thumb, is_h=bool(r.is_h), h_label=r.h_label,
        chapter_label=r.chapter_label, attrs=_json(r.attrs) or {}, bgm_detect=r.bgm_detect,
        bgm_may_change=bool(r.bgm_may_change))


class MySQLStoryRepository:
    """The MySQL implementation of :class:`StoryRepository` over the corpus async engine.

    以語料庫的非同步引擎實作的 ``StoryRepository``。

    See :class:`StoryRepository` for the contract of each query.

    各查詢的契約見 ``StoryRepository``。
    """

    def __init__(self, session_factory=None) -> None:
        """Create a repository.

        建立資料庫存取物件。

        Args:
            session_factory: the async session factory; ``None`` uses ``story_session_factory()`` at each query
                非同步 session 工廠；``None`` 時每次查詢使用 ``story_session_factory()``
        """
        self._factory = session_factory

    def _session(self):
        return (self._factory or story_session_factory())()

    async def _build_rows(self, where: str, params: dict[str, Any]) -> list[BuildRecord]:
        sql = text(f"""
            SELECT b.build_id, b.game_id, b.status, b.config, b.stats, g.engine, g.title_ja_jp, g.title_zh_hant,
                   g.languages
            FROM story_builds b JOIN game_profiles g ON g.game_id = b.game_id
            WHERE {where} ORDER BY b.game_id""")
        async with self._session() as session:
            rows = (await session.execute(sql, params)).all()
        return [BuildRecord(
            build_id=int(r.build_id), game_id=r.game_id, status=r.status, config=_json(r.config) or {},
            stats=_json(r.stats) or {}, engine=r.engine,
            titles={k: v for k, v in (("ja-JP", r.title_ja_jp), ("zh-Hant", r.title_zh_hant)) if v},
            languages=_json(r.languages) or []) for r in rows]

    async def active_builds(self) -> list[BuildRecord]:
        """Return the active build of every game.

        回傳每款遊戲上線中的版本。

        See ``StoryRepository.active_builds`` for the contract.

        契約見 ``StoryRepository.active_builds``。
        """
        return await self._build_rows("b.status = 'active'", {})

    async def active_build(self, game_id: str) -> BuildRecord | None:
        """Return the active build of a game.

        回傳一款遊戲上線中的版本。

        See ``StoryRepository.active_build`` for the contract.

        契約見 ``StoryRepository.active_build``。
        """
        rows = await self._build_rows("b.game_id = :g AND b.status = 'active'", {"g": game_id})
        return rows[0] if rows else None

    async def build(self, game_id: str, build_id: int) -> BuildRecord | None:
        """Return a readable build of a game.

        回傳一款遊戲可讀的版本。

        See ``StoryRepository.build`` for the contract.

        契約見 ``StoryRepository.build``。
        """
        rows = await self._build_rows("b.game_id = :g AND b.build_id = :b AND b.status IN ('active', 'retired', 'ready')",
                                      {"g": game_id, "b": build_id})
        return rows[0] if rows else None

    async def graph(self, build_id: int) -> dict[str, Any]:
        """Return the stored graph JSON of a build.

        回傳版本存下來的劇情圖 JSON。

        See ``StoryRepository.graph`` for the contract.

        契約見 ``StoryRepository.graph``。
        """
        async with self._session() as session:
            raw = (await session.execute(text("SELECT graph FROM story_builds WHERE build_id = :b"),
                                         {"b": build_id})).scalar_one()
        return _json(raw)

    async def node_lines(self, build_id: int, node_key: str, offset: int, limit: int) -> tuple[int, list[LineRecord]]:
        """Return one page of a node's lines, with their BGM.

        回傳一個節點的一頁台詞（含 BGM）。

        See ``StoryRepository.node_lines`` for the contract.

        契約見 ``StoryRepository.node_lines``。
        """
        async with self._session() as session:
            total = (await session.execute(
                text("SELECT COUNT(*) FROM story_node_lines WHERE build_id = :b AND node_key = :k"),
                {"b": build_id, "k": node_key})).scalar_one()
            rows = (await session.execute(text(f"""
                SELECT {_LINE_COLUMNS} {_LINE_JOINS}
                WHERE n.build_id = :b AND n.node_key = :k AND n.seq >= :lo AND n.seq < :hi
                ORDER BY n.seq"""), {"b": build_id, "k": node_key, "lo": offset, "hi": offset + limit})).all()
            records = [_line_record(r) for r in rows]
            await self._attach_bgm(session, records)
        return int(total), records

    async def line_in_build(self, build_id: int, line_id: int) -> tuple[str, LineRecord] | None:
        """Find a line in a build.

        在版本中找一句台詞。

        See ``StoryRepository.line_in_build`` for the contract.

        契約見 ``StoryRepository.line_in_build``。
        """
        async with self._session() as session:
            row = (await session.execute(text(f"""
                SELECT n.node_key, {_LINE_COLUMNS} {_LINE_JOINS}
                WHERE n.build_id = :b AND n.line_id = :l"""), {"b": build_id, "l": line_id})).first()
            if row is None:
                return None
            record = _line_record(row)
            await self._attach_bgm(session, [record])
        return row.node_key, record

    async def _attach_bgm(self, session, records: list[LineRecord]) -> None:
        """Load the BGM of these lines in one query (in ``ord`` order) and fill ``record.bgm``.

        一次查出這些台詞的 BGM（依 ``ord``）並填進 ``record.bgm``。
        """
        if not records:
            return
        sql = text("""
            SELECT lb.line_id, lb.ord, t.code, t.title_ja_jp, t.title_zh_hant, t.gallery_no, t.archive_index
            FROM game_line_bgm lb JOIN game_bgm_tracks t ON t.track_id = lb.track_id
            WHERE lb.line_id IN :ids ORDER BY lb.line_id, lb.ord""").bindparams(bindparam("ids", expanding=True))
        rows = (await session.execute(sql, {"ids": [r.line_id for r in records]})).all()
        by_line: dict[int, list[LineBgm]] = {}
        for r in rows:
            by_line.setdefault(int(r.line_id), []).append(LineBgm(
                code=r.code, ord=int(r.ord), title_ja=r.title_ja_jp, title_zh=r.title_zh_hant,
                gallery_no=r.gallery_no, archive_index=r.archive_index))
        for rec in records:
            rec.bgm = by_line.get(rec.line_id, [])

    async def card_links(self, line_ids: list[int]) -> list[CardLink]:
        """Return the active notes whose examples come from the given lines.

        回傳例句取自這些台詞、且有效的筆記。

        See ``StoryRepository.card_links`` for the contract.

        契約見 ``StoryRepository.card_links``。
        """
        if not line_ids:
            return []
        sql = text("""
            SELECT r.dst_id, r.src_id, JSON_UNQUOTE(JSON_EXTRACT(r.attrs, '$.card_type')) AS card_type, t.dst_id AS term
            FROM rel_links r
            LEFT JOIN rel_links t ON t.src_type = :note AND t.src_id = r.src_id
                 AND t.relation_type = :teaches AND t.status = :active
            WHERE r.relation_type = :rel AND r.dst_type = :line AND r.status = :active
              AND r.src_type = :note AND r.dst_id IN :ids""").bindparams(bindparam("ids", expanding=True))
        async with self._session() as session:
            rows = (await session.execute(sql, {**_REL_PARAMS, "rel": EXAMPLE_FROM, "teaches": TEACHES,
                                                "ids": [str(i) for i in line_ids]})).all()
        return [CardLink(int(r.dst_id), int(r.src_id), r.card_type, _verb(r.term)) for r in rows]

    async def next_annotated_seq(self, build_id: int, node_key: str, after: int) -> int | None:
        """Return the next ``seq`` in a node, after ``after``, whose line has a card.

        回傳節點中 ``after`` 之後、下一句有卡片的台詞 ``seq``。

        See ``StoryRepository.next_annotated_seq`` for the contract.

        契約見 ``StoryRepository.next_annotated_seq``。
        """
        sql = text("""
            SELECT MIN(n.seq) FROM story_node_lines n
            JOIN rel_links r ON r.dst_type = :line AND r.dst_id = CAST(n.line_id AS CHAR) COLLATE utf8mb4_bin
                 AND r.relation_type = :rel AND r.status = :active
            WHERE n.build_id = :b AND n.node_key = :k AND n.seq > :after""")
        async with self._session() as session:
            value = (await session.execute(sql, {**_REL_PARAMS, "rel": EXAMPLE_FROM, "b": build_id, "k": node_key,
                                                 "after": after})).scalar()
        return int(value) if value is not None else None

    async def annotation_counts(self, build_id: int) -> list[tuple[str, str | None, int]]:
        """Return, per node and card type, the number of distinct lines carrying cards.

        回傳每個節點、每種卡片類型有卡片的台詞句數（不重複）。

        See ``StoryRepository.annotation_counts`` for the contract.

        契約見 ``StoryRepository.annotation_counts``。
        """
        sql = text("""
            SELECT n.node_key, JSON_UNQUOTE(JSON_EXTRACT(r.attrs, '$.card_type')) AS card_type,
                   COUNT(DISTINCT n.line_id) AS line_count
            FROM rel_links r
            JOIN story_node_lines n ON n.build_id = :b AND n.line_id = CAST(r.dst_id AS UNSIGNED)
            WHERE r.relation_type = :rel AND r.dst_type = :line AND r.status = :active
            GROUP BY n.node_key, card_type""")
        async with self._session() as session:
            rows = (await session.execute(sql, {**_REL_PARAMS, "b": build_id, "rel": EXAMPLE_FROM})).all()
        return [(r.node_key, r.card_type, int(r.line_count)) for r in rows]

    async def verb_index(self, build_id: int) -> list[tuple[str, str | None, str]]:
        """Return the distinct verbs taught by cards in each node.

        回傳每個節點中，卡片所教的不重複動詞。

        See ``StoryRepository.verb_index`` for the contract.

        契約見 ``StoryRepository.verb_index``。
        """
        sql = text("""
            SELECT DISTINCT n.node_key, JSON_UNQUOTE(JSON_EXTRACT(r.attrs, '$.card_type')) AS card_type, t.dst_id AS term
            FROM rel_links r
            JOIN story_node_lines n ON n.build_id = :b AND n.line_id = CAST(r.dst_id AS UNSIGNED)
            JOIN rel_links t ON t.src_type = :note AND t.src_id = r.src_id
                 AND t.relation_type = :teaches AND t.status = :active
            WHERE r.relation_type = :rel AND r.dst_type = :line AND r.status = :active""")
        async with self._session() as session:
            rows = (await session.execute(sql, {**_REL_PARAMS, "b": build_id, "rel": EXAMPLE_FROM, "teaches": TEACHES})).all()
        verbs = ((r.node_key, r.card_type, _verb(r.term)) for r in rows)
        return [(node_key, card_type, verb) for node_key, card_type, verb in verbs if verb]

    async def lines_of_note(self, note_id: int) -> list[int]:
        """Return the lines a note's examples come from, through child notes if there is no direct one.

        回傳筆記取自哪些台詞；母卡沒有直接的例句時，經由子卡（情境、填空）找。

        Line ids that are not integers are skipped.

        不是整數的台詞 ID 會略過。

        Args:
            note_id: the note id
                筆記 ID

        Returns:
            the line ids; empty if none
            台詞 ID；沒有時為空串列
        """
        direct = text("""
            SELECT dst_id FROM rel_links
            WHERE relation_type = :rel AND src_type = :note AND src_id = :n AND dst_type = :line
              AND status = :active""")
        via_children = text("""
            SELECT r2.dst_id FROM rel_links r1
            JOIN rel_links r2 ON r2.src_type = :note AND r2.src_id = r1.dst_id AND r2.relation_type = :rel
                 AND r2.dst_type = :line AND r2.status = :active
            WHERE r1.src_type = :note AND r1.src_id = :n AND r1.dst_type = :note AND r1.status = :active
              AND r1.relation_type IN :children""").bindparams(bindparam("children", expanding=True))
        async with self._session() as session:
            rows = (await session.execute(direct, {**_REL_PARAMS, "rel": EXAMPLE_FROM, "n": str(note_id)})).all()
            if not rows:
                rows = (await session.execute(via_children, {**_REL_PARAMS, "rel": EXAMPLE_FROM, "n": str(note_id),
                                                             "children": list(CHILD_RELATIONS)})).all()
        out: list[int] = []
        for r in rows:
            try:
                out.append(int(r.dst_id))
            except (TypeError, ValueError):
                continue
        return out
