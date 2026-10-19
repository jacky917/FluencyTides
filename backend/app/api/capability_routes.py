"""能力路由設定 API（能力路由計畫 capability_routing_FEAT_2026-10-19.md；前身為模型路由計畫 D6）。

Capability route settings API.

- ``GET    /api/v1/capability-routes``                          全部用途鍵各能力的生效設定與可選項目
- ``PUT    /api/v1/capability-routes/{route_key}/{capability}``  寫入一組選擇（白名單＋試建客戶端檢查）
- ``DELETE /api/v1/capability-routes/{route_key}/{capability}``  恢復預設（退回全域，或 ``.env``）

能力見 :mod:`app.infrastructure.llm.capabilities`。全域設定的用途鍵是 ``*``（URL 中寫成 ``%2A``）；寫入或刪除它的 ``llm`` 之後
（不論成敗）會重建 ``app.state.llm_client``（診斷端點用，模型路由計畫 D9）。認證與其他 API 相同，
掛在 ``verify_api_key`` 之下（部署上的存取控制見 README 的「API 認證」）。可選項目只列 provider
ID、類型與白名單，不含金鑰。

錯誤由全域的 ``FluencyTidesError`` handler 轉成 ``{"error_code", "message"}``：用途鍵不存在
（PUT 時為未宣告）404（``ROUTE_NOT_FOUND``）、設定不合格或用途鍵沒有宣告這個能力 422
（``INVALID_ROUTE``）、讀不到資料庫或寫入時資料庫異常 503（``ROUTE_STORE_UNAVAILABLE``，訊息
區分兩者）；請求欄位格式不符則是 FastAPI 自己的 422（``detail``）。
"""

import logging

from fastapi import APIRouter, Depends, Request

from app.core.auth import verify_api_key
from app.infrastructure.llm import routing
from app.infrastructure.llm.capabilities import CAPABILITY_AUDIO_EVAL, CAPABILITY_LLM, CAPABILITY_ORDER, list_providers
from app.infrastructure.llm.routing import GLOBAL_ROUTE_KEY
from app.schemas.capability_route import (
    CapabilityRoutesResponse,
    ModelOptionRead,
    ProviderOptionRead,
    RouteDeleteResponse,
    RouteRead,
    RouteUpdate,
)
from app.schemas.card import ErrorResponse
from app.services import capability_route_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/capability-routes",
    tags=["Capability Routes"],
    dependencies=[Depends(verify_api_key)],
)

# 業務錯誤的格式（全域 handler 產生）；FastAPI 自己的欄位驗證 422 另有 detail 格式
_NOT_FOUND = {404: {"model": ErrorResponse, "description": "用途鍵不存在（PUT：未宣告）"}}
_INVALID = {
    422: {
        "model": ErrorResponse,
        "description": "不支援的能力、用途鍵沒有宣告這個能力、不在白名單或試建客戶端失敗（欄位格式錯誤時為 FastAPI 的 detail 格式）",
    }
}
_UNAVAILABLE = {503: {"model": ErrorResponse, "description": "讀不到資料庫，或寫入／刪除時資料庫異常（結果未知）"}}


async def _refresh_global_client_if(route_key: str, capability: str, request: Request) -> None:
    """改的是全域 ``*`` 的 ``llm`` 時重建診斷用的全域客戶端（與 TG 選單相同）；不論寫入成敗都做
    （503 結果未知時 DB 可能已改）。其他能力與這個客戶端無關，不重建。

    Rebuild the diagnostics' global client after any attempt to change the
    global ``llm`` row.
    """
    if route_key == GLOBAL_ROUTE_KEY and capability == CAPABILITY_LLM:
        await capability_route_service.refresh_global_client(request.app.state)


@router.get("", response_model=CapabilityRoutesResponse, summary="列出能力路由設定與可選項目")
async def list_capability_routes() -> CapabilityRoutesResponse:
    """全部用途鍵（含全域 ``*`` 與孤兒鍵）各能力的生效設定，以及各能力可選的 provider 與模型。

    Every route key's per-capability state plus the selectable providers.
    When the database cannot be read, every row reports ``db_unavailable``.
    """
    views, read_ok = await capability_route_service.list_routes()
    strategies_order = [option.id for option in list_providers(CAPABILITY_AUDIO_EVAL)]
    return CapabilityRoutesResponse(
        routes=[capability_route_service.to_read(view) for view in views],
        providers={
            capability: [
                ProviderOptionRead(
                    id=option.id,
                    type=option.type,
                    models=[ModelOptionRead(name=o.name, efforts=list(o.efforts)) for o in option.models],
                    title=option.title,
                )
                for option in list_providers(capability)
            ]
            for capability in CAPABILITY_ORDER
        },
        strategies={
            declared.key: [strategy for strategy in strategies_order if strategy in allowed]
            for declared in routing.list_route_keys()
            if (allowed := routing.allowed_strategies(declared.key)) is not None
        },
        db_unavailable=not read_ok,
    )


@router.put(
    "/{route_key}/{capability}",
    response_model=RouteRead,
    summary="設定一個用途在一種能力上用的工具",
    responses={**_NOT_FOUND, **_INVALID, **_UNAVAILABLE},
)
async def put_capability_route(route_key: str, capability: str, body: RouteUpdate, request: Request) -> RouteRead:
    """寫入一組選擇；下一次呼叫即生效、重啟後仍在。

    Store a choice for the route key and capability; effective from the next call.

    Raises:
        RouteNotFoundError: 404——用途鍵未宣告（孤兒鍵只能刪除，不能設定）。
        InvalidRouteError: 422——不支援的能力、用途鍵沒有宣告這個能力、不在白名單或試建客戶端失敗。
        RouteStoreUnavailableError: 503——寫入時資料庫異常（結果未知，請重新 GET 查看）。
    """
    try:
        view = await capability_route_service.set_route(
            route_key, body.provider, body.model, body.effort, updated_by="api", capability=capability
        )
    finally:
        await _refresh_global_client_if(route_key, capability, request)
    return capability_route_service.to_read(view)


@router.delete(
    "/{route_key}/{capability}",
    response_model=RouteDeleteResponse,
    summary="恢復一個用途在一種能力上的預設",
    responses={**_NOT_FOUND, **_INVALID, **_UNAVAILABLE},
)
async def delete_capability_route(route_key: str, capability: str, request: Request) -> RouteDeleteResponse:
    """刪除該用途鍵在這個能力上的設定，退回全域（或 ``.env``）。孤兒鍵也可刪除。

    Delete the row so it falls back to the next layer.

    Raises:
        RouteNotFoundError: 404——用途鍵既未宣告、DB 中也沒有。
        InvalidRouteError: 422——不支援的能力。
        RouteStoreUnavailableError: 503——讀不到 DB（無法判斷孤兒鍵是否存在），或刪除時
            資料庫異常（結果未知，請重新 GET 查看）。
    """
    try:
        result = await capability_route_service.delete_route(route_key, updated_by="api", capability=capability)
    finally:
        await _refresh_global_client_if(route_key, capability, request)
    return RouteDeleteResponse(
        deleted=result.deleted,
        route=capability_route_service.to_read(result.view) if result.view is not None else None,
        route_unavailable=result.view_unavailable,
    )
