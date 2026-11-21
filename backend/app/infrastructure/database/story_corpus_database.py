"""Session factory for the story flowchart's corpus reads, and the ``STORY_*`` settings.

劇情流程圖讀語料庫用的 Session 工廠與 ``STORY_*`` 設定。

Like every other feature, the story flowchart reads ``MYSQL_DATABASE``, sharing the engine and connection pool of
:mod:`app.infrastructure.database.corpus_database`. The database is decided only by the single ``MYSQL_DATABASE``
setting in ``.env``; the ``STORY_CORPUS_DATABASE`` override used for local testing during the corpus switch has
been removed (2026-11-14, after both the local and production machines switched).

劇情流程圖和其他功能一樣讀 ``MYSQL_DATABASE``，共用 ``app.infrastructure.database.corpus_database``
的引擎與連線池。資料庫只由 ``.env`` 的 ``MYSQL_DATABASE`` 一個設定決定；語料庫切換期間給本機測試用的
``STORY_CORPUS_DATABASE`` 覆寫已移除（2026-11-14，本機與正式機都已切換）。

``STORY_*`` settings (for example ``STORY_ASSETS_DIR``) are not ``Settings`` fields: they are read first from the
process environment (injected by Docker's ``env_file``), then from ``backend/.env`` (when running uvicorn directly
on the local machine, ``.env`` does not enter the environment).

``STORY_*`` 設定（例如 ``STORY_ASSETS_DIR``）不是 ``Settings`` 的欄位：先看程序的環境變數（Docker 的
``env_file`` 會注入），再看 ``backend/.env``（本機直接跑 uvicorn 時 ``.env`` 不會進環境變數）。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_BACKEND_DIR = Path(__file__).resolve().parents[3]


@lru_cache(maxsize=1)
def _dotenv() -> dict[str, str | None]:
    try:
        from dotenv import dotenv_values
    except ImportError:  # pragma: no cover - python-dotenv 是 pydantic-settings 的相依套件
        return {}
    path = _BACKEND_DIR / ".env"
    return dict(dotenv_values(path)) if path.is_file() else {}


def story_setting(name: str) -> str | None:
    """Read one ``STORY_*`` setting from the environment first, then ``backend/.env``.

    讀一個 ``STORY_*`` 設定：環境變數優先，其次 ``backend/.env``。

    An empty string counts as not set.

    空字串視為沒設定。

    Args:
        name: the setting name, for example ``"STORY_ASSETS_DIR"``
            設定名稱，例如 ``"STORY_ASSETS_DIR"``

    Returns:
        the setting value, or ``None`` if it is not set or empty
        設定值；沒設定或為空字串時為 ``None``
    """
    value = os.environ.get(name)
    if value is None:
        value = _dotenv().get(name)
    return value or None


def story_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the site-wide shared corpus session factory (``MYSQL_DATABASE``).

    回傳全站共用的語料庫 Session 工廠（``MYSQL_DATABASE``）。

    Returns:
        the shared ``corpus_async_session_factory``
        共用的 ``corpus_async_session_factory``

    Raises:
        RuntimeError: if the shared corpus engine failed to initialize
            若共用的語料庫引擎沒有建立成功
    """
    from app.infrastructure.database.corpus_database import corpus_async_session_factory

    if corpus_async_session_factory is None:
        raise RuntimeError("MySQL 語料庫連線尚未設定或初始化失敗。請檢查 .env 中的 MYSQL_* 設定。")
    return corpus_async_session_factory
