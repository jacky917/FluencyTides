"""Vocabulary mining handler module.

單字卡任務處理器（Vocabulary Mining Handler）模組。

The pilot of the handlers plan: owns TOEIC_Coach_Dark_v2 and uses the standard ``BaseHandler`` read, update and
delete and the standard ``BaseCreator`` create without overrides; the graph comes from ``RelationGraphMixin``
(relation links such as synonyms and collocations).

handlers 計畫書的試點：負責 TOEIC_Coach_Dark_v2，讀改刪用 ``BaseHandler``、建立用 ``BaseCreator`` 的標準實作、
不覆寫；圖譜用 ``RelationGraphMixin``（同義詞、搭配詞等關聯連線）。
"""

from typing import override

from app.services.task_handlers.capabilities import (
    CreateCapability,
    CreatorCapabilities,
    GraphCapability,
    GraphSpec,
    HandlerCapabilities,
    Operation,
)
from app.services.task_handlers.creator import BaseCreator
from app.services.task_handlers.registry import register_creator, register_handler
from app.services.task_handlers.relation_graph import RelationGraphMixin

NOTE_TYPE = "TOEIC_Coach_Dark_v2"


@register_handler
class VocabularyMiningHandler(RelationGraphMixin):
    """Vocabulary card task handler.

    單字卡任務處理器。

    Owns TOEIC_Coach_Dark_v2 only (the old TOEIC_Coach_Dark has no notes left and is no longer listed).

    - Create: see ``VocabularyMiningCreator``.
    - When ``Expression`` changes, the base class syncs the text at both ends of the relations through
      ``RELATION_LABEL_FIELD``, so the graph does not lose its links.
    - Graph nodes read their subtitle and part of speech from ``Target_Translation`` and ``Target_POS``.

    只負責 TOEIC_Coach_Dark_v2（舊版 TOEIC_Coach_Dark 已無筆記，不再列入）。

    - 建立：見 ``VocabularyMiningCreator``。
    - 修改 ``Expression`` 時，基底依 ``RELATION_LABEL_FIELD`` 同步關聯表兩端的文字，圖譜不會斷線。
    - 圖譜節點的副標、詞性讀 ``Target_Translation``、``Target_POS``。
    """

    CAPABILITIES = HandlerCapabilities(
        update_fields=Operation(supported=True),
        delete=Operation(supported=True),
        graph=GraphCapability(supported=True, links=True),
    )
    GRAPH = GraphSpec(subtitle="Target_Translation", pos="Target_POS")
    RELATION_LABEL_FIELD = "Expression"
    JSON_FIELDS = ("Word_Family_JSON", "Synonyms_JSON", "Collocations_JSON")
    MEDIA_FIELDS = {"Word_Audio": "audio", "Target_Example_Audio": "audio"}

    @property
    @override
    def handler_name(self) -> str:
        """The handler name used in URLs.

        處理器名稱，出現在網址裡。
        """
        return "vocabulary_mining"

    @property
    @override
    def title(self) -> str:
        """The Chinese display title.

        中文名稱。
        """
        return "單字卡"

    @property
    @override
    def supported_models(self) -> list[str]:
        """The Anki note types this handler owns.

        這個處理器負責的 Anki 筆記類型。
        """
        return [NOTE_TYPE]


@register_creator
class VocabularyMiningCreator(BaseCreator):
    """Creates vocabulary cards with the standard create.

    以標準建立建單字卡。

    A manual form whose fields are the ``_v2`` fields; JSON fields are not listed and stay empty, and audio fields
    are uploaded or recorded on the web (media_upload plan D5).

    手動填表，表單欄位就是 ``_v2`` 的欄位；JSON 欄位不列、建出來留空，音檔欄位在網頁上傳或錄音
    （media_upload 計畫書 D5）。
    """

    HANDLER = "vocabulary_mining"
    CAPABILITIES = CreatorCapabilities(
        create=CreateCapability(
            supported=True, channels=("web", "tg"), flow="form", deck="selected", note_type=NOTE_TYPE
        ),
    )
    CREATE_TAGS = ("VocabularyMining", "HandlerGenerated")

    @property
    @override
    def creator_name(self) -> str:
        """The creator name used in create URLs.

        creator 名稱，出現在建卡網址裡。
        """
        return "vocabulary_mining"
