"""
Telegram Deep Link 解析用的 Pydantic Models。

Pydantic models for parsing Telegram deep links.
"""

from typing import Literal

from pydantic import BaseModel, Field


class DeepLinkAction(BaseModel):
    """Deep Link 動作的基礎模型。

    Base model for deep-link actions.
    """

    action_type: str = Field(..., description="動作類型標識")


class RecordAudioAction(DeepLinkAction):
    """啟動錄音評分流程的動作。

    Action that starts the recording-evaluation flow.
    """

    action_type: Literal["record"] = "record"
    field_name: str = Field(..., description="要寫入結果的目標欄位 (例如 Recordings)")
    index: str = Field(..., description="要寫入的索引位置 (例如 last)")
    card_id: str = Field(..., description="要進行錄音評分的 Anki Card ID")


class DeleteEntryAction(DeepLinkAction):
    """刪除特定歷史紀錄的動作。

    Action that deletes a specific history entry.
    """

    action_type: Literal["delete"] = "delete"
    field_name: str = Field(..., description="要刪除的區塊 (例如 Recordings 或 References)")
    index: str = Field(..., description="要刪除的陣列索引字串")
    card_id: str = Field(..., description="目標 Anki Card ID")


class GenerateCardAction(DeepLinkAction):
    """生成特定主題對話卡的動作。

    Action that generates a topic-specific conversation card.
    """

    action_type: Literal["generate"] = "generate"
    target_id: str = Field(..., description="目標卡片 ID，若為 'new' 則為新增")


class AddAudioAction(DeepLinkAction):
    """新增語音到卡片的動作 (包含正面或參考範本)。

    Action that adds audio to a card (front side or references).
    """

    action_type: Literal["add_audio"] = "add_audio"
    field_name: str = Field(..., description="要新增語音的區塊 (例如 Prompt_Audios 或 References)")
    index: str = Field(..., description="要新增語音的陣列索引字串")
    card_id: str = Field(..., description="目標 Anki Card ID")


class TTSAudioAction(DeepLinkAction):
    """透過 TTS 生成語音到卡片的動作 (包含正面或參考範本)。

    Action that generates audio for a card via TTS (front or references).
    """

    action_type: Literal["tts_audio"] = "tts_audio"
    field_name: str = Field(..., description="要生成語音的區塊 (例如 Prompt_Audios 或 References)")
    index: str = Field(..., description="要生成語音的陣列索引字串")
    card_id: str = Field(..., description="目標 Anki Card ID")
