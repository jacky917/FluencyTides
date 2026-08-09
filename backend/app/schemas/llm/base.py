"""
LLM 交互相關 Pydantic V2 Schema 定義模組。

Pydantic V2 schemas for LLM interaction.

本模組定義了 LLM 結構化輸出的內部表示模型，用於
Service 層與 Infrastructure 層之間的資料傳遞。

Defines the internal representation models of LLM structured output, used
for data transfer between the service and infrastructure layers.

這些模型不直接暴露給前端 API，而是作為內部合約確保
LLM 回傳結果經過結構化驗證後才進入後續流程。

These models are not exposed to the frontend API; they act as internal
contracts ensuring LLM results are validated before further processing.

設計決策：
    - LLMGenerateResult 封裝了原始回傳文字與解析結果，
      便於日誌記錄與除錯（符合 llm-structured-output skill §9 的要求）。
    - attempt_count 記錄實際重試次數，便於監控 LLM 穩定度。

Design decisions:
    - LLMGenerateResult wraps both the raw text and the parsed result for
      logging and debugging (per llm-structured-output skill §9).
    - attempt_count records actual retries for monitoring LLM stability.
"""

from pydantic import BaseModel, Field


class LLMGenerateResult(BaseModel):
    """LLM 結構化輸出的結果封裝模型。

    Wrapper model for LLM structured-output results.

    將 LLM 回傳的原始文字、解析後的 JSON 資料、以及重試統計
    整合為一個結構化的 Pydantic 模型，取代裸字典回傳。

    Bundles the LLM's raw text, parsed JSON data, and retry statistics
    into one structured Pydantic model instead of returning a raw dict.

    Attributes:
        raw_content: LLM 回傳的原始文字（含可能的 Markdown 標記）。Raw LLM
            output text (possibly with Markdown markers).
        parsed_data: 解析與清理後的 JSON 字典。Parsed and cleaned JSON
            dict.
        model_name: 實際使用的 LLM 模型名稱。LLM model actually used.
        attempt_count: 達成有效 JSON 輸出所經歷的請求次數。Number of
            requests taken to obtain valid JSON output.
    """

    raw_content: str = Field(
        ...,
        description="LLM 回傳的原始文字（含可能的 Markdown 標記）",
    )
    parsed_data: dict[str, object] = Field(
        ...,
        description="解析與清理後的 JSON 字典",
    )
    model_name: str = Field(
        ...,
        description="實際使用的 LLM 模型名稱",
    )
    attempt_count: int = Field(
        ...,
        ge=1,
        description="達成有效 JSON 輸出所經歷的請求次數",
    )
