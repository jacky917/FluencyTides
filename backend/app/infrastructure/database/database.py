"""
非同步資料庫引擎與 Session 管理模組。

提供 SQLAlchemy 2.0 AsyncEngine 與 AsyncSession 工廠，
供 FastAPI 依賴注入使用。

設計決策：
- 使用 SQLAlchemy 2.0 的原生 async engine（而非 SQLModel 的同步 engine），
  因為 FluencyTides 是全非同步架構（FastAPI + httpx + aiogram），
  同步 I/O 會阻塞事件循環。
- Session 使用 expire_on_commit=False，避免在 commit 後存取屬性時
  觸發隱式的 lazy load（在 async 環境中 lazy load 會直接報錯）。

MySQL 相容性注意事項：
- 此模組不包含任何 SQLite 特有的連線參數。
- DATABASE_URL 從 Settings 讀取，切換 MySQL 只需改 .env。
- pool_pre_ping=True 在 MySQL 長連線場景下可防止
  'MySQL server has gone away' 錯誤。
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

# 必須在 engine 建立前 import conventions，確保 MetaData 已被覆蓋
import app.infrastructure.database.conventions  # noqa: F401

logger = logging.getLogger(__name__)

# 建立非同步引擎
# - echo: 僅在 DEBUG 模式下輸出 SQL 日誌
# - pool_pre_ping: 每次取連線前先 ping，避免使用已斷線的連線
#   （對 MySQL 長連線場景尤其重要）
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=(settings.LOG_LEVEL.upper() == "DEBUG"),
    pool_pre_ping=True,
)

# 建立 Session 工廠
# - expire_on_commit=False: 防止 async 環境中 commit 後 lazy load 報錯
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def create_db_and_tables() -> None:
    """建立所有 SQLModel 定義的資料表。

    僅在開發環境或首次啟動時使用。
    生產環境應透過 Alembic migration 管理 schema。

    注意：此函數依賴 conventions.py 中的 MetaData 設定，
    確保建表時所有約束名稱遵循顯式命名規範。
    """
    # 必須在此處 import models，確保所有 Table Model 已註冊到 metadata
    import app.infrastructure.database.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    logger.info("資料庫表結構建立完成。")


async def dispose_engine() -> None:
    """釋放資料庫引擎的所有連線池資源。

    應在應用程式關閉時呼叫（lifespan shutdown）。
    """
    await engine.dispose()
    logger.info("資料庫引擎連線池已釋放。")


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依賴注入用的 AsyncSession 生成器。

    每個 HTTP 請求取得一個獨立的 Session，
    請求結束後自動關閉，確保不會洩漏連線。

    Yields:
        AsyncSession: 非同步資料庫 Session。
    """
    async with async_session_factory() as session:
        yield session
