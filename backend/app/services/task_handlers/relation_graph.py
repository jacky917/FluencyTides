"""
關聯圖譜（handlers 計畫書 D12）：在標準的單點圖譜上加上學習狀態、關聯連線與幽靈節點。

Relation graph: adds study status, relation links and ghost nodes on top of
the standard single-node graph.

邏輯原樣搬自 ``vocabulary_mining`` 原本的 ``execute_read_graph``，只把寫死的欄位改成讀宣告：
節點名稱讀 ``RELATION_LABEL_FIELD``，副標、詞性讀 ``GRAPH.subtitle``、``GRAPH.pos``。

Ported as is from ``vocabulary_mining``'s former graph; only the hard-coded
fields became declarations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.exceptions import InvalidNoteQueryError
from app.services.task_handlers.base import BaseHandler, clean_html, field_value

if TYPE_CHECKING:
    from app.services.card_service import CardService
    from app.services.relation_service import RelationService

# 節點的 group（前端依它上色）與大小。Node groups (the frontend colours by them) and sizes.
NOTE_GROUP = 1
SYNONYM_GROUP = 2
COLLOCATION_GROUP = 3
GHOST_GROUP = 4
NOTE_SIZE = 20
GHOST_SIZE = 10


class RelationGraphMixin(BaseHandler):
    """改用關聯圖譜的 handler 繼承這個類別，並宣告 ``RELATION_LABEL_FIELD``。

    Handlers using the relation graph inherit this and declare
    ``RELATION_LABEL_FIELD``.

    關聯表以文字辨認節點（每條連線記了兩端的 ``source_label``、``target_label``），所以節點名稱
    固定用 ``RELATION_LABEL_FIELD``，不開放 ``label_field`` 切換（切換了連線會對不上）。

    Relations identify nodes by text, so node names always come from
    ``RELATION_LABEL_FIELD``; ``label_field`` is not accepted.
    """

    async def execute_read_graph(
        self,
        card_service: CardService,
        relation_service: RelationService,
        deck_name: str | None = None,
        *,
        note_type: str | None = None,
        label_field: str | None = None,
    ) -> dict[str, object]:
        """關聯圖譜：筆記節點依學習狀態上色，加上關聯連線與只記了文字的幽靈節點。

        Relation graph: note nodes coloured by study status, plus relation
        links and ghost nodes that only exist as text.

        Args:
            card_service: 卡片服務。Card service.
            relation_service: 關聯服務。Relation service.
            deck_name: 牌組；None 表示全部。The deck, or None for all.
            note_type: 只看其中一種筆記類型。One of the handler's note types.
            label_field: 不適用，帶了就拒絕。Not accepted.

        Returns:
            ``{"nodes", "links", "label_fields": [], "label_field": RELATION_LABEL_FIELD}``。

        Raises:
            InvalidNoteQueryError: 帶了 ``label_field``，或 ``note_type`` 不歸這個 handler。
        """
        label_name = self.RELATION_LABEL_FIELD
        if label_field is not None:
            raise InvalidNoteQueryError(f"「{self.title}」的圖譜節點名稱固定為 {label_name}，不能指定名稱欄位")

        # 1. 取得符合此任務的所有 Anki 筆記，以及每張筆記第一張卡片的學習狀態
        note_ids = await self.query_notes(card_service, deck_name, note_type)
        notes = []
        cards_info: list[dict[str, object]] = []
        if note_ids:
            notes = await card_service.anki_client.get_notes_info(note_ids)
            card_ids = [n.cards[0] for n in notes if n.cards]
            if card_ids:
                cards_info = await card_service.anki_client.get_cards_info(card_ids)
        status_by_note = _status_by_note(cards_info)

        nodes: dict[str, dict[str, object]] = {}
        links: list[dict[str, object]] = []
        valid_note_ids: set[int] = set()

        for note in notes:
            valid_note_ids.add(note.noteId)
            fields = note.fields
            label = str(fields[label_name].get("value", "")).strip() if label_name in fields else ""
            if not label:
                continue
            nodes[label] = {
                "id": label,
                "group": NOTE_GROUP,
                "val": NOTE_SIZE,
                "label": label,
                "translation": clean_html(field_value(fields, self.GRAPH.subtitle)),
                "pos": clean_html(field_value(fields, self.GRAPH.pos)),
                "note_id": note.noteId,
                "status": status_by_note.get(note.noteId, "new"),
            }

        # 2. 關聯連線；另一端沒有筆記節點的，補一個幽靈節點
        relations = await relation_service.get_relations_by_note_ids(valid_note_ids or None)
        for rel in relations:
            source_id = rel.source_label
            target_id = rel.target_label

            if source_id not in nodes:
                nodes[source_id] = {
                    "id": source_id,
                    "group": GHOST_GROUP,
                    "val": GHOST_SIZE,
                    "label": source_id,
                    "note_id": rel.source_note_id,
                }
            if target_id not in nodes:
                nodes[target_id] = {
                    "id": target_id,
                    "group": _target_group(rel.relation_type),
                    "val": GHOST_SIZE,
                    "note_id": rel.target_note_id,
                }
            links.append(
                {
                    "source": source_id,
                    "target": target_id,
                    "label": rel.relation_type.capitalize(),
                    "relation_id": rel.id,
                }
            )

        return {
            "nodes": list(nodes.values()),
            "links": links,
            "label_fields": [],
            "label_field": label_name,
        }


def _status_by_note(cards_info: list[dict[str, object]]) -> dict[int, str]:
    """依卡片的 queue 判斷學習狀態。Study status from each card's queue.

    Args:
        cards_info: AnkiConnect ``cardsInfo`` 的結果。The cardsInfo result.

    Returns:
        ``{note_id: "new" | "learning" | "review" | "suspended"}``。
    """
    statuses: dict[int, str] = {}
    for card in cards_info:
        note_id = card.get("note")
        queue = card.get("queue", 0)
        if not isinstance(queue, int):
            queue = 0
        if queue in (1, 3):
            status = "learning"
        elif queue == 2:
            status = "review"
        elif queue < 0:
            status = "suspended"
        else:
            status = "new"
        if note_id is not None:
            statuses[int(note_id)] = status
    return statuses


def _target_group(relation_type: str) -> int:
    """幽靈終點節點的 group：同義詞、搭配詞各一色，其他同幽靈節點。Ghost target group."""
    if relation_type == "synonym":
        return SYNONYM_GROUP
    if relation_type == "collocation":
        return COLLOCATION_GROUP
    return GHOST_GROUP
