"""Tests for the website's own corpus tables created at startup (``corpus_schema``).

網站啟動時建立自己的語料庫表（``corpus_schema``）的測試。

The DDL is MySQL-only (``COLLATE utf8mb4_bin``, ``ENGINE=InnoDB``), so these tests check the extracted SQL and
use a fake engine instead of SQLite.

建表 SQL 只適用 MySQL（``COLLATE utf8mb4_bin``、``ENGINE=InnoDB``），所以這裡檢查取出的 SQL，並用假的引擎代替
SQLite。
"""

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from unittest import mock

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.exc import OperationalError

from app.infrastructure.database import corpus_schema
from app.infrastructure.database.corpus_schema import SCHEMA_PATH, SITE_TABLES, ensure_site_tables, table_ddl
from app.infrastructure.database.corpus_tables import CFG_CAPABILITY_ROUTES
from app.infrastructure.database.models import CapabilityRouteSetting
from scripts.database.corpus_rebuild import rebuild

ENGINE = "app.infrastructure.database.corpus_database.corpus_engine"


class _FakeEngine:
    """Record the statements run inside ``begin()``; optionally fail on execute.

    記下 ``begin()`` 裡執行的語句；可選擇讓執行失敗。
    """

    def __init__(self, error: Exception | None = None) -> None:
        self.statements: list[str] = []
        self.error = error

    @asynccontextmanager
    async def begin(self):
        engine = self

        class _Conn:
            async def execute(self, statement):
                if engine.error is not None:
                    raise engine.error
                engine.statements.append(str(statement))

        yield _Conn()


def _ddl_columns(ddl: str) -> dict[str, str]:
    """Map each column in the DDL to the rest of its line (type, COLLATE, NULL and so on).

    建表 SQL 的欄位定義：欄位名 → 該行其餘部分（型別、COLLATE、NULL…）。
    """
    columns = {}
    for line in ddl.splitlines():
        if line.strip().startswith(("PRIMARY", "UNIQUE", "KEY", "CONSTRAINT", "CREATE", ")")):
            continue
        match = re.match(r"\s+([a-z_]+)\s+(.*?),?\s*$", line)
        if match:
            columns[match.group(1)] = match.group(2)
    return columns


def test_cfg_table_ddl_comes_from_schema():
    """``cfg_capability_routes`` 的建表 SQL 取自 schema.sql，可重跑，且有唯一鍵。"""
    ddl = table_ddl(SCHEMA_PATH.read_text(encoding="utf-8"), CFG_CAPABILITY_ROUTES)

    assert ddl.startswith(f"CREATE TABLE IF NOT EXISTS {CFG_CAPABILITY_ROUTES} (")
    assert "UNIQUE KEY uk_route_capability (route_key, capability)" in ddl
    assert CapabilityRouteSetting.__tablename__ == CFG_CAPABILITY_ROUTES


def test_cfg_table_columns_match_the_model():
    """schema.sql 與 SQLModel 模型的欄位一致：名稱、型別、字串長度、可否為空。

    只改一邊時，SQLite 上的測試照樣會過、要到 MySQL 實際寫入才失敗，所以在這裡對齊。
    """
    ddl_columns = _ddl_columns(table_ddl(SCHEMA_PATH.read_text(encoding="utf-8"), CFG_CAPABILITY_ROUTES))
    model_columns = {column.name: column for column in CapabilityRouteSetting.__table__.columns}

    assert set(ddl_columns) == set(model_columns)
    for name, column in model_columns.items():
        definition = ddl_columns[name]
        # SQLModel 的 str 欄位是 AutoString（TypeDecorator，不是 String 的子類別），兩種都有 length
        if isinstance(column.type, String) or getattr(column.type, "length", None) is not None:
            expected = f"VARCHAR({column.type.length})"
        elif isinstance(column.type, DateTime):
            expected = "DATETIME"
        elif isinstance(column.type, Integer):
            expected = "INT"
        else:
            raise AssertionError(f"沒有比對規則的型別：{name} {column.type!r}")
        assert definition.split()[0] == expected, (name, definition)
        assert ("NOT NULL" not in definition) == bool(column.nullable), (name, definition, column.nullable)


def test_rebuild_drops_and_protects_the_cfg_table():
    """語料庫重建：會刪掉重建這張表，但表裡有設定時要先擋下（設定沒辦法從舊庫重建）。"""
    assert CFG_CAPABILITY_ROUTES in rebuild.DROP_ORDER
    assert CFG_CAPABILITY_ROUTES in rebuild.PROTECTED_TABLES


def test_ensure_site_tables_runs_if_not_exists_ddl():
    """啟動時對每張網站表執行 ``CREATE TABLE IF NOT EXISTS``。"""
    engine = _FakeEngine()
    with mock.patch(ENGINE, engine):
        assert asyncio.run(ensure_site_tables()) is True

    assert len(engine.statements) == len(SITE_TABLES)
    assert engine.statements[0].startswith(f"CREATE TABLE IF NOT EXISTS {CFG_CAPABILITY_ROUTES} (")


def test_ensure_site_tables_without_corpus_connection(caplog):
    """沒有語料庫連線：記 ERROR、回 False，不中斷啟動。"""
    with mock.patch(ENGINE, None), caplog.at_level(logging.ERROR, logger=corpus_schema.__name__):
        assert asyncio.run(ensure_site_tables()) is False

    assert "MySQL 語料庫連線沒有設定" in caplog.text


def test_ensure_site_tables_database_failure_does_not_raise(caplog):
    """建表時資料庫異常（例如 MySQL 沒開）：記 ERROR 與 traceback、回 False，不中斷啟動。"""
    engine = _FakeEngine(OperationalError("CREATE TABLE ...", {}, Exception("Can't connect to MySQL server")))
    with mock.patch(ENGINE, engine), caplog.at_level(logging.ERROR, logger=corpus_schema.__name__):
        assert asyncio.run(ensure_site_tables()) is False

    records = [r for r in caplog.records if "建立 cfg_capability_routes 失敗" in r.getMessage()]
    assert len(records) == 1 and records[0].exc_info
