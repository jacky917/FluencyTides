"""自他動詞對卡片 LLM Schema 的範例版本。

Example version of the verb-pair LLM schemas.
"""

from typing import Literal
from pydantic import BaseModel, Field

class VerbPairDialogTurn(BaseModel):
    """LINE 風格上下文對話的單一句子。

    Single utterance in a LINE-style context dialog.
    """
    speaker: str = Field(description="說話者名稱 (如 A, B 或特定人物)")
    avatar: str = Field(description="說話者頭像 URL，若無則填 'none'")
    audio: str = Field(description="此句對話的語音檔名，若無則填空字串")
    text: str = Field(description="對話內容 (日文)")
    align: str = Field(description="氣泡對齊方向，'left' 或 'right'")
    is_target: bool = Field(description="是否為包含目標動詞的關鍵句")

class VerbPairContextResult(BaseModel):
    """上下文子卡片 (Context) 的生成結果。

    Generation result of the Context sub-card.
    """
    summary: str = Field(description="情境概要與動詞用法的簡短解說 (中文)")
    dialog: list[VerbPairDialogTurn] = Field(description="LINE 風格的對話陣列")

class VerbPairClozeResult(BaseModel):
    """例句克漏字子卡片 (Cloze) 的生成結果。

    Generation result of the example-sentence cloze sub-card.
    """
    verb_type_used: Literal["intransitive", "transitive"] = Field(description="使用的動詞類型，必須是 'intransitive' 或 'transitive'")
    speaker: str = Field(description="說話者名稱 (需與對話上下文中的人物一致)")
    avatar: str = Field(description="說話者頭像 URL，若無則填 'none'")
    cloze_sentence: str = Field(description="挖空後的句子，目標搭配詞請用 ____ 替換 (日文)")
    full_sentence: str = Field(description="完整的目標句子 (日文)")
    translation: str = Field(description="整句的中文翻譯")
    target_particle_verb: str = Field(description="被挖空的目標搭配詞 (強相關助詞+動詞，例如 'ドアを開ける')")

class VerbPairGenerationResult(BaseModel):
    """完整的自動詞/他動詞對生成結果。

    Complete intransitive/transitive verb-pair generation result.
    """
    context: VerbPairContextResult
    cloze: VerbPairClozeResult
