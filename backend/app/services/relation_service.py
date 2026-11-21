"""Card relation business-logic service module.

卡片關聯業務邏輯服務模組。

It works against the generic relation tables ``rel_links`` / ``rel_types`` of the corpus (MySQL
``fluencytides_corpus``), providing creation, batch creation, cascade deletion (orphan cleanup) of card relations,
and efficient knowledge-graph data queries.

負責與語料庫（MySQL ``fluencytides_corpus``）的通用關係表 ``rel_links`` / ``rel_types``
互動，提供卡片關聯的建立、批次建立、連動刪除（清理孤兒節點）以及高效的知識圖譜資料查詢。

**2026-11-01 revision** (C of D9 in docs/wip/corpus_db_rebuild_FEAT_2026-10-31.md): it used to read and write
``card_relations`` / ``relation_types`` in the website SQLite, and now reads and writes ``rel_links`` /
``rel_types`` in the corpus; the two SQLite tables stay as they are, but the code no longer reads or writes them.
The public interface and API responses are unchanged, so the frontend needs no change:

- Relation types keep their old public names and get a namespace when stored: ``has_context`` ↔
  ``core.has_context``, ``has_cloze`` ↔ ``core.has_cloze``, and everything else (``synonym``, ``conversation``,
  user-defined) ↔ ``user.<name>`` (``owner='user'``). Users cannot choose the namespace: ``core.*`` can only be
  used through the two public names above, and auto-registration only creates ``user.*``.
- Note endpoints are written as ``src_type/dst_type='note'`` with the ID stored as a string; dangling nodes still
  rely on labels.
- Deletion always marks ``status`` as ``deleted`` instead of deleting the row (``rel_links`` is a record table).
- The knowledge graph only looks at "note → note" relations; the "note → line / term" relations written by the
  card-creation pipeline do not appear on the graph, but when a card is deleted or sync cleanup runs, any of them
  with that note as an endpoint is marked ``deleted`` as well.

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
    """Map a public relation name to ``rel_types.relation_type``.

    把對外的關係名稱轉成 ``rel_types.relation_type``。

    ``has_context`` / ``has_cloze`` are system relations (``core.*``); everything else is a user relation and
    always gets ``user.``. Users cannot choose the namespace themselves: names that are blank, start with
    ``core.``, contain characters outside the allowed set (for example ``.`` or whitespace), or are too long are
    rejected, so the website can never create system relations such as ``core.example_from``.

    ``has_context`` / ``has_cloze`` 是系統關係（``core.*``）；其餘都是使用者的關係，一律加上
    ``user.``。使用者不能自己指定命名空間：空白、以 ``core.`` 開頭、含允許字元以外（例如 ``.``、
    空白）或過長的名稱都拒絕，避免網站建出 ``core.example_from`` 這類系統關係。

    Args:
        name: the public name (lower-cased and stripped)
            對外的名稱（會轉小寫、去空白）

    Returns:
        the stored relation type
        資料庫的關係類型

    Raises:
        InvalidRelationTypeError: if the name is invalid (the API returns 400)
            若名稱不合法（API 回 400）
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
    """Map a stored ``rel_types.relation_type`` back to its public name (removing ``core.`` / ``user.``).

    把 ``rel_types.relation_type`` 轉回對外的名稱（去掉 ``core.`` / ``user.``）。

    Args:
        relation_type: the stored relation type
            資料庫的關係類型

    Returns:
        the public name; other namespaces (card types' own relations) are returned unchanged
        對外名稱；其他命名空間（卡片類型自己的關係）原樣回傳
    """
    for prefix in _DISPLAY_PREFIXES:
        if relation_type.startswith(prefix):
            return relation_type[len(prefix):]
    return relation_type


def _note_ref(note_id: int | None) -> str | None:
    """Convert a note ID to the string endpoint ID of ``rel_links``.

    筆記 ID 轉成 ``rel_links`` 的字串 ID。
    """
    return str(int(note_id)) if note_id is not None else None


def _note_id(value: str | None) -> int | None:
    """Convert a string endpoint ID of ``rel_links`` back to a note ID; ``None`` if it is not an integer.

    ``rel_links`` 的字串 ID 轉回筆記 ID；不是整數時為 ``None``。
    """
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class RelationRecord:
    """One "note → note" relation, keeping the attribute names of the old ``CardRelation`` for graph and API code.

    一條「筆記 → 筆記」的關係（欄位名稱沿用舊的 ``CardRelation``，供圖譜與 API 使用）。

    Attributes:
        id: ``rel_links.relation_id``
            ``rel_links.relation_id``
        source_note_id: the source note ID; ``None`` when dangling
            起點筆記 ID；懸空時為 ``None``
        target_note_id: the target note ID; ``None`` when dangling
            終點筆記 ID；懸空時為 ``None``
        relation_type: the public relation name (``has_context``, ``synonym``, ...)
            對外的關係名稱（``has_context``、``synonym``…）
        source_label: the source label
            起點標籤
        target_label: the target label
            終點標籤
        created_at: the creation time
            建立時間
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
    """Map a result row to a :class:`RelationRecord`.

    查詢列轉成 ``RelationRecord``。
    """
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
    """Return the condition for active relations.

    回傳有效關係的條件。
    """
    return _links.c.status == REL_STATUS_ACTIVE


def _note_to_note() -> ColumnElement[bool]:
    """Return the condition for relations whose two endpoints are notes (the scope of the knowledge graph).

    回傳兩端都是筆記的關係條件（知識圖譜的範圍）。
    """
    return and_(_links.c.src_type == ENTITY_NOTE, _links.c.dst_type == ENTITY_NOTE)


def _touches_notes(note_ids: list[str]) -> ColumnElement[bool]:
    """Return the condition for relations with either endpoint among these notes.

    回傳任一端是這些筆記的關係條件。
    """
    return or_(
        and_(_links.c.src_type == ENTITY_NOTE, _links.c.src_id.in_(note_ids)),
        and_(_links.c.dst_type == ENTITY_NOTE, _links.c.dst_id.in_(note_ids)),
    )


def _row_count(result: Any) -> int | None:
    """Return the row count reported by the driver, or ``None`` if it reported none.

    回傳驅動回報的筆數；未回報時為 ``None``。
    """
    count = cast(CursorResult[Any], result).rowcount
    return None if count is None or count < 0 else count


@dataclass(frozen=True)
class RelationSyncResult:
    """The result of the sync cleanup with Anki.

    同步清理的結果。

    Attributes:
        scanned_notes: the number of notes Anki returned
            Anki 回傳的筆記數
        orphan_notes: the number of notes referenced by relations but no longer in Anki
            關聯引用、但 Anki 已沒有的筆記數
        deleted: the number of relations marked deleted; an estimate when the database driver reports no row count
            標成刪除的關聯數；資料庫驅動未回報筆數時為預估值
        considered: the number of relations in scope (created at least 2 seconds before the scan started)
            考慮範圍內（掃描開始前 2 秒以上建立）的關聯數
        ankiweb_synced: whether the sync with AnkiWeb before fetching notes succeeded; on failure the local
            collection is used. It is required with no default, so forgetting to pass it cannot silently report
            "sync succeeded".
            取筆記前與 AnkiWeb 的同步是否成功；失敗時以本機收藏為準。必填，不給預設值：
            忘了傳時不能靜默地報成「同步成功」
    """

    scanned_notes: int
    orphan_notes: int
    deleted: int
    considered: int
    ankiweb_synced: bool


class RelationService:
    """The card relation management service.

    卡片關聯管理服務。

    It provides methods operating on the corpus ``rel_links`` / ``rel_types`` and abstracts the conversion to the
    graph_data structure the frontend react-force-graph needs.

    提供操作語料庫 ``rel_links`` / ``rel_types`` 的方法，
    並抽象出前端 react-force-graph 所需的 graph_data 結構轉換。

    Attributes:
        _db_session: the injected async corpus session
            注入的語料庫非同步 Session
    """

    def __init__(self, db_session: AsyncSession) -> None:
        """Initialize the RelationService.

        初始化 RelationService。

        Args:
            db_session: the corpus AsyncSession (``get_corpus_async_session``)
                語料庫的 AsyncSession（``get_corpus_async_session``）
        """
        self._db_session = db_session

    async def _insert(
        self, request: CardRelationCreate, relation_type: str, *, created_by: str, attrs: dict | None,
    ) -> int:
        """Insert one "note → note" relation without committing, and return its relation_id.

        新增一條「筆記 → 筆記」的關係（不 commit），回傳 relation_id。
        """
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
        """Read relations back by relation_id, keeping the input order.

        依 relation_id 讀回關係（保持輸入順序）。
        """
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
        """Create a single card relation and commit it.

        建立單筆卡片關聯並提交。

        Args:
            request: the CardRelationCreate DTO
                CardRelationCreate DTO
            created_by: ``rel_links.created_by``; card handlers pass their own card type
                ``rel_links.created_by``；卡片 handler 傳自己的卡片類型
            attrs: ``rel_links.attrs`` (for example ``{"card_type": "jp_core_verb"}``)
                ``rel_links.attrs``（例 ``{"card_type": "jp_core_verb"}``）

        Returns:
            the created CardRelationRead
            已建立的 CardRelationRead 實例

        Raises:
            FluencyTidesError: if a system relation type is missing from ``rel_types``
                若系統關係類型不在 ``rel_types``
            InvalidRelationTypeError: if the relation type name is invalid
                若關係類型名稱不合法
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
        """Batch-create multiple card relations in one transaction, committed at the end.

        批次建立多筆卡片關聯（同一個 transaction，最後才提交）。

        All or nothing: any failure rolls back and re-raises, so it never leaves half of the relations. An empty list
        returns immediately. New user relation types are registered (and committed) before the relations are
        written, so they remain even if the batch is rolled back.

        全部寫入或全部不寫：任何一筆失敗就回滾並往上拋，不會只留下一半的關聯。
        若列表為空則直接返回。新的使用者關係類型在寫入關聯前就註冊（並提交），批次回滾時仍會留著。

        Args:
            requests: the list of CardRelationCreate DTOs
                CardRelationCreate DTO 串列
            created_by: ``rel_links.created_by``
                ``rel_links.created_by``
            attrs: the ``rel_links.attrs`` of every relation (for example ``{"card_type": "jp_core_verb"}``)
                每筆的 ``rel_links.attrs``（例 ``{"card_type": "jp_core_verb"}``）

        Returns:
            the created CardRelationRead instances
            已建立的 CardRelationRead 實例串列

        Raises:
            FluencyTidesError: if a system relation type is missing from ``rel_types``
                若系統關係類型不在 ``rel_types``
            InvalidRelationTypeError: if a relation type name is invalid
                若關係類型名稱不合法
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
        """Clean up every relation of the given note ID (marking them deleted).

        根據 Note ID 清理所有相關的關聯（標成 deleted）。

        Bidirectional: any relation whose source or target is this note is marked deleted (including the
        "note → line / term" relations written by the card-creation pipeline). It is used when a card is deleted, to
        avoid dead links (orphan nodes).

        雙向：只要起點或終點是這張筆記就會被標成刪除（包含建卡管線寫的「筆記 → 台詞／詞」）。
        用於卡片被刪除時避免產生死連結（孤兒節點）。

        Args:
            note_id: the target Anki note ID
                目標 Anki Note ID

        Returns:
            the number of relations marked deleted (an estimate when the driver reports no row count)
            被標成刪除的關聯數量（驅動未回報時為預估值）
        """
        deleted_count = await self._retire_note_relations(note_id)
        logger.info(
            "已清理與 Note ID %d 相關的 %d 筆關聯記錄", note_id, deleted_count
        )
        return deleted_count

    async def _retire_note_relations(self, note_id: int) -> int:
        """Mark this note's active relations deleted and commit.

        把這張筆記的有效關係標成 deleted 並提交。
        """
        return await self._retire_where(and_(_active(), _touches_notes([str(int(note_id))])))

    async def _retire_where(self, condition: ColumnElement[bool]) -> int:
        """Mark the matching relations deleted, commit, and return the count.

        把符合條件的關係標成 deleted 並提交，回傳筆數。

        The count is first computed in the same transaction; when the driver reports no rowcount, this estimate is
        used instead and a WARNING is logged, so "unknown" is never reported as 0 (the same approach as
        :meth:`sync_with_anki`).

        先在同一個 transaction 算出筆數；驅動未回報 rowcount 時改用這個預估值並記 WARNING，
        不把「不知道」報成 0（與 ``sync_with_anki`` 同一個做法）。

        Args:
            condition: the condition of the relations to mark deleted
                要標成刪除的關係條件

        Returns:
            the number of rows marked deleted
            標成刪除的筆數
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
        """Clean up orphan relations in the database against the notes that exist in Anki.

        依 Anki 現存的筆記清理資料庫中的孤兒關聯。

        See design doc ``relation_sync_and_search_escape_FIX_2026-10-19.md`` §3. The scope is active relations with
        at least one note endpoint; orphan relations are marked ``deleted`` (rows are not deleted). It refuses when
        Anki's answer looks incomplete.

        Steps (count first, then delete):

        0. Sync with AnkiWeb once first (a failure does not abort; the local collection is used, the result is
           recorded in ``ankiweb_synced``, and a WARNING is logged so a deletion can later be tied to the failed
           sync).
        1. Record the scan start time with **the database's clock**, then fetch every note ID from Anki. Only
           relations created at least 2 seconds before this time are considered: for a child card generated during
           the scan, the note is not in the scan result, so its relations must not be treated as orphans.
           ``created_at`` is filled in by the database (to the second), so the database's clock is used as well,
           with a 2-second margin.
        2. If Anki returns 0 notes: refuse (delete nothing).
        3. Read the note IDs referenced by relations in scope, subtract Anki's list to get the orphan IDs, and count
           how many rows would be deleted.
        4. If the relations to delete exceed half of those in scope and exceed :data:`SYNC_GUARD_MIN_ROWS` rows,
           refuse unless ``force``: the list may be incomplete (for example the collection is only partly loaded).
        5. Mark them deleted in batches, using only the orphan IDs as parameters (the parameter count does not grow
           with the number of Anki notes; production is MySQL, and batching bounds the size of each UPDATE and also
           keeps the test SQLite under its parameter limit), with the condition still limited to the scope; all
           batches run in one transaction, committed at the end.

        範圍是「至少一端是筆記」的有效關係；孤兒關係標成 ``deleted``（不刪列）。設計書
        ``relation_sync_and_search_escape_FIX_2026-10-19.md`` §3。

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
            anki_client: the Anki client (to fetch every note ID)
                Anki 客戶端（取全部筆記 ID）
            force: relax the share guard (an empty list is still always refused)
                放寬比例防護（空清單仍一律拒絕）

        Returns:
            the cleanup result
            清理結果

        Raises:
            AnkiConnectError: if Anki cannot be reached or reports an error (``AnkiActionError``) while fetching
                notes; nothing is deleted
                若取筆記時連不上 Anki，或 Anki 回報錯誤（``AnkiActionError``）；不刪任何資料
            RelationSyncEmptyAnkiError: if Anki returns 0 notes (``force`` does not help)
                若 Anki 回傳 0 張筆記（``force`` 也無效）
            RelationSyncNeedsForceError: if the share to delete is too high and ``force`` is not given
                若要刪的比例過高且未指定 ``force``
            sqlalchemy.exc.SQLAlchemyError: on a database error; all updates are in one transaction and take no
                effect unless committed
                若資料庫錯誤；所有更新在同一個 transaction，未提交即不生效
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
        """Delete all relations of the note, whether it is the source or the target (marking them deleted).

        刪除指定筆記的所有關聯（無論是來源或目標；標成 deleted）。

        Args:
            note_id: the note ID
                筆記 ID

        Returns:
            the number of rows marked deleted (an estimate when the driver reports no row count)
            標成刪除的紀錄數量（驅動未回報時為預估值）
        """
        deleted_count = await self._retire_note_relations(note_id)
        logger.info("已刪除卡片 %d 的所有關聯，共 %d 筆", note_id, deleted_count)
        return deleted_count

    async def update_labels_for_note(self, note_id: int, new_label: str) -> int | None:
        """Update the labels on both ends of the relation table that point at this note (handlers plan D12).

        更新關聯表裡這張筆記兩端的標籤（handlers 計畫書 D12）。

        Relations identify graph nodes by text; if only the source side were updated, links where this note is the
        target would keep the old text and the graph would split into two nodes. Relations of every status are
        updated.

        關聯以文字辨認圖譜節點；只更新起點的話，這張筆記當終點的連線還是舊文字，圖譜會分裂成兩個節點。
        所有狀態的關聯都會更新。

        Args:
            note_id: the note ID
                筆記 ID
            new_label: the new label text
                新的標籤文字

        Returns:
            the number of rows updated (a row whose two ends are both this note counts twice), or ``None`` when the
            database driver reports no row count
            更新的紀錄數量（同一列兩端都是這張筆記時算兩次）；資料庫驅動未回報筆數時為 ``None``
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
        """Get every "note → note" relation type, by public name.

        取得所有「筆記 → 筆記」的關聯類型（對外名稱）。

        The card-creation pipeline's "note → line / term" types are not listed (the web page's dropdown only creates
        relations between notes).

        建卡管線的「筆記 → 台詞／詞」類型不列出（網頁的下拉選單只建立筆記之間的關聯）。

        Returns:
            the relation type names, sorted by name
            按名稱排序的關聯類型字串串列
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
        """Ensure the relation type exists in ``rel_types`` and return the stored type name.

        確保關聯類型存在於 ``rel_types``，回傳資料庫的類型名稱。

        A missing user type is registered automatically (and committed). Only ``user.*`` is registered
        automatically (owner ``user``, "note → note"); system relations ``core.*`` are registered by the corpus
        migration, so a missing one means the database was not set up, and it raises instead of being created here.

        使用者的類型不存在時自動新增（並提交）。只會自動註冊 ``user.*``（owner 是 ``user``，「筆記 → 筆記」）；
        系統關係 ``core.*`` 由語料庫遷移登記，不存在代表資料庫沒建好，直接報錯，不在這裡補。

        Args:
            name: the relation type name (normalized to lowercase)
                關聯類型名稱（將被正規化為小寫）

        Returns:
            ``rel_types.relation_type``
            ``rel_types.relation_type``

        Raises:
            FluencyTidesError: if a system relation type is not in ``rel_types``
                若系統關係類型不在 ``rel_types``
            InvalidRelationTypeError: if the name is invalid (see :func:`storage_relation_type`)
                若名稱不合法（見 ``storage_relation_type``）
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
        """Precisely delete a specific relation, in both directions (marking it deleted).

        精準刪除特定關聯（包含雙向；標成 deleted）。

        It deletes every active "note → note" link of the given relation_type between the two nodes, matched by
        label (A->B and B->A).

        刪除兩個節點之間特定 relation_type 的所有有效「筆記 → 筆記」連線，以標籤比對 (A->B 與 B->A)。

        Args:
            request: the DTO with source_label, target_label, and relation_type
                包含 source_label, target_label, relation_type 的 DTO

        Returns:
            the deleted row count, ``{"deleted_count": N}``
            刪除的紀錄數量 ``{"deleted_count": N}``

        Raises:
            InvalidRelationTypeError: if the relation type name is invalid
                若關係類型名稱不合法
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
        """Query the active "note → note" relations related to a set of note IDs.

        根據一組 Note IDs 查詢相關的「筆記 → 筆記」有效關聯。

        With ``note_ids``, only relations whose source or target is included are queried, and an empty set
        matches nothing; only ``None`` queries all of them.

        傳入 ``note_ids`` 時，只查詢 source 或 target 包含在內的關聯，空集合查不到任何關聯；
        只有 ``None`` 才查詢全部。

        Args:
            note_ids: the note IDs to filter by; ``None`` means no filter
                要篩選的 Note ID 集合；``None`` 表示不篩選

        Returns:
            the RelationRecord list (same attributes as the old CardRelation), ordered by relation_id; empty if
            ``note_ids`` is an empty set
            RelationRecord 串列（欄位同舊的 CardRelation），依 relation_id 排序；``note_ids`` 為空集合時為空串列
        """
        if note_ids is not None and not note_ids:
            return []
        stmt = select(*_RECORD_COLUMNS).where(_active(), _note_to_note())
        if note_ids is not None:
            refs = [str(int(note_id)) for note_id in note_ids]
            stmt = stmt.where(or_(_links.c.src_id.in_(refs), _links.c.dst_id.in_(refs)))
        rows = (await self._db_session.execute(stmt.order_by(_links.c.relation_id))).all()
        return [_record(row) for row in rows]

    async def get_graph_data(
        self,
        notes_info: list["AnkiNoteInfo"] | None = None,
        cards_info: list[dict[str, object]] | None = None,
    ) -> dict[str, list[dict]]:
        """Merge the actual Anki cards with the corpus relation records into a full knowledge graph.

        合併 Anki 實際卡片與語料庫的關聯紀錄，建立完整的知識圖譜。

        Nodes are keyed by the ``Expression`` field; notes without it get no node of their own. When notes are given,
        only relations touching them are included (which amounts to filtering by deck), so an empty list gives an
        empty graph; only ``None`` includes all relations.

        節點以 ``Expression`` 欄位為鍵；沒有這個欄位的筆記不建立自己的節點。有傳入筆記時只納入與它們相關的關聯
        （等於過濾牌組），所以空串列得到空的圖譜；只有 ``None`` 才納入全部關聯。

        Args:
            notes_info: the note details fetched from Anki, used to create the "isolated nodes" of the graph;
                ``None`` means include every relation
                從 Anki 取得的卡片詳細資訊列表，用來建立圖譜中的「孤立節點」；``None`` 表示納入全部關聯
            cards_info: the card status info fetched from Anki, used to decide the node proficiency colors
                從 Anki 取得的卡片狀態資訊列表。用來決定節點的熟練度顏色

        Returns:
            the graph structure ``{"nodes": [...], "links": [...]}``
            圖譜結構字典 ``{"nodes": [...], "links": [...]}``
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

                # 預設使用 Expression 作為節點名稱（沒有 Expression 的筆記略過，不建立節點）
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

        # 2. 從資料庫查詢關聯紀錄（有傳入 Anki 卡片時，只查詢與這些卡片相關的關聯，等於過濾牌組）。
        #    牌組沒有卡片時傳入的是空串列，要查到空的，不能退回查全部
        relations = await self.get_relations_by_note_ids(valid_note_ids if notes_info is not None else None)

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
    """Coerce a JSON column value to a list (the driver may return a string).

    JSON 欄位值轉成 list（驅動可能回傳字串）。

    Invalid JSON or a non-list value yields an empty list.

    不合法的 JSON 或不是 list 的值回傳空串列。
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    return value if isinstance(value, list) else []
