"""應用程式的「今天」與「現在」：依 ``.env`` 的 ``APP_TIMEZONE``（預設 ``Asia/Tokyo``）。

The application's "today" and "now", in ``settings.APP_TIMEZONE``.

不依賴伺服器或容器的時區（``date.today()``）：本機開發不經 docker 時電腦的時區不一定相同，
寫在設定裡兩邊一致，測試也能固定時區（人脈卡登錄日計畫書 §4.7）。

Independent of the host's time zone, so local development and the
container agree.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.core.config import settings


def app_timezone() -> ZoneInfo:
    """設定的時區。The configured time zone."""
    return ZoneInfo(settings.APP_TIMEZONE)


def now() -> datetime:
    """現在（設定的時區，帶時區資訊）。Now, timezone-aware."""
    return datetime.now(app_timezone())


def today() -> date:
    """今天（設定的時區）。Today in the configured time zone."""
    return now().date()


def today_iso() -> str:
    """今天的 ``YYYY-MM-DD``。Today as ``YYYY-MM-DD``."""
    return today().isoformat()
