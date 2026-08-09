"""比較不同 Sudachi filter 組合對「する」分析結果的測試腳本。

Test script comparing how different Sudachi filter combinations analyze
the token "する" using a temporary index.
"""

import sys
import asyncio
import json
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

async def main():
    """建立臨時索引、對三種 analyzer 執行 _analyze 並列印結果。

    Create a temporary index, run _analyze with three analyzers, print
    the results, and clean up the index afterwards.
    """
    client = get_elasticsearch_client()
    try:
        # Create a temporary index with different analyzers
        await client.indices.delete(index='test_filters', ignore_unavailable=True)
        await client.indices.create(index='test_filters', body={
            "settings": {
                "analysis": {
                    "analyzer": {
                        "sudachi_no_stop": {
                            "type": "custom",
                            "tokenizer": "sudachi_tokenizer",
                            "filter": ["sudachi_baseform", "sudachi_part_of_speech"]
                        },
                        "sudachi_no_pos": {
                            "type": "custom",
                            "tokenizer": "sudachi_tokenizer",
                            "filter": ["sudachi_baseform", "sudachi_ja_stop"]
                        },
                        "sudachi_base_only": {
                            "type": "custom",
                            "tokenizer": "sudachi_tokenizer",
                            "filter": ["sudachi_baseform"]
                        }
                    }
                }
            }
        })
        
        for analyzer in ["sudachi_no_stop", "sudachi_no_pos", "sudachi_base_only"]:
            res = await client.indices.analyze(
                index='test_filters', 
                analyzer=analyzer, 
                text='する'
            )
            print(f"Analyzer: {analyzer}")
            print(json.dumps(res.body, ensure_ascii=False, indent=2))
            
    except Exception as e:
        print(e)
    finally:
        await client.indices.delete(index='test_filters', ignore_unavailable=True)
        await client.close()

if __name__ == "__main__":
    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8')
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
