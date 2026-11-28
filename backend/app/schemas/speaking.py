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

from typing import Literal

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


#: The scoring strategies a recording can record (the same as ``capabilities.STRATEGIES``).
#: 錄音紀錄可記的評分策略（與 ``capabilities.STRATEGIES`` 相同）。
RecordingStrategy = Literal["listen", "stt_llm", "stt_diff"]


class RecordingEvaluationInfo(BaseModel):
    """How a recording was scored, without the time (built before the audio is uploaded).

    錄音的評分方式，不含時間（在上傳音檔之前就組好）。

    ``voice.py`` validates these fields with this model when it obtains the evaluator, so a mismatch stops before
    the audio is uploaded instead of after scoring; :class:`RecordingItem` declares the same fields for the stored
    format.

    ``voice.py`` 取得評分器時就以這個模型檢查這些欄位：不符時在上傳音檔之前就停下，不會評完分才失敗；
    ``RecordingItem`` 以同樣的欄位定義存進卡片的格式。

    Attributes:
        strategy: the scoring strategy
            評分策略
        models: capability → the model used (see :class:`RecordingItem`)
            能力 → 用到的模型（見 ``RecordingItem``）
        settings: capability → the settings layer that took effect (see :class:`RecordingItem`)
            能力 → 生效的設定層（見 ``RecordingItem``）
    """

    strategy: RecordingStrategy
    models: dict[str, str]
    settings: dict[str, str]


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
        evaluated_at: when scoring finished, UTC ISO 8601 such as ``'2026-11-26T06:03:12Z'``; absent on recordings
            saved before this was recorded
            評分完成的時間，UTC、ISO 8601（例如 ``'2026-11-26T06:03:12Z'``）；開始記錄之前存的錄音沒有
        strategy: the scoring strategy (``listen`` / ``stt_llm`` / ``stt_diff``)
            評分策略（``listen`` / ``stt_llm`` / ``stt_diff``）
        models: capability → the model used, written ``(endpoint)model`` (only the endpoint when the capability has
            no model); a display string, not meant to be parsed back
            能力 → 用到的模型，寫成 ``(端點)模型``（能力沒有模型時只寫端點）；給人看的字串，不保證能拆回去
        settings: ``audio_eval`` and each capability the strategy used → the settings layer that took effect (the
            route key, ``*voice``, or ``.env``; ``（全局生效）`` is appended while the voice global's switch was on)
            ``audio_eval`` 與策略用到的各能力 → 生效的設定層（用途鍵、``*voice`` 或 ``.env``；語音全域的開關開著時
            另加「（全局生效）」）

    The four evaluation fields are written together on new recordings (``voice.py`` builds every new recording
    through this model) and are all absent on older ones.

    四個評分欄位在新錄音上一起寫入（``voice.py`` 一律經這個模型產生新錄音），舊錄音全都沒有。
    """

    date: str
    score: int = Field(ge=0, le=100)
    transcript: str = ""
    comment: str = ""
    audio: str = ""
    evaluated_at: str | None = None
    strategy: RecordingStrategy | None = None
    models: dict[str, str] = Field(default_factory=dict)
    settings: dict[str, str] = Field(default_factory=dict)


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
