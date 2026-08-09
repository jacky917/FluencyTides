"""將 SQL Dump 檔案分塊匯入 MySQL 的批次腳本。

Batch script that imports SQL dump files into MySQL, splitting large
dumps into chunks to stay under the max_allowed_packet limit.
"""

import os
import sys
import glob
import asyncio
from pathlib import Path

# 統一 bootstrap：向上尋找第一個含 app/ 的目錄即為 backend 根，與檔案深度無關；
# 腳本搬移目錄層級時不需再調整 parent 的層數，避免匯入路徑失準。
# Unified bootstrap: walk up the parent chain and take the first directory
# containing app/ as the backend root. Depth-independent, so relocating this
# script never breaks the import path.
_BACKEND_DIR = next(
    p for p in Path(__file__).resolve().parents if (p / "app").is_dir()
)
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import pymysql
from pymysql.constants import CLIENT
from app.core.config import settings

def chunk_sql_statements(filepath, max_chunk_size=2 * 1024 * 1024):
    """
    將大體積的 SQL Dump 拆分成多個小於 max_chunk_size 的區塊，
    以避免超過 MySQL 的 max_allowed_packet 限制（預設通常為 4MB）。

    Split a large SQL dump into chunks smaller than max_chunk_size to
    avoid exceeding MySQL's max_allowed_packet limit (usually 4MB).

    Args:
        filepath: SQL Dump 檔案路徑。Path to the SQL dump file.
        max_chunk_size: 單一區塊的位元組上限。Maximum chunk size in bytes.

    Yields:
        str: 一段以完整語句結尾的 SQL 區塊。A SQL chunk ending on a
        complete statement boundary.

    【原理說明】
    1. MySQL 伺服器對單次接收的封包大小有嚴格限制（max_allowed_packet），超過會直接斷線。
    2. mysqldump 產生的備份檔中，每一道完整的指令（例如超長的 INSERT）最後一定是以分號 `;` 加上換行結束。
    3. 我們逐行讀取檔案，將內容不斷拼接到 `chunk` 變數中。
    4. 每次遇到結尾是 `;` 的行，代表目前剛好是一道（或多道）完整 SQL 語句的結束點。這時我們檢查 `chunk` 的大小。
    5. 如果 `chunk` 累積超過了設定的安全閥值（如 2MB），我們就把這整塊安全的、完整的多筆語句拋出（yield）交給 PyMySQL 一次執行。
    6. PyMySQL 連線時啟用了 CLIENT.MULTI_STATEMENTS，因此它能在一筆封包內執行 chunk 裡包含的多個分號語句。
    
    這樣做完美避開了因為逐字元拆分導致破壞括號或字串內容的風險，同時解決了單次封包過大的問題。
    """
    chunk = ""
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            chunk += line
            
            # line.rstrip() 可以濾除掉行尾的空白與換行符號(\n)。
            # 如果濾除後該行以 ';' 結尾，意味著我們當前位於某個 SQL 語句的完整結束點。
            if line.rstrip().endswith(';'):
                
                # 在確保語句完整的狀態下，檢查目前累積的 chunk 記憶體大小。
                # 如果超過 2MB (低於 MySQL 預設的 4MB 上限)，就可以打包送出了。
                if len(chunk.encode('utf-8')) >= max_chunk_size:
                    yield chunk
                    # 清空 chunk，準備累積下一批語句
                    chunk = ""
        
        # 處理檔案讀到結尾時，剩餘未滿 2MB 的最後一小塊語句
        if chunk.strip():
            yield chunk

def import_sql_files():
    """投入位於 ../../sql 目錄下的所有 .sql 檔案。

    Import every .sql file found in the ../../sql directory into MySQL.
    """
    # 由統一 bootstrap 推導 SQL 目錄（專案根目錄下的 sql/），不再各自硬算路徑。
    # Derive the SQL directory (sql/ at the project root) from the backend
    # root resolved by the unified bootstrap instead of re-computing it.
    sql_dir = _BACKEND_DIR.parent / "sql"
    if not sql_dir.exists():
        print(f"❌ 找不到 SQL 目錄: {sql_dir}")
        return

    sql_files = list(sql_dir.glob("*.sql"))
    if not sql_files:
        print(f"⚠️ 在 {sql_dir} 找不到任何 .sql 檔案")
        return

    print(f"🔌 正在連線至 MySQL 準備匯入資料 ({settings.MYSQL_HOST}:{settings.MYSQL_PORT})...")
    
    try:
        # 開啟 MULTI_STATEMENTS 支援以一次執行多條 SQL
        conn = pymysql.connect(
            host=settings.MYSQL_HOST,
            port=settings.MYSQL_PORT,
            user=settings.MYSQL_USER,
            password=settings.MYSQL_PASSWORD,
            client_flag=CLIENT.MULTI_STATEMENTS,
            autocommit=True
        )
        
        with conn.cursor() as cursor:
            for sql_file in sql_files:
                print(f"\n📂 開始處理檔案: {sql_file.name} (大小: {sql_file.stat().st_size / 1024 / 1024:.2f} MB)")
                
                chunk_index = 1
                for chunk in chunk_sql_statements(sql_file):
                    if not chunk.strip():
                        continue
                    
                    try:
                        cursor.execute(chunk)
                        print(f"  ✅ 成功執行區塊 #{chunk_index} (約 {len(chunk.encode('utf-8')) / 1024:.1f} KB)")
                        chunk_index += 1
                    except Exception as e:
                        print(f"  ❌ 區塊 #{chunk_index} 執行失敗: {e}")
                        # 可以考慮在這裡寫入錯誤日誌或停止
                
                print(f"🎉 檔案 {sql_file.name} 匯入完成！")
                
    except pymysql.MySQLError as e:
        print(f"❌ 資料庫連線或操作失敗: {e}")
    finally:
        if 'conn' in locals() and conn.open:
            conn.close()
            print("🧹 已關閉資料庫連線。")

if __name__ == "__main__":
    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8')
    import_sql_files()
