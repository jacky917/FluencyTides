"""
Speaking_Coach_Dark 卡片類型專用 Pydantic V2 Schema。

嚴格對齊 Speaking_Coach_Dark_front.html 與 Speaking_Coach_Dark_back.html
中 JavaScript 解析的 JSON 結構，確保前後端（Anki 端與 FastAPI 端）資料一致。

欄位對應關係（Anki 模型欄位 → JSON 結構）：
- Prompt_Audios  → list[PromptAudioItem]
- Recordings     → list[RecordingItem]
- References     → list[ReferenceItem]
"""

from pydantic import BaseModel, Field


class PromptAudioItem(BaseModel):
    """Prompt 區塊的語音頭像項目。

    對應 Speaking_Coach_Dark_front.html 中 scCreateAvatarBtn() 的 item。

    Attributes:
        audio: 音檔檔名（存於 Anki collection.media 中）。
        speaker: 說話者名稱（顯示為頭像 tooltip）。
        avatar: 頭像圖片路徑（可選，若為空則顯示 ▶ 圖示）。
    """

    audio: str
    speaker: str = ""
    avatar: str = ""


class RecordingItem(BaseModel):
    """使用者歷史錄音項目。

    對應 Speaking_Coach_Dark_back.html 中 recordings 陣列的單一元素。
    score 決定視覺狀態圓點顏色：≥90 綠色、≥60 橘色、<60 紅色。

    Attributes:
        date: 錄音日期字串（例如 '2026-06-03'）。
        score: AI 評分 (0-100)。
        transcript: 使用者語音的逐字稿。
        comment: AI 產出的評語回饋。
        audio: 錄音檔檔名（存於 Anki collection.media 中）。
    """

    date: str
    score: int = Field(ge=0, le=100)
    transcript: str = ""
    comment: str = ""
    audio: str = ""


class ReferenceAudioItem(BaseModel):
    """參考範本的語音附件。

    Attributes:
        audio: 音檔檔名。
        speaker: 說話者名稱。
        avatar: 頭像圖片路徑（可選）。
    """

    audio: str
    speaker: str = ""
    avatar: str = ""


class ReferenceItem(BaseModel):
    """參考範本回覆項目。

    對應 Speaking_Coach_Dark_back.html 中 references 陣列的單一元素。
    status 決定視覺呈現：1=啟用（綠色圓點）、0=停用（紅色圓點 + 淡化）。

    Attributes:
        date: 範本建立日期字串。
        content: 參考回覆的文字內容。
        status: 啟用狀態 (0 或 1)。
        audios: 範本對應的語音附件列表。
    """

    date: str
    content: str
    status: int = Field(default=1, ge=0, le=1)
    audios: list[ReferenceAudioItem] = Field(default_factory=list)


class NewCardPayload(BaseModel):
    """Workflow A: /newcard 指令的 JSON Payload。

    來自外部 Gemini 客製化 Agent 嚴格產出的 JSON 結構。

    Attributes:
        deck: 目標牌組名稱（例如 '日文::語言島'）。
        front: 卡片正面 Prompt（對方的發言）。
        back: 卡片背面 Context（文脈、中譯）。
        answers: 參考回覆列表（字串陣列，自動轉為 ReferenceItem）。
    """

    deck: str
    front: str
    back: str = ""
    answers: list[str] = Field(default_factory=list)


class AudioEvaluationResult(BaseModel):
    """Workflow B: LLM 語音評分結果。

    透過 JSON Schema 強制 LLM 輸出此結構。

    status_code 語意：
    - 0: 語意吻合範例，分數 > 80（綠色）
    - 1: 接近範例或無範例但有瑕疵，分數 < 80（橘色）
    - 2: 無匹配範例但整體自然，分數 > 80（藍色）

    Attributes:
        score: 總分 (0-100)。
        status_code: 評分狀態碼 (0/1/2)。
        feedback: AI 評語文字。
        transcript: 語音逐字稿。
    """

    score: int = Field(ge=0, le=100)
    status_code: int = Field(ge=0, le=2)
    feedback: str
    transcript: str = ""
