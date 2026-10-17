"""LLM 用途設定 API（模型路由計畫 llm_model_routing_FEAT_2026-10-15.md D6）。

LLM route settings API.

- ``GET    /api/v1/llm-routes``             全部用途鍵的生效設定與可選項目
- ``PUT    /api/v1/llm-routes/{route_key}`` 寫入一組選擇（白名單＋試建客戶端檢查）
- ``DELETE /api/v1/llm-routes/{route_key}`` 恢復預設（退回全域，或 ``.env``）

全域設定的用途鍵是 ``*``。認證與其他 API 相同，掛在 ``verify_api_key`` 之下（部署上的
存取控制見 README 的「API 認證」）。可選項目只列端點 ID、類型與白名單，不含金鑰。

錯誤由全域的 ``FluencyTidesError`` handler 轉成 ``{"error_code", "message"}``：用途鍵不存在
（PUT 時為未宣告）404（``LLM_ROUTE_NOT_FOUND``）、設定不合格 422（``INVALID_LLM_ROUTE``）、
讀不到資料庫或寫入時資料庫異常 503（``LLM_ROUTE_STORE_UNAVAILABLE``，訊息區分兩者）；
請求欄位格式不符則是 FastAPI 自己的 422（``detail``）。
"""

import logging

from fastapi import APIRouter, Depends

from app.core.auth import verify_api_key
from app.infrastructure.llm.endpoints import list_endpoints
from app.schemas.llm_route import (
    EndpointOptionRead,
    LLMRouteDeleteResponse,
    LLMRouteRead,
    LLMRoutesResponse,
    LLMRouteUpdate,
    ModelOptionRead,
    RejectedLayerRead,
    RouteChoiceRead,
)
from app.services import llm_route_service
from app.services.llm_route_service import RouteView

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/llm-routes",
    tags=["LLM Routes"],
    dependencies=[Depends(verify_api_key)],
)


def _to_read(view: RouteView) -> LLMRouteRead:
    """服務層的狀態轉成 API 回應。Convert a service view into the response model."""
    resolved = view.resolved
    return LLMRouteRead(
        route_key=view.route_key,
        description=view.description,
        declared=view.declared,
        source=resolved.source,
        effective=RouteChoiceRead(endpoint=resolved.endpoint, model=resolved.model, effort=view.effective_effort),
        label=view.label,
        stored=(
            RouteChoiceRead(endpoint=view.stored.endpoint, model=view.stored.model, effort=view.stored.effort)
            if view.stored is not None
            else None
        ),
        rejected=[RejectedLayerRead(route_key=layer.route_key, reason=layer.reason) for layer in resolved.rejected],
        db_unavailable=resolved.db_unavailable,
        problem=view.problem,
    )


@router.get("", response_model=LLMRoutesResponse, summary="列出 LLM 用途設定與可選項目")
async def list_llm_routes() -> LLMRoutesResponse:
    """全部用途鍵（含全域 ``*`` 與孤兒鍵）的生效設定，以及白名單內可選的端點與模型。

    Every route key's effective setting plus the selectable endpoints. When
    the database cannot be read, every row reports ``db_unavailable``.
    """
    views, read_ok = await llm_route_service.list_routes()
    return LLMRoutesResponse(
        routes=[_to_read(view) for view in views],
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


@router.put("/{route_key}", response_model=LLMRouteRead, summary="設定一個用途的模型")
async def put_llm_route(route_key: str, body: LLMRouteUpdate) -> LLMRouteRead:
    """寫入一組選擇；下一次呼叫即生效、重啟後仍在。

    Store a choice for the route key; effective from the next call.

    Raises:
        LLMRouteNotFoundError: 404——用途鍵未宣告（孤兒鍵只能刪除，不能設定）。
        InvalidLLMRouteError: 422——不在白名單或試建客戶端失敗。
        LLMRouteStoreUnavailableError: 503——寫入時資料庫異常（結果未知，請重新 GET 查看）。
    """
    view = await llm_route_service.set_route(route_key, body.endpoint, body.model, body.effort, updated_by="api")
    return _to_read(view)


@router.delete("/{route_key}", response_model=LLMRouteDeleteResponse, summary="恢復一個用途的預設")
async def delete_llm_route(route_key: str) -> LLMRouteDeleteResponse:
    """刪除該用途鍵的設定，退回全域（或 ``.env``）。孤兒鍵也可刪除。

    Delete the route key's row so it falls back to the next layer.

    Raises:
        LLMRouteNotFoundError: 404——用途鍵既未宣告、DB 中也沒有。
        LLMRouteStoreUnavailableError: 503——讀不到 DB（無法判斷孤兒鍵是否存在），或刪除時
            資料庫異常（結果未知，請重新 GET 查看）。
    """
    result = await llm_route_service.delete_route(route_key, updated_by="api")
    return LLMRouteDeleteResponse(
        deleted=result.deleted,
        route=_to_read(result.view) if result.view is not None else None,
        route_unavailable=result.view_unavailable,
    )
