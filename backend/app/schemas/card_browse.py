"""Response models for the card browsing API.

卡片瀏覽 API 的回應模型（網頁版計畫 web_ui_parity_FEAT_2026-10-18.md 階段 A）。

Part of phase A of the web UI plan, web_ui_parity_FEAT_2026-10-18.md.

屬於網頁版計畫 web_ui_parity_FEAT_2026-10-18.md 的階段 A。
"""

from pydantic import BaseModel


class CardSummary(BaseModel):
    """A card in the list.

    清單中的一張卡。

    Attributes:
        card_id: the Anki card ID (its creation timestamp, so larger means newer)
            Anki 卡片 ID（建立時間戳，越大越新）
        note_id: the ID of the owning note
            所屬筆記 ID
        deck_name: the full name of the deck the card is in (``::``-separated levels)
            所在牌組的完整名稱（``::`` 分層）
        model_name: the note type
            筆記類型
        ord: the card template ordinal, counting from 0
            卡片模板序號（0 起算）
        fields: field name mapped to raw value, in the note type's field order
            欄位名 → 原始值，依筆記類型的欄位順序
    """

    card_id: int
    note_id: int
    deck_name: str
    model_name: str
    ord: int
    fields: dict[str, str]


class CardPage(BaseModel):
    """A page of cards.

    一頁卡片。

    Attributes:
        cards: the cards on this page, newest first
            本頁的卡片（新到舊）
        total: the total number of matching cards
            符合條件的卡片總數
        page: the page number, counting from 1
            頁碼（1 起算）
        page_size: the number of cards per page
            每頁張數
    """

    cards: list[CardSummary]
    total: int
    page: int
    page_size: int


class CardView(CardSummary):
    """A single card with the faces rendered by Anki.

    單一卡片與 Anki 渲染好的卡面。

    Attributes:
        question: the front HTML (includes ``<style>`` and the template scripts, rendered by Anki; play markers
            are already replaced by play buttons)
            正面 HTML（含 ``<style>`` 與模板腳本，由 Anki 渲染；播放標記已換成播放鈕）
        answer: the back HTML (includes the front when the template uses ``{{FrontSide}}``)
            背面 HTML（模板用 ``{{FrontSide}}`` 時含正面）
    """

    question: str
    answer: str
