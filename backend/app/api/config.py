"""Runtime configuration API (read-only).

執行期設定 API（唯讀）。

Plan docs/archive/runtime_config_service_FEAT_2026-08-29.md §3.5:

- ``GET  /api/v1/config``: the backend runtime reconciliation block (LLM label, Anki endpoint,
  claude-code diagnostics, capability routes, and the endpoint whitelist)

The former whitelist settings (``configs``, ``GET /config/{key}``, from ``MODIFY_*`` in ``.env``)
were replaced by capability routing (``/api/v1/capability-routes``) and removed.

對應計畫 docs/archive/runtime_config_service_FEAT_2026-08-29.md §3.5：

- ``GET  /api/v1/config``  後端 runtime 對帳區塊（LLM 標籤、Anki 端點、claude-code 診斷、
  能力路由與端點白名單）

原本的白名單設定（``configs``、``GET /config/{key}``，來自 ``.env`` 的 ``MODIFY_*``）已由
能力路由取代（``/api/v1/capability-routes``），一併移除。
"""

import logging

from fastapi import APIRouter, Request

from app.services.runtime_config_service import RuntimeConfigService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/config", tags=["Runtime Config"])

_service = RuntimeConfigService()


@router.get("")
async def get_runtime_config(request: Request, check_auth: bool = False) -> dict:
    """Return the backend runtime reconciliation block.

    回傳後端 runtime 對帳資訊。

    Args:
        request: the FastAPI request
            FastAPI 請求物件
        check_auth: with ``?check_auth=true``, send one minimal haiku request to claude-code to
            verify token authentication (uses one tiny subscription request and takes several to
            tens of seconds)
            ``?check_auth=true`` 時對 claude-code 實際打一次最小 haiku 請求驗證 token 認證
            （消耗一次極小的訂閱請求，耗時數秒～數十秒）

    Returns:
        ``runtime`` (llm_label / llm_provider / anki_connect_url / claude_code diagnostics /
        capability_routes / llm_endpoints), for scripts to reconcile and display at startup; see
        plan §3.5
        ``runtime``（llm_label / llm_provider / anki_connect_url / claude_code 診斷 /
        capability_routes / llm_endpoints），供腳本啟動對帳與顯示，詳見計畫 §3.5
    """
    return {
        "runtime": await _service.get_runtime_info(
            request.app.state, check_auth=check_auth
        ),
    }
