"""
單字卡任務處理器 (Vocabulary Mining Handler) 模組。

handlers 計畫書的試點：負責 TOEIC_Coach_Dark_v2，增刪改查全部用 ``BaseHandler`` 的標準實作、
不覆寫；圖譜用 ``RelationGraphMixin``（同義詞、搭配詞等關聯連線）。

Vocabulary mining handler module.

The pilot of the handlers plan: owns TOEIC_Coach_Dark_v2 and uses the
standard ``BaseHandler`` CRUD without overrides; the graph comes from
``RelationGraphMixin``.
"""

from typing import TYPE_CHECKING, override

from app.services.task_handlers.capabilities import (
    CreateCapability,
    GraphCapability,
    GraphSpec,
    HandlerCapabilities,
    Operation,
)
from app.services.task_handlers.registry import register_handler
from app.services.task_handlers.relation_graph import RelationGraphMixin
from app.infrastructure.anki.search import note_types_query

if TYPE_CHECKING:
    from app.services.card_service import CardService
    from app.services.relation_service import RelationService

NOTE_TYPE = "TOEIC_Coach_Dark_v2"


@register_handler
class VocabularyMiningHandler(RelationGraphMixin):
    """單字卡任務處理器。

    Vocabulary card task handler.

    只負責 TOEIC_Coach_Dark_v2（舊版 TOEIC_Coach_Dark 已無筆記，不再列入）。

    - 建立：手動填表，表單欄位就是 ``_v2`` 的欄位；JSON 與音檔欄位不列、建出來留空。
    - 修改 ``Expression`` 時，基底依 ``RELATION_LABEL_FIELD`` 同步關聯表兩端的文字，圖譜不會斷線。
    - 圖譜節點的副標、詞性讀 ``Target_Translation``、``Target_POS``。

    Owns TOEIC_Coach_Dark_v2 only. Creation is a manual form; renaming
    ``Expression`` keeps relation labels in sync through the base class.
    """

    CAPABILITIES = HandlerCapabilities(
        create=CreateCapability(
            supported=True, channels=("web", "tg"), flow="form", deck="selected", note_type=NOTE_TYPE
        ),
        update_fields=Operation(supported=True),
        delete=Operation(supported=True),
        graph=GraphCapability(supported=True, links=True),
    )
    GRAPH = GraphSpec(subtitle="Target_Translation", pos="Target_POS")
    RELATION_LABEL_FIELD = "Expression"
    JSON_FIELDS = ("Word_Family_JSON", "Synonyms_JSON", "Collocations_JSON")
    MEDIA_FIELDS = ("Word_Audio", "Target_Example_Audio")
    CREATE_TAGS = ("VocabularyMining", "HandlerGenerated")

    @property
    @override
    def handler_name(self) -> str:
        """處理器名稱。

        Handler name.
        """
        return "vocabulary_mining"

    @property
    @override
    def title(self) -> str:
        """中文名稱。Display title."""
        return "單字卡"

    @property
    @override
    def supported_models(self) -> list[str]:
        """支援的 Anki 模型名稱清單。

        Supported Anki model names.
        """
        return [NOTE_TYPE]

    @override
    def get_input_schema(self) -> dict[str, object]:
        """舊的建立參數 Schema（第 5 步移除）。Legacy create schema."""
        return {
            "type": "object",
            "properties": {
                "Expression": {"type": "string", "description": "單字或片語"},
            },
            "required": ["Expression"],
        }

    @override
    async def execute_read_list(
        self,
        card_service: "CardService",
        relation_service: "RelationService",
        deck_name: str | None = None
    ) -> list[dict[str, object]]:
        """舊的卡片列表（第 5 步移除）。Legacy card list.

        Args:
            card_service: 卡片服務。Card service.
            relation_service: 關聯服務。Relation service.
            deck_name: 可選的牌組篩選。Optional deck filter.

        Returns:
            AnkiConnect 的筆記資訊。Note info from AnkiConnect.
        """
        note_ids = await card_service.find_notes(note_types_query(self.supported_models, deck_name))
        return await card_service.get_notes_info(note_ids)
