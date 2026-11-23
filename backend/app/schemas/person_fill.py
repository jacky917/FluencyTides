"""API schemas of the person card "AI 整理" on the web.

網頁人脈卡「AI 整理」的 API 格式。

Plan ``docs/wip/person_llm_fill_FEAT_2026-11-23.md``: a description is organized by the LLM into proposals (the
shapes of ``person_profile_ingest.Proposal``), which the web edits and ticks before writing. The data of each
proposal is checked again when writing (``person_profile_fill.normalize``), and once more by the handler.

計畫書 ``docs/wip/person_llm_fill_FEAT_2026-11-23.md``：一段描述經 LLM 整理成建議（形狀同
``person_profile_ingest.Proposal``），網頁編輯、勾選後寫入。每項建議的內容在寫入時再檢查一次
（``person_profile_fill.normalize``），handler 寫入時還會再驗證一次。
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.drafts import DraftOut

#: The longest description, in characters.
#: 描述的長度上限（字數）。
MAX_FILL_TEXT = 4000

#: The proposal kinds (``person_profile_ingest.Proposal``).
#: 建議的種類（``person_profile_ingest.Proposal``）。
ProposalKind = Literal[
    "display_name",
    "interaction",
    "followup",
    "goal",
    "family",
    "field",
    "history_add",
    "history_end",
    # 使用者在對話框修改卡片原本的內容（不是 LLM 的建議）：文字欄位整段取代、清單的一筆以 edit_item 修改
    "edit_field",
    "edit_item",
]


class StartFillRequest(BaseModel):
    """The input of an "AI 整理" draft: the description, and the card to add to (or the deck for a new card).

    「AI 整理」草稿的輸入：描述，以及要補的卡（或建新卡的牌組）。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=MAX_FILL_TEXT, description="描述：今天聊了什麼、對他多了解了什麼")
    note_id: int | None = Field(None, ge=1, description="要補的人脈卡；省略為建新卡")
    deck: str | None = Field(None, min_length=1, description="建新卡時的牌組（補舊卡時不用）")

    @model_validator(mode="after")
    def _deck_for_new_card(self) -> "StartFillRequest":
        """Require a deck when creating a new card.

        建新卡時一定要有牌組。

        Returns:
            this request
            這個請求

        Raises:
            ValueError: if neither ``note_id`` nor ``deck`` is given
                若 ``note_id`` 與 ``deck`` 都沒有
        """
        if self.note_id is None and not self.deck:
            raise ValueError("建新卡要選牌組")
        return self


class ProposalIn(BaseModel):
    """One proposal as the web sends it back (possibly edited).

    網頁送回的一項建議（可能改過）。
    """

    kind: ProposalKind
    data: dict[str, Any]


class ApplyFillRequest(BaseModel):
    """Write the ticked proposals of an "AI 整理" draft.

    寫入「AI 整理」草稿裡勾選的建議。
    """

    version: int = Field(ge=1, description="草稿版本（同 PATCH /drafts/{id}）")
    proposals: list[ProposalIn] = Field(description="全部建議（編輯後），順序與草稿相同")
    selected: list[int] = Field(description="勾選的建議序號；建新卡時稱呼一定要在裡面")


class ApplyFillResponse(BaseModel):
    """The result of writing.

    寫入的結果。
    """

    done: bool = Field(description="勾選的都寫入了（草稿已結束）")
    note_id: int | None = Field(description="寫入的人脈卡（建新卡時是新卡）；建卡失敗時為 null")
    saved: list[int] = Field(description="已寫入的建議序號（含之前寫入的）")
    errors: dict[str, str] = Field(description="沒寫入的建議與原因：{序號: 原因}")
    sync_warning: str | None = Field(None, description="寫入後同步 AnkiWeb 的提醒")
    draft: DraftOut | None = Field(description="還沒寫完時的草稿；寫完時為 null")
