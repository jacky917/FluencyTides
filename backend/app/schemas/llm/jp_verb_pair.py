"""JP_VerbPair（自他動詞對）卡片的 LLM 結構化輸出 Schema。

LLM structured-output schemas for JP_VerbPair (verb-pair) cards.
"""

from typing import Literal
from pydantic import BaseModel, Field

class VerbPairDialogTurn(BaseModel):
    """LINE 風格上下文對話的單一句子。

    Single utterance in a LINE-style context dialog.
    """
    speaker: str = Field(description="說話者名稱 (如 A, B 或特定人物)")
    avatar: str = Field(description="說話者頭像名稱，若無則填 'none'")
    text: str = Field(description="對話內容 (日文)")
    align: str = Field(description="氣泡對齊方向，'left' 或 'right'")
    is_target: bool = Field(description="是否為包含目標動詞的關鍵句")

class DialogTranslation(BaseModel):
    """單句對話的翻譯結果。

    Translation result of a single dialog line.
    """
    id: int = Field(description="原始對話的索引編號 (0-based)")
    translation: str = Field(description="該句的純台詞繁體中文翻譯 (絕對不要包含說話者名稱與冒號)")

class VerbPairContextResult(BaseModel):
    """上下文子卡片 (Context) 的生成結果。

    Generation result of the Context sub-card.
    """
    summary: str = Field(description="情境概要與動詞用法的簡短解說 (繁體中文)")
    dialog_translations: list[DialogTranslation] = Field(description="每一句話的繁體中文翻譯陣列，請確保與原文的索引編號一一對應")

class VerbPairClozeResult(BaseModel):
    """例句克漏字子卡片 (Cloze) 的生成結果。

    Generation result of the example-sentence cloze sub-card.

    LLM 負責「決定挖空哪裡」(回傳要挖空的子字串清單)，
    Python 負責「精準執行挖空」(在原文中定位並替換)，
    徹底消除 LLM 改動原文的風險。

    The LLM decides where to blank (returns the substrings to remove) while
    Python performs the blanking precisely in the original text,
    eliminating any risk of the LLM altering the sentence.

    當助詞與動詞相鄰時 (如 'を開ける')，cloze_blanks 只有一個元素。
    當助詞與動詞被副詞等隔開時 (如 'をゆっくり開ける')，
    cloze_blanks 會有兩個元素 ['を', '開ける']，產生兩個空格。

    When particle and verb are adjacent, cloze_blanks has one element; when
    separated (e.g. by an adverb), it has two, producing two blanks.
    """
    verb_type_used: Literal["intransitive", "transitive"] = Field(
        description="使用的動詞類型，必須是 'intransitive' 或 'transitive'"
    )
    cloze_blanks: list[str] = Field(
        description=(
            "要從【目標句子】中挖空的子字串清單。"
            "每個元素必須是目標句子中一字不差的連續子字串。"
            "若助詞與動詞相鄰，回傳一個元素即可 (例如 ['を刺す'])。"
            "若助詞與動詞被其他詞隔開，請分別列出 (例如 ['を', '開ける'])。"
        )
    )
    target_particle_verb: str = Field(
        description=(
            "完整的目標搭配詞 (助詞+動詞)，用於顯示在卡片背面的解答區。"
            "例如 'を刺す' 或 'を開ける'。即使助詞與動詞在原文中被隔開，"
            "此欄位仍應寫成連在一起的形式。"
        )
    )
    translation: str = Field(description="目標句子的繁體中文翻譯")
    conjugation_explanation: str = Field(
        description="動詞變化補充說明。若包含被動、使役、複合動詞等會影響助詞或自他性質的變化，請提供說明；若僅為單純時態變化(如出た,出ます)，請填空字串",
        default=""
    )

class ChildCardGenerationResult(BaseModel):
    """完整的自動詞/他動詞對生成結果 (不包含多媒體)。

    Complete intransitive/transitive verb-pair generation result (media
    excluded).
    """
    context: VerbPairContextResult
    cloze: VerbPairClozeResult
