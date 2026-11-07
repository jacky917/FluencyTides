"""建卡草稿 API 的請求與回應模型（``docs/wip/server_drafts_FEAT_2026-11-04.md`` §4）。

Request and response models of the create draft API.
"""

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.notes import SuggestedTag
from app.services.create_session import DraftStatus
from app.services.task_handlers.base import DuplicateDecision


class DraftResult(BaseModel):
    """生成結果：預先填好的建立參數。The generated create parameters."""

    params: dict[str, Any] = Field(description="建立參數的草稿；勾選、修改後送到 POST /handlers/{name}/notes")
    suggested_tags: list[SuggestedTag] = Field(
        default_factory=list, description="LLM 建議的標籤（建卡時放進請求的 tags）；沒有建議時為空"
    )


class DraftError(BaseModel):
    """生成失敗的原因。Why the generation failed."""

    error_code: str = Field(
        description="沿用生成失敗時的錯誤碼，例如 LLM_SERVICE_ERROR；未預期的錯誤為 DRAFT_GENERATION_FAILED"
    )
    message: str


class DraftOut(BaseModel):
    """一份建卡草稿（§4.1）。全站同一時間最多一份。One create draft; at most one site-wide."""

    id: str
    handler: str = Field(description="handler 名稱")
    status: DraftStatus = Field(
        description=(
            "generating 生成中；ready 可預覽、建立、廢棄；failed 生成失敗；creating 從草稿建立中；"
            "form_creating 填表建卡暫時佔用（沒有輸入與生成結果）"
        )
    )
    input: dict[str, Any] | None = Field(description="生成的輸入；form_creating 時為 null")
    result: DraftResult | None = Field(description="生成結果，只有 ready、creating 時有值（form_creating 沒有）")
    state: dict[str, Any] | None = Field(description="網頁的編輯狀態，第一次存檔前為 null")
    error: DraftError | None = Field(description="只有 failed 時有值")
    version: int = Field(description="每次變更（含存編輯狀態）加一；修改、重試、建立時帶上，與目前的不符時回 409")
    created_at: str
    updated_at: str
    generation_started_at: str | None


class StartDraftRequest(BaseModel):
    """``POST /handlers/{name}/drafts``：開始生成。"""

    params: dict[str, Any] = Field(default_factory=dict, description="草稿輸入，依 draft_schema")
    duplicate_decision: DuplicateDecision | None = Field(
        default=None,
        description=(
            "收到 409 DUPLICATE_FOUND（Anki 已有同一張卡）後的決定：overwrite 新卡建好後刪掉舊卡、"
            "allow 允許重複；第一次送出時省略"
        ),
    )


class UpdateDraftRequest(BaseModel):
    """``PATCH /drafts/{id}``：存網頁的編輯狀態。"""

    state: dict[str, Any] = Field(description="網頁的編輯狀態（勾選、修改過的內容），後端不解讀；上限 256 KB")
    version: int = Field(description="讀到的草稿版本")


class RetryDraftRequest(BaseModel):
    """``POST /drafts/{id}/retry``：生成失敗後重試。"""

    version: int = Field(description="讀到的草稿版本")
    params: dict[str, Any] | None = Field(default=None, description="新的草稿輸入；省略時沿用原本的輸入")
    duplicate_decision: DuplicateDecision | None = Field(
        default=None,
        description=(
            "換了輸入、收到 409 DUPLICATE_FOUND 後的決定（同開始生成）；只在帶 params 時有用。省略時，原本的決定"
            "只在查到的還是同一張舊卡時沿用，換了一張就回 409"
        ),
    )
