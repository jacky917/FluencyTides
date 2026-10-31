"""
從 repo 的筆記類型定義檔讀欄位名，給註冊時檢查 handler 宣告的欄位用（handlers 計畫書 D12）。

Reads note-type field names from the repo's model definition files, so the
registry can check the fields a handler declares.

註冊發生在 import 時、還沒有 AnkiConnect，所以讀 ``app/anki_models/*.json`` 的
``inOrderFields``，不向 Anki 查。

Registration happens at import time without AnkiConnect, so this reads the
definition files instead of asking Anki.
"""

import json
from functools import cache
from pathlib import Path

from app.core.config import settings

# backend/（與 core/dependencies.py 的 _ANKI_MODELS_DIR 同一個基準）
_MODELS_DIR = Path(__file__).resolve().parents[3] / settings.ANKI_MODELS_DIR


@cache
def model_field_names(model_name: str) -> tuple[str, ...] | None:
    """筆記類型定義檔裡的欄位名（依順序）。The note type's field names, in order.

    Args:
        model_name: 筆記類型名稱。The note type.

    Returns:
        欄位名；repo 沒有這個筆記類型的定義檔時為 None。None when there is no
        definition file for it.
    """
    path = _MODELS_DIR / f"{model_name}.json"
    if not path.is_file():
        return None
    return tuple(json.loads(path.read_text(encoding="utf-8"))["inOrderFields"])
