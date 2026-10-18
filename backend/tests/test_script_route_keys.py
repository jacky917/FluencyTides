"""腳本寫死的 LLM 用途鍵必須是後端宣告的鍵（模型路由計畫 D9）。

Contract test: route keys hard-coded in scripts must be declared by the
backend, or the label lookup silently falls back to the local ``.env``.
"""

import importlib
import re
from pathlib import Path

from app.infrastructure.llm import routing

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_CALL = re.compile(r"build_llm_model_label\(\s*[\"']([^\"']+)[\"']\s*\)")
_DECLARATION = re.compile(r"=\s*llm_route\(")


def _declared_keys() -> set[str]:
    """import 所有宣告用途鍵的後端模組，回傳登記到的鍵。"""
    for path in (_BACKEND_DIR / "app").rglob("*.py"):
        if path.name == "routing.py" or not _DECLARATION.search(path.read_text(encoding="utf-8")):
            continue
        module = ".".join(path.relative_to(_BACKEND_DIR).with_suffix("").parts)
        importlib.import_module(module)
    return {route.key for route in routing.list_route_keys()}


def test_script_route_keys_are_declared_by_the_backend():
    """每支腳本傳給 build_llm_model_label 的用途鍵都是後端宣告的鍵；打錯字或後端改了鍵名時，
    標籤查詢會找不到該用途而默默退回本機 .env（失敗紀錄錯標），所以在這裡擋下。"""
    used: dict[str, list[str]] = {}
    for path in (_BACKEND_DIR / "scripts").rglob("*.py"):
        for key in _CALL.findall(path.read_text(encoding="utf-8")):
            used.setdefault(key, []).append(str(path.relative_to(_BACKEND_DIR)))

    assert used, "找不到任何 build_llm_model_label 呼叫（正規式或呼叫方式改了？）"
    undeclared = {key: paths for key, paths in used.items() if key not in _declared_keys()}
    assert not undeclared, f"腳本使用了後端沒有宣告的用途鍵：{undeclared}"
