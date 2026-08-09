"""
pytest 共用設定：確保 backend 根目錄在 sys.path 中。

Shared pytest configuration: ensures the backend root is on sys.path.
"""

import sys
from pathlib import Path

# tests/ 的上一層即為 backend/ 根目錄
# The parent of tests/ is the backend/ root.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
