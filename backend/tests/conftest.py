"""
pytest 共用設定：確保 backend 根目錄在 sys.path 中；預設不讓 LLM 路由讀真實 DB。

Shared pytest configuration: ensures the backend root is on sys.path, and
by default keeps LLM routing away from the real database.
"""

import sys
from pathlib import Path
from unittest import mock

import pytest

# tests/ 的上一層即為 backend/ 根目錄
# The parent of tests/ is the backend/ root.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


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

    handler 或服務層的測試走到 ``await XXX_LLM.client()`` 時，否則會讀開發機的真實
    DB。需要真表的測試自行把 ``routing._fetch_choices`` 換回原函式（見
    ``test_llm_routing.py``）。

    By default routing sees no stored settings and never opens the real
    database; tests needing a real table patch the original fetch back in.
    """
    from app.infrastructure.llm import routing

    with mock.patch.object(routing, "_fetch_choices", mock.AsyncMock(return_value={})):
        routing.invalidate_cache()
        routing._db_failing = False
        yield
        routing.invalidate_cache()
        routing._db_failing = False
