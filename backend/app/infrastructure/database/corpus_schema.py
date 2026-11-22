"""Table DDL taken from ``schema.sql``, and creation of the website's own corpus tables at startup.

從 ``schema.sql`` 取出建表 SQL，以及網站啟動時建立自己在語料庫裡的表。

``backend/scripts/database/corpus_rebuild/schema.sql`` is the single source of the corpus DDL. Tables the card
scripts own are created by ``scripts/common/database/init_db.py``; the tables in :data:`SITE_TABLES` belong to
the website itself (it cannot work without them), so :func:`ensure_site_tables` creates them on every startup.
Both go through :func:`table_ddl`, which turns the statement into ``CREATE TABLE IF NOT EXISTS`` so it can run
again.

``backend/scripts/database/corpus_rebuild/schema.sql`` 是語料庫建表 SQL 的唯一來源。建卡腳本自己的表由
``scripts/common/database/init_db.py`` 建立；:data:`SITE_TABLES` 的表屬於網站本身（沒有它們網站無法運作），
所以 :func:`ensure_site_tables` 每次啟動都會建。兩邊都經過 :func:`table_ddl`，把語句改成
``CREATE TABLE IF NOT EXISTS``，可以重複執行。
"""

import logging
import re
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.infrastructure.database.corpus_tables import CFG_CAPABILITY_ROUTES

logger = logging.getLogger(__name__)

#: The corpus DDL file (the single source of every corpus table's structure).
#: 語料庫的建表 SQL（所有語料庫表結構的唯一來源）。
SCHEMA_PATH = Path(__file__).resolve().parents[3] / "scripts" / "database" / "corpus_rebuild" / "schema.sql"

#: The corpus tables the website owns and creates at startup.
#: 網站自己擁有、啟動時建立的語料庫表。
SITE_TABLES = (CFG_CAPABILITY_ROUTES,)


def table_ddl(schema_sql: str, table: str) -> str:
    """Extract one table's ``CREATE TABLE`` from ``schema.sql`` as ``CREATE TABLE IF NOT EXISTS``.

    從 ``schema.sql`` 取出一張表的 ``CREATE TABLE``，改成 ``CREATE TABLE IF NOT EXISTS``。

    Args:
        schema_sql: the text of ``schema.sql``
            ``schema.sql`` 的內容
        table: the table name
            表名

    Returns:
        the re-runnable DDL
        可重複執行的建表 SQL

    Raises:
        LookupError: if ``schema.sql`` has no ``CREATE TABLE`` for this table
            若 ``schema.sql`` 沒有這張表的 ``CREATE TABLE``
    """
    match = re.search(rf"CREATE TABLE {re.escape(table)} \(.*?\)[^;]*;", schema_sql, re.DOTALL)
    if match is None:
        raise LookupError(f"{SCHEMA_PATH.name} 沒有 {table} 的 CREATE TABLE")
    return match.group(0).replace(f"CREATE TABLE {table} ", f"CREATE TABLE IF NOT EXISTS {table} ", 1)


async def ensure_site_tables() -> bool:
    """Create the website's own corpus tables (:data:`SITE_TABLES`) if they do not exist.

    建立網站自己的語料庫表（:data:`SITE_TABLES`），已存在就略過。

    A database failure only logs an ERROR and does not stop startup: the capability routes then fall back to
    ``.env`` and the settings interface reports the store as unavailable, and the next startup tries again.

    資料庫失敗只記 ERROR、不中斷啟動：能力路由會退回 ``.env``，設定介面回報讀不到，下次啟動再試。

    Returns:
        ``True`` if every table exists afterwards; ``False`` if the corpus connection is not configured or the
        database failed
        之後每張表都存在時為 ``True``；語料庫連線沒有設定或資料庫失敗時為 ``False``

    Raises:
        LookupError: if ``schema.sql`` lacks one of the tables (a programming error, so it is not swallowed)
            若 ``schema.sql`` 缺少其中一張表（程式錯誤，不吞掉）
        OSError: if ``schema.sql`` cannot be read
            若讀不到 ``schema.sql``
    """
    from app.infrastructure.database.corpus_database import corpus_engine

    statements = [table_ddl(SCHEMA_PATH.read_text(encoding="utf-8"), table) for table in SITE_TABLES]
    if corpus_engine is None:
        logger.error("MySQL 語料庫連線沒有設定，無法建立 %s；能力路由會退回 .env。", "、".join(SITE_TABLES))
        return False
    try:
        async with corpus_engine.begin() as conn:
            for statement in statements:
                await conn.execute(text(statement))
    except (DBAPIError, PoolTimeoutError, OSError):
        logger.exception("建立 %s 失敗；能力路由會退回 .env，下次啟動再試。", "、".join(SITE_TABLES))
        return False
    logger.info("語料庫的網站設定表已就緒：%s", "、".join(SITE_TABLES))
    return True
