"""日文分詞詞典查詢端點的回應模型。

Response model for the Japanese tokenizer dictionary endpoint.
"""

from pydantic import BaseModel, Field


class TokenizerDictionaryResponse(BaseModel):
    """本服務行程實際載入的分詞詞典。

    The tokenizer dictionary actually loaded by this service process.
    """

    kind: str = Field(..., description="unidic（完整版）或 unidic-lite（回退）")
    version: str = Field(..., description="詞典自帶的版本字串")
    dicdir: str = Field(..., description="詞典目錄的絕對路徑")
    is_preferred: bool = Field(
        ..., description="是否為偏好的完整版；false 代表跑在回退詞典上"
    )
