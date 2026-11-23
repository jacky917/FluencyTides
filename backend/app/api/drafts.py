"""Card creation draft REST API.

建卡草稿 REST API。

Plan ``docs/wip/server_drafts_FEAT_2026-11-04.md`` §4.

- ``GET    /drafts/current``: the current draft (at most one site-wide), or null if there is none
- ``PATCH  /drafts/{id}``: save the web page's editing state (with a version)
- ``POST   /drafts/{id}/retry``: retry after generation failed (202)
- ``POST   /drafts/{id}/apply``: write the ticked proposals of an "AI 整理" draft (person_llm_fill plan)
- ``DELETE /drafts/{id}``: discard (only drafts that are ready to create or whose generation failed)

Starting generation lives in ``POST /handlers/{name}/drafts`` and creating cards from a draft in
``POST /handlers/{name}/notes`` (``app/api/handlers.py``). Operations the state does not allow
return 409 with the current draft attached as ``draft``; when ``{id}`` is not the current draft, the
response is 404 ``DRAFT_NOT_FOUND``.

計畫 ``docs/wip/server_drafts_FEAT_2026-11-04.md`` §4。

- ``GET    /drafts/current``      目前的草稿（全站最多一份），沒有時為 null
- ``PATCH  /drafts/{id}``         存網頁的編輯狀態（帶版本）
- ``POST   /drafts/{id}/retry``   生成失敗後重試（202）
- ``POST   /drafts/{id}/apply``   寫入「AI 整理」草稿裡勾選的建議（person_llm_fill 計畫書）
- ``DELETE /drafts/{id}``         廢棄（只限可建立、生成失敗的草稿）

開始生成在 ``POST /handlers/{name}/drafts``、從草稿建卡在 ``POST /handlers/{name}/notes``（``app/api/handlers.py``）。
狀態不允許的操作回 409，回應附上目前的草稿 ``draft``；``{id}`` 不是目前那份時回 404 ``DRAFT_NOT_FOUND``。
"""

from fastapi import APIRouter, Depends, Response, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.api.handlers import _params_errors
from app.core.auth import verify_api_key
from app.core.exceptions import HandlerOperationUnsupportedError
from app.core.dependencies import get_note_service
from app.schemas.card import ErrorResponse
from app.schemas.drafts import DraftOut, RetryDraftRequest, UpdateDraftRequest
from app.schemas.person_fill import ApplyFillRequest, ApplyFillResponse, StartFillRequest
from app.services import person_profile_fill
from app.services.create_session import CreateSession, get_create_session
from app.services.note_service import NoteService
from app.services.task_handlers.base import DuplicateResolution

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
    """Return the current draft.

    回傳目前的草稿。

    Args:
        session: the site-wide create session
            全站共用的建卡工作階段

    Returns:
        the current draft, or ``None`` if there is none
        目前的草稿；沒有時為 ``None``
    """
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
    """Save the web page's editing state.

    存網頁的編輯狀態。

    Args:
        draft_id: the draft's ID
            草稿 ID
        body: the editing state and the version it was based on
            編輯狀態與它所根據的版本
        session: the site-wide create session
            全站共用的建卡工作階段

    Returns:
        the draft after saving
        儲存後的草稿

    Raises:
        DraftBusyError: if the draft is not ready
            若草稿不是可建立（ready）狀態
        DraftNotFoundError: if ``draft_id`` is not the current draft
            若 ``draft_id`` 不是目前的草稿
        DraftStateTooLargeError: if the editing state exceeds the size limit
            若編輯狀態超過大小上限
        DraftVersionConflictError: if the version is not the latest
            若版本不是最新的
    """
    return await session.save_state(draft_id, body.state, body.version)


@router.post(
    "/{draft_id}/retry",
    response_model=DraftOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="生成失敗後重試",
    description="沿用原本的輸入；帶 params 就換成新的輸入。立刻回 202，LLM 在背景生成。",
    responses={
        **_GONE,
        409: {"model": ErrorResponse, "description": f"{_STATE}；換了輸入時 Anki 已有同一張卡（DUPLICATE_FOUND，附舊卡 duplicate）"},
        422: {"description": "新的輸入驗證失敗（detail）"},
    },
)
async def retry_draft(
    draft_id: str,
    body: RetryDraftRequest,
    note_service: NoteService = Depends(get_note_service),
    session: CreateSession = Depends(get_create_session),
) -> dict[str, object]:
    """Retry a draft whose generation failed.

    重試生成失敗的草稿。

    The draft is read once outside the lock only to get the handler name for validating the new
    input; the state and version are checked inside the lock of ``session.retry``.

    先在鎖外讀一次草稿只是為了取得 handler 名稱來驗證新的輸入；狀態與版本在 ``session.retry`` 的鎖內才檢查。

    Args:
        draft_id: the draft's ID
            草稿 ID
        body: the version, plus optional new input and duplicate decision
            版本，以及可選的新輸入與重複卡處理決定
        note_service: the note service that validates input and generates the draft
            驗證輸入並生成草稿的筆記服務
        session: the site-wide create session
            全站共用的建卡工作階段

    Returns:
        the draft after the retry was accepted; the LLM generates in the background
        接受重試後的草稿；LLM 在背景生成

    Raises:
        DraftNotFailedError: if the draft's generation did not fail
            若不是生成失敗的草稿
        DraftNotFoundError: if ``draft_id`` is not the current draft
            若 ``draft_id`` 不是目前的草稿
        DraftVersionConflictError: if the version is not the latest
            若版本不是最新的
        DuplicateFoundError: if the new input already has the same card in Anki and no decision has
            been made yet
            若新的輸入在 Anki 已有同一張卡，還沒決定怎麼處理
        RequestValidationError: if the new input fails validation
            若新的輸入驗證失敗
    """
    previous = session.get(draft_id)
    handler_name = previous.handler_name
    if person_profile_fill.is_fill_draft(previous.input):
        return await _retry_fill(draft_id, body, note_service, session, previous.input or {})
    parameters = None
    if body.params is not None:
        try:
            parameters = note_service.validate_draft_input(handler_name, body.params)
        except ValidationError as exc:
            raise RequestValidationError(_params_errors(exc)) from exc
        # 換了輸入就重新查重複。沒帶新的決定時，上一次的決定只在查到的還是同一張舊卡時沿用（例如只改了情境）；
        # 換了原文、對到另一張舊卡，就再回 409 讓使用者看過再決定
        parameters = await note_service.resolve_draft_duplicate(
            handler_name, parameters, body.duplicate_decision, previous=DuplicateResolution.from_input(previous.input)
        )
    return await session.retry(
        draft_id, body.version, lambda p: note_service.generate_draft(handler_name, p), parameters
    )


async def _retry_fill(
    draft_id: str,
    body: RetryDraftRequest,
    note_service: NoteService,
    session: CreateSession,
    previous_input: dict[str, object],
) -> dict[str, object]:
    """Retry an "AI 整理" draft: new input is checked as a start request; the card or deck is kept.

    重試「AI 整理」的草稿：新的輸入以開始時的格式檢查；要補的卡或建新卡的牌組沿用原本的。

    Raises:
        RequestValidationError: if the new description fails validation
            若新的描述驗證失敗
    """
    parameters = None
    if body.params is not None:
        try:
            request = StartFillRequest.model_validate({
                "note_id": previous_input.get("note_id"),
                "deck": previous_input.get("deck"),
                **body.params,
            })
        except ValidationError as exc:
            raise RequestValidationError(_params_errors(exc)) from exc
        parameters = person_profile_fill.draft_input(request.text, request.note_id, request.deck)
    card_service = note_service.card_service
    return await session.retry(
        draft_id, body.version, lambda p: person_profile_fill.generate(card_service, p), parameters
    )


@router.post(
    "/{draft_id}/apply",
    response_model=ApplyFillResponse,
    summary="寫入「AI 整理」草稿裡勾選的建議",
    description=(
        "帶草稿版本、編輯後的全部建議與勾選的序號。建新卡時先以稱呼建卡，再把其他建議寫進新卡。不合格或被拒絕的項目"
        "回報在 errors、其他照常寫入；勾選的都寫入了就結束草稿，否則草稿留著（記下新卡與已寫入的項目），"
        "再送一次只補寫剩下的、不會建第二張卡。"
    ),
    responses={
        **_GONE,
        400: {"model": ErrorResponse, "description": "這份草稿不是 AI 整理的（預覽建卡用 POST /handlers/{name}/notes）"},
        409: {"model": ErrorResponse, "description": _STATE},
        502: {"model": ErrorResponse, "description": "AnkiConnect 連不上；已寫入的項目記在草稿裡"},
    },
)
async def apply_draft(
    draft_id: str,
    body: ApplyFillRequest,
    note_service: NoteService = Depends(get_note_service),
    session: CreateSession = Depends(get_create_session),
) -> dict[str, object]:
    """Write the ticked proposals of an "AI 整理" draft.

    寫入「AI 整理」草稿裡勾選的建議。

    The draft is claimed like a preview draft being created (``ready`` → ``creating``, version checked), so it
    cannot be discarded or written twice at the same time; afterwards ``finish_apply`` either ends it or puts it
    back with the progress.

    草稿和預覽建卡一樣先佔用（``ready`` → ``creating``，檢查版本），寫入期間不能廢棄或重複送出；結束時
    ``finish_apply`` 結束草稿，或帶著進度放回去。

    Args:
        draft_id: the draft's ID
            草稿 ID
        body: the version, the edited proposals, and the ticked indexes
            版本、編輯後的建議與勾選的序號
        note_service: the note service
            筆記服務
        session: the site-wide create session
            全站共用的建卡工作階段

    Returns:
        what was written, what was not and why, and the draft when it stays
        寫入了哪些、哪些沒寫入與原因，以及草稿還在時的草稿

    Raises:
        AnkiConnectError: if AnkiConnect fails; the progress is kept in the draft
            若 AnkiConnect 失敗；進度記在草稿裡
        DraftBusyError: if the draft is not ready to be written
            若草稿不是可寫入的狀態
        DraftNotFoundError: if ``draft_id`` is not the current draft
            若 ``draft_id`` 不是目前的草稿
        DraftVersionConflictError: if the version is not the latest
            若版本不是最新的
        HandlerOperationUnsupportedError: if the draft is not an "AI 整理" draft
            若不是「AI 整理」的草稿
    """
    draft = session.get(draft_id)
    if not person_profile_fill.is_fill_draft(draft.input):
        raise HandlerOperationUnsupportedError("這份草稿不是 AI 整理的，請到建卡畫面建立")
    claimed = await session.begin_create(draft.handler_name, draft_id, body.version)
    params = dict((draft.result or {}).get("params") or {})  # type: ignore[call-overload]
    progress = person_profile_fill.ApplyProgress.from_params(params)
    done = False
    try:
        done = await person_profile_fill.apply(
            note_service, note_service.card_service, params, body.proposals, body.selected, progress
        )
    finally:
        remaining = await session.finish_apply(claimed, done=done, progress=progress.to_params())
    return {
        "done": done,
        "note_id": progress.note_id,
        "saved": sorted(progress.saved),
        "errors": {str(i): message for i, message in sorted(progress.errors.items())},
        "sync_warning": progress.warning,
        "draft": remaining,
    }


@router.delete(
    "/{draft_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="廢棄草稿",
    description="只限可建立（ready）與生成失敗（failed）；生成中、建立中回 409 DRAFT_BUSY。",
    responses={**_GONE, 409: {"model": ErrorResponse, "description": _STATE}},
)
async def discard_draft(draft_id: str, session: CreateSession = Depends(get_create_session)) -> Response:
    """Discard a draft that is ready to create or whose generation failed.

    廢棄可建立或生成失敗的草稿。

    Args:
        draft_id: the draft's ID
            草稿 ID
        session: the site-wide create session
            全站共用的建卡工作階段

    Returns:
        an empty 204 response
        空的 204 回應

    Raises:
        DraftBusyError: if the draft is generating or being created
            若草稿生成中或建立中
        DraftNotFoundError: if ``draft_id`` is not the current draft
            若 ``draft_id`` 不是目前的草稿
    """
    await session.discard(draft_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
