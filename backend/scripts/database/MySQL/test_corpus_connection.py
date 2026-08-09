"""
語料庫資料庫 (MySQL) 連線測試腳本。

用於測試 app.infrastructure.database.corpus_database 的配置是否正確，
以及能否成功連線至 MySQL 資料庫並執行簡單的查詢。

Corpus database (MySQL) connection test script. Verifies that
app.infrastructure.database.corpus_database is configured correctly and
that a simple query can be executed against MySQL.
"""

import asyncio
import sys
from pathlib import Path

# 確保 sys.path 包含 backend 根目錄並載入 .env
_backend_dir = Path(__file__).resolve().parents[3]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))
import scripts.common.env  # noqa

from sqlalchemy import text
from app.infrastructure.database.corpus_database import corpus_async_session_factory, dispose_corpus_engine
from app.core.config import settings

# 解決 Windows 命令提示字元輸出 Emoji 時的 cp950 編碼錯誤
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

async def main() -> None:
    """執行連線測試查詢並在結束時釋放連線池。

    Run the connection-test query and dispose of the engine pool on
    completion.
    """
    if not corpus_async_session_factory:
        print("❌ 錯誤：corpus_async_session_factory 尚未初始化！")
        print("請檢查 .env 中的 MYSQL_* 設定，確保提供正確的連線資訊。")
        return

    print("🔌 正在嘗試連線至 MySQL 語料庫...")
    print("--------------------------------------------------")
    print(f"📌 連線目標: {settings.MYSQL_HOST}:{settings.MYSQL_PORT}")
    print(f"📌 使用者帳號: {settings.MYSQL_USER}")
    print(f"📌 資料庫名稱: {settings.MYSQL_DATABASE}")
    print("--------------------------------------------------\n")
    
    try:
        async with corpus_async_session_factory() as session:
            # 執行一個簡單的查詢測試連線
            result = await session.execute(text("SELECT DATABASE(), VERSION()"))
            db_name, version = result.fetchone()
            
            print("✅ 連線成功！")
            print(f"   - 資料庫名稱: {db_name}")
            print(f"   - MySQL 版本: {version}")
            
    except Exception as e:
        print(f"❌ 連線失敗: {e}")
        
    finally:
        # 關閉引擎釋放連線池
        print("\n🧹 正在釋放連線池資源...")
        await dispose_corpus_engine()
        print("✅ 資源已釋放。")

if __name__ == "__main__":
    # 在 Windows 上設定正確的 asyncio event loop policy
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
