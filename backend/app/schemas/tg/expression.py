"""Telegram 表達糾錯請求 Schema。

Telegram expression-correction request schemas.
"""

from pydantic import BaseModel, Field

class TGExpressionCorrectionRequest(BaseModel):
    """TG 傳送過來的糾錯請求資料。

    Correction request data sent from Telegram.
    """
    native_language: str = Field(description="使用者的母語，例如：zh, 中文")
    target_language: str = Field(description="欲學習的目標語言，例如：ja, 日文")
    original_text: str = Field(description="使用者輸入的錯誤原文")
    context: str = Field(default="", description="這個句子的發生情境或上下文")
    source_tag: str = Field(default="", description="來源情境標籤（例如：仕事, GRAVITY）")
    user_grammar_correction: str = Field(default="", description="使用者自行提供的文法修正版（選填）")
    user_reorganization: str = Field(default="", description="使用者自行提供的重新組織高階版（選填）")
