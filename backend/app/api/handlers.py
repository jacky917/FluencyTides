"""Task handlers REST API router module.

Task Handlers REST API 路由模組。

Handlers plan D5, §4.

- ``GET    /handlers``: every handler, its capabilities, and its card creation form
- ``POST   /handlers/{name}/drafts``: web preview flow; start generating a draft in the background
  (202, server_drafts plan)
- ``POST   /handlers/{name}/notes``: create cards (201), optionally with the user's tags and a draft ID
- ``GET    /handlers/{name}/tags``: tags this kind of card has used, with counts (for autocomplete and
  suggestions)
- ``GET    /handlers/{name}/notes/{note_id}``: read through the handler
- ``PATCH  /handlers/{name}/notes/{note_id}``: update fields
- ``PUT    /handlers/{name}/notes/{note_id}/json/{field}``: write a whole JSON field (json_field_editor
  plan)
- ``POST   /handlers/{name}/search-query``: build an Anki query from field searches
- ``DELETE /handlers/{name}/notes/{note_id}``: delete
- ``GET    /handlers/{name}/graph``: graph

Every endpoint goes through ``NoteService`` (capability check, note ownership check, sync after
writes) and never calls a handler directly. Business errors are turned into
``{"error_code", "message"}`` by the global handler (D6); create parameter validation failures are
FastAPI's standard 422 with ``loc`` ``["body", "params", field]``. Generic note reading and the deck
list are in ``app/api/notes.py``; reading, updating, retrying, and discarding drafts are in
``app/api/drafts.py``.

Card creation and generation from the web page first go through the site-wide create slot
(``CreateSession``): only one draft or one card being created at a time. Telegram and local scripts
do not go through here (server_drafts plan D2, D10).

handlers 計畫書 D5、§4。

- ``GET    /handlers``                             所有 handler、能力與建卡表單
- ``POST   /handlers/{name}/drafts``                 網頁預覽流程：開始在背景生成草稿（202，server_drafts 計畫書）
- ``POST   /handlers/{name}/notes``                  建卡（201），可帶使用者的標籤與草稿 ID
- ``GET    /handlers/{name}/tags``                   這種卡片用過的標籤與次數（自動完成、推薦用）
- ``GET    /handlers/{name}/notes/{note_id}``        handler 讀取
- ``PATCH  /handlers/{name}/notes/{note_id}``        修改欄位
- ``PUT    /handlers/{name}/notes/{note_id}/json/{field}``  整欄寫入 JSON 欄位（json_field_editor 計畫）
- ``POST   /handlers/{name}/search-query``           欄位搜尋組成 Anki 查詢
- ``DELETE /handlers/{name}/notes/{note_id}``        刪除
- ``GET    /handlers/{name}/graph``                  圖譜

全部經過 ``NoteService``（能力檢查、筆記歸屬檢查、寫入後同步），不直接呼叫 handler。業務錯誤由全域
handler 轉成 ``{"error_code", "message"}``（D6）；建立參數驗證失敗是 FastAPI 標準的 422，``loc`` 為
``["body", "params", 欄位]``。通用的筆記讀取與牌組清單在 ``app/api/notes.py``，草稿的讀取、修改、
重試與廢棄在 ``app/api/drafts.py``。

網頁的建卡與生成先經過全站的建卡槽位（``CreateSession``）：同一時間只有一份草稿或一張建立中的卡片。
TG 與本機腳本不經過這裡（server_drafts 計畫書 D2、D10）。
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, Query, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.core.auth import verify_api_key
from app.core.dependencies import get_note_service
from app.schemas.card import ErrorResponse
from app.schemas.drafts import DraftOut, StartDraftRequest
from app.schemas.notes import (
    CreateNoteRequest,
    CreateNoteResponse,
    DeleteNoteResponse,
    GraphResponse,
    HandlerInfo,
    NoteDetail,
    SearchQueryRequest,
    SearchQueryResponse,
    TagCount,
    UpdateJsonRequest,
    UpdateJsonResponse,
    UpdateNoteRequest,
    UpdateNoteResponse,
)
from app.services.create_session import CreateSession, get_create_session
from app.services.note_service import NoteService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/handlers",
    tags=["Handlers"],
    dependencies=[Depends(verify_api_key)],
)

_NOT_FOUND = {404: {"model": ErrorResponse, "description": "handler 或筆記不存在"}}
_REFUSED = {400: {"model": ErrorResponse, "description": "操作不開放、筆記不歸這個 handler、欄位不能修改或不存在"}}
_ANKI_ERROR = {502: {"model": ErrorResponse, "description": "AnkiConnect 連不上或回傳錯誤"}}
_SESSION_BUSY = "已經有一份草稿或一張建立中的卡片（CREATE_SESSION_BUSY）；回應附上目前的草稿 draft"
_DUPLICATE_FOUND = (
    "Anki 已有同一張卡（DUPLICATE_FOUND，例如外語糾錯的同一句原文），回應附上舊卡 duplicate；"
    "帶 duplicate_decision 重送才會生成"
)
_DRAFT_STATE = "草稿不能建立（DRAFT_BUSY）或版本不是最新的（DRAFT_VERSION_CONFLICT）；回應附上目前的草稿 draft"


@router.get(
    "",
    response_model=list[HandlerInfo],
    summary="列出所有 handler 與能力",
    description="能力是 handler 層級的開關；標準建立的表單向 Anki 查欄位即時產生，Anki 連不上時該項為 null。",
)
async def list_handlers(note_service: NoteService = Depends(get_note_service)) -> list[dict[str, object]]:
    """Return every handler, in registration order.

    回傳所有 handler，依註冊順序。

    Args:
        note_service: the note service
            筆記服務

    Returns:
        each handler's information, capabilities, and creation form
        每個 handler 的資訊、能力與建卡表單
    """
    return await note_service.list_handlers()


@router.post(
    "/{handler_name}/notes",
    response_model=CreateNoteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="透過 handler 建卡",
    responses={
        **_REFUSED,
        404: {"model": ErrorResponse, "description": "handler 不存在，或帶的草稿已不在（DRAFT_NOT_FOUND）"},
        409: {"model": ErrorResponse, "description": f"首欄位重複；{_SESSION_BUSY}；{_DRAFT_STATE}"},
        422: {"description": "筆記類型不能建立（ErrorResponse），或參數驗證失敗（detail）"},
        **_ANKI_ERROR,
    },
)
async def create_note(
    handler_name: str,
    body: CreateNoteRequest,
    note_service: NoteService = Depends(get_note_service),
    session: CreateSession = Depends(get_create_session),
) -> dict[str, object]:
    """Create notes, then sync and wait for the sync to finish.

    建卡，寫入後同步並等它完成。

    Before writing, the site-wide create slot is claimed: with ``draft``, that draft is switched to
    being created; otherwise the slot must be empty. On success the slot is cleared; on failure the
    draft goes back to ready (server_drafts plan D2, D7). An error raised by ``NoteService.create``
    after it has written to Anki (or a cancelled request) also counts as a failure, and the draft goes
    back to ready (plan §11.4).

    寫入前先佔用全站的建卡槽位：帶 ``draft`` 時把那份草稿改成建立中，否則槽位必須是空的；
    成功後清空槽位，失敗時草稿退回可建立（server_drafts 計畫書 D2、D7）。``NoteService.create`` 在寫入
    Anki 之後才拋錯（或請求被取消）也算失敗，草稿會退回可建立（計畫書 §11.4）。

    ``begin_create`` is deliberately outside the ``try``: when claiming fails, ``finish_create`` must
    not be called, or it would touch someone else's draft.

    ``begin_create`` 刻意放在 ``try`` 外面：佔用失敗時不能呼叫 ``finish_create``，否則會動到別人的草稿。

    Args:
        handler_name: the handler to create through
            負責建卡的 handler
        body: the deck, note type, parameters, tags, and optional draft
            牌組、筆記類型、參數、標籤與可選的草稿
        note_service: the note service
            筆記服務
        session: the site-wide create session
            全站共用的建卡工作階段

    Returns:
        the creation result
        建卡結果

    Raises:
        CreateSessionBusyError: if the create slot is not available (see ``CreateSession.begin_create``)
            若建卡槽位不允許（見 ``CreateSession.begin_create``）
        DraftBusyError: if the create slot is not available (see ``CreateSession.begin_create``)
            若建卡槽位不允許（見 ``CreateSession.begin_create``）
        DraftNotFoundError: if the create slot is not available (see ``CreateSession.begin_create``)
            若建卡槽位不允許（見 ``CreateSession.begin_create``）
        DraftVersionConflictError: if the create slot is not available (see
            ``CreateSession.begin_create``)
            若建卡槽位不允許（見 ``CreateSession.begin_create``）
        RequestValidationError: if the create parameters fail validation; ``loc`` is prefixed with
            ``body``, ``params`` so the front end can map errors to form fields
            若建立參數驗證失敗；``loc`` 前面接上 ``body``、``params``，前端可以把錯誤對到表單欄位
    """
    draft_id, version = (body.draft.id, body.draft.version) if body.draft else (None, None)
    claimed = await session.begin_create(handler_name, draft_id, version)
    created = False
    try:
        result = await note_service.create(handler_name, body.deck, body.note_type, body.params, tags=body.tags)
        created = True
        return result
    except ValidationError as exc:
        raise RequestValidationError(_params_errors(exc)) from exc
    except asyncio.CancelledError:
        logger.warning("建卡請求被取消，Anki 可能已寫入；草稿 %s 退回可建立", claimed)
        raise
    finally:
        await session.finish_create(claimed, created=created)


def _params_errors(exc: ValidationError) -> list[dict[str, object]]:
    """Convert parameter validation errors to FastAPI's 422 shape.

    參數驗證錯誤轉成 FastAPI 的 422 形狀。

    ``loc`` is prefixed with ``body``, ``params`` (so the front end can map errors back to form
    fields). A ``ValidationError`` raised inside an endpoint is not turned into a 422 by FastAPI
    automatically; raise ``RequestValidationError`` instead.

    ``loc`` 前面接上 ``body``、``params``（前端對回表單欄位）。端點內拋的 ValidationError 不會被
    FastAPI 自動轉成 422，要改拋 RequestValidationError。

    Args:
        exc: the validation error of the parameters
            參數的驗證錯誤

    Returns:
        the errors with the prefixed ``loc``
        ``loc`` 已接上前綴的錯誤串列
    """
    return [
        {**error, "loc": ("body", "params", *error["loc"])}
        for error in exc.errors(include_url=False, include_context=False)
    ]


@router.get(
    "/{handler_name}/tags",
    response_model=list[TagCount],
    summary="這種卡片用過的標籤",
    description="依使用次數排序，排除系統標籤（建卡固定加的、流程標記用的、LLM:: 開頭的等）。",
    responses={**_REFUSED, **_NOT_FOUND, **_ANKI_ERROR},
)
async def list_tags(handler_name: str, note_service: NoteService = Depends(get_note_service)) -> list[dict[str, object]]:
    """Return the tags this kind of card has used, with counts.

    回傳這種卡片用過的標籤與次數。

    Args:
        handler_name: the handler whose cards to inspect
            要查看其卡片的 handler
        note_service: the note service
            筆記服務

    Returns:
        the tags with their counts
        標籤與次數
    """
    return await note_service.tags(handler_name)


@router.post(
    "/{handler_name}/drafts",
    response_model=DraftOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="網頁預覽流程：開始在背景生成建卡草稿",
    description=(
        "驗證輸入後立刻回 202，LLM 在背景生成；結果用 GET /drafts/current 讀。全站同一時間只有一份草稿，"
        "已經有草稿時回 409 CREATE_SESSION_BUSY。只生成、不寫入 Anki。"
    ),
    responses={
        **_REFUSED,
        **_NOT_FOUND,
        409: {"model": ErrorResponse, "description": f"{_SESSION_BUSY}；{_DUPLICATE_FOUND}"},
        422: {"description": "輸入驗證失敗（detail）"},
    },
)
async def start_draft(
    handler_name: str,
    body: StartDraftRequest,
    note_service: NoteService = Depends(get_note_service),
    session: CreateSession = Depends(get_create_session),
) -> dict[str, object]:
    """Start generating a draft.

    開始生成草稿。

    Duplicates (for example the same original sentence in a foreign-language correction) are checked
    only when the slot is empty: when a draft already exists, ``session.start`` returns 409 anyway, so
    there is no need to query Anki first.

    槽位是空的才查重複（例如外語糾錯的同一句原文）：已經有草稿時 ``session.start`` 本來就會回 409，
    不必先查 Anki。

    Args:
        handler_name: the handler that generates the draft
            負責生成草稿的 handler
        body: the input parameters and optional duplicate decision
            輸入參數與可選的重複卡處理決定
        note_service: the note service
            筆記服務
        session: the site-wide create session
            全站共用的建卡工作階段

    Returns:
        the new draft; the LLM generates in the background
        新的草稿；LLM 在背景生成

    Raises:
        DuplicateFoundError: if Anki already has the same card and no decision has been made yet (D10.2)
            若 Anki 已有同一張卡，還沒決定怎麼處理（D10.2）
        RequestValidationError: if the input fails validation; ``loc`` is prefixed with ``body``,
            ``params``
            若輸入驗證失敗；``loc`` 前面接上 ``body``、``params``
    """
    try:
        parameters = note_service.validate_draft_input(handler_name, body.params)
    except ValidationError as exc:
        raise RequestValidationError(_params_errors(exc)) from exc
    if session.snapshot() is None:
        parameters = await note_service.resolve_draft_duplicate(handler_name, parameters, body.duplicate_decision)
    return await session.start(handler_name, parameters, lambda p: note_service.generate_draft(handler_name, p))


@router.get(
    "/{handler_name}/notes/{note_id}",
    response_model=NoteDetail,
    summary="透過 handler 讀取筆記",
    description="成組卡片的 handler 之後會附上整組；目前都是單一筆記，與 GET /notes/{note_id} 的欄位相同。",
    responses={**_REFUSED, **_NOT_FOUND, **_ANKI_ERROR},
)
async def read_note(
    handler_name: str,
    note_id: int,
    note_service: NoteService = Depends(get_note_service),
) -> dict[str, object]:
    """Read a note through its handler.

    透過 handler 讀取筆記。

    Args:
        handler_name: the handler the note belongs to
            筆記所屬的 handler
        note_id: the Anki note ID
            Anki 筆記 ID
        note_service: the note service
            筆記服務

    Returns:
        the note's details
        筆記內容
    """
    return await note_service.read(handler_name, note_id)


@router.patch(
    "/{handler_name}/notes/{note_id}",
    response_model=UpdateNoteResponse,
    summary="修改筆記欄位",
    description="只送有變的欄位；回傳修改後的完整筆記與同步提醒。",
    responses={**_REFUSED, **_NOT_FOUND, **_ANKI_ERROR},
)
async def update_note(
    handler_name: str,
    note_id: int,
    body: UpdateNoteRequest,
    note_service: NoteService = Depends(get_note_service),
) -> dict[str, object]:
    """Update a note's fields.

    修改筆記欄位。

    Args:
        handler_name: the handler the note belongs to
            筆記所屬的 handler
        note_id: the Anki note ID
            Anki 筆記 ID
        body: only the fields that changed
            只含有變的欄位
        note_service: the note service
            筆記服務

    Returns:
        the full note after the update, with a sync warning
        修改後的完整筆記與同步提醒
    """
    return await note_service.update_fields(handler_name, note_id, body.fields)


@router.put(
    "/{handler_name}/notes/{note_id}/json/{field_name}",
    response_model=UpdateJsonResponse,
    summary="整欄寫入 JSON 欄位",
    description="送出整個清單與讀取時的版本；別處改過時回 409，某一筆格式不對時回 422（loc 指到第幾筆的哪個鍵）。",
    responses={
        **_REFUSED,
        **_NOT_FOUND,
        409: {"model": ErrorResponse, "description": "這一欄在別處被改過"},
        422: {"description": "某一筆格式不對（detail）"},
        **_ANKI_ERROR,
    },
)
async def update_json_field(
    handler_name: str,
    note_id: int,
    field_name: str,
    body: UpdateJsonRequest,
    note_service: NoteService = Depends(get_note_service),
) -> dict[str, object]:
    """Write a whole JSON field, then sync.

    整欄寫入 JSON 欄位，寫入後同步。

    Args:
        handler_name: the handler the note belongs to
            筆記所屬的 handler
        note_id: the Anki note ID
            Anki 筆記 ID
        field_name: the JSON field to write
            要寫入的 JSON 欄位
        body: the whole list and the version read earlier
            整個清單與讀取時的版本
        note_service: the note service
            筆記服務

    Returns:
        the write result
        寫入結果

    Raises:
        RequestValidationError: if an item is malformed; ``loc`` is prefixed with ``body``, ``items``
            若某一筆格式不對；``loc`` 前面接上 ``body``、``items``
    """
    try:
        return await note_service.update_json(handler_name, note_id, field_name, list(body.items), body.version)
    except ValidationError as exc:
        errors = [
            {**error, "loc": ("body", "items", *error["loc"])}
            for error in exc.errors(include_url=False, include_context=False)
        ]
        raise RequestValidationError(errors) from exc


@router.post(
    "/{handler_name}/search-query",
    response_model=SearchQueryResponse,
    summary="欄位搜尋組成 Anki 查詢",
    description="把「欄位按鈕＋關鍵字」的條件（條件之間是「且」）組成 Anki 查詢；不開放欄位搜尋時回 400，不能搜尋的欄位回 422。",
    responses={**_REFUSED},
)
async def build_search_query(
    handler_name: str,
    body: SearchQueryRequest,
    note_service: NoteService = Depends(get_note_service),
) -> dict[str, object]:
    """Build an Anki query from field searches without touching Anki.

    欄位搜尋組成 Anki 查詢（不碰 Anki）。

    Args:
        handler_name: the handler whose fields are searched
            要搜尋其欄位的 handler
        body: the field and keyword conditions, combined with AND
            欄位與關鍵字的條件，條件之間是「且」
        note_service: the note service
            筆記服務

    Returns:
        ``{"query": ...}`` with the Anki query
        含 Anki 查詢的 ``{"query": ...}``
    """
    query = note_service.search_query(handler_name, [(c.field, c.value) for c in body.conditions])
    return {"query": query}


@router.delete(
    "/{handler_name}/notes/{note_id}",
    response_model=DeleteNoteResponse,
    summary="刪除筆記",
    description="由 handler 刪除筆記與關聯；不開放時回 400，Anki 與關聯都不會被動到。",
    responses={**_REFUSED, **_NOT_FOUND, **_ANKI_ERROR},
)
async def delete_note(
    handler_name: str,
    note_id: int,
    note_service: NoteService = Depends(get_note_service),
) -> dict[str, object]:
    """Delete a note and its relations through its handler.

    透過 handler 刪除筆記與關聯。

    Args:
        handler_name: the handler the note belongs to
            筆記所屬的 handler
        note_id: the Anki note ID
            Anki 筆記 ID
        note_service: the note service
            筆記服務

    Returns:
        ``{"sync_warning": ...}``
        ``{"sync_warning": ...}``
    """
    return {"sync_warning": await note_service.delete(handler_name, note_id)}


@router.get(
    "/{handler_name}/graph",
    response_model=GraphResponse,
    summary="讀取 handler 的圖譜",
    responses={
        400: {"model": ErrorResponse, "description": "這個 handler 不開放圖譜"},
        **_NOT_FOUND,
        422: {"model": ErrorResponse, "description": "note_type 或 label_field 不適用"},
        **_ANKI_ERROR,
    },
)
async def read_graph(
    handler_name: str,
    deck: str | None = Query(None, description="牌組完整名稱（含子牌組）；省略為全部牌組"),
    note_type: str | None = Query(None, description="只看這個 handler 負責的其中一種筆記類型"),
    label_field: str | None = Query(None, description="節點名稱用的欄位；省略為 handler 的預設（沒宣告就是 nid）"),
    note_service: NoteService = Depends(get_note_service),
) -> dict[str, object]:
    """Return the handler's graph.

    回傳 handler 的圖譜。

    Args:
        handler_name: the handler whose graph to read
            要讀取圖譜的 handler
        deck: the full deck name (subdecks included); ``None`` means all decks
            牌組完整名稱（含子牌組）；``None`` 為全部牌組
        note_type: only this one of the handler's note types; ``None`` means all of them
            只看這個 handler 負責的其中一種筆記類型；``None`` 為全部
        label_field: the field used for node names; ``None`` means the handler's default (``nid`` if
            it declares none)
            節點名稱用的欄位；``None`` 為 handler 的預設（沒宣告就是 nid）
        note_service: the note service
            筆記服務

    Returns:
        the graph's nodes and edges
        圖譜的節點與邊
    """
    return await note_service.graph(handler_name, deck, note_type=note_type, label_field=label_field)
