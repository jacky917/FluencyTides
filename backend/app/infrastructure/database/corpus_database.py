"""
語料庫專用非同步 MySQL 資料庫引擎與 Session 管理模組。

Async MySQL engine and session management dedicated to the corpus database.

提供專門連接語料庫 (MySQL) 的 SQLAlchemy 2.0 AsyncEngine 與 AsyncSession 工廠，
用於 NLP 檢索與例句擷取。

Provides a SQLAlchemy 2.0 AsyncEngine and AsyncSession factory that connect
to the corpus database (MySQL) for NLP retrieval and example-sentence
extraction.

與主應用程式的 database.py (SQLite) 分開，確保兩者的連線池與資源互不干擾。

Kept separate from the main application's database.py (SQLite) so the two
connection pools and resources never interfere with each other.
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
    """釋放語料庫資料庫引擎的所有連線池資源。

    Dispose all connection-pool resources held by the corpus database engine.

    應在應用程式關閉時呼叫（lifespan shutdown）。

    Should be called at application shutdown (lifespan shutdown).
    """
    if corpus_engine:
        await corpus_engine.dispose()
        logger.info("語料庫資料庫引擎 (MySQL) 連線池已釋放。")


async def get_corpus_async_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依賴注入用的 AsyncSession 生成器 (針對語料庫)。

    AsyncSession generator for FastAPI dependency injection (corpus database).

    每個 HTTP 請求取得一個獨立的 Session，
    請求結束後自動關閉，確保不會洩漏連線。

    Each HTTP request gets its own session, which is closed automatically
    when the request ends, preventing connection leaks.

    Yields:
        AsyncSession: 非同步資料庫 Session。The async database session.

    Raises:
        RuntimeError: 語料庫連線尚未設定或初始化失敗時。Raised when the
            corpus connection is not configured or failed to initialize.
    """
    if not corpus_async_session_factory:
        raise RuntimeError("MySQL 語料庫連線尚未設定或初始化失敗。請檢查 .env 中的 MYSQL_* 設定。")

    async with corpus_async_session_factory() as session:
        yield session
