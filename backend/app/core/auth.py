"""
API Key 認證模組。

本模組提供基於 HTTP Header 的 API Key 認證機制，
透過 FastAPI 的 Security 依賴注入系統實現。

設計決策：
    - 使用 X-API-Key Header 而非 Bearer Token，是因為此 API
      主要面向內部服務（前端 SPA、Telegram Bot）調用，
      不需要 OAuth2 的完整流程。
    - API Key 從 .env 環境變數讀取，透過 Settings 統一管理。
    - Health Check 端點不受認證保護，確保監控系統可正常運作。
"""

import logging

from fastapi import Security
from fastapi.security import APIKeyHeader

from app.core.config import settings
from app.core.exceptions import AuthenticationError

logger = logging.getLogger(__name__)

# 定義 API Key Header 的名稱與行為
# auto_error=False 讓我們自行處理錯誤，而非讓 FastAPI 回傳預設的 403
_api_key_header = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
    description="API 認證金鑰，從 .env 的 API_SECRET_KEY 取得。",
)


async def verify_api_key(
    api_key: str | None = Security(_api_key_header),
) -> str:
    """驗證 API Key 的有效性。

    此函數作為 FastAPI 的 Depends() 依賴注入使用，
    在受保護的路由被呼叫前自動執行認證檢查。

    Args:
        api_key: 從 X-API-Key Header 中提取的 API Key 值。

    Returns:
        通過驗證的 API Key 字串。

    Raises:
        AuthenticationError: 當 API Key 未提供或與設定不符時。
    """
    if not settings.API_SECRET_KEY:
        # 若 API_SECRET_KEY 未設定，視為開發環境，跳過認證
        # 這允許在本地開發時不需要配置 API Key 即可使用 Swagger UI
        logger.warning(
            "⚠️ API_SECRET_KEY 未設定，跳過認證檢查。"
            "請勿在生產環境使用此配置！"
        )
        return "dev-mode-no-auth"

    if api_key is None:
        logger.warning("收到未攜帶 X-API-Key Header 的請求。")
        raise AuthenticationError(
            "認證失敗：請在 X-API-Key Header 中提供有效的 API Key。"
        )

    if api_key != settings.API_SECRET_KEY:
        logger.warning("收到無效的 API Key。")
        raise AuthenticationError(
            "認證失敗：提供的 API Key 無效。"
        )

    return api_key
