"""LLM 用途設定 API（模型路由計畫 llm_model_routing_FEAT_2026-10-15.md D6）。

LLM route settings API.

- ``GET    /api/v1/llm-routes``             全部用途鍵的生效設定與可選項目
- ``PUT    /api/v1/llm-routes/{route_key}`` 寫入一組選擇（白名單＋試建客戶端檢查）
- ``DELETE /api/v1/llm-routes/{route_key}`` 恢復預設（退回全域，或 ``.env``）

全域設定的用途鍵是 ``*``；寫入或刪除它之後（不論成敗）會重建 ``app.state.llm_client``（診斷端點用，D9）。
認證與其他 API 相同，掛在 ``verify_api_key`` 之下（部署上的存取控制見 README 的「API 認證」）。
可選項目只列端點 ID、類型與白名單，不含金鑰。

錯誤由全域的 ``FluencyTidesError`` handler 轉成 ``{"error_code", "message"}``：用途鍵不存在
（PUT 時為未宣告）404（``LLM_ROUTE_NOT_FOUND``）、設定不合格 422（``INVALID_LLM_ROUTE``）、
讀不到資料庫或寫入時資料庫異常 503（``LLM_ROUTE_STORE_UNAVAILABLE``，訊息區分兩者）；
請求欄位格式不符則是 FastAPI 自己的 422（``detail``）。
"""

import logging

from fastapi import APIRouter, Depends, Request

from app.core.auth import verify_api_key
from app.infrastructure.llm.endpoints import list_endpoints
from app.infrastructure.llm.routing import GLOBAL_ROUTE_KEY
from app.schemas.card import ErrorResponse
from app.schemas.llm_route import (
    EndpointOptionRead,
    LLMRouteDeleteResponse,
    LLMRouteRead,
    LLMRoutesResponse,
    LLMRouteUpdate,
    ModelOptionRead,
)
from app.services import llm_route_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/llm-routes",
    tags=["LLM Routes"],
    dependencies=[Depends(verify_api_key)],
)

# 業務錯誤的格式（全域 handler 產生）；FastAPI 自己的欄位驗證 422 另有 detail 格式
_NOT_FOUND = {404: {"model": ErrorResponse, "description": "用途鍵不存在（PUT：未宣告）"}}
_INVALID = {422: {"model": ErrorResponse, "description": "不在白名單或試建客戶端失敗（欄位格式錯誤時為 FastAPI 的 detail 格式）"}}
_UNAVAILABLE = {503: {"model": ErrorResponse, "description": "讀不到資料庫，或寫入／刪除時資料庫異常（結果未知）"}}


async def _refresh_global_client_if(route_key: str, request: Request) -> None:
    """改的是全域 ``*`` 時重建診斷用的全域客戶端；不論寫入成敗都做（503 結果未知時 DB 可能已改）。

    Rebuild the diagnostics' global client after any attempt to change ``*``.
    """
    if route_key == GLOBAL_ROUTE_KEY:
        await llm_route_service.refresh_global_client(request.app.state)


@router.get("", response_model=LLMRoutesResponse, summary="列出 LLM 用途設定與可選項目")
async def list_llm_routes() -> LLMRoutesResponse:
    """全部用途鍵（含全域 ``*`` 與孤兒鍵）的生效設定，以及白名單內可選的端點與模型。

    Every route key's effective setting plus the selectable endpoints. When
    the database cannot be read, every row reports ``db_unavailable``.
    """
    views, read_ok = await llm_route_service.list_routes()
    return LLMRoutesResponse(
        routes=[llm_route_service.to_read(view) for view in views],
        endpoints=[
            EndpointOptionRead(
                id=endpoint.id,
                type=endpoint.type,
                models=[ModelOptionRead(name=o.name, efforts=list(o.efforts)) for o in endpoint.models],
            )
            for endpoint in list_endpoints()
        ],
        db_unavailable=not read_ok,
    )


@router.put(
    "/{route_key}",
    response_model=LLMRouteRead,
    summary="設定一個用途的模型",
    responses={**_NOT_FOUND, **_INVALID, **_UNAVAILABLE},
)
async def put_llm_route(route_key: str, body: LLMRouteUpdate, request: Request) -> LLMRouteRead:
    """寫入一組選擇；下一次呼叫即生效、重啟後仍在。

    Store a choice for the route key; effective from the next call.

    Raises:
        LLMRouteNotFoundError: 404——用途鍵未宣告（孤兒鍵只能刪除，不能設定）。
        InvalidLLMRouteError: 422——不在白名單或試建客戶端失敗。
        LLMRouteStoreUnavailableError: 503——寫入時資料庫異常（結果未知，請重新 GET 查看）。
    """
    try:
        view = await llm_route_service.set_route(
            route_key, body.endpoint, body.model, body.effort, updated_by="api"
        )
    finally:
        await _refresh_global_client_if(route_key, request)
    return llm_route_service.to_read(view)


@router.delete(
    "/{route_key}",
    response_model=LLMRouteDeleteResponse,
    summary="恢復一個用途的預設",
    responses={**_NOT_FOUND, **_UNAVAILABLE},
)
async def delete_llm_route(route_key: str, request: Request) -> LLMRouteDeleteResponse:
    """刪除該用途鍵的設定，退回全域（或 ``.env``）。孤兒鍵也可刪除。

    Delete the route key's row so it falls back to the next layer.

    Raises:
        LLMRouteNotFoundError: 404——用途鍵既未宣告、DB 中也沒有。
        LLMRouteStoreUnavailableError: 503——讀不到 DB（無法判斷孤兒鍵是否存在），或刪除時
            資料庫異常（結果未知，請重新 GET 查看）。
    """
    try:
        result = await llm_route_service.delete_route(route_key, updated_by="api")
    finally:
        await _refresh_global_client_if(route_key, request)
    return LLMRouteDeleteResponse(
        deleted=result.deleted,
        route=llm_route_service.to_read(result.view) if result.view is not None else None,
        route_unavailable=result.view_unavailable,
    )
