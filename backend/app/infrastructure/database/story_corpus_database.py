"""劇情流程圖讀語料庫用的 Session 工廠：預設就是全站共用的語料庫引擎。

Session factory used by the story flowchart to read the corpus database.
By default it is the shared corpus engine of
:mod:`app.infrastructure.database.corpus_database` (``MYSQL_DATABASE``).

整個分支和語料庫切換一起上線，所以劇情流程圖和其他功能一樣讀 ``MYSQL_DATABASE``，共用同一個
引擎與連線池。只有本機測試時（例如本機的 ``MYSQL_DATABASE`` 還沒改成新語料庫）可以設定
``STORY_CORPUS_DATABASE`` 明確覆寫資料庫名稱；這時才另建一個引擎（第一次使用時建立，關閉時由
lifespan 呼叫 :func:`dispose_story_engine` 釋放）。
The branch ships together with the corpus cutover, so the story feature
reads ``MYSQL_DATABASE`` through the shared engine. ``STORY_CORPUS_DATABASE``
is only an explicit local-test override; when it names another database a
separate, lazily created engine is used and disposed in the lifespan.

``STORY_*`` 設定不是 ``Settings`` 的欄位：先看程序的環境變數（Docker 的 ``env_file`` 會注入），
再看 ``backend/.env``（本機直接跑 uvicorn 時 ``.env`` 不會進環境變數）。
``STORY_*`` values are read from the process environment, then from
``backend/.env``, because they are not ``Settings`` fields.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parents[3]

_engine: AsyncEngine | None = None
_factory: async_sessionmaker[AsyncSession] | None = None


@lru_cache(maxsize=1)
def _dotenv() -> dict[str, str | None]:
    try:
        from dotenv import dotenv_values
    except ImportError:  # pragma: no cover - python-dotenv 是 pydantic-settings 的相依套件
        return {}
    path = _BACKEND_DIR / ".env"
    return dict(dotenv_values(path)) if path.is_file() else {}


def story_setting(name: str) -> str | None:
    """讀一個 ``STORY_*`` 設定：環境變數優先，其次 ``backend/.env``；空字串視為沒設定。

    Read a ``STORY_*`` setting from the environment, then ``backend/.env``.
    """
    value = os.environ.get(name)
    if value is None:
        value = _dotenv().get(name)
    return value or None


def story_database_override() -> str | None:
    """本機測試用的資料庫覆寫（``STORY_CORPUS_DATABASE``）；和 ``MYSQL_DATABASE`` 相同時視為沒有。

    The explicit local-test override, or None when unset or equal to
    ``MYSQL_DATABASE``.
    """
    override = story_setting("STORY_CORPUS_DATABASE")
    return override if override and override != settings.MYSQL_DATABASE else None


def story_database_name() -> str:
    """劇情流程圖實際讀的資料庫名稱。The database the story feature reads."""
    return story_database_override() or settings.MYSQL_DATABASE


def story_database_url() -> str:
    """組 aiomysql 連線字串（帳密、主機沿用 ``MYSQL_*``）。Build the aiomysql URL."""
    user = quote_plus(str(settings.MYSQL_USER))
    password = quote_plus(str(settings.MYSQL_PASSWORD))
    return (f"mysql+aiomysql://{user}:{password}@{settings.MYSQL_HOST}:{settings.MYSQL_PORT}/"
            f"{story_database_name()}?charset=utf8mb4")


def story_session_factory() -> async_sessionmaker[AsyncSession]:
    """取得 Session 工廠：沒有覆寫時就是共用的語料庫工廠。Get the session factory.

    Raises:
        RuntimeError: 共用的語料庫引擎沒有建立成功時。When the shared
            corpus engine failed to initialise.
    """
    global _engine, _factory
    if story_database_override() is None:
        from app.infrastructure.database.corpus_database import corpus_async_session_factory

        if corpus_async_session_factory is None:
            raise RuntimeError("MySQL 語料庫連線尚未設定或初始化失敗。請檢查 .env 中的 MYSQL_* 設定。")
        return corpus_async_session_factory
    if _factory is None:
        _engine = create_async_engine(story_database_url(), pool_pre_ping=True, pool_recycle=3600)
        _factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)
        logger.warning("STORY_CORPUS_DATABASE=%s：劇情流程圖改用獨立的語料庫引擎（僅供本機測試）。",
                       story_database_name())
    return _factory


async def dispose_story_engine() -> None:
    """釋放覆寫用的獨立引擎（沒有建立時什麼都不做）。Dispose the override engine, if any."""
    global _engine, _factory
    if _engine is not None:
        await _engine.dispose()
    _engine, _factory = None, None
