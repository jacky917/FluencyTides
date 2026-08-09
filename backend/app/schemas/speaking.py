"""
Speaking_Coach_Dark 卡片類型專用 Pydantic V2 Schema。

Pydantic V2 schemas dedicated to the Speaking_Coach_Dark card type.

嚴格對齊 Speaking_Coach_Dark_front.html 與 Speaking_Coach_Dark_back.html
中 JavaScript 解析的 JSON 結構，確保前後端（Anki 端與 FastAPI 端）資料一致。

Strictly aligned with the JSON structures parsed by the JavaScript in
Speaking_Coach_Dark_front.html and Speaking_Coach_Dark_back.html, keeping
the Anki side and the FastAPI side consistent.

欄位對應關係（Anki 模型欄位 → JSON 結構）：
- Prompt_Audios  → list[PromptAudioItem]
- Recordings     → list[RecordingItem]
- References     → list[ReferenceItem]

Field mapping (Anki model field → JSON structure):
- Prompt_Audios  → list[PromptAudioItem]
- Recordings     → list[RecordingItem]
- References     → list[ReferenceItem]
"""

from pydantic import BaseModel, Field


class PromptAudioItem(BaseModel):
    """Prompt 區塊的語音頭像項目。

    Voice-avatar item in the Prompt section.

    對應 Speaking_Coach_Dark_front.html 中 scCreateAvatarBtn() 的 item。

    Corresponds to the item consumed by scCreateAvatarBtn() in
    Speaking_Coach_Dark_front.html.

    Attributes:
        audio: 音檔檔名（存於 Anki collection.media 中）。Audio filename
            stored in Anki collection.media.
        speaker: 說話者名稱（顯示為頭像 tooltip）。Speaker name shown as
            the avatar tooltip.
        avatar: 頭像圖片路徑（可選，若為空則顯示 ▶ 圖示）。Avatar image
            path; a ▶ icon is shown when empty.
    """

    audio: str
    speaker: str = ""
    avatar: str = ""


class RecordingItem(BaseModel):
    """使用者歷史錄音項目。

    Historical user recording item.

    對應 Speaking_Coach_Dark_back.html 中 recordings 陣列的單一元素。
    score 決定視覺狀態圓點顏色：≥90 綠色、≥60 橘色、<60 紅色。

    Corresponds to one element of the recordings array in
    Speaking_Coach_Dark_back.html. score drives the status-dot color:
    >=90 green, >=60 orange, <60 red.

    Attributes:
        date: 錄音日期字串（例如 '2026-06-03'）。Recording date string.
        score: AI 評分 (0-100)。AI score (0-100).
        transcript: 使用者語音的逐字稿。Transcript of the user's speech.
        comment: AI 產出的評語回饋。AI-generated feedback comment.
        audio: 錄音檔檔名（存於 Anki collection.media 中）。Recording
            filename stored in Anki collection.media.
    """

    date: str
    score: int = Field(ge=0, le=100)
    transcript: str = ""
    comment: str = ""
    audio: str = ""


class ReferenceAudioItem(BaseModel):
    """參考範本的語音附件。

    Audio attachment of a reference answer.

    Attributes:
        audio: 音檔檔名。Audio filename.
        speaker: 說話者名稱。Speaker name.
        avatar: 頭像圖片路徑（可選）。Optional avatar image path.
    """

    audio: str
    speaker: str = ""
    avatar: str = ""


class ReferenceItem(BaseModel):
    """參考範本回覆項目。

    Reference answer item.

    對應 Speaking_Coach_Dark_back.html 中 references 陣列的單一元素。
    status 決定視覺呈現：1=啟用（綠色圓點）、0=停用（紅色圓點 + 淡化）。

    Corresponds to one element of the references array in
    Speaking_Coach_Dark_back.html. status drives the visuals: 1=active
    (green dot), 0=disabled (red dot + faded).

    Attributes:
        date: 範本建立日期字串。Reference creation date string.
        content: 參考回覆的文字內容。Text content of the reference answer.
        status: 啟用狀態 (0 或 1)。Active status (0 or 1).
        audios: 範本對應的語音附件列表。Audio attachments of the reference.
    """

    date: str
    content: str
    status: int = Field(default=1, ge=0, le=1)
    audios: list[ReferenceAudioItem] = Field(default_factory=list)


class NewCardPayload(BaseModel):
    """Workflow A: /newcard 指令的 JSON Payload。

    Workflow A: JSON payload of the /newcard command.

    來自外部 Gemini 客製化 Agent 嚴格產出的 JSON 結構。

    Strict JSON structure produced by the external customized Gemini agent.

    Attributes:
        deck: 目標牌組名稱（例如 '日文::語言島'）。Target deck name.
        front: 卡片正面 Prompt（對方的發言）。Front-side prompt (the other
            party's utterance).
        back: 卡片正面 Context（文脈、背景）。Front-side context
            (background).
        answers: 參考回覆列表（字串陣列，自動轉為 ReferenceItem）。
            Reference answers (string array, auto-converted to
            ReferenceItem).
    """

    deck: str
    front: str
    back: str = ""
    answers: list[str] = Field(default_factory=list)
