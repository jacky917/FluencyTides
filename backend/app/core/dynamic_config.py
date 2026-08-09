"""
動態配置解析模組。

Dynamic configuration parsing module.

負責讀取 .env 檔案中以 `MODIFY_` 開頭的環境變數，
藉此決定哪些變數允許透過 Telegram 等介面進行動態修改，
以及這些變數的可用選項（若設定為陣列）。

Reads environment variables prefixed with `MODIFY_` from the .env file to
determine which settings may be modified dynamically (e.g. via Telegram) and
their allowed options (when specified as an array).
"""

import ast
from pathlib import Path
from dotenv import dotenv_values

def get_modifiable_configs(env_path: str = ".env") -> dict[str, list[str] | None]:
    """從指定的 .env 檔案讀取允許修改的配置與可選項。

    Read modifiable settings and their allowed options from the given .env.

    規則：
    - 只要鍵以 MODIFY_ 開頭，即代表允許修改 (對應到後面的設定名稱)。
    - 如果值為空白，代表無限制輸入 (None)。
    - 如果值為類似 ['A', 'B'] 的陣列字串，代表限制選項。

    Rules:
    - A key starting with MODIFY_ marks the trailing setting name modifiable.
    - An empty value means unrestricted input (None).
    - An array-like string such as ['A', 'B'] restricts the allowed options.

    Args:
        env_path: .env 檔案的路徑（相對於 backend 根目錄解析）。
            Path to the .env file, resolved relative to the backend root.

    Returns:
        dict: 鍵為允許修改的屬性名 (如 'AUDIO_MODEL_NAME')，
            值為選項列表 (如 ['model-a', 'model-b']) 或 None。
            Keys are modifiable setting names; values are option lists or
            None when unrestricted.
    """
    # 確保路徑是相對專案根目錄
    base_dir = Path(__file__).resolve().parent.parent.parent
    full_path = base_dir / env_path

    # 在 Docker 容器中，.env 會被忽略 (由 .dockerignore 排除)，
    # 但 docker-compose 會將變數注入到 os.environ，所以必須讀取 os.environ。
    import os
    env_vars = dict(os.environ)
    
    # 在本地開發環境中，直接讀取 .env 檔案補充
    if full_path.exists():
        env_vars.update(dotenv_values(full_path) or {})

    modifiable: dict[str, list[str] | None] = {}

    for key, value in env_vars.items():
        if key.startswith("MODIFY_"):
            target_key = key[7:]  # 去除 MODIFY_
            
            if not value or not value.strip():
                modifiable[target_key] = None
            else:
                try:
                    # 嘗試將字串解析為 Python list
                    parsed = ast.literal_eval(value.strip())
                    if isinstance(parsed, list):
                        modifiable[target_key] = [str(x) for x in parsed]
                    else:
                        modifiable[target_key] = None
                except (ValueError, SyntaxError):
                    # 如果解析失敗（可能不是合法的 list 格式），預設為無限制
                    modifiable[target_key] = None

    return modifiable
