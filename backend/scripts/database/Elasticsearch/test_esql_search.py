"""使用 ES|QL MATCH 函數以動詞原型搜尋台詞的測試腳本。

Test script that searches dialogue lines by verb lemma using the ES|QL
MATCH function.
"""

import sys
import asyncio
from pathlib import Path

# 統一 bootstrap：向上尋找第一個含 app/ 的目錄即為 backend 根，與檔案深度無關；
# 取代原先硬編碼的開發者本機絕對路徑，任何機器（CI／伺服器）皆可執行。
# Unified bootstrap: walk up the parent chain and take the first directory
# containing app/ as the backend root. Replaces the hard-coded developer
# machine path so the script runs on any machine (CI/server included).
_BACKEND_DIR = next(
    p for p in Path(__file__).resolve().parents if (p / "app").is_dir()
)
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
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
