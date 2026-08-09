"""腳本共用環境初始化模組：定位 backend 根目錄、載入 .env 並設定 sys.path。

Shared environment bootstrap for scripts: locates the backend root
directory, loads the .env file, and configures ``sys.path``.
"""

import sys
from pathlib import Path
from dotenv import load_dotenv
import logging

logger = logging.getLogger(__name__)

def init_env():
    """
    尋找專案根目錄 (backend)，載入 .env 並設定 sys.path。
    在其他模組 (如 app.*) 被 import 之前呼叫此函數，
    確保環境變數（特別是資料庫連線等設定）正確載入。

    Locate the project root directory (backend), load the .env file, and
    configure ``sys.path``. Call this before importing other modules
    (e.g. ``app.*``) so environment variables, especially database
    connection settings, are loaded correctly.
    """
    current = Path(__file__).resolve()
    backend_dir = None
    
    # 向上尋找名為 'backend' 或包含 'app' 資料夾的目錄
    for parent in current.parents:
        if parent.name == "backend" or (parent / "app").exists():
            backend_dir = parent
            break
            
    if backend_dir:
        # 將 backend 目錄優先加入 sys.path
        if str(backend_dir) not in sys.path:
            sys.path.insert(0, str(backend_dir))
            
        env_path = backend_dir / ".env"
        if env_path.exists():
            load_dotenv(dotenv_path=env_path)
            # 只有在非預設 logger 級別下才印出，保持乾淨
            # logger.info(f"Loaded environment variables from {env_path}")
        else:
            print(f"[Warning] .env file not found at {env_path}")
    else:
        print("[Warning] Could not find backend root directory.")

# 當模組被匯入時自動執行
init_env()
