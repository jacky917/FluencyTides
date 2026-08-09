"""檢驗 ES Lemma 搜尋是否誤將「見える/見せる」判為「見る」的測試腳本。

Test script that checks whether ES lemma search falsely matches
"見える"/"見せる" sentences when searching for "見る".
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
    """搜尋「見る」並統計「見え/見せ」誤判比例，輸出分析結論。

    Search for "見る", count how many hits only contain "見え"/"見せ"
    derivations (false positives), and print an analysis summary.
    """
    target_verb = "見る"
    print(f"🔍 正在搜尋動詞: {target_verb}")
    
    # 撈取大量的資料來檢查
    results = await search_dialogue_by_verb(target_verb, limit=5000)
    
    print(f"📦 總共撈出 {len(results)} 筆包含「{target_verb}」(或其活用) 的句子。")
    mieru_count = 0
    miseru_count = 0
    
    both_count = 0
    false_positive_count = 0
    false_positive_examples = []
    
    # 真正的「見る」活用形（排除見え、見せ開頭的衍生動詞）
    miru_forms = [
        "見る", "見て", "見た", "見ない", "見ま", "見よう", "見ろ", "見れ", "見よ", 
        "見られ", "見さ", "見ず", "見ん", "見な", "見つつ"
    ]

    print("--- 開始分析是否包含 見える 或 見せる ---")
    
    for hit in results:
        text = hit.get("dialogue", "")
        
        has_mie = "見え" in text
        has_mise = "見せ" in text
        
        if has_mie:
            mieru_count += 1
        if has_mise:
            miseru_count += 1
            
        if has_mie or has_mise:
            # 檢查是否同時包含真正的見る
            has_true_miru = any(form in text for form in miru_forms)
            
            if has_true_miru:
                both_count += 1
            else:
                false_positive_count += 1
                if len(false_positive_examples) < 10:
                    false_positive_examples.append(text)

    print("------------------------------------------")
    print(f"總筆數: {len(results)}")
    print(f"包含「見え」的筆數: {mieru_count}")
    print(f"包含「見せ」的筆數: {miseru_count}")
    print(f"同時包含目標與真正『見る』的筆數: {both_count}")
    print(f"『真的誤判』(僅包含見え/見せ，不含真正見る)的筆數: {false_positive_count}")
    print("------------------------------------------")
    
    if false_positive_count > 0:
        print("💡 真的誤判範例 (Top 10)：")
        for i, ex in enumerate(false_positive_examples, 1):
            print(f"  {i}. {ex}")
        print("\n💡 結論：")
        print(f"雖然 Elasticsearch 的 Lemma 搜尋大部分很精準，但仍有 {false_positive_count} 筆是 Sudachi 誤判的「見え/見せ」。")
    else:
        print("💡 結論：")
        print("沒有發現任何真正的誤判。所有包含『見え』或『見せ』的句子，都同時包含了真正的『見る』！")
        print("Elasticsearch 的 Lemma 搜尋是非常精準的。")
    
    await dispose_elasticsearch_client()

if __name__ == "__main__":
    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8')
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
