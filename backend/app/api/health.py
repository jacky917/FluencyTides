"""Health check API router module.

健康檢查 API 路由模組。
"""

import os

from fastapi import APIRouter

router = APIRouter()

# 映像建置時由 Dockerfile 的 ARG GIT_SHA 寫入；本機開發或未傳入時為 "unknown"。
# CI 的 deploy job 輪詢此值，等它變成本次 commit 才算部署完成。
# Baked in at image build time via the Dockerfile's GIT_SHA build arg;
# "unknown" for local runs. CI's deploy job polls it to confirm the new
# image is actually live.
_UNKNOWN_SHA = "unknown"


@router.get("/health", summary="Health Check")
async def health_check() -> dict[str, str]:
    """Check that the API is running and report the image's git commit.

    檢查 API 是否正常運作，並回報映像對應的 git commit。

    Returns:
        the status dict ``{"status": "ok", "git_sha": "<commit sha or unknown>"}``
        狀態字典 ``{"status": "ok", "git_sha": "<commit sha 或 unknown>"}``
    """
    return {"status": "ok", "git_sha": os.getenv("APP_GIT_SHA") or _UNKNOWN_SHA}
