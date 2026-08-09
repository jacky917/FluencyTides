"""使用 ES|QL MATCH 函數以動詞原型搜尋台詞的測試腳本。

Test script that searches dialogue lines by verb lemma using the ES|QL
MATCH function.
"""

import sys
import asyncio
from pathlib import Path

# 強制指向 backend 資料夾
sys.path.insert(0, r'c:\Users\forip\Desktop\WorkSpace\Python\FluencyTides\backend')
import scripts.common.env  # noqa

from app.infrastructure.database.elasticsearch_client import get_elasticsearch_client

async def test_search():
    """以 ES|QL 搜尋「広まる」相關台詞並列印結果。

    Search for dialogue lines related to "広まる" via ES|QL and print
    the matching rows.
    """
    client = get_elasticsearch_client()
    target_verb = "広まる"  # 原型
    
    # 使用 ES|QL 的 MATCH 函數，Sudachi 會自動將文本斷詞並還原成原型比對
    query_string = f"""
        FROM fluencytides_dialogue 
        | WHERE MATCH(dialogue, "{target_verb}")
        | LIMIT 10
        | KEEP source, dialogue
    """
    
    print(f"🔍 搜尋原型：'{target_verb}'")
    print(f"📡 執行的 ES|QL: {query_string.strip()}\n")
    
    try:
        response = await client.esql.query(query=query_string)
        
        columns = [col['name'] for col in response.body.get('columns', [])]
        values = response.body.get('values', [])
        
        if not values:
            print("⚠️ 找不到任何相關台詞！")
            return
            
        print(f"✅ 找到 {len(values)} 筆包含該動詞（或其變形）的台詞：\n")
        
        for row in values:
            row_dict = dict(zip(columns, row))
            print(f"[{row_dict['source']}]")
            print(f"  {row_dict['dialogue']}\n")
            
    except Exception as e:
        print(f"❌ 查詢失敗: {e}")
    finally:
        await client.close()

if __name__ == "__main__":
    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8')
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(test_search())
