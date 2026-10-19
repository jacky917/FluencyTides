"""llm_routes → capability_routes 遷移的測試（能力路由計畫 R1）。

Tests for the migration that moves LLM route settings into
``capability_routes``. They reproduce the deployed states: startup's
``create_all`` usually creates the new (empty) table before the migration
runs, and an older database may already hold ``llm_routes`` although alembic
is still at the previous revision.
"""

import asyncio
import sqlite3
from pathlib import Path
from unittest import mock

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

from app.core.config import settings
from app.infrastructure.database import database as database_module
from app.infrastructure.database.models import CapabilityRouteSetting

_BACKEND_DIR = Path(__file__).resolve().parent.parent

# P1～P2 的 llm_routes（a01287171364 建立的形狀）
_OLD_TABLE = """
CREATE TABLE llm_routes (
    id INTEGER NOT NULL, route_key VARCHAR(64) NOT NULL, endpoint VARCHAR(32) NOT NULL,
    model VARCHAR(128) NOT NULL, effort VARCHAR(16), updated_by VARCHAR(64) NOT NULL,
    created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
    updated_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
    CONSTRAINT pk_llm_routes PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_llm_routes_route_key ON llm_routes (route_key);
INSERT INTO llm_routes (route_key, endpoint, model, effort, updated_by) VALUES
    ('*', 'yinli', 'm1', NULL, 'tg:1'),
    ('jp_verb_pair', 'claude-code', 'opus', 'high', 'api');
"""


@pytest.fixture
def database(tmp_path):
    """一個只有舊 llm_routes 的 SQLite 檔，與指向它的 alembic 設定。

    alembic 設定不讀 alembic.ini：``env.py`` 在有設定檔時會呼叫 ``fileConfig``，那會停用既有的
    logger，影響其他測試的 log 斷言。

    Yields:
        ``(資料庫路徑, alembic Config)``。
    """
    path = tmp_path / "routes.db"
    connection = sqlite3.connect(path)
    connection.executescript(_OLD_TABLE)
    connection.close()
    config = Config()
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    with mock.patch.object(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{path.as_posix()}"):
        yield path, config


def _create_new_table(path: Path) -> None:
    """模擬啟動時的 create_all：建立空的 capability_routes。"""

    async def create() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{path.as_posix()}")
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: SQLModel.metadata.create_all(sync_conn, tables=[CapabilityRouteSetting.__table__])
            )
        await engine.dispose()

    asyncio.run(create())


def _tables(path: Path) -> set[str]:
    with sqlite3.connect(path) as connection:
        return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _rows(path: Path, sql: str) -> list[tuple]:
    with sqlite3.connect(path) as connection:
        return connection.execute(sql).fetchall()


def test_upgrade_moves_rows_after_create_all_and_is_repeatable(database):
    """部署後的常見狀態：啟動時已建好空的新表，遷移照樣把舊設定搬過去、刪掉舊表；重跑沒有副作用。"""
    path, config = database
    command.stamp(config, "a01287171364")
    _create_new_table(path)

    command.upgrade(config, "head")
    command.upgrade(config, "head")

    assert "llm_routes" not in _tables(path)
    assert _rows(path, "SELECT route_key, capability, provider, model, effort, updated_by FROM capability_routes "
                       "ORDER BY route_key") == [
        ("*", "llm", "yinli", "m1", None, "tg:1"),
        ("jp_verb_pair", "llm", "claude-code", "opus", "high", "api"),
    ]


def test_upgrade_keeps_rows_already_in_the_new_table(database):
    """遷移前已在新表存過的設定（create_all 之後、跑遷移之前寫的）優先，舊表的同鍵列不覆蓋它。"""
    path, config = database
    command.stamp(config, "a01287171364")
    _create_new_table(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO capability_routes (route_key, capability, provider, model, effort, updated_by, "
            "created_at, updated_at) VALUES ('*', 'llm', 'google', 'newer', NULL, 'api', "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )

    command.upgrade(config, "head")

    assert _rows(path, "SELECT route_key, provider, model FROM capability_routes ORDER BY route_key") == [
        ("*", "google", "newer"),
        ("jp_verb_pair", "claude-code", "opus"),
    ]


def test_startup_reports_llm_routes_until_migrated(database, caplog):
    """沒跑遷移就啟動：create_all 照常建表，並記 ERROR 提示要執行 alembic；遷移後不再提示。"""
    path, config = database
    engine = create_async_engine(f"sqlite+aiosqlite:///{path.as_posix()}")

    async def start() -> None:
        with mock.patch.object(database_module, "engine", engine):
            await database_module.create_db_and_tables()

    try:
        with caplog.at_level("ERROR", logger=database_module.logger.name):
            asyncio.run(start())
        assert "capability_routes" in _tables(path)
        assert any("llm_routes" in record.getMessage() and "alembic upgrade head" in record.getMessage()
                   for record in caplog.records)

        command.stamp(config, "a01287171364")
        command.upgrade(config, "head")
        caplog.clear()
        with caplog.at_level("ERROR", logger=database_module.logger.name):
            asyncio.run(start())
        assert not caplog.records
    finally:
        asyncio.run(engine.dispose())


def test_upgrade_from_the_revision_before_llm_routes(database):
    """舊表是 create_all 建的、alembic 卻還在上一版：建表那一版跳過，設定照樣搬過去。"""
    path, config = database
    command.stamp(config, "9bbc72f7c470")

    command.upgrade(config, "head")

    assert _rows(path, "SELECT route_key, provider FROM capability_routes ORDER BY route_key") == [
        ("*", "yinli"),
        ("jp_verb_pair", "claude-code"),
    ]


def test_downgrade_restores_llm_routes(database):
    """降級把 llm 能力的列搬回舊表。"""
    path, config = database
    command.stamp(config, "a01287171364")
    command.upgrade(config, "head")

    command.downgrade(config, "a01287171364")

    assert "capability_routes" not in _tables(path)
    assert _rows(path, "SELECT route_key, endpoint, model, effort FROM llm_routes ORDER BY route_key") == [
        ("*", "yinli", "m1", None),
        ("jp_verb_pair", "claude-code", "opus", "high"),
    ]
