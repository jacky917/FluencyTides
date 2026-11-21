"""Pydantic V2 request/response models for the card-relation API.

卡片關聯 API 請求/回應 Pydantic V2 模型。

Defines the DTOs (Data Transfer Objects) the frontend and Telegram use to
operate on card relations. They are decoupled from the database layer
(``RelationRecord`` inside ``RelationService`` and ``rel_links_table`` in
infrastructure/database/corpus_tables.py), so the API never exposes the
database structure directly.

此模組定義了前端與 TG 操作卡片關聯時使用的 DTO (Data Transfer Object)。
與資料庫層解耦（``RelationService`` 內部的 ``RelationRecord`` 與
infrastructure/database/corpus_tables.py 的 ``rel_links_table``），
確保 API 介面不直接暴露資料庫結構。

Every str field of the request models ``CardRelationCreate`` and
``CardRelationDelete`` specifies ``max_length`` to meet the MySQL
compatibility guideline.

請求模型 ``CardRelationCreate`` 與 ``CardRelationDelete`` 的所有 str 欄位均指定
``max_length`` 以符合 MySQL 相容性準則。

Relations are stored in the corpus ``rel_links`` (C of D9 in
docs/wip/corpus_db_rebuild_FEAT_2026-10-31.md), but these DTOs keep their old
shape and field names: ``relation_type`` uses the public names
(``has_context``, ``synonym``, and so on, which ``RelationService`` converts to
``core.*`` / ``user.*``), and ``id`` is ``rel_links.relation_id``.

關聯存在語料庫的 ``rel_links``（docs/wip/corpus_db_rebuild_FEAT_2026-10-31.md D9 的 C），但這些 DTO
維持舊的形狀與欄位名：``relation_type`` 用對外名稱（``has_context``、``synonym``…，由
``RelationService`` 轉成 ``core.*`` / ``user.*``），``id`` 是 ``rel_links.relation_id``。
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CardRelationCreate(BaseModel):
    """The request model for creating a card relation.

    建立卡片關聯的請求模型。

    Attributes:
        source_note_id: the source Anki note ID; ``None`` means an orphan relation without a source (rarely used)
            起點 Anki Note ID。若為 ``None`` 則代表這是一個沒有來源的孤兒關係（極少用）
        target_note_id: the target Anki note ID. ``None`` means a ghost relation, that is, the target word has
            no card yet; when it is created later, the system can fill it in through ``target_label``.
            終點 Anki Note ID。若為 ``None`` 則代表這是一個「懸空關係 (Ghost Relation)」，
            意即目標單字尚未建立卡片。未來建立時，系統可透過 ``target_label`` 將其補齊。
        relation_type: the relation type (synonym, antonym, parent, collocation, and so on)
            關係類型（synonym, antonym, parent, collocation 等）
        source_label: the human-readable label of the source (for example the word itself), used for graph
            drawing
            起點的人類可讀標籤（如單字本身），用於圖譜繪製
        target_label: the human-readable label of the target
            終點的人類可讀標籤
    """

    source_note_id: int | None = Field(default=None)
    target_note_id: int | None = Field(default=None)
    relation_type: str = Field(min_length=1, max_length=50)
    source_label: str = Field(default="", max_length=200)
    target_label: str = Field(default="", max_length=200)


class CardRelationRead(BaseModel):
    """The response model for a card relation.

    卡片關聯的回應模型。

    Records read from the database are serialized through this model and
    returned to the frontend.

    從資料庫讀取後，將透過此模型序列化並回傳給前端。

    Attributes:
        id: the relation record ID (the database primary key)
            關聯記錄 ID (資料庫的主鍵)
        source_note_id: the source Anki note ID
            起點 Anki Note ID
        target_note_id: the target Anki note ID (``None`` means a ghost node)
            終點 Anki Note ID（若為 ``None`` 則為懸空節點）
        relation_type: the relation type
            關係類型
        source_label: the human-readable label of the source
            起點的人類可讀標籤
        target_label: the human-readable label of the target
            終點的人類可讀標籤
        created_at: the creation time (UTC)
            建立時間 (UTC)
    """

    model_config = {"from_attributes": True}

    id: int
    source_note_id: int | None
    target_note_id: int | None
    relation_type: str
    source_label: str
    target_label: str
    created_at: datetime


class CardRelationDelete(BaseModel):
    """The request model for deleting one card relation or a pair of them.

    刪除單筆/雙筆卡片關聯的請求模型。

    Attributes:
        source_label: the human-readable label of the source
            起點的人類可讀標籤
        target_label: the human-readable label of the target
            終點的人類可讀標籤
        relation_type: the relation type
            關係類型
    """
    source_label: str = Field(max_length=200)
    target_label: str = Field(max_length=200)
    relation_type: str = Field(max_length=50)


class CardRelationBatchDelete(BaseModel):
    """The request model for batch-deleting relations (cascading cleanup when cards are deleted).

    批次刪除關聯的請求模型（用於卡片刪除時的連動清理）。
    """

    note_ids: list[int] = Field(
        description=(
            "要清理關聯的 Anki Note ID 列表，"
            "所有包含這些 ID（source 或 target）的關聯記錄都會被刪除"
        ),
    )


class RelationDef(BaseModel):
    """A single relation definition used by JSON import and LLM generation.

    供 JSON 匯入與 LLM 生成時使用的單筆關聯定義。

    Unlike ``CardRelationCreate``, this model is independent of the database
    layer; it is purely the structure of definition files and LLM output, and
    supports ``direction`` to express bidirectional relations. ``target_label``
    is the target name (for example another word), and ``relation_type`` is the
    relation name (for example synonym, antonym, collocation, or parent). When
    ``direction`` is ``bidirectional``, the service layer creates two records
    (A->B and B->A).

    與 ``CardRelationCreate`` 的差異在於，這個 Model 獨立於資料庫層，
    純粹作為定義檔與 LLM 輸出的結構，並支援 ``direction`` 來表示雙向關聯。
    ``target_label`` 是關聯目標名稱（如另一個單字），``relation_type`` 是關係名稱
    （如 synonym, antonym, collocation, parent 等）。``direction`` 若為
    ``bidirectional``，Service 層將會建立兩筆 (A->B, B->A) 的紀錄。
    """
    target_label: str = Field(description="關聯目標名稱")
    relation_type: str = Field(description="關係名稱")
    direction: Literal["forward", "bidirectional"] = Field(
        default="forward",
        description="關係方向性",
    )


class RelationSyncResponse(BaseModel):
    """The response of ``POST /relations/sync``.

    ``POST /relations/sync`` 的回應。
    """

    deleted_count: int = Field(..., ge=0, description="刪除的關聯數；資料庫驅動未回報筆數時為預估值")
    scanned_notes: int = Field(..., ge=0, description="這次從 Anki 取得的筆記數")
    orphan_notes: int = Field(..., ge=0, description="關聯中已不在 Anki 的筆記數")
    ankiweb_synced: bool = Field(..., description="清理前與 AnkiWeb 的同步是否成功；false 表示以本機收藏為準")
