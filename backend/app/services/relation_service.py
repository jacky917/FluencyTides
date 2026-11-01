"""
卡片關聯業務邏輯服務模組。

負責與語料庫（MySQL ``fluencytides_corpus``）的通用關係表 ``rel_links`` / ``rel_types``
互動，提供卡片關聯的建立、批次建立、連動刪除（清理孤兒節點）以及高效的知識圖譜資料查詢。

Card relation business-logic service module.

Works against the generic relation tables ``rel_links`` / ``rel_types`` of
the corpus database (MySQL ``fluencytides_corpus``), providing creation,
batch creation, cascade deletion (orphan cleanup), and efficient
knowledge-graph data queries.

**2026-11-01 改版**（docs/wip/corpus_db_rebuild_FEAT_2026-10-31.md D9 的 C）：原本讀寫網站 SQLite 的
``card_relations`` / ``relation_types``，改成讀寫語料庫的 ``rel_links`` / ``rel_types``；SQLite 的兩張表
原樣保留，程式不再讀寫。對外的介面與 API 回應不變，前端不用改：

- 關係類型對外仍是舊名，存進資料庫時加上命名空間：``has_context`` ↔ ``core.has_context``、
  ``has_cloze`` ↔ ``core.has_cloze``，其餘（``synonym``、``conversation``、使用者自訂）↔ ``user.<名稱>``
  （``owner='user'``）。使用者不能指定命名空間：``core.*`` 只能經由上面兩個對外名稱使用，
  自動註冊只會建立 ``user.*``。
- 筆記端點寫成 ``src_type/dst_type='note'``，ID 存字串；懸空節點照舊靠標籤。
- 刪除一律把 ``status`` 標成 ``deleted``，不刪列（``rel_links`` 是紀錄表）。
- 知識圖譜只看「筆記 → 筆記」的關係；建卡管線寫的「筆記 → 台詞／詞」不出現在圖上，但刪卡與同步清理
  時，只要端點是那張筆記，一樣會被標成 ``deleted``。

The SQLite tables stay in place but are no longer used. Public names and
API responses are unchanged: relation types are stored namespaced
(``core.*`` / ``user.*``) and exposed under their old names; deletions mark
rows ``deleted``; the graph only shows note → note relations.
"""

import json
import logging
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from app.schemas.relation import CardRelationDelete
    from app.schemas.anki import AnkiNoteInfo
    from app.infrastructure.anki.client import AnkiClient

from sqlalchemy import CursorResult, and_, func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.exceptions import (
    FluencyTidesError,
    InvalidRelationTypeError,
    RelationSyncEmptyAnkiError,
    RelationSyncNeedsForceError,
)
from app.infrastructure.anki.client import AnkiConnectError
from app.infrastructure.database.corpus_tables import (
    ENTITY_NOTE,
    REL_HAS_CLOZE,
    REL_HAS_CONTEXT,
    REL_STATUS_ACTIVE,
    REL_STATUS_DELETED,
    rel_links_table,
    rel_types_table,
)
from app.schemas.relation import CardRelationCreate, CardRelationRead

logger = logging.getLogger(__name__)

# 同步清理：資料庫時鐘的餘裕（created_at 由資料庫填入，只到秒）
SYNC_CLOCK_MARGIN_SECONDS = 2
# 比例防護：要刪的關聯超過這個列數、且超過考慮範圍的一半時，需要 force
SYNC_GUARD_MIN_ROWS = 20
# 每批刪除的孤兒筆記 ID 數。正式環境是 MySQL，分批是為了讓單條 UPDATE 的大小有上限；
# 每條帶 2 × 批次 + 數個參數（起點、終點各展開一次），400 → 約 805 個，也讓測試用的 SQLite
# 不超過參數上限（3.32 之前是 999）。
# Orphan note ids per UPDATE: production is MySQL, so batching keeps each
# statement bounded; ~805 parameters also keep the SQLite test DB under its
# parameter limit (999 before 3.32).
SYNC_DELETE_BATCH = 400

# 網站建立的關係，``created_by`` 預設是這個。Default ``created_by`` of website relations.
USER_CREATED_BY = "user"
USER_OWNER = "user"
USER_PREFIX = "user."

# 舊名 → 系統關係（D9 的 C）。Old names that map to system relations.
_SYSTEM_TYPES = {"has_context": REL_HAS_CONTEXT, "has_cloze": REL_HAS_CLOZE}
# 系統命名空間：使用者給的名稱不能用它開頭。System namespace users may not name directly.
CORE_PREFIX = "core."
# 對外顯示時去掉的命名空間。Namespaces stripped for display.
_DISPLAY_PREFIXES = (CORE_PREFIX, USER_PREFIX)
# 使用者關係名稱允許的字元：字母（含中日文）、數字、底線、連字號；不能有 ``.``（命名空間分隔）
# 與空白。長度扣掉 ``user.`` 後要放得進 ``rel_types.relation_type``（64）。
# Allowed user relation names: word characters (any script) and hyphens; no
# dots (namespace separator) or spaces, and short enough for VARCHAR(64).
_USER_NAME_PATTERN = re.compile(r"[\w\-]+")
_USER_NAME_MAX = 64 - len(USER_PREFIX)

_links = rel_links_table
_types = rel_types_table


def storage_relation_type(name: str) -> str:
    """把對外的關係名稱轉成 ``rel_types.relation_type``。

    Map a public relation name to ``rel_types.relation_type``.

    ``has_context`` / ``has_cloze`` 是系統關係（``core.*``）；其餘都是使用者的關係，一律加上
    ``user.``。使用者不能自己指定命名空間：空白、以 ``core.`` 開頭、或含允許字元以外（例如 ``.``、
    空白）的名稱都拒絕，避免網站建出 ``core.example_from`` 這類系統關係。
    The two child-card relations are system types; every other name becomes
    ``user.<name>``. Blank names, names starting with ``core.`` and names
    with characters outside the allowed set are rejected, so users can never
    create or address system relation types.

    Args:
        name: 對外的名稱（會轉小寫、去空白）。Public name (lower-cased, stripped).

    Returns:
        str: 資料庫的關係類型。The stored relation type.

    Raises:
        InvalidRelationTypeError: 名稱不合法（API 回 400）。The name is invalid (HTTP 400).
    """
    normalized = name.strip().lower()
    if normalized in _SYSTEM_TYPES:
        return _SYSTEM_TYPES[normalized]
    if not normalized:
        raise InvalidRelationTypeError("關係類型不能是空白")
    if normalized.startswith(CORE_PREFIX):
        raise InvalidRelationTypeError(f"關係類型 {name!r} 使用了系統命名空間 {CORE_PREFIX!r}")
    if len(normalized) > _USER_NAME_MAX or not _USER_NAME_PATTERN.fullmatch(normalized):
        raise InvalidRelationTypeError(
            f"關係類型 {name!r} 不合法：只能用字母、數字、底線與連字號，最多 {_USER_NAME_MAX} 個字"
        )
    return f"{USER_PREFIX}{normalized}"


def display_relation_type(relation_type: str) -> str:
    """把 ``rel_types.relation_type`` 轉回對外的名稱（去掉 ``core.`` / ``user.``）。

    Map a stored relation type back to its public name.

    Args:
        relation_type: 資料庫的關係類型。The stored relation type.

    Returns:
        str: 對外名稱；其他命名空間（卡片類型自己的關係）原樣回傳。The public name.
    """
    for prefix in _DISPLAY_PREFIXES:
        if relation_type.startswith(prefix):
            return relation_type[len(prefix):]
    return relation_type


def _note_ref(note_id: int | None) -> str | None:
    """筆記 ID 轉成 ``rel_links`` 的字串 ID。Note id → string endpoint id."""
    return str(int(note_id)) if note_id is not None else None


def _note_id(value: str | None) -> int | None:
    """``rel_links`` 的字串 ID 轉回筆記 ID；不是整數時為 None。String endpoint id → note id."""
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class RelationRecord:
    """一條「筆記 → 筆記」的關係（欄位名稱沿用舊的 ``CardRelation``，供圖譜與 API 使用）。

    One note → note relation, with the attribute names of the old
    ``CardRelation`` model so graph code and ``CardRelationRead`` keep working.

    Attributes:
        id: ``rel_links.relation_id``。Relation id.
        source_note_id: 起點筆記 ID；懸空時為 None。Source note id.
        target_note_id: 終點筆記 ID；懸空時為 None。Target note id.
        relation_type: 對外的關係名稱（``has_context``、``synonym``…）。Public relation name.
        source_label: 起點標籤。Source label.
        target_label: 終點標籤。Target label.
        created_at: 建立時間。Creation time.
    """

    id: int
    source_note_id: int | None
    target_note_id: int | None
    relation_type: str
    source_label: str
    target_label: str
    created_at: datetime


_RECORD_COLUMNS = (
    _links.c.relation_id,
    _links.c.src_id,
    _links.c.dst_id,
    _links.c.relation_type,
    _links.c.src_label,
    _links.c.dst_label,
    _links.c.created_at,
)


def _record(row: Any) -> RelationRecord:
    """查詢列轉成 :class:`RelationRecord`。Map a result row to a RelationRecord."""
    return RelationRecord(
        id=int(row[0]),
        source_note_id=_note_id(row[1]),
        target_note_id=_note_id(row[2]),
        relation_type=display_relation_type(row[3]),
        source_label=row[4] or "",
        target_label=row[5] or "",
        created_at=row[6],
    )


def _active() -> ColumnElement[bool]:
    """有效的關係。Active relations."""
    return _links.c.status == REL_STATUS_ACTIVE


def _note_to_note() -> ColumnElement[bool]:
    """兩端都是筆記的關係（知識圖譜的範圍）。Relations between two notes."""
    return and_(_links.c.src_type == ENTITY_NOTE, _links.c.dst_type == ENTITY_NOTE)


def _touches_notes(note_ids: list[str]) -> ColumnElement[bool]:
    """任一端是這些筆記的關係。Relations with either endpoint among these notes."""
    return or_(
        and_(_links.c.src_type == ENTITY_NOTE, _links.c.src_id.in_(note_ids)),
        and_(_links.c.dst_type == ENTITY_NOTE, _links.c.dst_id.in_(note_ids)),
    )


def _row_count(result: Any) -> int | None:
    """驅動回報的筆數；未回報時為 None。The reported row count, or None."""
    count = cast(CursorResult[Any], result).rowcount
    return None if count is None or count < 0 else count


@dataclass(frozen=True)
class RelationSyncResult:
    """同步清理的結果。The result of syncing relations with Anki.

    Attributes:
        scanned_notes: Anki 回傳的筆記數。Notes Anki returned.
        orphan_notes: 關聯引用、但 Anki 已沒有的筆記數。Referenced notes missing in Anki.
        deleted: 標成刪除的關聯數；資料庫驅動未回報筆數時為預估值。Relations deleted (the estimate
            when the driver reports no row count).
        considered: 考慮範圍內（掃描開始前 2 秒以上建立）的關聯數。Relations considered.
        ankiweb_synced: 取筆記前與 AnkiWeb 的同步是否成功；失敗時以本機收藏為準。必填，不給預設值：
            忘了傳時不能靜默地報成「同步成功」。Whether the AnkiWeb sync before scanning succeeded.
    """

    scanned_notes: int
    orphan_notes: int
    deleted: int
    considered: int
    ankiweb_synced: bool


class RelationService:
    """卡片關聯管理服務。

    Card relation management service.

    提供操作語料庫 ``rel_links`` / ``rel_types`` 的方法，
    並抽象出前端 react-force-graph 所需的 graph_data 結構轉換。

    Provides methods operating on the corpus ``rel_links`` / ``rel_types``
    tables and abstracts the graph_data structure needed by the
    react-force-graph frontend.

    Attributes:
        _db_session: 注入的語料庫非同步 Session。Injected async corpus session.
    """

    def __init__(self, db_session: AsyncSession) -> None:
        """初始化 RelationService。

        Initialize the RelationService.

        Args:
            db_session: 語料庫的 AsyncSession（``get_corpus_async_session``）。The
                corpus AsyncSession.
        """
        self._db_session = db_session

    async def _insert(
        self, request: CardRelationCreate, relation_type: str, *, created_by: str, attrs: dict | None,
    ) -> int:
        """新增一條「筆記 → 筆記」的關係（不 commit），回傳 relation_id。Insert one relation."""
        result = await self._db_session.execute(
            insert(_links).values(
                relation_type=relation_type,
                src_type=ENTITY_NOTE,
                src_id=_note_ref(request.source_note_id),
                src_label=request.source_label,
                dst_type=ENTITY_NOTE,
                dst_id=_note_ref(request.target_note_id),
                dst_label=request.target_label,
                attrs=attrs,
                status=REL_STATUS_ACTIVE,
                created_by=created_by,
            )
        )
        return int(result.inserted_primary_key[0])

    async def _records_by_ids(self, relation_ids: list[int]) -> list[RelationRecord]:
        """依 relation_id 讀回關係（保持輸入順序）。Read relations back by id, in input order."""
        if not relation_ids:
            return []
        rows = (
            await self._db_session.execute(
                select(*_RECORD_COLUMNS).where(_links.c.relation_id.in_(relation_ids))
            )
        ).all()
        by_id = {int(row[0]): _record(row) for row in rows}
        return [by_id[relation_id] for relation_id in relation_ids if relation_id in by_id]

    async def create_relation(
        self,
        request: CardRelationCreate,
        *,
        created_by: str = USER_CREATED_BY,
        attrs: dict | None = None,
    ) -> CardRelationRead:
        """建立單筆卡片關聯。

        Create a single card relation.

        Args:
            request: CardRelationCreate DTO。CardRelationCreate DTO.
            created_by: ``rel_links.created_by``；卡片 handler 傳自己的卡片類型。Who created it.
            attrs: ``rel_links.attrs``（例 ``{"card_type": "jp_core_verb"}``）。Extra attributes.

        Returns:
            已建立的 CardRelationRead 實例。The created CardRelationRead.
        """
        relation_type = await self.get_or_create_relation_type(request.relation_type)
        relation_id = await self._insert(request, relation_type, created_by=created_by, attrs=attrs)
        await self._db_session.commit()
        (record,) = await self._records_by_ids([relation_id])
        logger.info(
            "成功建立關聯: %s -> %s (%s)",
            record.source_label,
            record.target_label,
            relation_type,
        )
        return CardRelationRead.model_validate(record)

    async def batch_create_relations(
        self,
        requests: list[CardRelationCreate],
        *,
        created_by: str = USER_CREATED_BY,
        attrs: dict | None = None,
    ) -> list[CardRelationRead]:
        """批次建立多筆卡片關聯（同一個 transaction，最後才提交）。

        Batch-create multiple card relations in one transaction.

        全部寫入或全部不寫：任何一筆失敗就回滾並往上拋，不會只留下一半的關聯。
        若列表為空則直接返回。
        All or nothing: any failure rolls back and re-raises, so callers never
        end up with half of the relations. Returns early on empty input.

        Args:
            requests: CardRelationCreate DTO 列表。List of DTOs.
            created_by: ``rel_links.created_by``。Who created them.
            attrs: 每筆的 ``rel_links.attrs``（例 ``{"card_type": "jp_core_verb"}``）。Extra
                attributes stored on every relation.

        Returns:
            已建立的 CardRelationRead 實例列表。Created CardRelationRead
            instances.
        """
        if not requests:
            return []

        storage_types: dict[str, str] = {}
        for name in {req.relation_type for req in requests}:
            storage_types[name] = await self.get_or_create_relation_type(name)

        try:
            relation_ids = [
                await self._insert(req, storage_types[req.relation_type], created_by=created_by, attrs=attrs)
                for req in requests
            ]
            await self._db_session.commit()
        except Exception:
            await self._db_session.rollback()
            raise

        logger.info("批次建立 %d 筆關聯完成", len(relation_ids))
        return [CardRelationRead.model_validate(record) for record in await self._records_by_ids(relation_ids)]

    async def delete_relations_by_note_id(self, note_id: int) -> int:
        """根據 Note ID 清理所有相關的關聯（標成 deleted）。

        Retire every relation referring to the given note ID.

        雙向：只要起點或終點是這張筆記就會被標成刪除（包含建卡管線寫的「筆記 → 台詞／詞」）。
        用於卡片被刪除時避免產生死連結（孤兒節點）。
        Bidirectional: any relation with this note as an endpoint is retired.

        Args:
            note_id: 目標 Anki Note ID。Target Anki note ID.

        Returns:
            被標成刪除的關聯數量（驅動未回報時為預估值）。Number of retired relations
            (an estimate when the driver reports no row count).
        """
        deleted_count = await self._retire_note_relations(note_id)
        logger.info(
            "已清理與 Note ID %d 相關的 %d 筆關聯記錄", note_id, deleted_count
        )
        return deleted_count

    async def _retire_note_relations(self, note_id: int) -> int:
        """把這張筆記的有效關係標成 deleted 並提交。Retire a note's active relations and commit."""
        return await self._retire_where(and_(_active(), _touches_notes([str(int(note_id))])))

    async def _retire_where(self, condition: ColumnElement[bool]) -> int:
        """把符合條件的關係標成 deleted 並提交，回傳筆數。

        Mark the matching relations deleted, commit, and return the count.

        先在同一個 transaction 算出筆數；驅動未回報 rowcount 時改用這個預估值並記 WARNING，
        不把「不知道」報成 0（與 :meth:`sync_with_anki` 同一個做法）。
        The count is computed first in the same transaction and used, with a
        WARNING, when the driver reports no row count, so "unknown" is never
        reported as 0 (same approach as :meth:`sync_with_anki`).

        Args:
            condition: 要標成刪除的關係條件。Which relations to retire.

        Returns:
            int: 標成刪除的筆數。Relations retired.
        """
        expected = (
            await self._db_session.execute(select(func.count()).select_from(_links).where(condition))
        ).scalar_one()
        result = await self._db_session.execute(update(_links).where(condition).values(status=REL_STATUS_DELETED))
        await self._db_session.commit()
        count = _row_count(result)
        if count is None:
            logger.warning("資料庫未回報標成刪除的關聯筆數，改用預估值 %d", expected)
            return int(expected)
        return count

    async def sync_with_anki(self, anki_client: "AnkiClient", *, force: bool = False) -> "RelationSyncResult":
        """依 Anki 現存的筆記清理資料庫中的孤兒關聯（設計書 ``relation_sync_and_search_escape_FIX_2026-10-19.md`` §3）。

        Retire relations whose notes no longer exist in Anki, refusing when
        Anki's answer looks incomplete.

        範圍是「至少一端是筆記」的有效關係；孤兒關係標成 ``deleted``（不刪列）。
        Scope: active relations with at least one note endpoint; orphans are
        marked ``deleted``.

        步驟（先算再刪）：

        0. 先與 AnkiWeb 同步一次（失敗不中止，以本機收藏為準，結果標在 ``ankiweb_synced`` 並記 WARNING，
           事後能把刪除與同步失敗連在一起）。
        1. 以**資料庫的時鐘**記下掃描開始時間，再向 Anki 取全部筆記 ID。只有比這個時間早 2 秒以上
           建立的關聯才會被考慮——掃描期間剛生成的子卡，筆記不在掃描結果裡，它的關聯不能被當成孤兒。
           ``created_at`` 由資料庫填入（只到秒），所以同樣用資料庫的時鐘、並留 2 秒餘裕。
        2. Anki 回傳 0 張筆記：拒絕（不刪任何資料）。
        3. 讀出考慮範圍內的關聯引用的筆記 ID，與 Anki 的清單做差集得到孤兒 ID，算出要刪幾筆。
        4. 要刪的關聯超過考慮範圍內的一半、且超過 :data:`SYNC_GUARD_MIN_ROWS` 列時，除非 ``force``
           否則拒絕：清單可能不完整（例如收藏只載入了一部分）。
        5. 只以孤兒 ID 當參數分批標成刪除（參數量不隨 Anki 筆記數增長；正式環境是 MySQL，分批讓每條
           UPDATE 的大小有上限，也讓測試用的 SQLite 不超過參數上限），條件同樣限定考慮範圍；
           全部批次在同一個 transaction，最後才提交。

        Args:
            anki_client: Anki 客戶端（取全部筆記 ID）。The Anki client.
            force: 放寬比例防護（空清單仍一律拒絕）。Relax the share guard.

        Returns:
            清理結果。The sync result.

        Raises:
            RelationSyncEmptyAnkiError: Anki 回傳 0 張筆記（``force`` 也無效）。
            RelationSyncNeedsForceError: 要刪的比例過高且未指定 ``force``。
            AnkiConnectError: 連不上 Anki，或 Anki 回報錯誤（``AnkiActionError``）；不刪任何資料。
            sqlalchemy.exc.SQLAlchemyError: 資料庫錯誤；所有更新在同一個 transaction，未提交即不生效。
        """
        ankiweb_synced = True
        try:
            await anki_client.sync(raise_errors=True)
        except AnkiConnectError as error:
            ankiweb_synced = False
            logger.warning("同步清理：與 AnkiWeb 同步失敗，以本機收藏為準：%s", error)

        scan_started = (await self._db_session.execute(select(func.now()))).scalar_one()
        cutoff = scan_started - timedelta(seconds=SYNC_CLOCK_MARGIN_SECONDS)
        valid_note_ids = set(await anki_client.find_notes("deck:*"))
        if not valid_note_ids:
            logger.warning("同步清理被拒絕：Anki 回傳 0 張筆記（AnkiWeb 同步%s）", "成功" if ankiweb_synced else "失敗")
            # 同步也失敗時很可能就是原因（換了設定檔、需要完整同步），一併說出來
            next_step = (
                "清理前與 AnkiWeb 同步也失敗，請先在 Anki 處理同步後再試"
                if not ankiweb_synced
                else "請確認 Anki 已開啟並載入收藏後再試"
            )
            raise RelationSyncEmptyAnkiError(
                f"Anki 回傳 0 張筆記，為避免誤刪已停止清理（強制清理也不會執行）；{next_step}。"
            )

        considered = and_(
            _active(),
            _links.c.created_at < cutoff,
            or_(_links.c.src_type == ENTITY_NOTE, _links.c.dst_type == ENTITY_NOTE),
        )
        rows = (
            await self._db_session.execute(
                select(_links.c.src_type, _links.c.src_id, _links.c.dst_type, _links.c.dst_id).where(considered)
            )
        ).all()

        def note_ends(row: Any) -> list[int]:
            ends = []
            for kind, value in ((row[0], row[1]), (row[2], row[3])):
                note_id = _note_id(value) if kind == ENTITY_NOTE else None
                if note_id is not None:
                    ends.append(note_id)
            return ends

        ends_by_row = [note_ends(row) for row in rows]
        referenced = {note_id for ends in ends_by_row for note_id in ends}
        orphan_ids = referenced - valid_note_ids
        to_delete = sum(1 for ends in ends_by_row if any(note_id in orphan_ids for note_id in ends))
        result = RelationSyncResult(
            scanned_notes=len(valid_note_ids), orphan_notes=len(orphan_ids), deleted=0, considered=len(rows),
            ankiweb_synced=ankiweb_synced,
        )
        if not orphan_ids:
            logger.info("同步清理完成：沒有孤兒關聯（考慮 %d 筆，Anki %d 張筆記）", len(rows), len(valid_note_ids))
            return result

        if not force and to_delete > SYNC_GUARD_MIN_ROWS and to_delete * 2 > len(rows):
            logger.warning(
                "同步清理被拒絕：將刪除 %d / %d 筆關聯（Anki %d 張筆記）", to_delete, len(rows), len(valid_note_ids)
            )
            raise RelationSyncNeedsForceError(
                f"將刪除 {to_delete} / {len(rows)} 筆關聯（超過一半），Anki 的回應可能不完整，已停止清理。",
                to_delete=to_delete,
                considered=len(rows),
            )

        deleted = 0
        unreported = False
        orphan_list = sorted(str(note_id) for note_id in orphan_ids)
        for start in range(0, len(orphan_list), SYNC_DELETE_BATCH):
            batch = orphan_list[start:start + SYNC_DELETE_BATCH]
            outcome = await self._db_session.execute(
                update(_links).where(considered, _touches_notes(batch)).values(status=REL_STATUS_DELETED)
            )
            count = _row_count(outcome)
            if count is None:
                unreported = True
            else:
                deleted += count
        await self._db_session.commit()

        if unreported:
            # 資料已更新並提交，只是驅動沒回報筆數：顯示「移除 0 筆」會誤導，改用同一個 transaction 裡算出的預估值
            logger.warning("同步清理：資料庫未回報刪除筆數，改用預估值 %d", to_delete)
            deleted = to_delete
        elif deleted != to_delete:
            logger.warning("同步清理：預估刪除 %d 筆，實際刪除 %d 筆", to_delete, deleted)
        logger.info(
            "同步清理完成：刪除 %d 筆孤兒關聯（孤兒筆記 %d 張，Anki %d 張筆記%s，AnkiWeb 同步%s）",
            deleted, len(orphan_ids), len(valid_note_ids), "，強制" if force else "",
            "成功" if ankiweb_synced else "失敗",
        )
        return replace(result, deleted=deleted)

    async def delete_relations_for_note(self, note_id: int) -> int:
        """刪除指定筆記的所有關聯（無論是來源或目標；標成 deleted）。

        Retire all relations of the note (as source or target).

        Args:
            note_id: 筆記 ID。Note ID.

        Returns:
            標成刪除的紀錄數量。Number of retired rows.
        """
        deleted_count = await self._retire_note_relations(note_id)
        logger.info("已刪除卡片 %d 的所有關聯，共 %d 筆", note_id, deleted_count)
        return deleted_count

    async def update_labels_for_note(self, note_id: int, new_label: str) -> int | None:
        """更新關聯表裡這張筆記兩端的標籤（handlers 計畫書 D12）。

        Update the denormalized labels on both ends that point at this note.

        關聯以文字辨認圖譜節點；只更新起點的話，這張筆記當終點的連線還是舊文字，圖譜會分裂成兩個節點。
        Relations identify graph nodes by text; updating only the source side
        would split the node in two.

        Args:
            note_id: 筆記 ID。The note ID.
            new_label: 新的標籤文字。The new label.

        Returns:
            更新的紀錄數量（同一列兩端都是這張筆記時算兩次）；資料庫驅動未回報筆數時為 None。
            Rows updated, a row counted once per matching end; None when the
            driver reports no row count.
        """
        note_ref = str(int(note_id))
        source = await self._db_session.execute(
            update(_links)
            .where(_links.c.src_type == ENTITY_NOTE, _links.c.src_id == note_ref)
            .values(src_label=new_label)
        )
        target = await self._db_session.execute(
            update(_links)
            .where(_links.c.dst_type == ENTITY_NOTE, _links.c.dst_id == note_ref)
            .values(dst_label=new_label)
        )
        await self._db_session.commit()
        counts = [_row_count(result) for result in (source, target)]
        if any(count is None for count in counts):
            logger.info("已更新卡片 %d 兩端的關聯標籤為 '%s'（資料庫未回報筆數）", note_id, new_label)
            return None
        updated = sum(cast(list[int], counts))
        logger.info("已更新卡片 %d 兩端的關聯標籤為 '%s'，共 %d 筆", note_id, new_label, updated)
        return updated

    async def get_all_relation_types(self) -> list[str]:
        """取得所有「筆記 → 筆記」的關聯類型（對外名稱）。

        Get the public names of every note → note relation type.

        建卡管線的「筆記 → 台詞／詞」類型不列出（網頁的下拉選單只建立筆記之間的關聯）。
        Pipeline relation types to lines or terms are not listed.

        Returns:
            按名稱排序的關聯類型字串列表。Relation type names sorted by
            name.
        """
        rows = (
            await self._db_session.execute(select(_types.c.relation_type, _types.c.src_types, _types.c.dst_types))
        ).all()
        names = {
            display_relation_type(relation_type)
            for relation_type, src_types, dst_types in rows
            if ENTITY_NOTE in _json_list(src_types) and ENTITY_NOTE in _json_list(dst_types)
        }
        return sorted(names)

    async def get_or_create_relation_type(self, name: str) -> str:
        """確保關聯類型存在於 ``rel_types``，使用者的類型不存在時自動新增；回傳資料庫的類型名稱。

        Ensure the relation type exists in ``rel_types``, auto-registering
        user types, and return the stored type.

        只會自動註冊 ``user.*``（owner 是 ``user``，「筆記 → 筆記」）；系統關係 ``core.*`` 由語料庫遷移
        登記，不存在代表資料庫沒建好，直接報錯，不在這裡補。
        Only ``user.*`` types (owner ``user``, note → note) are registered
        here; a missing ``core.*`` type means the corpus was not migrated and
        raises instead of being created.

        Args:
            name: 關聯類型名稱（將被正規化為小寫）。Relation type name
                (normalized to lowercase).

        Returns:
            str: ``rel_types.relation_type``。The stored type.

        Raises:
            InvalidRelationTypeError: 名稱不合法（見 :func:`storage_relation_type`）。Invalid name.
            FluencyTidesError: 系統關係類型不在 ``rel_types``。A system type is missing.
        """
        relation_type = storage_relation_type(name)

        exists = (
            await self._db_session.execute(
                select(_types.c.relation_type).where(_types.c.relation_type == relation_type)
            )
        ).first()
        if not exists:
            if not relation_type.startswith(USER_PREFIX):
                logger.error("系統關聯類型 %s 不在 rel_types（語料庫遷移沒有登記）", relation_type)
                raise FluencyTidesError(
                    f"系統關聯類型 {relation_type} 不在 rel_types，請先執行語料庫重建的建表與遷移"
                )
            display = display_relation_type(relation_type)
            await self._db_session.execute(
                insert(_types).values(
                    relation_type=relation_type,
                    owner=USER_OWNER,
                    name_ja_jp=display[:64],
                    name_zh_hant=display[:64],
                    src_types=[ENTITY_NOTE],
                    dst_types=[ENTITY_NOTE],
                    directed=1,
                )
            )
            await self._db_session.commit()
            logger.info("自動註冊新的關聯類型: %s", relation_type)
        return relation_type

    async def delete_relation_by_nodes(
        self, request: "CardRelationDelete"
    ) -> dict[str, int]:
        """精準刪除特定關聯（包含雙向；標成 deleted）。

        Precisely delete a specific relation (both directions).

        刪除兩個節點之間特定 relation_type 的所有連線 (A->B 與 B->A)。
        Retires all links of the given relation_type between the two
        nodes (A->B and B->A).

        Args:
            request: 包含 source_label, target_label, relation_type 的
                DTO。DTO with source_label, target_label, relation_type.

        Returns:
            刪除的紀錄數量 {"deleted_count": N}。Dict with the deleted row
            count.
        """
        condition = and_(
            _active(),
            _note_to_note(),
            _links.c.relation_type == storage_relation_type(request.relation_type),
            or_(
                and_(
                    _links.c.src_label == request.source_label,
                    _links.c.dst_label == request.target_label,
                ),
                and_(
                    _links.c.src_label == request.target_label,
                    _links.c.dst_label == request.source_label,
                ),
            ),
        )
        deleted_count = await self._retire_where(condition)
        logger.info(
            "刪除關聯 %s <-> %s (%s)，共 %d 筆",
            request.source_label, request.target_label, request.relation_type, deleted_count
        )
        return {"deleted_count": deleted_count}

    async def get_relations_by_note_ids(
        self, note_ids: set[int] | None = None
    ) -> list[RelationRecord]:
        """根據一組 Note IDs 查詢相關的「筆記 → 筆記」有效關聯。

        Query active note → note relations related to a set of note IDs.

        若傳入 note_ids，只查詢 source 或 target 包含在內的關聯。
        若傳入 None 或空集合，查詢全部。
        With note_ids, only relations whose source or target is included
        are returned; None or an empty set returns all rows.

        Args:
            note_ids: 要篩選的 Note ID 集合 (可選)。Optional set of note
                IDs to filter by.

        Returns:
            RelationRecord 列表（欄位同舊的 CardRelation）。List of relation records.
        """
        stmt = select(*_RECORD_COLUMNS).where(_active(), _note_to_note())
        if note_ids:
            refs = [str(int(note_id)) for note_id in note_ids]
            stmt = stmt.where(or_(_links.c.src_id.in_(refs), _links.c.dst_id.in_(refs)))
        rows = (await self._db_session.execute(stmt.order_by(_links.c.relation_id))).all()
        return [_record(row) for row in rows]

    async def get_graph_data(
        self,
        notes_info: list["AnkiNoteInfo"] | None = None,
        cards_info: list[dict[str, object]] | None = None,
    ) -> dict[str, list[dict]]:
        """合併 Anki 實際卡片與語料庫的關聯紀錄，建立完整的知識圖譜。

        Merge actual Anki cards with the corpus relation rows into a full
        knowledge graph.

        Args:
            notes_info: 從 Anki 取得的卡片詳細資訊列表。用來建立圖譜中的
                「孤立節點」。Note details from Anki, used to create
                isolated nodes in the graph.
            cards_info: 從 Anki 取得的卡片狀態資訊列表。用來決定節點的熟練度
                顏色。Card status info from Anki, used to decide node
                proficiency colors.

        Returns:
            圖譜結構字典: {"nodes": [...], "links": [...]}。Graph structure
            dict with "nodes" and "links".
        """
        nodes_dict = {}
        links = []
        valid_note_ids = set()

        def clean_html(html_str: str) -> str:
            # 移除 Anki 可能加入的 HTML 標籤與跳脫字元
            text = re.sub(r'<[^>]+>', '', str(html_str))
            return text.replace("&quot;", '"').replace("&nbsp;", " ").replace("<br>", "").replace("<div>", "").replace("</div>", "")

        # 建立 note_id -> status 對映表
        card_status_map = {}
        if cards_info:
            for c in cards_info:
                n_id = c.get("note")
                queue = c.get("queue", 0)
                # queue: 0=new, 1=learning, 2=review, 3=relearning, <0=suspended/buried
                if queue in (1, 3):
                    status = "learning"
                elif queue == 2:
                    status = "review"
                elif queue < 0:
                    status = "suspended"
                else:
                    status = "new"
                if n_id:
                    card_status_map[n_id] = status

        # 1. 將所有從 Anki 傳入的卡片加入 nodes_dict (確保沒有關聯的卡片也會顯示)
        if notes_info:
            for note in notes_info:
                valid_note_ids.add(note.noteId)
                fields = note.fields

                # 預設使用 Expression 作為節點名稱 (如果沒有則使用 noteId 作為 fallback)
                expression = ""
                if "Expression" in fields:
                    expression = str(fields["Expression"].get("value", "")).strip()

                if not expression:
                    continue

                translation = clean_html(fields.get("Meaning", {}).get("value", ""))
                pos = clean_html(fields.get("PartOfSpeech", {}).get("value", ""))

                status = card_status_map.get(note.noteId, "new")

                nodes_dict[expression] = {
                    "id": expression,
                    "group": 1,
                    "val": 20,
                    "label": expression,
                    "translation": translation,
                    "pos": pos,
                    "note_id": note.noteId,
                    "status": status,
                }

        # 2. 從資料庫查詢關聯紀錄（有傳入 Anki 卡片時，只查詢與這些卡片相關的關聯，等於過濾牌組）
        relations = await self.get_relations_by_note_ids(valid_note_ids or None)

        # 3. 合併關聯與建立懸空節點
        for rel in relations:
            source_id = rel.source_label
            target_id = rel.target_label

            # 若 source 節點不在 nodes_dict 中 (可能它屬於其他牌組，或在 Anki 被刪除)
            if source_id not in nodes_dict:
                nodes_dict[source_id] = {
                    "id": source_id,
                    "group": 4, # 灰色懸空/未知節點
                    "val": 10,
                    "label": source_id,
                    "note_id": rel.source_note_id
                }

            # 若 target 節點不在 nodes_dict 中 (幽靈節點)
            if target_id not in nodes_dict:
                group = 2 if rel.relation_type == "synonym" else 3 if rel.relation_type == "collocation" else 4
                nodes_dict[target_id] = {
                    "id": target_id,
                    "group": group,
                    "val": 10,
                    "note_id": rel.target_note_id,
                }

            # 建立連線
            links.append(
                {
                    "source": source_id,
                    "target": target_id,
                    "label": rel.relation_type.capitalize(),
                    "relation_id": rel.id,
                }
            )

        return {
            "nodes": list(nodes_dict.values()),
            "links": links,
        }


def _json_list(value: Any) -> list:
    """JSON 欄位值轉成 list（驅動可能回傳字串）。Coerce a JSON column value to a list."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    return value if isinstance(value, list) else []
