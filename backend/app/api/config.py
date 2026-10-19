"""執行期設定 API(唯讀)。

Runtime configuration API (read-only).

對應計畫 docs/archive/runtime_config_service_FEAT_2026-08-29.md §3.5:
- ``GET  /api/v1/config``  後端 runtime 對帳區塊(LLM 標籤、Anki 端點、claude-code 診斷、
  能力路由與端點白名單)

原本的白名單設定(``configs``、``GET /config/{key}``,來自 ``.env`` 的 ``MODIFY_*``)已由
能力路由取代(``/api/v1/capability-routes``),一併移除。
The former ``MODIFY_*`` settings listing was replaced by capability routing.
"""

import logging

from fastapi import APIRouter, Request

from app.services.runtime_config_service import RuntimeConfigService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/config", tags=["Runtime Config"])

_service = RuntimeConfigService()


@router.get("")
async def get_runtime_config(request: Request, check_auth: bool = False) -> dict:
    """後端 runtime 對帳資訊。

    The backend runtime reconciliation block.

    Args:
        request: FastAPI 請求物件。The request object.
        check_auth: ``?check_auth=true`` 時對 claude-code 實際打一次最小
            haiku 請求驗證 token 認證(消耗一次極小的訂閱請求,耗時
            數秒~數十秒)。Fire a minimal auth-verification request when
            true.

    Returns:
        dict: ``runtime``(llm_label / llm_provider / anki_connect_url /
        claude_code 診斷 / capability_routes / llm_endpoints——供腳本啟動對帳與顯示,
        詳見計畫 §3.5)。
    """
    return {
        "runtime": await _service.get_runtime_info(
            request.app.state, check_auth=check_auth
        ),
    }
