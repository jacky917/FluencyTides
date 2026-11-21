"""Note-type field names read from the repo's definition files, for checking handler declarations.

從 repo 的筆記類型定義檔讀欄位名，給註冊時檢查 handler 宣告的欄位用（handlers 計畫書 D12）。

The registry uses these names to check the fields a handler declares (handlers plan D12). Registration happens
at import time, before AnkiConnect is available, so this reads ``inOrderFields`` from
``app/anki_models/*.json`` instead of asking Anki.

註冊發生在 import 時、還沒有 AnkiConnect，所以讀 ``app/anki_models/*.json`` 的
``inOrderFields``，不向 Anki 查。
"""

import json
from functools import cache
from pathlib import Path

from app.core.config import settings

# backend/（與 core/dependencies.py 的 _ANKI_MODELS_DIR 同一個基準）
_MODELS_DIR = Path(__file__).resolve().parents[3] / settings.ANKI_MODELS_DIR


@cache
def model_field_names(model_name: str) -> tuple[str, ...] | None:
    """Return the field names in the note type's definition file, in order.

    回傳筆記類型定義檔裡的欄位名（依順序）。

    Results are cached per ``model_name``.

    結果依 ``model_name`` 快取。

    Args:
        model_name: the note type name
            筆記類型名稱

    Returns:
        the field names, or ``None`` when the repo has no definition file for this note type
        欄位名；repo 沒有這個筆記類型的定義檔時為 ``None``
    """
    path = _MODELS_DIR / f"{model_name}.json"
    if not path.is_file():
        return None
    return tuple(json.loads(path.read_text(encoding="utf-8"))["inOrderFields"])
