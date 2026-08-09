"""JP_CoreVerb（核心動詞）卡片的 LLM 結構化輸出 Schema。

LLM structured-output schemas for the JP_CoreVerb (core-verb) cards.

與 ``jp_verb_pair.py`` 高度相似，但 LLM 任務由「自他判定」
改為「深度動詞解析」（``VerbAnalysis`` 五欄）：
變化過程、自他分類、搭配、語感、口語自然度。

Context 段直接共用 ``VerbPairContextResult``（模型定義共用、卡片實體不共用）。

Very similar to ``jp_verb_pair.py``, but the LLM task changes from
transitivity judgment to deep verb analysis (the five ``VerbAnalysis``
fields): conjugation chain, transitivity, collocation, nuance, and
colloquial naturalness. The Context part reuses ``VerbPairContextResult``
(shared model definition, not shared card instances).
"""

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.llm.jp_verb_pair import VerbPairContextResult


class VerbAnalysis(BaseModel):
    """單一例句中核心動詞的深度解析結果（五欄必填）。

    Deep analysis of the core verb in one example sentence (all five
    fields required).
    """

    conjugation_chain: str = Field(
        description=(
            "動詞變化過程：從字典形到句中實際形態的逐步推導，"
            "例如「掛ける → 掛けて（て形）→ 掛けている（進行/狀態）」。"
            "簡單時態時允許一步（如「見る → 見た（た形）」）。"
        )
    )
    transitivity: Literal["自動詞", "他動詞", "自他兩用", "補助動詞用法"] = Field(
        description=(
            "本句中該動詞的自他分類。"
            "接在 V-て 之後作補助動詞（如 てみる、てかける類）時，"
            "請選「補助動詞用法」，不要硬塞自/他分類。"
        )
    )
    collocation: str = Field(
        description=(
            "本句的動詞搭配：助詞＋名詞的慣用組合，"
            "例如「迷惑を掛ける（に＋人 搭配使用）」。"
        )
    )
    nuance: str = Field(
        description=(
            "語感說明（繁體中文）：語域（書面/口語/商務）、情感色彩、"
            "與近義說法的對比，例如「帶歉意的客套語感，比『困らせる』更委婉」。"
        )
    )
    colloquial_score: int = Field(
        ge=0,
        le=99,
        description=(
            "口語自然度（整數 0-99）：99=極自然的日常口語、0=書面語/舞台腔。"
            "讓學習者判斷這句台詞「能不能直接拿來用」。"
        ),
    )


class CoreVerbClozeResult(BaseModel):
    """核心動詞例句克漏字子卡片 (Cloze) 的生成結果。

    Generation result of the core-verb cloze sub-card.

    LLM 負責「決定挖空哪裡」(回傳要挖空的子字串清單)，
    Python 負責「精準執行挖空」(在原文中定位並替換)，
    徹底消除 LLM 改動原文的風險。

    The LLM decides where to blank (returns the substrings to remove) while
    Python performs the blanking precisely in the original text,
    eliminating any risk of the LLM altering the sentence.

    當助詞與動詞相鄰時 (如 'を見る')，cloze_blanks 只有一個元素。
    當助詞與動詞被副詞等隔開時 (如 'をじっと見る')，
    cloze_blanks 會有兩個元素 ['を', '見る']，產生兩個空格。

    When particle and verb are adjacent, cloze_blanks has one element; when
    separated (e.g. by an adverb), it has two, producing two blanks.
    """

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
    verb_analysis: VerbAnalysis = Field(
        description="目標動詞在本句中的深度解析（五欄必填）"
    )


class CoreVerbCardGenerationResult(BaseModel):
    """完整的核心動詞子卡片生成結果 (不包含多媒體)。

    Complete core-verb sub-card generation result (media excluded).
    """

    context: VerbPairContextResult
    cloze: CoreVerbClozeResult
