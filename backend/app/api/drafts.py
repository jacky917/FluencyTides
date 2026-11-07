"""
建卡草稿 REST API（``docs/wip/server_drafts_FEAT_2026-11-04.md`` §4）。

Create draft REST API.

- ``GET    /drafts/current``      目前的草稿（全站最多一份），沒有時為 null
- ``PATCH  /drafts/{id}``         存網頁的編輯狀態（帶版本）
- ``POST   /drafts/{id}/retry``   生成失敗後重試（202）
- ``DELETE /drafts/{id}``         廢棄（只限可建立、生成失敗的草稿）

開始生成在 ``POST /handlers/{name}/drafts``、從草稿建卡在 ``POST /handlers/{name}/notes``（``app/api/handlers.py``）。
狀態不允許的操作回 409，回應附上目前的草稿 ``draft``；``{id}`` 不是目前那份時回 404 ``DRAFT_NOT_FOUND``。

Starting a draft and creating from it live in ``app/api/handlers.py``.
"""

from fastapi import APIRouter, Depends, Response, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.api.handlers import _params_errors
from app.core.auth import verify_api_key
from app.core.dependencies import get_note_service
from app.schemas.card import ErrorResponse
from app.schemas.drafts import DraftOut, RetryDraftRequest, UpdateDraftRequest
from app.services.create_session import CreateSession, get_create_session
from app.services.note_service import NoteService

router = APIRouter(
    prefix="/drafts",
    tags=["Drafts"],
    dependencies=[Depends(verify_api_key)],
)

_GONE = {404: {"model": ErrorResponse, "description": "這份草稿已不在（已建立、已廢棄，或伺服器重新啟動過）"}}
_STATE = "草稿狀態不允許，或版本不是最新的；回應附上目前的草稿 draft"


@router.get(
    "/current",
    response_model=DraftOut | None,
    summary="目前的建卡草稿",
    description="全站同一時間最多一份；沒有時回 null。生成中、建立中時網頁輪詢這個端點。",
)
async def current_draft(session: CreateSession = Depends(get_create_session)) -> dict[str, object] | None:
    """目前的草稿。The current draft."""
    return session.snapshot()


@router.patch(
    "/{draft_id}",
    response_model=DraftOut,
    summary="存網頁的編輯狀態",
    description="只在可建立（ready）時接受；版本不符時回 409 DRAFT_VERSION_CONFLICT 與最新的草稿。",
    responses={
        **_GONE,
        409: {"model": ErrorResponse, "description": _STATE},
        413: {"model": ErrorResponse, "description": "編輯狀態超過 256 KB"},
    },
)
async def update_draft(
    draft_id: str,
    body: UpdateDraftRequest,
    session: CreateSession = Depends(get_create_session),
) -> dict[str, object]:
    """存編輯狀態。Save the editing state."""
    return await session.save_state(draft_id, body.state, body.version)


@router.post(
    "/{draft_id}/retry",
    response_model=DraftOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="生成失敗後重試",
    description="沿用原本的輸入；帶 params 就換成新的輸入。立刻回 202，LLM 在背景生成。",
    responses={
        **_GONE,
        409: {"model": ErrorResponse, "description": _STATE},
        422: {"description": "新的輸入驗證失敗（detail）"},
    },
)
async def retry_draft(
    draft_id: str,
    body: RetryDraftRequest,
    note_service: NoteService = Depends(get_note_service),
    session: CreateSession = Depends(get_create_session),
) -> dict[str, object]:
    """重試。Retry a failed draft.

    先在鎖外讀一次草稿只是為了取得 handler 名稱來驗證新的輸入；狀態與版本在 ``session.retry`` 的鎖內才檢查。

    Raises:
        DraftNotFoundError: ``{id}`` 不是目前的草稿。Not the current draft.
        DraftNotFailedError: 不是生成失敗的草稿。Not failed.
        DraftVersionConflictError: 版本不是最新的。Stale version.
        RequestValidationError: 新的輸入驗證失敗。On invalid new input.
    """
    handler_name = session.get(draft_id).handler_name
    parameters = None
    if body.params is not None:
        try:
            parameters = note_service.validate_draft_input(handler_name, body.params)
        except ValidationError as exc:
            raise RequestValidationError(_params_errors(exc)) from exc
    return await session.retry(
        draft_id, body.version, lambda p: note_service.generate_draft(handler_name, p), parameters
    )


@router.delete(
    "/{draft_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="廢棄草稿",
    description="只限可建立（ready）與生成失敗（failed）；生成中、建立中回 409 DRAFT_BUSY。",
    responses={**_GONE, 409: {"model": ErrorResponse, "description": _STATE}},
)
async def discard_draft(draft_id: str, session: CreateSession = Depends(get_create_session)) -> Response:
    """廢棄。Discard a draft."""
    await session.discard(draft_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
