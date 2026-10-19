"""卡片瀏覽 API 的回應模型（網頁版計畫 web_ui_parity_FEAT_2026-10-18.md 階段 A）。

Response models for the card browsing API.
"""

from pydantic import BaseModel


class CardSummary(BaseModel):
    """清單中的一張卡。A card in the list.

    Attributes:
        card_id: Anki 卡片 ID（建立時間戳，越大越新）。Card ID.
        note_id: 所屬筆記 ID。Note ID.
        deck_name: 所在牌組的完整名稱（``::`` 分層）。Full deck name.
        model_name: 筆記類型。Note type.
        ord: 卡片模板序號（0 起算）。Template ordinal.
        fields: 欄位名 → 原始值，依筆記類型的欄位順序。Field values in order.
    """

    card_id: int
    note_id: int
    deck_name: str
    model_name: str
    ord: int
    fields: dict[str, str]


class CardPage(BaseModel):
    """一頁卡片。A page of cards.

    Attributes:
        cards: 本頁的卡片（新到舊）。The cards, newest first.
        total: 符合條件的卡片總數。Total matching cards.
        page: 頁碼（1 起算）。Page number.
        page_size: 每頁張數。Page size.
    """

    cards: list[CardSummary]
    total: int
    page: int
    page_size: int


class CardView(CardSummary):
    """單一卡片與 Anki 渲染好的卡面。A card with its rendered faces.

    Attributes:
        question: 正面 HTML（含 ``<style>`` 與模板腳本，由 Anki 渲染；播放標記已換成播放鈕）。Front HTML.
        answer: 背面 HTML（模板用 ``{{FrontSide}}`` 時含正面）。Back HTML.
    """

    question: str
    answer: str
