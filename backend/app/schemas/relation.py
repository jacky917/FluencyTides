"""
卡片關聯 API 請求/回應 Pydantic V2 模型。

Pydantic V2 request/response models for the card-relation API.

此模組定義了前端與 TG 操作卡片關聯時使用的 DTO (Data Transfer Object)。
與 infrastructure/database/models.py 中的 Table Model 解耦，
確保 API 介面不直接暴露資料庫結構。

Defines the DTOs used by the frontend and Telegram to manipulate card
relations, decoupled from the table models in
infrastructure/database/models.py so the API never exposes DB structure.

所有 str 欄位均指定 max_length 以符合 MySQL 相容性準則。

Every str field specifies max_length for MySQL compatibility.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CardRelationCreate(BaseModel):
    """建立卡片關聯的請求模型。

    Request model for creating a card relation.

    Attributes:
        source_note_id (int | None): 起點 Anki Note ID。若為 None 則代表這是
            一個沒有來源的孤兒關係（極少用）。Source Anki note ID; None means
            an orphan relation without a source (rarely used).
        target_note_id (int | None): 終點 Anki Note ID。若為 None 則代表這是
            一個「懸空關係 (Ghost Relation)」，意即目標單字尚未建立卡片。
            未來建立時，系統可透過 target_label 將其補齊。Target Anki note
            ID; None means a ghost relation whose target card does not yet
            exist and can later be filled in via target_label.
        relation_type (str): 關係類型（synonym, antonym, parent, collocation
            等）。Relation type (synonym, antonym, parent, collocation, ...).
        source_label (str): 起點的人類可讀標籤（如單字本身）。用於圖譜繪製。
            Human-readable label of the source node, used for graph drawing.
        target_label (str): 終點的人類可讀標籤。Human-readable label of the
            target node.
    """

    source_note_id: int | None = Field(default=None)
    target_note_id: int | None = Field(default=None)
    relation_type: str = Field(max_length=50)
    source_label: str = Field(default="", max_length=200)
    target_label: str = Field(default="", max_length=200)


class CardRelationRead(BaseModel):
    """卡片關聯的回應模型。

    Response model for a card relation.

    從資料庫讀取後，將透過此模型序列化並回傳給前端。

    Records read from the database are serialized through this model
    before being returned to the frontend.

    Attributes:
        id (int): 關聯記錄 ID (資料庫的主鍵)。Relation record ID (DB
            primary key).
        source_note_id (int | None): 起點 Anki Note ID。Source Anki note ID.
        target_note_id (int | None): 終點 Anki Note ID（若為 None 則為懸空
            節點）。Target Anki note ID; None means a ghost node.
        relation_type (str): 關係類型。Relation type.
        source_label (str): 起點的人類可讀標籤。Human-readable source label.
        target_label (str): 終點的人類可讀標籤。Human-readable target label.
        created_at (datetime): 建立時間 (UTC)。Creation time (UTC).
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
    """刪除單筆/雙筆卡片關聯的請求模型。

    Request model for deleting one (or a paired) card relation.

    Attributes:
        source_label (str): 起點的人類可讀標籤。Human-readable source label.
        target_label (str): 終點的人類可讀標籤。Human-readable target label.
        relation_type (str): 關係類型。Relation type.
    """
    source_label: str = Field(max_length=200)
    target_label: str = Field(max_length=200)
    relation_type: str = Field(max_length=50)


class CardRelationBatchDelete(BaseModel):
    """批次刪除關聯的請求模型（用於卡片刪除時的連動清理）。

    Batch-delete request model (cascading cleanup on card deletion).

    Attributes:
        note_ids: 要清理關聯的 Anki Note ID 列表。Anki note IDs whose
            relations should be cleaned up.
    """

    note_ids: list[int] = Field(
        description=(
            "要清理關聯的 Anki Note ID 列表，"
            "所有包含這些 ID（source 或 target）的關聯記錄都會被刪除"
        ),
    )


class RelationDef(BaseModel):
    """供 JSON 匯入與 LLM 生成時使用的單筆關聯定義。

    Single relation definition used by JSON import and LLM generation.

    與 CardRelationCreate 的差異在於，這個 Model 獨立於資料庫層，
    純粹作為定義檔與 LLM 輸出的結構，並支援 `direction` 來表示雙向關聯。

    Unlike CardRelationCreate, this model is independent of the database
    layer — purely a definition-file/LLM-output structure — and supports
    `direction` for bidirectional relations.

    Attributes:
        target_label (str): 關聯目標名稱 (如另一個單字)。Target label
            (e.g. another word).
        relation_type (str): 關係名稱 (如 synonym, antonym, collocation,
            parent, etc.)。Relation name.
        direction (Literal["forward", "bidirectional"]): 方向性。若為
            bidirectional，Service 層將會建立兩筆 (A->B, B->A) 的紀錄。
            Directionality; for bidirectional the service layer creates
            two records (A->B and B->A).
    """
    target_label: str = Field(description="關聯目標名稱")
    relation_type: str = Field(description="關係名稱")
    direction: Literal["forward", "bidirectional"] = Field(
        default="forward",
        description="關係方向性",
    )
