"""
單字與片語挖掘任務處理器 (Vocabulary Mining Handler) 模組。

負責 TOEIC_Coach_Dark 等單字類卡片的完整 CRUD 生命週期，
以及知識圖譜的拼裝與讀取。

Vocabulary mining handler module.

Owns the full CRUD lifecycle of vocabulary-type cards such as
TOEIC_Coach_Dark, as well as assembling and reading the knowledge graph.
"""

import logging
import re

from typing import override, TYPE_CHECKING

from app.services.task_handlers.base import BaseHandler
from app.services.task_handlers.registry import register_handler

if TYPE_CHECKING:
    from app.services.card_service import CardService
    from app.services.relation_service import RelationService

logger = logging.getLogger(__name__)


@register_handler
class VocabularyMiningHandler(BaseHandler):
    """單字與片語挖掘任務處理器。

    Vocabulary and phrase mining task handler.

    支援單字與句型的基本卡片 (如 TOEIC_Coach_Dark)，
    並且負責生成包含同義詞、搭配詞的知識圖譜。

    Supports basic vocabulary/sentence-pattern cards (e.g.
    TOEIC_Coach_Dark) and builds the knowledge graph containing synonyms
    and collocations.

    設計決策：
    - execute_read_graph 中的拼裝邏輯原屬 RelationService.get_graph_data，
      現已搬移至此 Handler，讓 RelationService 保持純粹的 SQLite CRUD。
    - 圖譜拼裝邏輯高度依賴 Expression/Meaning/PartOfSpeech 等欄位，
      這些是單字卡片特有的視圖需求，不應汙染通用的 RelationService。
    """

    @property
    @override
    def handler_name(self) -> str:
        """處理器名稱。

        Handler name.
        """
        return "vocabulary_mining"

    @property
    @override
    def supported_models(self) -> list[str]:
        """支援的 Anki 模型名稱清單。

        Supported Anki model names.
        """
        return ["TOEIC_Coach_Dark", "TOEIC_Coach_Dark_v2", "Default"]

    @override
    def get_input_schema(self) -> dict[str, object]:
        """回傳前端需要的參數 Schema。

        Return the parameter JSON schema required by the frontend.
        """
        return {
            "type": "object",
            "properties": {
                "word": {"type": "string", "description": "要挖掘的單字或片語"},
            },
            "required": ["word"],
        }

    @override
    async def execute_create(
        self,
        card_service: "CardService",
        relation_service: "RelationService",
        deck_name: str,
        model_name: str,
        parameters: dict[str, object]
    ) -> int:
        """根據單字呼叫 LLM 進行擴充，並寫入 Anki。

        Expand the given word via the LLM and write it to Anki.

        注意：目前尚未包含呼叫 LLM 的邏輯。
        Phase 9 重構專注於 CRUD 封裝，LLM 呼叫將在後續 Phase 移植。
        Note: the LLM call is not yet implemented; Phase 9 focuses on the
        CRUD encapsulation and the LLM call will be ported later.

        Args:
            card_service: 卡片服務。Card service.
            relation_service: 關聯服務。Relation service.
            deck_name: 目標牌組。Target deck.
            model_name: 目標模型。Target model.
            parameters: 必須包含 'word' 欄位。Must contain a 'word' key.

        Returns:
            建立成功的 Note ID。ID of the created note.
        """
        word = str(parameters.get("word", ""))

        fields = {
            "Expression": word,
            "Meaning": "（由 LLM 產生的翻譯）",
            "Context": "",
        }

        logger.info("建立單字任務卡片 (Model: %s) -> Word: %s", model_name, word)
        return await card_service.create_note(
            deck_name=deck_name,
            model_name=model_name,
            fields=fields,
            tags=["VocabularyMining", "HandlerGenerated"],
        )

    @override
    async def execute_read_list(
        self,
        card_service: "CardService",
        relation_service: "RelationService",
        deck_name: str | None = None
    ) -> list[dict[str, object]]:
        """讀取單字卡片列表。

        Read the list of vocabulary cards.

        Args:
            card_service: 卡片服務。Card service.
            relation_service: 關聯服務。Relation service.
            deck_name: 可選的牌組篩選。Optional deck filter.

        Returns:
            經過清洗的卡片資訊列表。Sanitized list of card info dicts.
        """
        query = " OR ".join([f'"note:{m}"' for m in self.supported_models])
        if deck_name:
            query = f'"deck:{deck_name}" ({query})'

        note_ids = await card_service.find_notes(query)
        notes_info = await card_service.get_notes_info(note_ids)

        # 可在這裡進行隱藏欄位的過濾
        return notes_info

    @override
    async def execute_read_graph(
        self,
        card_service: "CardService",
        relation_service: "RelationService",
        deck_name: str | None = None
    ) -> dict[str, list[dict[str, object]]]:
        """拼裝此任務的知識圖譜。

        Assemble the knowledge graph for this task.

        將從 Anki 撈出來的卡片，與 SQLite 的關聯資料進行合併。
        此邏輯原屬 RelationService.get_graph_data，
        現搬移至此以符合「CRUD 全部封裝於 Handler」的架構要求。
        Merges cards fetched from Anki with relation rows from SQLite.
        This logic moved here from RelationService.get_graph_data to keep
        all CRUD encapsulated inside handlers.

        Args:
            card_service: 卡片服務。Card service.
            relation_service: 關聯服務。Relation service.
            deck_name: 可選的牌組篩選。Optional deck filter.

        Returns:
            圖譜結構字典: {"nodes": [...], "links": [...]}。Graph structure
            dict with "nodes" and "links".
        """
        # 1. 取得符合此任務的所有 Anki 卡片
        query = " OR ".join([f'"note:{m}"' for m in self.supported_models])
        if deck_name:
            query = f'"deck:{deck_name}" ({query})'

        note_ids = await card_service.find_notes(query)
        notes_info_objs = []
        cards_info: list[dict[str, object]] = []
        if note_ids:
            notes_info_objs = await card_service.anki_client.get_notes_info(note_ids)
            card_ids = [n.cards[0] for n in notes_info_objs if n.cards]
            if card_ids:
                cards_info = await card_service.anki_client.get_cards_info(card_ids)

        nodes_dict: dict[str, dict[str, object]] = {}
        links: list[dict[str, object]] = []
        valid_note_ids: set[int] = set()

        # 建立 note_id -> status 對映表 (用來決定節點熟練度顏色)
        card_status_map: dict[int, str] = {}
        if cards_info:
            for c in cards_info:
                n_id = c.get("note")
                queue = c.get("queue", 0)
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
                if n_id is not None:
                    card_status_map[int(n_id)] = status

        if notes_info_objs:
            for note in notes_info_objs:
                valid_note_ids.add(note.noteId)
                fields = note.fields

                expression = ""
                if "Expression" in fields:
                    expression = str(
                        fields["Expression"].get("value", "")
                    ).strip()

                if not expression:
                    continue

                translation = _clean_html(
                    fields.get("Meaning", {}).get("value", "")
                )
                pos = _clean_html(
                    fields.get("PartOfSpeech", {}).get("value", "")
                )
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

        # 2. 透過 RelationService 的公開方法查詢關聯 (不直接存取 _db_session)
        relations = await relation_service.get_relations_by_note_ids(
            valid_note_ids if valid_note_ids else None
        )

        for rel in relations:
            source_id = rel.source_label
            target_id = rel.target_label

            if source_id not in nodes_dict:
                nodes_dict[source_id] = {
                    "id": source_id,
                    "group": 4,
                    "val": 10,
                    "label": source_id,
                    "note_id": rel.source_note_id,
                }

            if target_id not in nodes_dict:
                group = (
                    2
                    if rel.relation_type == "synonym"
                    else 3 if rel.relation_type == "collocation" else 4
                )
                nodes_dict[target_id] = {
                    "id": target_id,
                    "group": group,
                    "val": 10,
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
            "nodes": list(nodes_dict.values()),
            "links": links,
        }

    @override
    async def execute_update(
        self,
        card_service: "CardService",
        relation_service: "RelationService",
        note_id: int,
        parameters: dict[str, object]
    ) -> None:
        """更新單字卡片。

        Update a vocabulary card.

        Args:
            card_service: 卡片服務。Card service.
            relation_service: 關聯服務。Relation service.
            note_id: 目標 Note ID。Target note ID.
            parameters: 包含 'fields' 的字典。Dict containing 'fields'.
        """
        update_fields = parameters.get("fields", {})
        if not isinstance(update_fields, dict):
            return

        if update_fields:
            # 將值統一轉為字串 (CardService.update_note_fields 預期 dict[str, str])
            str_fields = {k: str(v) for k, v in update_fields.items()}
            await card_service.update_note_fields(note_id, str_fields)

            # 若 Expression 改變，同步更新 SQLite 內的冗餘標籤
            if "Expression" in str_fields:
                await relation_service.update_source_label(
                    note_id, str_fields["Expression"]
                )

    @override
    async def execute_delete(
        self,
        card_service: "CardService",
        relation_service: "RelationService",
        note_id: int
    ) -> None:
        """刪除單字卡片及其關聯。

        Delete a vocabulary card and its relations.

        Args:
            card_service: 卡片服務。Card service.
            relation_service: 關聯服務。Relation service.
            note_id: 目標 Note ID。Target note ID.
        """
        await card_service.delete_note(note_id)
        await relation_service.delete_relations_for_note(note_id)


def _clean_html(html_str: object) -> str:
    """移除 Anki 可能加入的 HTML 標籤與跳脫字元。

    Strip HTML tags and escape entities that Anki may have added.

    Args:
        html_str: 可能包含 HTML 的原始字串。Raw string that may contain
            HTML.

    Returns:
        清洗後的純文字。Cleaned plain text.
    """
    text = re.sub(r"<[^>]+>", "", str(html_str))
    return (
        text.replace("&quot;", '"')
        .replace("&nbsp;", " ")
        .replace("<br>", "")
        .replace("<div>", "")
        .replace("</div>", "")
    )
