"""
Task Handlers REST API 路由模組（handlers 計畫書 D5、§4）。

Task handlers REST API router module.

- ``GET    /handlers``                               所有 handler、能力與建卡表單
- ``POST   /handlers/{name}/notes``                  建卡（201）
- ``GET    /handlers/{name}/notes/{note_id}``        handler 讀取
- ``PATCH  /handlers/{name}/notes/{note_id}``        修改欄位
- ``PUT    /handlers/{name}/notes/{note_id}/json/{field}``  整欄寫入 JSON 欄位（json_field_editor 計畫）
- ``POST   /handlers/{name}/search-query``           欄位搜尋組成 Anki 查詢
- ``DELETE /handlers/{name}/notes/{note_id}``        刪除
- ``GET    /handlers/{name}/graph``                  圖譜

全部經過 ``NoteService``（能力檢查、筆記歸屬檢查、寫入後同步），不直接呼叫 handler。業務錯誤由全域
handler 轉成 ``{"error_code", "message"}``（D6）；建立參數驗證失敗是 FastAPI 標準的 422，``loc`` 為
``["body", "params", 欄位]``。通用的筆記讀取與牌組清單在 ``app/api/notes.py``。

Every endpoint goes through ``NoteService``; business errors become
``{"error_code", "message"}`` and invalid create parameters a standard 422.
"""

from fastapi import APIRouter, Depends, Query, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.core.auth import verify_api_key
from app.core.dependencies import get_note_service
from app.schemas.card import ErrorResponse
from app.schemas.notes import (
    CreateNoteRequest,
    CreateNoteResponse,
    DeleteNoteResponse,
    GraphResponse,
    HandlerInfo,
    NoteDetail,
    SearchQueryRequest,
    SearchQueryResponse,
    UpdateJsonRequest,
    UpdateJsonResponse,
    UpdateNoteRequest,
    UpdateNoteResponse,
)
from app.services.note_service import NoteService

router = APIRouter(
    prefix="/handlers",
    tags=["Handlers"],
    dependencies=[Depends(verify_api_key)],
)

_NOT_FOUND = {404: {"model": ErrorResponse, "description": "handler 或筆記不存在"}}
_REFUSED = {400: {"model": ErrorResponse, "description": "操作不開放、筆記不歸這個 handler、欄位不能修改或不存在"}}
_ANKI_ERROR = {502: {"model": ErrorResponse, "description": "AnkiConnect 連不上或回傳錯誤"}}


@router.get(
    "",
    response_model=list[HandlerInfo],
    summary="列出所有 handler 與能力",
    description="能力是 handler 層級的開關；標準建立的表單向 Anki 查欄位即時產生，Anki 連不上時該項為 null。",
)
async def list_handlers(note_service: NoteService = Depends(get_note_service)) -> list[dict[str, object]]:
    """所有 handler，依註冊順序。Every handler, in registration order."""
    return await note_service.list_handlers()


@router.post(
    "/{handler_name}/notes",
    response_model=CreateNoteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="透過 handler 建卡",
    responses={
        **_REFUSED,
        **_NOT_FOUND,
        409: {"model": ErrorResponse, "description": "首欄位重複"},
        422: {"description": "筆記類型不能建立（ErrorResponse），或參數驗證失敗（detail）"},
        **_ANKI_ERROR,
    },
)
async def create_note(
    handler_name: str,
    body: CreateNoteRequest,
    note_service: NoteService = Depends(get_note_service),
) -> dict[str, object]:
    """建卡，寫入後同步並等它完成。Create notes and sync afterwards.

    Raises:
        RequestValidationError: 建立參數驗證失敗；``loc`` 前面接上 ``body``、``params``，
            前端可以把錯誤對到表單欄位。On invalid parameters.
    """
    try:
        return await note_service.create(handler_name, body.deck, body.note_type, body.params)
    except ValidationError as exc:
        # 端點內拋的 ValidationError 不會被 FastAPI 自動轉成 422，要改拋 RequestValidationError
        errors = [
            {**error, "loc": ("body", "params", *error["loc"])}
            for error in exc.errors(include_url=False, include_context=False)
        ]
        raise RequestValidationError(errors) from exc


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
    """handler 讀取。Read a note through its handler."""
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
    """修改欄位。Update fields."""
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
    """整欄寫入 JSON 欄位，寫入後同步。Write a whole JSON field.

    Raises:
        RequestValidationError: 某一筆格式不對；``loc`` 前面接上 ``body``、``items``。On invalid items.
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
    """欄位搜尋組成 Anki 查詢（不碰 Anki）。Build the Anki query; Anki is not touched."""
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
    """刪除。Delete a note."""
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
    """handler 的圖譜。The handler's graph."""
    return await note_service.graph(handler_name, deck, note_type=note_type, label_field=label_field)
