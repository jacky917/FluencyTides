"""
建立建卡管線自己的兩張表：卡片生成紀錄 ``card_gen_log`` 與讀音判斷快取 ``nlp_line_readings``。

Create the two tables owned by the card pipelines: the generation log
``card_gen_log`` and the reading-judgment cache ``nlp_line_readings``.

建表 SQL 直接取自 ``backend/scripts/database/corpus_rebuild/schema.sql``（唯一來源），
加上 ``IF NOT EXISTS`` 後執行，所以可以重跑；兩張表的外鍵指向 ``game_profiles`` 與
``game_lines``，必須在 ``fluencytides_corpus`` 已有遊戲內容表之後執行
（docs/wip/corpus_db_rebuild_FEAT_2026-10-31.md D2、§5）。
舊庫 ``generated_sentences_log`` 的欄位補齊與 unique key 遷移已移除：新表一次建成最終結構。

The DDL is taken from ``schema.sql`` (single source of truth) and run with
``IF NOT EXISTS``, so the script is re-runnable. The foreign keys point at
``game_profiles`` / ``game_lines``, which must already exist. The legacy
column-backfill and unique-key migrations are gone: the new tables are
created in their final shape.
"""

import asyncio
import logging
import re
import sys
from pathlib import Path

from sqlalchemy import text

# 確保 sys.path 包含 backend 根目錄並載入 .env
_backend_dir = Path(__file__).resolve().parents[3]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))
import scripts.common.env  # noqa

from app.infrastructure.database.corpus_database import corpus_async_session_factory, dispose_corpus_engine
from app.infrastructure.database.corpus_tables import CARD_GEN_LOG, NLP_LINE_READINGS

logger = logging.getLogger(__name__)

SCHEMA_PATH = _backend_dir / "scripts" / "database" / "corpus_rebuild" / "schema.sql"

# 本腳本負責建的表（依外鍵順序）。Tables created here, in foreign-key order.
PIPELINE_TABLES = (NLP_LINE_READINGS, CARD_GEN_LOG)


def table_ddl(schema_sql: str, table: str) -> str:
    """從 schema.sql 取出一張表的 ``CREATE TABLE``，改成 ``CREATE TABLE IF NOT EXISTS``。

    Extract one table's ``CREATE TABLE`` from schema.sql as ``IF NOT EXISTS``.

    Args:
        schema_sql: schema.sql 的內容。The schema.sql text.
        table: 表名。Table name.

    Returns:
        str: 可重跑的建表 SQL。The re-runnable DDL.

    Raises:
        LookupError: schema.sql 沒有這張表。When the table is not in schema.sql.
    """
    match = re.search(rf"CREATE TABLE {re.escape(table)} \(.*?\)[^;]*;", schema_sql, re.DOTALL)
    if match is None:
        raise LookupError(f"{SCHEMA_PATH.name} 沒有 {table} 的 CREATE TABLE")
    return match.group(0).replace(f"CREATE TABLE {table} ", f"CREATE TABLE IF NOT EXISTS {table} ", 1)


async def init_pipeline_tables() -> None:
    """建立（或確認存在）``card_gen_log`` 與 ``nlp_line_readings``。

    Create ``card_gen_log`` and ``nlp_line_readings`` if they do not exist,
    then dispose of the corpus database engine.

    失敗時記錄完整 traceback 並往上拋（``__main__`` 以結束碼 1 離開），不讓呼叫端以為建表成功。
    Failures are logged with a traceback and re-raised (exit code 1).

    Raises:
        RuntimeError: 語料庫連線沒有設定。The corpus connection is not configured.
        Exception: 建表失敗（原樣往上拋）。Any DDL failure, re-raised.
    """
    if not corpus_async_session_factory:
        logger.error("❌ 語料庫資料庫連線尚未初始化，請檢查 .env 設定。")
        raise RuntimeError("語料庫資料庫連線尚未初始化")

    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    try:
        async with corpus_async_session_factory() as session:
            for table in PIPELINE_TABLES:
                logger.info(f"🔧 開始檢查/建立 {table} 資料表...")
                await session.execute(text(table_ddl(schema_sql, table)))
                await session.commit()
                logger.info(f"✅ {table} 資料表建立成功 (或已存在)。")
    except Exception:
        logger.exception("❌ 建立資料表時發生錯誤")
        raise
    finally:
        await dispose_corpus_engine()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8')
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(init_pipeline_tables())
    except Exception:
        # 錯誤已在 init_pipeline_tables 記錄 / already logged
        sys.exit(1)
