"""``story_builds``／``story_node_lines`` 的查詢，以及 join 語料庫的 ``game_*``、``rel_links``。

Queries over ``story_builds``/``story_node_lines`` joined with the corpus
``game_*`` tables and ``rel_links`` (D2「索引對應的查詢」).

全部是唯讀查詢；寫入只在匯入腳本。服務層只依賴 :class:`StoryRepository` 介面，測試用記憶體
版本替換。
Read-only; writes happen in the import script. The service depends on the
:class:`StoryRepository` protocol so tests can swap in a fake.
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
#: 關係圖 SQL 共用的繫結參數（端點類型、狀態），不在 SQL 裡寫死字串。Shared bind params.
_REL_PARAMS = {"note": ENTITY_NOTE, "line": ENTITY_SCRIPT_LINE, "active": REL_STATUS_ACTIVE}


@dataclass
class BuildRecord:
    """一個流程圖版本（不含 graph）。A build without its graph."""

    build_id: int
    game_id: str
    status: str
    config: dict[str, Any]
    stats: dict[str, Any]
    engine: str
    titles: dict[str, str]
    languages: list[str] = field(default_factory=list)


@dataclass
class LineRecord:
    """一句台詞（抽屜與存檔用）。One line for the drawer and saves."""

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
    bgm: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CardLink:
    """一張筆記取自一句台詞。A note whose example comes from a line."""

    line_id: int
    note_id: int
    card_type: str | None
    verb: str | None


class StoryRepository(Protocol):
    """服務層需要的查詢。Queries used by the service."""

    async def active_builds(self) -> list[BuildRecord]: ...
    async def active_build(self, game_id: str) -> BuildRecord | None: ...
    async def build(self, game_id: str, build_id: int) -> BuildRecord | None: ...
    async def graph(self, build_id: int) -> dict[str, Any]: ...
    async def node_lines(self, build_id: int, node_key: str, offset: int, limit: int) -> tuple[int, list[LineRecord]]: ...
    async def line_in_build(self, build_id: int, line_id: int) -> tuple[str, LineRecord] | None: ...
    async def card_links(self, line_ids: list[int]) -> list[CardLink]: ...
    async def next_annotated_seq(self, build_id: int, node_key: str, after: int) -> int | None: ...
    async def annotation_counts(self, build_id: int) -> list[tuple[str, str | None, int]]: ...
    async def verb_index(self, build_id: int) -> list[tuple[str, str | None, str]]: ...
    async def lines_of_note(self, note_id: int) -> list[int]: ...


def _json(raw: Any) -> Any:
    if raw is None or isinstance(raw, (dict, list)):
        return raw
    return json.loads(raw)


def _verb(term: str | None) -> str | None:
    """詞的鍵 → 原形（``ja-JP:見る`` → ``見る``）；解析一律交給 ``corpus_tables.lemma_of``。

    格式不對或不是日文的鍵記 WARNING 並回 None（這張卡就不帶動詞），不讓一筆壞資料拖垮整個
    註記或台詞 API。
    Term key to lemma via the single strict parser in
    :mod:`app.infrastructure.database.corpus_tables`; a malformed or
    non-Japanese key is logged and yields None instead of failing the request.
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
    l.is_h, l.h_label, l.chapter_label, l.attrs, s.label, f.file_name,
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
        chapter_label=r.chapter_label, attrs=_json(r.attrs) or {})


class MySQLStoryRepository:
    """以語料庫的非同步引擎實作。MySQL implementation over the corpus engine."""

    def __init__(self, session_factory=None) -> None:
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
        return await self._build_rows("b.status = 'active'", {})

    async def active_build(self, game_id: str) -> BuildRecord | None:
        rows = await self._build_rows("b.game_id = :g AND b.status = 'active'", {"g": game_id})
        return rows[0] if rows else None

    async def build(self, game_id: str, build_id: int) -> BuildRecord | None:
        rows = await self._build_rows("b.game_id = :g AND b.build_id = :b AND b.status IN ('active', 'retired', 'ready')",
                                      {"g": game_id, "b": build_id})
        return rows[0] if rows else None

    async def graph(self, build_id: int) -> dict[str, Any]:
        async with self._session() as session:
            raw = (await session.execute(text("SELECT graph FROM story_builds WHERE build_id = :b"),
                                         {"b": build_id})).scalar_one()
        return _json(raw)

    async def node_lines(self, build_id: int, node_key: str, offset: int, limit: int) -> tuple[int, list[LineRecord]]:
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
        if not records:
            return
        sql = text("""
            SELECT lb.line_id, lb.ord, t.code, t.title_ja_jp, t.title_zh_hant
            FROM game_line_bgm lb JOIN game_bgm_tracks t ON t.track_id = lb.track_id
            WHERE lb.line_id IN :ids ORDER BY lb.line_id, lb.ord""").bindparams(bindparam("ids", expanding=True))
        rows = (await session.execute(sql, {"ids": [r.line_id for r in records]})).all()
        by_line: dict[int, list[dict[str, Any]]] = {}
        for r in rows:
            by_line.setdefault(int(r.line_id), []).append(
                {"code": r.code, "title_ja": r.title_ja_jp, "title_zh": r.title_zh_hant})
        for rec in records:
            rec.bgm = by_line.get(rec.line_id, [])

    async def card_links(self, line_ids: list[int]) -> list[CardLink]:
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
        """筆記取自哪些台詞；母卡沒有直接的例句時，經由子卡（情境、填空）找。Lines of a note."""
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
