"""Pydantic models for parsing Telegram deep links.

Telegram Deep Link 解析用的 Pydantic Models。
"""

from typing import Literal

from pydantic import BaseModel, Field


class DeepLinkAction(BaseModel):
    """The base model for deep-link actions.

    Deep Link 動作的基礎模型。
    """

    action_type: str = Field(..., description="動作類型標識")


class RecordAudioAction(DeepLinkAction):
    """The action that starts the recording-evaluation flow.

    啟動錄音評分流程的動作。
    """

    action_type: Literal["record"] = "record"
    field_name: str = Field(..., description="要寫入結果的目標欄位 (例如 Recordings)")
    index: str = Field(..., description="要寫入的索引位置 (例如 last)")
    card_id: str = Field(..., description="要進行錄音評分的 Anki Card ID")


class DeleteEntryAction(DeepLinkAction):
    """The action that deletes a specific history entry.

    刪除特定歷史紀錄的動作。
    """

    action_type: Literal["delete"] = "delete"
    field_name: str = Field(..., description="要刪除的區塊 (例如 Recordings 或 References)")
    index: str = Field(..., description="要刪除的陣列索引字串")
    card_id: str = Field(..., description="目標 Anki Card ID")


class GenerateCardAction(DeepLinkAction):
    """The action that generates a conversation card on a specific topic.

    生成特定主題對話卡的動作。
    """

    action_type: Literal["generate"] = "generate"
    target_id: str = Field(..., description="目標卡片 ID，若為 'new' 則為新增")


class AddAudioAction(DeepLinkAction):
    """The action that adds audio to a card (the front or the references).

    新增語音到卡片的動作 (包含正面或參考範本)。
    """

    action_type: Literal["add_audio"] = "add_audio"
    field_name: str = Field(..., description="要新增語音的區塊 (例如 Prompt_Audios 或 References)")
    index: str = Field(..., description="要新增語音的陣列索引字串")
    card_id: str = Field(..., description="目標 Anki Card ID")


class TTSAudioAction(DeepLinkAction):
    """The action that generates audio for a card through TTS (the front or the references).

    透過 TTS 生成語音到卡片的動作 (包含正面或參考範本)。
    """

    action_type: Literal["tts_audio"] = "tts_audio"
    field_name: str = Field(..., description="要生成語音的區塊 (例如 Prompt_Audios 或 References)")
    index: str = Field(..., description="要生成語音的陣列索引字串")
    card_id: str = Field(..., description="目標 Anki Card ID")


class PersonLinkAction(DeepLinkAction):
    """A deep link from a person profile card.

    人脈卡的深連結。

    It covers the card's "+" (``add`` adds to one field; ``section`` is the "+" of a section, where
    ``field_name`` is the section code and Telegram first asks which item to add), delete (``del``), edit a
    text field (``edit``), done, strike out (``end``), edit the profile, photo, AI analysis and topics, and the
    AI name-masking toggle (``index`` is on / off: the state after the toggle); see the person profile card
    plan §3.8 and the registration-date plan §4.4. Long-pressing a list item (``item``) or a text field
    (``field``) first opens a Telegram menu (hide or show, strike out, delete; edit, hide or show), as in the
    work history plan §5.4. The ``del`` and ``edit`` links of older card faces still work.

    包含卡面的「+」（``add`` 新增到一個欄位；``section`` 是區塊的「+」，``field_name`` 為區塊代號，
    TG 先問新增哪一項）、刪除（``del``）、修改文字欄位（``edit``）、完成、劃掉（``end``）、改資料、照片、
    AI 分析與話題、AI 遮名字開關
    （``index`` 為 on／off：切換後的狀態）（人脈卡計畫書 §3.8、登錄日計畫書 §4.4）。長按清單的一筆（``item``）與
    長按文字欄位（``field``）先開 TG 的選單（隱藏或顯示、劃掉、刪除；修改、隱藏或顯示），工作經歷計畫書 §5.4；
    舊卡面的 ``del``、``edit`` 照樣可用。
    """

    action_type: Literal["person"] = "person"
    verb: Literal[
        "view", "add", "section", "del", "done", "end", "edit", "photo", "topic", "mask", "item", "field"
    ] = Field(
        ..., description="要做什麼"
    )
    field_name: str = Field(..., description="對象欄位（例如 Followups_JSON）；section 時為區塊代號（例如 work）")
    index: str = Field(..., description="序號與指紋 `{序號}_{指紋}`；不適用時為 none")
    card_id: str = Field(..., description="人脈卡的 Card_ID")
