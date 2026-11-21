"""The application's "today" and "now", in ``APP_TIMEZONE`` from ``.env`` (default ``Asia/Tokyo``).

應用程式的「今天」與「現在」：依 ``.env`` 的 ``APP_TIMEZONE``（預設 ``Asia/Tokyo``）。

Independent of the server's or container's time zone (``date.today()``): when developing locally
without docker, the computer's time zone may differ, so putting it in the settings keeps both sides
consistent and lets tests fix the time zone (person card registration date plan §4.7).

不依賴伺服器或容器的時區（``date.today()``）：本機開發不經 docker 時電腦的時區不一定相同，
寫在設定裡兩邊一致，測試也能固定時區（人脈卡登錄日計畫書 §4.7）。
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.core.config import settings


def app_timezone() -> ZoneInfo:
    """Return the configured time zone.

    回傳設定的時區。

    Returns:
        the time zone named by ``APP_TIMEZONE``
        ``APP_TIMEZONE`` 指定的時區
    """
    return ZoneInfo(settings.APP_TIMEZONE)


def now() -> datetime:
    """Return the current time in the configured time zone.

    回傳現在的時間（設定的時區）。

    Returns:
        the current time, timezone-aware
        現在的時間，帶時區資訊
    """
    return datetime.now(app_timezone())


def today() -> date:
    """Return today's date in the configured time zone.

    回傳今天的日期（設定的時區）。

    Returns:
        today's date
        今天的日期
    """
    return now().date()


def today_iso() -> str:
    """Return today's date in the configured time zone as ``YYYY-MM-DD``.

    回傳今天的 ``YYYY-MM-DD``（設定的時區）。

    Returns:
        today's date in ISO format
        ISO 格式的今天日期
    """
    return today().isoformat()
