"""劇情流程圖讀語料庫用的 Session 工廠與 ``STORY_*`` 設定。

Session factory and ``STORY_*`` settings of the story flowchart.

劇情流程圖和其他功能一樣讀 ``MYSQL_DATABASE``，共用 :mod:`app.infrastructure.database.corpus_database`
的引擎與連線池。資料庫只由 ``.env`` 的 ``MYSQL_DATABASE`` 一個設定決定；語料庫切換期間給本機測試用的
``STORY_CORPUS_DATABASE`` 覆寫已移除（2026-11-14，本機與正式機都已切換）。
The story flowchart reads ``MYSQL_DATABASE`` through the shared corpus
engine; the database is decided by that single setting.

``STORY_*`` 設定（例如 ``STORY_ASSETS_DIR``）不是 ``Settings`` 的欄位：先看程序的環境變數（Docker 的
``env_file`` 會注入），再看 ``backend/.env``（本機直接跑 uvicorn 時 ``.env`` 不會進環境變數）。
``STORY_*`` values are read from the process environment, then from
``backend/.env``, because they are not ``Settings`` fields.
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
    """讀一個 ``STORY_*`` 設定：環境變數優先，其次 ``backend/.env``；空字串視為沒設定。

    Read a ``STORY_*`` setting from the environment, then ``backend/.env``.
    """
    value = os.environ.get(name)
    if value is None:
        value = _dotenv().get(name)
    return value or None


def story_session_factory() -> async_sessionmaker[AsyncSession]:
    """全站共用的語料庫 Session 工廠（``MYSQL_DATABASE``）。The shared corpus session factory.

    Raises:
        RuntimeError: 共用的語料庫引擎沒有建立成功時。When the shared
            corpus engine failed to initialise.
    """
    from app.infrastructure.database.corpus_database import corpus_async_session_factory

    if corpus_async_session_factory is None:
        raise RuntimeError("MySQL 語料庫連線尚未設定或初始化失敗。請檢查 .env 中的 MYSQL_* 設定。")
    return corpus_async_session_factory
