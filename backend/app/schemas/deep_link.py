"""
Telegram Deep Link 解析用的 Pydantic Models。
"""

from typing import Literal

from pydantic import BaseModel, Field


class DeepLinkAction(BaseModel):
    """Deep Link 動作的基礎模型。"""

    action_type: str = Field(..., description="動作類型標識")


class RecordAudioAction(DeepLinkAction):
    """啟動錄音評分流程的動作。"""

    action_type: Literal["record"] = "record"
    card_id: str = Field(..., description="要進行錄音評分的 Anki Card ID")


class DeleteEntryAction(DeepLinkAction):
    """刪除特定歷史紀錄的動作。"""

    action_type: Literal["delete"] = "delete"
    section: Literal["ref", "rec"] = Field(..., description="要刪除的區塊 (ref 或 rec)")
    index: int = Field(..., description="要刪除的陣列索引")
    card_id: str = Field(..., description="目標 Anki Card ID")


class GenerateCardAction(DeepLinkAction):
    """生成特定主題對話卡的動作。"""

    action_type: Literal["generate"] = "generate"
    target_id: str = Field(..., description="目標卡片 ID，若為 'new' 則為新增")
