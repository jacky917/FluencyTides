"""健康檢查 API 路由模組。

Health check API router module.
"""

from fastapi import APIRouter

router = APIRouter()

@router.get("/health", summary="Health Check")
async def health_check() -> dict[str, str]:
    """檢查 API 是否正常運作。

    Check if the API is running correctly.

    Returns:
        狀態字典 {"status": "ok"}。Status dict {"status": "ok"}.
    """
    return {"status": "ok"}
