"""人脈卡「用一段話記錄」的 LLM 輸出格式（人脈卡計畫書 §3.13 P5）。

LLM output schema of the person profile "record in one paragraph" flow.

LLM 只提建議；使用者在 TG 上逐項勾選後才寫入，寫入時再經 handler 的模型驗證一次。
"""

from typing import Literal

from pydantic import BaseModel, Field


class IngestInteraction(BaseModel):
    """這次見面或聊天。The meeting or conversation described."""

    date: str = Field(description="YYYY-MM-DD；文中沒提就是今天，「昨天」「上週五」等換算成日期")
    topics: str = Field(description="聊了什麼（簡短，用頓號分隔話題）")
    updates: str = Field("", description="對方的近況：工作、家人、健康、計畫等的變化；沒有就空字串")
    place: str = Field("", description="在哪裡見面或用什麼方式聊；沒提就空字串")
    quote: str = Field("", description="對方說過、值得記住的一句話（原話或接近原話）；沒有就空字串")


class IngestFollowup(BaseModel):
    """一件之後要追蹤的事。One follow-up."""

    item: str = Field(description="要追蹤的事，寫成之後一看就懂的一句話")
    type: Literal["ask", "my_promise", "their_promise"] = Field(
        description="ask：下次見面要問對方的事；my_promise：我答應對方的事；their_promise：對方答應我的事"
    )
    due: str = Field("", description="YYYY-MM-DD；有明確時間點時才填（例如考試日、約定的日期），否則空字串")


class IngestFieldUpdate(BaseModel):
    """建議更新的一個人物資料欄位。One suggested profile field update."""

    field: str = Field(description="欄位名稱，必須是提示中列出的欄位之一")
    value: str = Field(description="要寫入的內容（純文字）")
    mode: Literal["append", "replace"] = Field(
        description="append：接在原本內容後面（興趣、個性等描述）；replace：取代原本的值（公司、職稱、生日等單一值）"
    )
    reason: str = Field(description="依據文中的哪句話（簡短）")


class LLMPersonIngestResult(BaseModel):
    """整理結果。The structured result."""

    interaction: IngestInteraction
    followups: list[IngestFollowup] = Field(default_factory=list)
    field_updates: list[IngestFieldUpdate] = Field(default_factory=list)
