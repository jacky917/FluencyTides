"""將舊 M-Both 卡片遷移為 JP_VerbPair_Master_Dark 空殼母卡片。

Migrate legacy "M-Both" verb-pair cards into empty JP_VerbPair_Master_Dark
master cards, copying the intransitive/transitive words over.
"""

import os
import re
import sys
from pathlib import Path

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

# 統一 bootstrap：向上尋找第一個含 app/ 的目錄即為 backend 根，與檔案深度無關；
# 腳本搬移目錄層級時不需再調整硬編碼的上層層數，避免匯入路徑失準。
# Unified bootstrap: walk up the parent chain and take the first directory
# containing app/ as the backend root. Depth-independent, so relocating this
# script never breaks the import path.
_BACKEND_DIR = next(
    p for p in Path(__file__).resolve().parents if (p / "app").is_dir()
)
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
import scripts.common.env  # noqa

import asyncio
from app.infrastructure.anki.client import AnkiClient
from app.infrastructure.utils.id_generator import generate_unique_card_id
from scripts.local_anki.JP_VerbPair.import_models import import_all_models

def clean_html(raw_html):
    """移除字串中的 HTML 標籤並修剪空白。

    Strip HTML tags from a string and trim surrounding whitespace.

    Args:
        raw_html: 含 HTML 的原始字串。Raw string that may contain HTML tags.

    Returns:
        去除標籤後的純文字。Plain text with tags removed.
    """
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    return cleantext.strip()

async def main():
    """腳本主入口：搜尋舊卡片並批次建立母卡片。

    Script entry point: find legacy M-Both cards and batch-create the
    corresponding master cards in Anki.
    """
    client = AnkiClient()
    try:
        print("🔧 確保所需的 Anki 筆記模型已經匯入...")
        # 由統一 bootstrap 推導的 backend 根目錄，避免二次硬算路徑。
        # Reuse the backend root resolved by the unified bootstrap above.
        await import_all_models(client, str(_BACKEND_DIR))
        print("-" * 50 + "\n")
        
        target_name = "Japanese Verbs - Transitive and Intransitive Pairs"
        deck_query = f'deck:"{target_name}" card:"M-Both"'
        print(f"正在搜尋: {deck_query}")
        cards = await client.find_cards(deck_query)
        
        if not cards:
            print("❌ 找不到任何符合條件的卡片！")
            return

        print(f"✅ 共找到 {len(cards)} 張 M-Both 舊卡片，準備開始建立空殼母卡片...\n")
        
        cards_info = await client.get_cards_info(cards)
        target_deck_name = "日本語::自他動詞::Master"
        
        notes_to_add = []
        for i, card in enumerate(cards_info, 1):
            fields = card.get("fields", {})
            intransitive_raw = fields.get("Intransitive", {}).get("value", "")
            transitive_raw = fields.get("Transitive", {}).get("value", "")
            
            # 清理可能的 HTML 標籤
            intransitive = clean_html(intransitive_raw)
            transitive = clean_html(transitive_raw)
            
            print(f"[{i}/{len(cards)}] 準備資料 -> 自動詞: {intransitive} | 他動詞: {transitive}")
            
            master_card_id = generate_unique_card_id(prefix="vp-m")
            
            note = {
                "deckName": target_deck_name,
                "modelName": "JP_VerbPair_Master_Dark",
                "fields": {
                    "Card_ID": master_card_id,
                    "Intransitive_Word": intransitive,
                    "Transitive_Word": transitive,
                    "Intransitive_Data_JSON": "[]",
                    "Transitive_Data_JSON": "[]"
                },
                "options": {
                    "allowDuplicate": True
                },
                "tags": ["VerbPair", "Migrated"]
            }
            notes_to_add.append(note)
        
        print("\n🚀 開始批次寫入 Anki...")
        results = await client._invoke("addNotes", notes=notes_to_add)
        
        success_count = sum(1 for res in results if res is not None)
        failed_count = len(results) - success_count
        
        print(f"\n🎉 遷移完成！")
        print(f"   ✅ 成功建立: {success_count} 張母卡片")
        if failed_count > 0:
            print(f"   ❌ 失敗: {failed_count} 張卡片")
            
    except Exception as e:
        print(f"❌ 發生未預期的錯誤: {e}")
            
    finally:
        await client.close()

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
