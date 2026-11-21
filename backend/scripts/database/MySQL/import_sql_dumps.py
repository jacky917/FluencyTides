"""將 SQL Dump 檔案分塊匯入 MySQL 的批次腳本。

Batch script that imports SQL dump files into MySQL, splitting large
dumps into chunks to stay under the max_allowed_packet limit.
"""

import sys
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

def import_sql_files(sql_files: list[Path], *, execute: bool) -> int:
    """Import the given SQL dump files into MySQL, stopping at the first failure.

    將指定的 SQL Dump 檔匯入 MySQL，遇到第一個失敗就停止。

    The files must be named explicitly: dumps routinely contain
    ``DROP TABLE`` or ``DELETE``, so importing whatever happens to sit in
    a directory can destroy data. Without ``execute`` this only lists the
    files and their sizes. Statements run with autocommit, so chunks that
    already ran before a failure stay applied.

    必須明確指定檔案：dump 常含 ``DROP TABLE`` 或 ``DELETE``，把某個目錄裡剛好有的檔案全部匯入可能毀掉資料。
    沒有 ``execute`` 時只列出檔案與大小。語句以 autocommit 執行，所以失敗之前已執行的區塊仍會生效。

    Args:
        sql_files: the ``.sql`` files to import, in order
            要匯入的 ``.sql`` 檔，依序匯入
        execute: whether to actually run the statements; ``False`` only
            prints the plan
            是否真的執行語句；``False`` 時只印出計畫

    Returns:
        the process exit code: ``0`` on success or in plan mode, ``1`` if a
        file is missing or any chunk fails
        行程結束碼：成功或只印計畫時為 ``0``；檔案不存在或任一區塊失敗時為 ``1``
    """
    missing = [p for p in sql_files if not p.is_file()]
    if missing:
        for p in missing:
            print(f"❌ 找不到檔案: {p}")
        return 1

    print(f"🎯 目標 MySQL: {settings.MYSQL_HOST}:{settings.MYSQL_PORT}")
    for p in sql_files:
        print(f"   - {p} ({p.stat().st_size / 1024 / 1024:.2f} MB)")
    if not execute:
        print("🧪 只列出計畫，沒有執行。確認檔案內容後加上 --yes 才會真的匯入。")
        return 0

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
                        # With MULTI_STATEMENTS every statement leaves a result;
                        # drain them all, otherwise the next execute fails with
                        # "commands out of sync" and later errors go unseen.
                        # 開啟 MULTI_STATEMENTS 時每道語句都有結果，要全部讀完，
                        # 否則下一次 execute 會出現 commands out of sync，後面語句的錯誤也看不到。
                        while cursor.nextset():
                            pass
                        print(f"  ✅ 成功執行區塊 #{chunk_index} (約 {len(chunk.encode('utf-8')) / 1024:.1f} KB)")
                        chunk_index += 1
                    except Exception as e:
                        print(f"  ❌ 區塊 #{chunk_index} 執行失敗，停止匯入: {e}")
                        print("  ⚠️ 失敗之前的區塊已經寫入（autocommit），請檢查資料庫狀態。")
                        return 1

                print(f"🎉 檔案 {sql_file.name} 匯入完成！")

    except pymysql.MySQLError as e:
        print(f"❌ 資料庫連線或操作失敗: {e}")
        return 1
    finally:
        if 'conn' in locals() and conn.open:
            conn.close()
            print("🧹 已關閉資料庫連線。")
    return 0

if __name__ == "__main__":
    import argparse

    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(
        description="將指定的 SQL Dump 檔分塊匯入 MySQL（預設只列計畫，加 --yes 才執行）。",
    )
    parser.add_argument("files", nargs="+", type=Path, help="要匯入的 .sql 檔（依序匯入）")
    parser.add_argument("--yes", action="store_true", help="確認後真的執行匯入")
    args = parser.parse_args()
    sys.exit(import_sql_files(args.files, execute=args.yes))
