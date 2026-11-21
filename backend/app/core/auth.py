"""API key authentication module.

API Key 認證模組。

This module provides an HTTP-header-based API-key authentication mechanism,
implemented via FastAPI's Security dependency-injection system.

本模組提供基於 HTTP Header 的 API Key 認證機制，
透過 FastAPI 的 Security 依賴注入系統實現。

Design decisions:
    - An X-API-Key header is used instead of a Bearer token because this API
      mainly serves internal clients (frontend SPA, Telegram Bot) and does
      not need a full OAuth2 flow.
    - The API key is read from .env and managed centrally via Settings.
    - The health-check endpoint is not protected, so monitoring keeps working.

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
    """Validate the provided API key.

    驗證 API Key 的有效性。

    Used as a FastAPI Depends() dependency; runs the authentication check
    automatically before a protected route is invoked. When
    ``API_SECRET_KEY`` is not set, it is treated as a development
    environment and the check is skipped.

    此函式作為 FastAPI 的 Depends() 依賴注入使用，
    在受保護的路由被呼叫前自動執行認證檢查。
    ``API_SECRET_KEY`` 未設定時視為開發環境，跳過認證。

    Args:
        api_key: the API key value extracted from the X-API-Key header;
            ``None`` if the header is missing
            從 X-API-Key Header 中提取的 API Key 值；沒有該 Header 時為 ``None``

    Returns:
        the validated API key, or ``"dev-mode-no-auth"`` when
        ``API_SECRET_KEY`` is not set
        通過驗證的 API Key 字串；``API_SECRET_KEY`` 未設定時為 ``"dev-mode-no-auth"``

    Raises:
        AuthenticationError: if the API key is missing or does not match
            the settings
            若 API Key 未提供或與設定不符
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
