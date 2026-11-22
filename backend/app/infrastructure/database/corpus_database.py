"""Async MySQL engine and session management dedicated to the corpus database.

語料庫專用非同步 MySQL 資料庫引擎與 Session 管理模組。

Provides a SQLAlchemy 2.0 AsyncEngine and AsyncSession factory that connect
to the corpus database (MySQL ``fluencytides_corpus``) for NLP retrieval,
example-sentence extraction, and the website's card relations
(``rel_links`` / ``rel_types`` via ``RelationService``) and story
flowchart. Because card relations live here, the website itself requires
this connection, not just the card scripts.

提供專門連接語料庫 (MySQL ``fluencytides_corpus``) 的 SQLAlchemy 2.0 AsyncEngine 與
AsyncSession 工廠，用於 NLP 檢索、例句擷取，以及網站的卡片關聯（``rel_links`` / ``rel_types``，
``RelationService``）與劇情流程圖。因為卡片關聯存在這裡，網站本身也需要這個連線，不只是建卡腳本。

If creating the engine fails at import time, the error is logged and both ``corpus_engine`` and
``corpus_async_session_factory`` are ``None``.

若匯入時建立引擎失敗，會記錄錯誤，且 ``corpus_engine`` 與 ``corpus_async_session_factory`` 皆為 ``None``。

The website's own settings (``cfg_capability_routes``) are stored here as well, so this is the website's only
database; it no longer uses SQLite.

網站自己的設定（``cfg_capability_routes``）也存在這裡，所以這是網站唯一的資料庫，已不再使用 SQLite。
"""

import logging
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel

from app.core.config import settings

logger = logging.getLogger(__name__)

# 建立語料庫專用非同步 MySQL 引擎
# - echo: 僅在 DEBUG 模式下輸出 SQL 日誌
# - pool_pre_ping: 每次取連線前先 ping，防止 MySQL wait_timeout 斷線問題
#   (MySQL Server Has Gone Away)
# - pool_recycle: 定期回收連線，適配 MySQL 預設 8 小時 timeout
try:
    corpus_engine = create_async_engine(
        settings.mysql_async_url,
        echo=(settings.LOG_LEVEL.upper() == "DEBUG"),
        pool_pre_ping=True,
        pool_recycle=3600,
    )
except Exception as e:
    logger.error(f"建立 MySQL 語料庫連線引擎失敗: {e}")
    corpus_engine = None

# 建立 Session 工廠
if corpus_engine:
    corpus_async_session_factory = async_sessionmaker(
        corpus_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
else:
    corpus_async_session_factory = None


async def dispose_corpus_engine() -> None:
    """Dispose all connection-pool resources held by the corpus database engine.

    釋放語料庫資料庫引擎的所有連線池資源。

    Should be called at application shutdown (lifespan shutdown). Does nothing if the engine was never created.

    應在應用程式關閉時呼叫（lifespan shutdown）。引擎未建立時不做任何事。
    """
    if corpus_engine:
        await corpus_engine.dispose()
        logger.info("語料庫資料庫引擎 (MySQL) 連線池已釋放。")


def require_corpus_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the corpus session factory, read at call time so tests can replace the module attribute.

    回傳語料庫的 session 工廠；每次呼叫才讀模組屬性，測試替換它即可生效。

    Raises:
        ConnectionError: if the corpus connection is not configured or failed to initialize; it is an ``OSError``,
            so callers that treat database failures as unavailable (the capability routes) handle it the same way
            若語料庫連線沒有設定或初始化失敗；它是 ``OSError``，把資料庫故障當成讀不到的呼叫端（能力路由）會一樣處理
    """
    if corpus_async_session_factory is None:
        raise ConnectionError("MySQL 語料庫連線尚未設定或初始化失敗。請檢查 .env 中的 MYSQL_* 設定。")
    return corpus_async_session_factory


async def get_corpus_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an AsyncSession of the corpus database for FastAPI dependency injection.

    FastAPI 依賴注入用的 AsyncSession 生成器 (針對語料庫)。

    Each HTTP request gets its own session, which is closed automatically
    when the request ends, preventing connection leaks.

    每個 HTTP 請求取得一個獨立的 Session，
    請求結束後自動關閉，確保不會洩漏連線。

    Yields:
        the async database session
        非同步資料庫 Session

    Raises:
        RuntimeError: if the corpus connection is not configured or failed to initialize
            若語料庫連線尚未設定或初始化失敗
    """
    if not corpus_async_session_factory:
        raise RuntimeError("MySQL 語料庫連線尚未設定或初始化失敗。請檢查 .env 中的 MYSQL_* 設定。")

    async with corpus_async_session_factory() as session:
        yield session
