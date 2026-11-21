"""Pydantic V2 schemas dedicated to the Speaking_Coach_Dark card type.

Speaking_Coach_Dark 卡片類型專用 Pydantic V2 Schema。

Strictly aligned with the JSON structures parsed by the JavaScript in
Speaking_Coach_Dark_front.html and Speaking_Coach_Dark_back.html, keeping the
data consistent between the Anki side and the FastAPI side.

嚴格對齊 Speaking_Coach_Dark_front.html 與 Speaking_Coach_Dark_back.html
中 JavaScript 解析的 JSON 結構，確保前後端（Anki 端與 FastAPI 端）資料一致。

Field mapping (Anki model field → JSON structure):
- Prompt_Audios  → list[PromptAudioItem]
- Recordings     → list[RecordingItem]
- References     → list[ReferenceItem]

欄位對應關係（Anki 模型欄位 → JSON 結構）：
- Prompt_Audios  → list[PromptAudioItem]
- Recordings     → list[RecordingItem]
- References     → list[ReferenceItem]
"""

from pydantic import BaseModel, Field


class PromptAudioItem(BaseModel):
    """A voice-avatar item in the Prompt section.

    Prompt 區塊的語音頭像項目。

    Corresponds to the item used by ``scCreateAvatarBtn()`` in
    Speaking_Coach_Dark_front.html.

    對應 Speaking_Coach_Dark_front.html 中 ``scCreateAvatarBtn()`` 的 item。

    Attributes:
        audio: the audio filename, stored in Anki ``collection.media``
            音檔檔名（存於 Anki ``collection.media`` 中）
        speaker: the speaker name, shown as the avatar tooltip
            說話者名稱（顯示為頭像 tooltip）
        avatar: the avatar image path (optional; a ▶ icon is shown when empty)
            頭像圖片路徑（可選，若為空則顯示 ▶ 圖示）
    """

    audio: str
    speaker: str = ""
    avatar: str = ""


class RecordingItem(BaseModel):
    """A historical user recording item.

    使用者歷史錄音項目。

    Corresponds to one element of the ``recordings`` array in
    Speaking_Coach_Dark_back.html. ``score`` decides the color of the status
    dot: ≥90 green, ≥60 orange, <60 red.

    對應 Speaking_Coach_Dark_back.html 中 ``recordings`` 陣列的單一元素。
    ``score`` 決定視覺狀態圓點顏色：≥90 綠色、≥60 橘色、<60 紅色。

    Attributes:
        date: the recording date string, for example ``'2026-06-03'``
            錄音日期字串（例如 ``'2026-06-03'``）
        score: the AI score (0-100)
            AI 評分 (0-100)
        transcript: the transcript of the user's speech
            使用者語音的逐字稿
        comment: the feedback comment produced by the AI
            AI 產出的評語回饋
        audio: the recording filename, stored in Anki ``collection.media``
            錄音檔檔名（存於 Anki ``collection.media`` 中）
    """

    date: str
    score: int = Field(ge=0, le=100)
    transcript: str = ""
    comment: str = ""
    audio: str = ""


class ReferenceAudioItem(BaseModel):
    """An audio attachment of a reference answer.

    參考範本的語音附件。

    Attributes:
        audio: the audio filename
            音檔檔名
        speaker: the speaker name
            說話者名稱
        avatar: the avatar image path (optional)
            頭像圖片路徑（可選）
    """

    audio: str
    speaker: str = ""
    avatar: str = ""


class ReferenceItem(BaseModel):
    """A reference answer item.

    參考範本回覆項目。

    Corresponds to one element of the ``references`` array in
    Speaking_Coach_Dark_back.html. ``status`` decides the visuals: 1 = active
    (green dot), 0 = disabled (red dot and faded).

    對應 Speaking_Coach_Dark_back.html 中 ``references`` 陣列的單一元素。
    ``status`` 決定視覺呈現：1=啟用（綠色圓點）、0=停用（紅色圓點 + 淡化）。

    Attributes:
        date: the date string when the reference was created
            範本建立日期字串
        content: the text content of the reference answer
            參考回覆的文字內容
        status: the active status (0 or 1)
            啟用狀態 (0 或 1)
        audios: the audio attachments of the reference
            範本對應的語音附件列表
    """

    date: str
    content: str
    status: int = Field(default=1, ge=0, le=1)
    audios: list[ReferenceAudioItem] = Field(default_factory=list)


class NewCardPayload(BaseModel):
    """Workflow A: the JSON payload of the ``/newcard`` command.

    Workflow A: ``/newcard`` 指令的 JSON Payload。

    The strict JSON structure produced by the external customized Gemini agent.

    來自外部 Gemini 客製化 Agent 嚴格產出的 JSON 結構。

    Attributes:
        deck: the target deck name, for example ``'日文::語言島'``
            目標牌組名稱（例如 ``'日文::語言島'``）
        front: the front-side prompt of the card (the other party's utterance)
            卡片正面 Prompt（對方的發言）
        back: the front-side context of the card (the background)
            卡片正面 Context（文脈、背景）
        answers: the reference answers (a string array, converted to ``ReferenceItem`` automatically)
            參考回覆列表（字串陣列，自動轉為 ``ReferenceItem``）
    """

    deck: str
    front: str
    back: str = ""
    answers: list[str] = Field(default_factory=list)
