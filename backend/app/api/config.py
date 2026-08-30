"""執行期設定 API(唯讀切片)。

Runtime configuration API (read-only slice).

對應計畫 docs/wip/runtime_config_service_FEAT_2026-08-29.md §3.5:
- ``GET  /api/v1/config``        全部白名單設定 + runtime 對帳區塊
- ``GET  /api/v1/config/{key}``  單一設定(白名單外一律 404,防鍵名探測)
寫入側 ``PUT /config/{key}`` 依計畫後續補上。
The write side (PUT) follows in a later slice per the plan.
"""

import logging
from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request

from app.services.runtime_config_service import RuntimeConfigService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/config", tags=["Runtime Config"])

_service = RuntimeConfigService()


@router.get("")
async def list_configs(request: Request) -> dict:
    """列出全部可動態修改的設定與後端 runtime 對帳資訊。

    List every dynamically modifiable setting plus the backend runtime
    reconciliation block.

    Returns:
        dict: ``configs``(白名單設定,含當前值/選項/是否觸發重建)與
        ``runtime``(llm_label / llm_provider / anki_connect_url——
        供腳本啟動對帳與顯示,詳見計畫 §3.5)。
    """
    return {
        "configs": [asdict(entry) for entry in _service.list_configs()],
        "runtime": await _service.get_runtime_info(request.app.state),
    }


@router.get("/{key}")
async def get_config(key: str) -> dict:
    """讀取單一白名單設定。

    Read one whitelisted setting.

    Args:
        key: 設定鍵名。Setting key.

    Returns:
        dict: 該設定的 key / current_value / options / requires_rebuild。

    Raises:
        HTTPException: 404——key 不在白名單(不區分「存在但不可讀」,
            避免以此 API 探測 settings 鍵名)。
    """
    entry = _service.get_config(key)
    if entry is None:
        raise HTTPException(
            status_code=404, detail=f"設定 '{key}' 不允許透過此介面存取"
        )
    return asdict(entry)
