"""
pytest 共用設定。Shared pytest configuration.

- 確保 backend 根目錄在 sys.path 中
- 預設隔離 LLM 路由：不讀開發機 .env 的自訂端點、不讀真實 DB
- 共用 fixture：``llm_route_db``（暫存 ``capability_routes`` 表）、``llm_whitelist``（常用白名單）、
  ``declare_route_key``（測試期間宣告用途鍵）

Ensures the backend root is on sys.path, isolates LLM routing from the
developer's .env and database by default, and provides shared fixtures for
route tests.
"""

import asyncio
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

import pytest

# tests/ 的上一層即為 backend/ 根目錄
# The parent of tests/ is the backend/ root.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.infrastructure.llm import routing as _routing  # noqa: E402 - 須在 sys.path 設定之後

# _no_real_llm_routes_db 會把 _fetch_choices 換成空結果；在任何 patch 之前留一份原函式給
# 需要真表的 fixture
_REAL_FETCH_CHOICES = _routing._fetch_choices


@pytest.fixture(autouse=True)
def _no_local_llm_endpoints():
    """忽略開發機 .env 裡的自訂端點與 CLI 白名單；需要的測試自行覆寫。

    ``create_llm_client()`` 會先看 ``LLM_PROVIDER`` 是否指向 ``LLM_ENDPOINTS``；不隔離的話，
    本機 .env 多寫一組端點就會讓只 patch 了 ``LLM_PROVIDER`` 的測試改走端點路徑。

    Ignore the developer's own endpoint settings so results never depend on
    the local .env.
    """
    from app.core.config import settings

    with mock.patch.object(settings, "LLM_ENDPOINTS", {}), \
         mock.patch.object(settings, "LLM_CLAUDE_CODE_MODELS", ()), \
         mock.patch.object(settings, "LLM_ANTIGRAVITY_MODELS", ()):
        yield


@pytest.fixture(autouse=True)
def _no_real_llm_routes_db():
    """LLM 路由預設讀到「沒有任何設定」，不碰 .env 的 DATABASE_URL。

    handler 或服務層的測試走到 ``await XXX_LLM.client()`` 時，否則會讀開發機的真實 DB。
    一般需要真表的測試用 :func:`llm_route_db`；記憶體 SQLite、缺表等特殊情境見
    ``test_llm_routing.py``。

    By default routing sees no stored settings and never opens the real
    database.
    """
    with mock.patch.object(_routing, "_fetch_choices", mock.AsyncMock(return_value={})):
        _routing.invalidate_cache()
        _routing._db_failing = False
        yield
        _routing.invalidate_cache()
        _routing._db_failing = False


@pytest.fixture
def llm_route_db(tmp_path):
    """建一個只有 ``capability_routes`` 表的暫存 SQLite，讓路由與寫入服務都讀寫它。

    以 ``NullPool`` 每次操作都開新連線，可跨 ``asyncio.run`` 與 TestClient 的事件迴圈使用。

    A temporary SQLite database holding only ``capability_routes``, used by both the
    resolver and the write service; ``NullPool`` lets it span event loops.

    Yields:
        session 工廠。The session factory.
    """
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from sqlmodel import SQLModel

    from app.infrastructure.database.models import CapabilityRouteSetting

    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'routes.db').as_posix()}", poolclass=NullPool)

    async def create_table() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: SQLModel.metadata.create_all(sync_conn, tables=[CapabilityRouteSetting.__table__])
            )

    asyncio.run(create_table())
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    with mock.patch("app.infrastructure.database.database.async_session_factory", session_factory), \
         mock.patch.object(_routing, "_fetch_choices", _REAL_FETCH_CHOICES):
        _routing.invalidate_cache()
        yield session_factory
    asyncio.run(engine.dispose())


@pytest.fixture
def llm_whitelist(tmp_path):
    """常用的 .env 白名單：舊式 google ＋中轉站 yinli（m1, m2）＋ claude-code（opus@low|high、sonnet@high、haiku）。

    CLI 的工作目錄指到暫存目錄、關閉審計，執行檔路徑以假值取代，讓「試建客戶端」在測試中
    可以成功。

    A typical whitelist with the CLI paths faked so trial builds succeed.
    """
    from pydantic import SecretStr

    from app.core.config import LLMEndpointSettings, settings
    from app.infrastructure.llm.claude_code_client import ClaudeCodeLLMClient

    values = {
        "LLM_PROVIDER": "google",
        "LLM_MODEL_NAME": "gemini-3.5-flash",
        "LLM_API_KEY": "legacy-key",
        "LLM_BASE_URL": "https://legacy.example/v1",
        "LLM_ENDPOINTS": {
            "yinli": LLMEndpointSettings(
                base_url="https://yinli.example/v1", api_key=SecretStr("sk-yinli-secret"), models=("m1", "m2")
            ),
        },
        "LLM_CLAUDE_CODE_MODELS": ("opus@low|high", "sonnet@high", "haiku"),
        "LLM_ANTIGRAVITY_MODELS": (),
        "LLM_CLAUDE_CODE_EFFORT": "high",
        "LLM_CLAUDE_CODE_OAUTH_TOKEN": "",
        "LLM_CLAUDE_CODE_WORKDIR": str(tmp_path / "cc"),
        "LLM_CLAUDE_CODE_AUDIT_DIR": "",
    }
    with ExitStack() as stack:
        for name, value in values.items():
            stack.enter_context(mock.patch.object(settings, name, value))
        stack.enter_context(mock.patch.object(ClaudeCodeLLMClient, "_resolve_cli_path", return_value="/fake/claude"))
        yield


@pytest.fixture
def declare_route_key():
    """在測試期間宣告用途鍵，結束時移除（不動正式模組宣告的鍵）。

    Declare route keys for the duration of a test.

    Yields:
        ``declare(key, description)`` 函式。A function declaring a key.
    """
    added: list[str] = []

    def declare(key: str, description: str = "測試用途"):
        route = _routing.route(key, description, {"llm"})
        added.append(key)
        return route

    yield declare
    for key in added:
        _routing._DECLARED.pop(key, None)
