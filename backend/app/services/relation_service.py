"""
卡片關聯業務邏輯服務模組。

負責與資料庫中的 CardRelation 表進行互動，提供卡片關聯的建立、批次建立、
連動刪除（清理孤兒節點）以及高效的知識圖譜資料查詢。

此模組實作了 Phase 5 計畫中的 RelationService。
"""

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.schemas.relation import CardRelationDelete
    from app.schemas.anki import AnkiNoteInfo

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models import CardRelation, RelationType
from app.schemas.relation import CardRelationCreate, CardRelationRead

logger = logging.getLogger(__name__)


class RelationService:
    """卡片關聯管理服務。

    提供操作關聯資料庫（CardRelation 表）的方法，
    並抽象出前端 react-force-graph 所需的 graph_data 結構轉換。

    Attributes:
        _db_session: 注入的非同步資料庫 Session。
    """

    def __init__(self, db_session: AsyncSession) -> None:
        """初始化 RelationService。

        Args:
            db_session: 注入的 AsyncSession 實例。
        """
        self._db_session = db_session

    async def create_relation(
        self, request: CardRelationCreate
    ) -> CardRelationRead:
        """建立單筆卡片關聯。

        Args:
            request: CardRelationCreate DTO。

        Returns:
            已建立的 CardRelationRead 實例。
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

        使用 add_all 優化寫入效能。若列表為空則直接返回。

        Args:
            requests: CardRelationCreate DTO 列表。

        Returns:
            已建立的 CardRelationRead 實例列表。
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

        執行雙向刪除：只要 source_note_id 或 target_note_id 等於該 note_id，
        就會被刪除。用於卡片被刪除時避免產生死連結（孤兒節點）。

        Args:
            note_id: 目標 Anki Note ID。

        Returns:
            被刪除的關聯記錄數量。
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

    async def sync_with_anki(self, valid_note_ids: list[int]) -> int:
        """根據 Anki 現存的 Note ID，清理資料庫中的孤兒關聯。

        若 CardRelation 的 source_note_id 或 target_note_id
        (非 None 時) 不存在於 valid_note_ids，則將該筆關聯刪除。
        這確保了資料庫關聯 (為輔) 嚴格追隨 Anki 卡片 (為主)。

        Args:
            valid_note_ids: 當前 Anki 中所有存在的 Note ID 列表。

        Returns:
            被刪除的孤兒關聯記錄數量。
        """
        stmt = delete(CardRelation).where(
            or_(
                and_(
                    CardRelation.source_note_id.is_not(None),
                    CardRelation.source_note_id.not_in(valid_note_ids)
                ),
                and_(
                    CardRelation.target_note_id.is_not(None),
                    CardRelation.target_note_id.not_in(valid_note_ids)
                ),
            )
        )
        result = await self._db_session.execute(stmt)
        await self._db_session.commit()
        
        deleted_count = result.rowcount
        logger.info("同步清理完成：已刪除 %d 筆孤兒關聯", deleted_count)
        return deleted_count

    async def delete_relations_for_note(self, note_id: int) -> int:
        """刪除指定筆記的所有關聯（無論是來源或目標）。

        Args:
            note_id: 筆記 ID。

        Returns:
            刪除的紀錄數量。
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

        當 Anki 卡片的主要欄位 (如 Expression) 改變時，同步更新冗餘資料。

        Args:
            note_id: 來源筆記 ID。
            new_label: 新的標籤文字。

        Returns:
            更新的紀錄數量。
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
        """取得所有已註冊的關聯類型名稱。"""
        stmt = select(RelationType.name).order_by(RelationType.name)
        result = await self._db_session.execute(stmt)
        return list(result.scalars().all())

    async def get_or_create_relation_type(self, name: str) -> None:
        """確保關聯類型存在於資料庫中，若不存在則自動新增。"""
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
        
        刪除兩個節點之間特定 relation_type 的所有連線 (A->B 與 B->A)。
        
        Args:
            request: 包含 source_label, target_label, relation_type 的 DTO。
            
        Returns:
            刪除的紀錄數量 {"deleted_count": N}。
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

    async def get_graph_data(
        self, 
        notes_info: list["AnkiNoteInfo"] | None = None,
        cards_info: list[dict[str, object]] | None = None,
    ) -> dict[str, list[dict]]:
        """合併 Anki 實際卡片與 SQLite 關聯紀錄，建立完整的知識圖譜。

        Args:
            notes_info: 從 Anki 取得的卡片詳細資訊列表。用來建立圖譜中的「孤立節點」。
            cards_info: 從 Anki 取得的卡片狀態資訊列表。用來決定節點的熟練度顏色。

        Returns:
            圖譜結構字典: {"nodes": [...], "links": [...]}
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
