"""LLM output schema of the trilingual speaking card web-create draft.

三語對話卡「網頁建卡」的 LLM 輸出格式（計畫書 ``docs/archive/trilingual_web_create_FEAT_2026-10-30.md`` §4.1）。

See the plan ``docs/archive/trilingual_web_create_FEAT_2026-10-30.md`` §4.1.
The field order of each saying is the generation order: first the sentence a
native speaker would really say, then the note, and only then the familiarity,
judged from that sentence itself. The LLM only proposes a draft; cards are
created only after the user ticks items on the web page, and at creation the
handler's create parameters are validated once more.

每句的欄位順序就是生成順序：先寫出母語者真的會說的句子，再寫說明，最後才依這句話本身評熟識度。
LLM 只提草稿；使用者在網頁上勾選後才建卡，建卡時再經 handler 的建立參數驗證一次。
"""

from pydantic import BaseModel, Field


class GeneratedSaying(BaseModel):
    """一種自然的說法。One natural way to say it."""

    content: str = Field(description="母語者在這個場景真的會說出口的一句話（口語）")
    note: str = Field(description="繁體中文一兩句：用在什麼對象與場合、語氣的關鍵，或對場景的補充；不要翻譯句子")
    familiarity: int = Field(
        ge=0, le=9, description="熟識度 0–9：依上面這句話本身判斷它會對什麼關係的人說（0＝最正式，9＝最親密）"
    )


class LLMTrilingualDraft(BaseModel):
    """一張三語對話卡的草稿。The draft of one card."""

    prompt: str = Field(description="卡片正面的情境（繁體中文，對學習者說「你……」）")
    title: str = Field(
        default="",
        description=(
            "這個情境的短標題（日文，大約 2–15 字的名詞片語，例如「残業を断る」）；也是子牌組名，不能含 :: 或引號"
        ),
    )
    context: str = Field(description="其他值得記下的背景（繁體中文）；沒有就空字串")
    zh: list[GeneratedSaying] = Field(description="中文（台灣口語，繁體字）的說法")
    ja: list[GeneratedSaying] = Field(description="日文的說法")
    en: list[GeneratedSaying] = Field(description="英文的說法")
    tags: list[str] = Field(
        default_factory=list,
        description="建議的主題標籤 0–3 個：優先照原寫法沿用提示中列出的既有標籤；不能有空白",
    )
