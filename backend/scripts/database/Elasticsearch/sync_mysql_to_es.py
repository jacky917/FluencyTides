"""將 MySQL scripts 資料表全量同步至 Elasticsearch 索引。

Full sync of the MySQL scripts table into the Elasticsearch index,
recreating the index first and bulk-writing cleaned dialogue documents.
"""

import os
import sys
import time
import asyncio
from pathlib import Path

# 確保 sys.path 包含 backend 根目錄並載入 .env
_backend_dir = Path(__file__).resolve().parents[3]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))
import scripts.common.env  # noqa

import pymysql
from pymysql.cursors import SSCursor
from elasticsearch import helpers

from app.core.config import settings
from app.infrastructure.database.elasticsearch_client import get_elasticsearch_client, recreate_index, dispose_elasticsearch_client

async def sync_mysql_to_es():
    """重建 ES 索引並從 MySQL 流式讀取台詞批次寫入。

    Recreate the ES index, stream dialogue rows from MySQL, and bulk
    index them in batches (idempotent via script_id as document _id).
    """
    # 1. 刪除舊索引並重建 Elasticsearch Index
    print("🔌 連線至 Elasticsearch 並重建 Index...")
    await recreate_index()
    
    es_client = get_elasticsearch_client()
    
    # 2. 連線至 MySQL
    print(f"🔌 連線至遠端資料庫 ({settings.MYSQL_HOST}:{settings.MYSQL_PORT})...")
    conn = pymysql.connect(
        host=settings.MYSQL_HOST,
        port=settings.MYSQL_PORT,
        user=settings.MYSQL_USER,
        password=settings.MYSQL_PASSWORD,
        database=settings.MYSQL_DATABASE,
        charset='utf8mb4'
    )
    
    # 3. 流式讀取並準備批次寫入
    print("📥 開始從 MySQL 讀取 scripts 資料 (全量同步)...")
    
    total_processed = 0
    total_indexed = 0
    batch_actions = []
    BATCH_SIZE = 5000  # 每一萬筆批次寫入一次
    
    start_time = time.time()
    
    try:
        with conn.cursor(SSCursor) as read_cursor:
            # 讀取所有有效台詞
            read_sql = """
                SELECT id, source, dialogue 
                FROM scripts 
                WHERE status = '1'
            """
            read_cursor.execute(read_sql)
            
            for row in read_cursor:
                script_id, source, dialogue = row
                total_processed += 1
                
                if dialogue is None:
                    continue
                
                # 清洗對話，去除換行等雜訊
                clean_dialogue = dialogue.replace('\n', '').strip()
                if not clean_dialogue:
                    continue
                
                # 準備 Elasticsearch 的 action
                # _id 顯式使用 script_id（MySQL 主鍵）：使寫入天然冪等（upsert）——
                # 重跑 sync 不 recreate 也不會產生同 script_id 的重複文檔，
                # bulk 部分失敗後重試亦安全。
                action = {
                    "_index": "fluencytides_dialogue",
                    "_id": script_id,
                    "_source": {
                        "script_id": script_id,
                        "source": source,
                        "dialogue": clean_dialogue
                    }
                }
                batch_actions.append(action)
                total_indexed += 1
                
                # 批次寫入
                if len(batch_actions) >= BATCH_SIZE:
                    print(f"  [進度] 讀取了 {total_processed} 筆台詞，正批次寫入 ES...")
                    await helpers.async_bulk(es_client, batch_actions)
                    batch_actions.clear()
            
            # 寫入最後殘餘的批次
            if batch_actions:
                print(f"  [進度] 讀取了 {total_processed} 筆台詞，正批次寫入最後一批...")
                await helpers.async_bulk(es_client, batch_actions)
                batch_actions.clear()
                
    except Exception as e:
        print(f"❌ 同步過程發生錯誤: {e}")
    finally:
        conn.close()
        await dispose_elasticsearch_client()
        
    elapsed = time.time() - start_time
    print(f"🎉 全量同步完成！共花費 {elapsed:.2f} 秒。")
    print(f"總共掃描 {total_processed} 筆台詞，並成功同步 {total_indexed} 筆至 Elasticsearch。")

if __name__ == "__main__":
    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8')
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    asyncio.run(sync_mysql_to_es())
