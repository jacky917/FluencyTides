"""
Speaking_Coach_Dark 相關的 LLM 結構化輸出 Schema。

LLM structured-output schemas for Speaking_Coach_Dark.
"""

from pydantic import BaseModel, Field

class AudioEvaluationResult(BaseModel):
    """Workflow B: LLM 語音評分結果。

    Workflow B: LLM audio evaluation result.

    透過 JSON Schema 強制 LLM 輸出此結構。

    The LLM is forced to output this structure via JSON Schema.

    Attributes:
        score: 總分 (0-100)。Total score (0-100).
        feedback: AI 評語文字。AI feedback text.
        transcript: 語音逐字稿。Speech transcript.
    """

    score: int = Field(ge=0, le=100)
    feedback: str
    transcript: str = ""
