"""透過 Elasticsearch (Sudachi) 以動詞原型搜尋台詞的測試腳本。

Test script that searches dialogue lines by verb lemma via
Elasticsearch (Sudachi analyzer).
"""

import sys
import asyncio
from pathlib import Path

# 強制指向 backend 資料夾以正確解析模組
_backend_dir = Path(__file__).resolve().parents[3]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))
import scripts.common.env  # noqa

from app.infrastructure.database.elasticsearch_client import (
    search_dialogue_by_verb,
    dispose_elasticsearch_client
)

async def main():
    """以命令列第一個參數（預設「聞く」）為動詞原型執行搜尋並列印結果。

    Search using the first CLI argument (default "聞く") as the verb
    lemma and print matching dialogue lines.
    """
    target_verb = sys.argv[1] if len(sys.argv) > 1 else "聞く"
    
    print(f"🔍 正在透過 Elasticsearch (Sudachi) 搜尋原型 '{target_verb}' 相關的台詞...")
    
    try:
        # 將 limit 設大一點以撈出所有相關句子 (假設不超過 10000 句)
        results = await search_dialogue_by_verb(target_verb=target_verb, limit=10000)
        
        if not results:
            print(f"⚠️ 找不到任何包含 '{target_verb}' 或其變形的台詞！")
            return
            
        print(f"✅ 成功找到 {len(results)} 筆相關台詞：\n")
        
        for idx, row in enumerate(results, start=1):
            print(f"{idx}. [{row['source']}] (ID: {row['script_id']})")
            print(f"   {row['dialogue']}\n")
            
    except Exception as e:
        print(f"❌ 執行搜尋時發生錯誤: {e}")
    finally:
        await dispose_elasticsearch_client()

if __name__ == "__main__":
    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8')
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
