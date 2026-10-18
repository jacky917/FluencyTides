"""日文同表層多讀動詞的讀音判讀——請求、回應與 LLM 輸出 schema。

Request / response / LLM-output schemas for judging the reading of a
Japanese verb whose kanji surface has multiple readings.

對應計畫 docs/wip/verb_reading_judgments_FEAT_2026-09-02.md §3.2。
端點與專案無關：請求只含台詞、上下文、表層與候選讀音。
"""

from pydantic import BaseModel, ConfigDict, Field

# 單次請求的句數上限。20 句（含前後各 2 行）約 100 行對話，模型仍能逐句對照；
# 超過 40 句後逐項注意力下降、串位機率上升，故硬性拒收。
# Hard cap per request; see the plan for the rationale.
MAX_ITEMS_PER_REQUEST = 40


class JudgeReadingItem(BaseModel):
    """一句待判的台詞。One line to judge."""

    script_id: int = Field(..., description="台詞 ID（scripts.id）")
    surface: str = Field(..., min_length=1, max_length=32, description="同表層多讀的表層，如 汚す")
    candidates: list[str] = Field(..., min_length=2, description="候選讀音（平假名），至少兩個")
    line: str = Field(..., min_length=1, description="目標台詞原文")
    context_before: list[str] = Field(default_factory=list, description="前文（依時序，最多數行）")
    context_after: list[str] = Field(default_factory=list, description="後文（依時序，最多數行）")


class JudgeReadingsRequest(BaseModel):
    """判讀請求。Judge request.

    模型由用途鍵 ``jp_verb_reading`` 的路由設定決定（TG ``/models`` 或 ``/api/v1/llm-routes``），
    請求不能覆寫。多餘欄位一律拒收（422）：舊版腳本帶 ``model`` / ``effort`` 時會明確失敗，
    不會被默默忽略、讓人以為用了指定的模型。

    The model comes from the ``jp_verb_reading`` route; unknown fields
    (including the removed ``model`` / ``effort`` overrides) are rejected.
    """

    model_config = ConfigDict(extra="forbid")

    items: list[JudgeReadingItem] = Field(..., min_length=1, max_length=MAX_ITEMS_PER_REQUEST)


class ReadingJudgment(BaseModel):
    """單句判定結果。One judgment."""

    script_id: int
    reading: str = Field("", description="判定讀音；無法確定為空字串")


class ReadingJudgmentLLMOutput(BaseModel):
    """LLM 結構化輸出（response_schema）。Structured LLM output."""

    results: list[ReadingJudgment]


class JudgeReadingsResponse(BaseModel):
    """端點回應。Endpoint response."""

    llm_model: str = Field(..., description="實際使用的模型標籤（用途鍵 jp_verb_reading 生效的設定）")
    results: list[ReadingJudgment]
