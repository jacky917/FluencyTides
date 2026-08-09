"""表達糾錯相關的 LLM 結構化輸出 Schema。

LLM structured-output schemas for expression correction.
"""

from pydantic import BaseModel, Field

class LLMMicroPoint(BaseModel):
    """LLM 萃取出的原子化語句知識點。

    Atomic language knowledge point extracted by the LLM.
    """
    original_phrase: str = Field(description="對應的錯誤原文片段（若無對應或為全新補充，請填空字串）。")
    target_phrase: str = Field(description="正確的單字、片語或語法點。")
    native_translation: str = Field(description="包含該知識點的『完整句子的母語翻譯』，提供完整的語意脈絡，不要只翻譯單字片段。")
    context_hint: str = Field(description="詳細的母語情境提示，提供更完整的上下文背景（例如：跟朋友分享昨天的休閒活動時...），幫助回憶。")
    context_sentence: str = Field(description="包含該單字/片語的完整目標語言句子。")
    error_hint: str = Field(description="針對此知識點的詳細解析（為什麼原本那樣說不自然、文法錯在哪，或是重組版本為什麼更好）。")

class LLMExpressionCorrectionResult(BaseModel):
    """LLM 產出的完整糾錯結果與解析。

    Complete correction result and analysis produced by the LLM.
    """
    error_comparison: str = Field(description="標示出原文中錯誤的部分。重現使用者的原文，並將其中不自然或錯誤的片段用 HTML 標籤 <u> 與 </u> 包裹起來，以便在畫面上標示紅底線（請以 grammar_correction 為對比基準）。請在輸出時適當加入換行符號（\\n）以利閱讀。")
    grammar_correction: str = Field(description="維持原意與架構的文法修正版。請在輸出時適當加入換行符號（\\n）以利閱讀。")
    reorganized_expression: str = Field(description="完全打破原本句構限制的重新組織版（母語人士高階說法）。請在輸出時適當加入換行符號（\\n）以利閱讀。")
    grammar_micro_points: list[LLMMicroPoint] = Field(description="從文法修正中萃取出的微型知識點列表。")
    reorganized_micro_points: list[LLMMicroPoint] = Field(description="從重新組織表達中萃取出的微型知識點列表。")
