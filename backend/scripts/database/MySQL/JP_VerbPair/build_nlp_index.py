"""以 Fugashi (UniDic) 斷詞建置動詞倒排索引 (dialogue_verbs_index)。

Build the verb inverted index (dialogue_verbs_index) by tokenizing
dialogue with Fugashi (UniDic) and extracting verb lemmas.
"""

import os
import sys
import time
from pathlib import Path

# 確保 sys.path 包含 backend 根目錄並載入 .env
_backend_dir = Path(__file__).resolve().parents[4]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))
import scripts.common.env  # noqa

import pymysql
from pymysql.cursors import SSCursor
import fugashi
from app.core.config import settings

def setup_database(conn):
    """建立倒排索引資料表並清除目標遊戲的舊索引。

    Create the inverted-index table and clear old index rows for the
    target game so the script can be re-run safely.

    Args:
        conn: PyMySQL 連線物件。PyMySQL connection object.
    """
    ddl = """
    CREATE TABLE IF NOT EXISTS dialogue_verbs_index (
        id INT AUTO_INCREMENT PRIMARY KEY,
        script_id BIGINT UNSIGNED NOT NULL,
        source VARCHAR(255) NOT NULL COMMENT '遊戲名稱',
        verb_lemma VARCHAR(255) NOT NULL,
        INDEX idx_lemma_source (verb_lemma, source),
        FOREIGN KEY (script_id) REFERENCES scripts(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """
    with conn.cursor() as cursor:
        print("🔧 檢查/建立 dialogue_verbs_index 表...")
        cursor.execute(ddl)
        
        # 為了讓腳本可重複執行，先清空本次目標遊戲的舊有索引
        print("🧹 清除舊有 [サノバウィッチ] 索引資料以防重複...")
        cursor.execute("DELETE FROM dialogue_verbs_index WHERE source = 'サノバウィッチ'")
    conn.commit()

def build_nlp_index():
    """流式讀取台詞、以 Fugashi 萃取動詞原型並批次寫入索引。

    Stream dialogue rows, extract verb lemmas with Fugashi, and batch
    insert them into the index table.
    """
    print(f"🔌 連線至遠端資料庫 ({settings.MYSQL_HOST}:{settings.MYSQL_PORT})...")
    conn = pymysql.connect(
        host=settings.MYSQL_HOST,
        port=settings.MYSQL_PORT,
        user=settings.MYSQL_USER,
        password=settings.MYSQL_PASSWORD,
        database=settings.MYSQL_DATABASE,
        charset='utf8mb4'
    )
    
    setup_database(conn)
    
    print("🧠 正在初始化 Fugashi NLP Tagger (UniDic)...")
    tagger = fugashi.Tagger()
    
    # 使用 SSCursor (Server Side Cursor) 可以一筆一筆流式讀取，不佔用本地記憶體
    # 避免 20MB 的資料一次全部載入造成 MemoryError
    print("📥 開始讀取 scripts 資料 (過濾條件: source='サノバウィッチ', status='1')...")
    
    insert_sql = """
        INSERT INTO dialogue_verbs_index (script_id, source, verb_lemma)
        VALUES (%s, %s, %s)
    """
    
    total_processed = 0
    total_indexed = 0
    batch_data = []
    BATCH_SIZE = 5000  # 每一萬筆批次寫入一次
    
    start_time = time.time()
    
    try:
        # SSCursor 必須單獨佔用一個連線，因此我們需要兩個 cursor，一個讀一個寫
        with conn.cursor(SSCursor) as read_cursor, conn.cursor() as write_cursor:
            read_sql = """
                SELECT id, source, dialogue 
                FROM scripts 
                WHERE source = 'サノバウィッチ' AND status = '1'
            """
            read_cursor.execute(read_sql)
            
            for row in read_cursor:
                script_id, source, dialogue = row
                total_processed += 1
                
                if dialogue is None:
                    continue
                
                # 清洗對話，去除換行等干擾 NLP 的雜訊
                clean_dialogue = dialogue.replace('\n', '')
                
                # 斷詞解析
                for node in tagger(clean_dialogue):
                    # fugashi / unidic 節點特性：
                    # feature[0]: 詞性階層1 (如 '動詞')
                    # feature[7]: 語彙素讀音/字型 (lemma)
                    if len(node.feature) > 7 and node.feature[0] == "動詞":
                        lemma = node.feature[7]
                        # 排除掉解析失敗的回傳 '*' 或是空字串
                        if lemma and lemma != "*":
                            batch_data.append((script_id, source, lemma))
                            total_indexed += 1
                
                # 批次寫入
                if len(batch_data) >= BATCH_SIZE:
                    write_cursor.executemany(insert_sql, batch_data)
                    conn.commit()
                    batch_data.clear()
                    print(f"  [進度] 處理了 {total_processed} 筆句型... 已寫入 {total_indexed} 個動詞索引。")
            
            # 寫入最後殘餘的批次
            if batch_data:
                write_cursor.executemany(insert_sql, batch_data)
                conn.commit()
                batch_data.clear()
                print(f"  [進度] 處理了 {total_processed} 筆句型... 已寫入 {total_indexed} 個動詞索引。")
                
    finally:
        conn.close()
        
    elapsed = time.time() - start_time
    print(f"🎉 預處理完成！共花費 {elapsed:.2f} 秒。")
    print(f"總共掃描 {total_processed} 句有效台詞，萃取出 {total_indexed} 個動詞。")

if __name__ == "__main__":
    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8')
    build_nlp_index()
