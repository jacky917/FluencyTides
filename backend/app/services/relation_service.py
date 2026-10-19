"""
卡片關聯業務邏輯服務模組。

負責與資料庫中的 CardRelation 表進行互動，提供卡片關聯的建立、批次建立、
連動刪除（清理孤兒節點）以及高效的知識圖譜資料查詢。

此模組實作了 Phase 5 計畫中的 RelationService。

Card relation business-logic service module.

Interacts with the CardRelation table in the database, providing
creation, batch creation, cascade deletion (orphan cleanup), and
efficient knowledge-graph data queries. Implements the RelationService
described in the Phase 5 plan.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from app.schemas.relation import CardRelationDelete
    from app.schemas.anki import AnkiNoteInfo
    from app.infrastructure.anki.client import AnkiClient

from sqlalchemy import CursorResult, and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from sqlmodel import col

from app.core.exceptions import RelationSyncEmptyAnkiError, RelationSyncNeedsForceError
from app.infrastructure.anki.client import AnkiConnectError
from app.infrastructure.database.models import CardRelation, RelationType
from app.schemas.relation import CardRelationCreate, CardRelationRead

logger = logging.getLogger(__name__)

# 同步清理：資料庫時鐘的餘裕（created_at 由資料庫填入，SQLite 只到秒）
SYNC_CLOCK_MARGIN_SECONDS = 2
# 比例防護：要刪的關聯超過這個列數、且超過考慮範圍的一半時，需要 force
SYNC_GUARD_MIN_ROWS = 20
# 每批刪除的孤兒筆記 ID 數。每條 DELETE 帶 2 × 批次 + 1 個參數（source、target 各展開一次，加上 cutoff），
# 400 → 801 個，也在舊版 SQLite（3.32 之前）的 999 上限內
SYNC_DELETE_BATCH = 400


@dataclass(frozen=True)
class RelationSyncResult:
    """同步清理的結果。The result of syncing relations with Anki.

    Attributes:
        scanned_notes: Anki 回傳的筆記數。Notes Anki returned.
        orphan_notes: 關聯引用、但 Anki 已沒有的筆記數。Referenced notes missing in Anki.
        deleted: 刪除的關聯數；資料庫驅動未回報筆數時為預估值。Relations deleted (the estimate
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

    提供操作關聯資料庫（CardRelation 表）的方法，
    並抽象出前端 react-force-graph 所需的 graph_data 結構轉換。

    Provides methods operating on the CardRelation table and abstracts
    the graph_data structure needed by the react-force-graph frontend.

    Attributes:
        _db_session: 注入的非同步資料庫 Session。Injected async DB session.
    """

    def __init__(self, db_session: AsyncSession) -> None:
        """初始化 RelationService。

        Initialize the RelationService.

        Args:
            db_session: 注入的 AsyncSession 實例。Injected AsyncSession.
        """
        self._db_session = db_session

    async def create_relation(
        self, request: CardRelationCreate
    ) -> CardRelationRead:
        """建立單筆卡片關聯。

        Create a single card relation.

        Args:
            request: CardRelationCreate DTO。CardRelationCreate DTO.

        Returns:
            已建立的 CardRelationRead 實例。The created CardRelationRead.
        """
        await self.get_or_create_relation_type(request.relation_type)
        
        relation = CardRelation(**request.model_dump())
        self._db_session.add(relation)
        await self._db_session.commit()
        await self._db_session.refresh(relation)
        logger.info(
            "成功建立關聯: %s -> %s (%s)",
            relation.source_label,
            relation.target_label,
            relation.relation_type,
        )
        return CardRelationRead.model_validate(relation)

    async def batch_create_relations(
        self, requests: list[CardRelationCreate]
    ) -> list[CardRelationRead]:
        """批次建立多筆卡片關聯。

        Batch-create multiple card relations.

        使用 add_all 優化寫入效能。若列表為空則直接返回。
        Uses add_all for write performance; returns early on empty input.

        Args:
            requests: CardRelationCreate DTO 列表。List of DTOs.

        Returns:
            已建立的 CardRelationRead 實例列表。Created CardRelationRead
            instances.
        """
        if not requests:
            return []

        # Auto register relation types
        types_to_register = {req.relation_type for req in requests}
        for rt in types_to_register:
            await self.get_or_create_relation_type(rt)

        relations = [CardRelation(**req.model_dump()) for req in requests]
        self._db_session.add_all(relations)
        await self._db_session.commit()

        # 在 async SQLAlchemy 中，batch insert 若需要返回自增 ID，
        # 需要對每個實例進行 refresh。
        for rel in relations:
            await self._db_session.refresh(rel)

        logger.info("批次建立 %d 筆關聯完成", len(relations))
        return [CardRelationRead.model_validate(rel) for rel in relations]

    async def delete_relations_by_note_id(self, note_id: int) -> int:
        """根據 Note ID 清理所有相關的關聯。

        Clean up all relations referring to the given note ID.

        執行雙向刪除：只要 source_note_id 或 target_note_id 等於該 note_id，
        就會被刪除。用於卡片被刪除時避免產生死連結（孤兒節點）。
        Bidirectional delete: rows whose source_note_id or target_note_id
        equals note_id are removed, preventing dangling links.

        Args:
            note_id: 目標 Anki Note ID。Target Anki note ID.

        Returns:
            被刪除的關聯記錄數量。Number of deleted relation rows.
        """
        stmt = delete(CardRelation).where(
            or_(
                CardRelation.source_note_id == note_id,
                CardRelation.target_note_id == note_id,
            )
        )
        result = await self._db_session.execute(stmt)
        await self._db_session.commit()
        
        deleted_count = result.rowcount
        logger.info(
            "已清理與 Note ID %d 相關的 %d 筆關聯記錄", note_id, deleted_count
        )
        return deleted_count

    async def sync_with_anki(self, anki_client: "AnkiClient", *, force: bool = False) -> "RelationSyncResult":
        """依 Anki 現存的筆記清理資料庫中的孤兒關聯（設計書 ``relation_sync_and_search_escape_FIX_2026-10-19.md`` §3）。

        Remove relations whose notes no longer exist in Anki, refusing when
        Anki's answer looks incomplete.

        步驟（先算再刪）：

        0. 先與 AnkiWeb 同步一次（失敗不中止，以本機收藏為準，結果標在 ``ankiweb_synced`` 並記 WARNING，
           事後能把刪除與同步失敗連在一起）。
        1. 以**資料庫的時鐘**記下掃描開始時間，再向 Anki 取全部筆記 ID。只有比這個時間早 2 秒以上
           建立的關聯才會被考慮——掃描期間剛生成的子卡，筆記不在掃描結果裡，它的關聯不能被當成孤兒。
           ``created_at`` 由資料庫填入（SQLite 只到秒），所以同樣用資料庫的時鐘、並留 2 秒餘裕。
        2. Anki 回傳 0 張筆記：拒絕（不刪任何資料）。
        3. 讀出考慮範圍內的關聯引用的筆記 ID，與 Anki 的清單做差集得到孤兒 ID，算出要刪幾筆。
        4. 要刪的關聯超過考慮範圍內的一半、且超過 :data:`SYNC_GUARD_MIN_ROWS` 列時，除非 ``force``
           否則拒絕：清單可能不完整（例如收藏只載入了一部分）。
        5. 只以孤兒 ID 當參數分批刪除（不會因為筆記數上萬而超過 SQLite 的參數上限），刪除條件同樣限定
           考慮範圍；全部批次在同一個 transaction，最後才提交。

        Args:
            anki_client: Anki 客戶端（取全部筆記 ID）。The Anki client.
            force: 放寬比例防護（空清單仍一律拒絕）。Relax the share guard.

        Returns:
            清理結果。The sync result.

        Raises:
            RelationSyncEmptyAnkiError: Anki 回傳 0 張筆記（``force`` 也無效）。
            RelationSyncNeedsForceError: 要刪的比例過高且未指定 ``force``。
            AnkiConnectError: 連不上 Anki，或 Anki 回報錯誤（``AnkiActionError``）；不刪任何資料。
            sqlalchemy.exc.SQLAlchemyError: 資料庫錯誤；所有刪除在同一個 transaction，未提交即不生效。
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

        considered = col(CardRelation.created_at) < cutoff
        rows = (
            await self._db_session.execute(
                select(CardRelation.source_note_id, CardRelation.target_note_id).where(considered)
            )
        ).all()
        referenced = {note_id for row in rows for note_id in row if note_id is not None}
        orphan_ids = referenced - valid_note_ids
        to_delete = sum(1 for source, target in rows if source in orphan_ids or target in orphan_ids)
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
        orphan_list = sorted(orphan_ids)
        for start in range(0, len(orphan_list), SYNC_DELETE_BATCH):
            batch = orphan_list[start:start + SYNC_DELETE_BATCH]
            outcome = cast(
                CursorResult[Any],
                await self._db_session.execute(
                    delete(CardRelation).where(
                        considered,
                        or_(col(CardRelation.source_note_id).in_(batch), col(CardRelation.target_note_id).in_(batch)),
                    )
                ),
            )
            count = outcome.rowcount
            if count is None or count < 0:
                unreported = True
            else:
                deleted += count
        await self._db_session.commit()

        if unreported:
            # 資料已刪除並提交，只是驅動沒回報筆數：顯示「移除 0 筆」會誤導，改用同一個 transaction 裡算出的預估值
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
        """刪除指定筆記的所有關聯（無論是來源或目標）。

        Delete all relations of the note (as source or target).

        Args:
            note_id: 筆記 ID。Note ID.

        Returns:
            刪除的紀錄數量。Number of deleted rows.
        """
        stmt = delete(CardRelation).where(
            or_(
                CardRelation.source_note_id == note_id,
                CardRelation.target_note_id == note_id,
            )
        )
        result = await self._db_session.execute(stmt)
        await self._db_session.commit()
        deleted_count = result.rowcount
        logger.info("已刪除卡片 %d 的所有關聯，共 %d 筆", note_id, deleted_count)
        return deleted_count

    async def update_source_label(self, note_id: int, new_label: str) -> int:
        """更新關聯紀錄中的 source_label。

        Update the redundant source_label on relation rows.

        當 Anki 卡片的主要欄位 (如 Expression) 改變時，同步更新冗餘資料。
        Keeps the denormalized label in sync when the card's primary
        field (e.g. Expression) changes.

        Args:
            note_id: 來源筆記 ID。Source note ID.
            new_label: 新的標籤文字。New label text.

        Returns:
            更新的紀錄數量。Number of updated rows.
        """
        stmt = (
            update(CardRelation)
            .where(CardRelation.source_note_id == note_id)
            .values(source_label=new_label)
        )
        result = await self._db_session.execute(stmt)
        await self._db_session.commit()
        updated_count = result.rowcount
        logger.info("已更新卡片 %d 的 source_label 為 '%s'，共 %d 筆", note_id, new_label, updated_count)
        return updated_count

    async def get_all_relation_types(self) -> list[str]:
        """取得所有已註冊的關聯類型名稱。

        Get all registered relation type names.

        Returns:
            按名稱排序的關聯類型字串列表。Relation type names sorted by
            name.
        """
        stmt = select(RelationType.name).order_by(RelationType.name)
        result = await self._db_session.execute(stmt)
        return list(result.scalars().all())

    async def get_or_create_relation_type(self, name: str) -> None:
        """確保關聯類型存在於資料庫中，若不存在則自動新增。

        Ensure the relation type exists in the DB, auto-registering it.

        Args:
            name: 關聯類型名稱（將被正規化為小寫）。Relation type name
                (normalized to lowercase).
        """
        name = name.strip().lower()
        if not name:
            return
            
        stmt = select(RelationType).where(RelationType.name == name)
        result = await self._db_session.execute(stmt)
        if not result.scalars().first():
            rel_type = RelationType(name=name)
            self._db_session.add(rel_type)
            await self._db_session.commit()
            logger.info("自動註冊新的關聯類型: %s", name)

    async def delete_relation_by_nodes(
        self, request: "CardRelationDelete"
    ) -> dict[str, int]:
        """精準刪除特定關聯（包含雙向）。

        Precisely delete a specific relation (both directions).

        刪除兩個節點之間特定 relation_type 的所有連線 (A->B 與 B->A)。
        Removes all links of the given relation_type between the two
        nodes (A->B and B->A).

        Args:
            request: 包含 source_label, target_label, relation_type 的
                DTO。DTO with source_label, target_label, relation_type.

        Returns:
            刪除的紀錄數量 {"deleted_count": N}。Dict with the deleted row
            count.
        """
        condition = and_(
            CardRelation.relation_type == request.relation_type,
            or_(
                and_(
                    CardRelation.source_label == request.source_label,
                    CardRelation.target_label == request.target_label,
                ),
                and_(
                    CardRelation.source_label == request.target_label,
                    CardRelation.target_label == request.source_label,
                )
            )
        )
            
        stmt = delete(CardRelation).where(condition)
        result = await self._db_session.execute(stmt)
        await self._db_session.commit()
        
        deleted_count = result.rowcount
        logger.info(
            "刪除關聯 %s <-> %s (%s)，共 %d 筆", 
            request.source_label, request.target_label, request.relation_type, deleted_count
        )
        return {"deleted_count": deleted_count}

    async def get_relations_by_note_ids(
        self, note_ids: set[int] | None = None
    ) -> list["CardRelation"]:
        """根據一組 Note IDs 查詢相關的所有關聯紀錄。

        Query relation rows related to a set of note IDs.

        若傳入 note_ids，只查詢 source 或 target 包含在內的關聯。
        若傳入 None 或空集合，查詢全部。
        With note_ids, only relations whose source or target is included
        are returned; None or an empty set returns all rows.

        Args:
            note_ids: 要篩選的 Note ID 集合 (可選)。Optional set of note
                IDs to filter by.

        Returns:
            CardRelation ORM 實例列表。List of CardRelation ORM instances.
        """
        stmt = select(CardRelation)
        if note_ids:
            stmt = stmt.where(
                or_(
                    CardRelation.source_note_id.in_(note_ids),
                    CardRelation.target_note_id.in_(note_ids),
                )
            )
        result = await self._db_session.execute(stmt)
        return list(result.scalars().all())

    async def get_graph_data(
        self, 
        notes_info: list["AnkiNoteInfo"] | None = None,
        cards_info: list[dict[str, object]] | None = None,
    ) -> dict[str, list[dict]]:
        """合併 Anki 實際卡片與 SQLite 關聯紀錄，建立完整的知識圖譜。

        Merge actual Anki cards with SQLite relation rows into a full
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
        import re

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

        # 2. 從資料庫查詢關聯紀錄
        stmt = select(CardRelation)
        if valid_note_ids:
            # 如果有傳入 Anki 卡片，只查詢與這些卡片相關的關聯 (過濾牌組)
            stmt = stmt.where(
                or_(
                    CardRelation.source_note_id.in_(valid_note_ids),
                    CardRelation.target_note_id.in_(valid_note_ids),
                )
            )
            
        result = await self._db_session.execute(stmt)
        relations: Sequence[CardRelation] = result.scalars().all()

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
