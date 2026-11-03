"""劇情流程圖腳本用的語料庫連線（同步 pymysql）。

Synchronous connection to the corpus database for the story scripts.

連線設定取自 ``backend/.env`` 的 ``MYSQL_HOST``/``PORT``/``USER``/``PASSWORD``/``DATABASE``（環境變數
優先），和後端 ``settings.MYSQL_DATABASE`` 是同一個值；整個分支和語料庫切換一起上線，所以預設就讀
``MYSQL_DATABASE``。切換前要對新語料庫跑時，用腳本的 ``--database fluencytides_corpus`` 明確指定。
這裡直接讀 ``backend/.env`` 而不是 ``settings``，是因為 ``settings`` 依目前目錄找 ``.env``，
而腳本通常從專案根目錄執行。

Settings come from ``backend/.env`` (environment first), i.e. the same
``MYSQL_DATABASE`` the backend uses; pass ``--database`` to target another
database before cutover. ``backend/.env`` is read directly because
``settings`` resolves ``.env`` relative to the working directory.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pymysql
from dotenv import dotenv_values

BACKEND_DIR = Path(__file__).resolve().parents[3]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

#: ``.env`` 沒有 ``MYSQL_DATABASE`` 時的預設（和 ``Settings.MYSQL_DATABASE`` 的預設相同）。Fallback name.
DEFAULT_DATABASE = "fluencytides_corpus"


def _env() -> dict[str, str | None]:
    return {**dotenv_values(BACKEND_DIR / ".env"), **os.environ}


def database_name() -> str:
    """預設的語料庫名稱：``MYSQL_DATABASE``。The default corpus database name."""
    return _env().get("MYSQL_DATABASE") or DEFAULT_DATABASE


def connect(database: str | None = None, autocommit: bool = False) -> pymysql.connections.Connection:
    """開一條到語料庫的連線（DictCursor）。Open a DictCursor connection to the corpus.

    Args:
        database: 資料庫名稱；省略時用 ``MYSQL_DATABASE``。Database name, default ``MYSQL_DATABASE``.
        autocommit: 是否自動提交。Autocommit mode.
    """
    env = _env()
    return pymysql.connect(
        host=env.get("MYSQL_HOST") or "127.0.0.1",
        port=int(env.get("MYSQL_PORT") or 3306),
        user=env.get("MYSQL_USER") or "root",
        password=env.get("MYSQL_PASSWORD") or "",
        database=database or database_name(),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=autocommit,
    )
