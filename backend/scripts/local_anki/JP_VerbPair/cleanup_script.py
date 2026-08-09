"""JP_VerbPair 清除腳本：刪除子卡片、清空母卡片 JSON、清理資料庫與媒體。

Cleanup script for JP_VerbPair: delete all child cards, blank master-card
JSON fields, purge project media files and clear the MySQL dedup log.
"""

import os
import sys
import re
from pathlib import Path
import asyncio
import argparse

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

# 確保 sys.path 包含 backend 根目錄並載入 .env
backend_dir = str(Path(__file__).resolve().parents[3])
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)
import scripts.common.env  # noqa

from app.infrastructure.anki.client import AnkiClient
from app.infrastructure.database.corpus_database import corpus_async_session_factory, dispose_corpus_engine
from scripts.common.database.log_repository import GeneratedLogRepository

async def main() -> None:
    """腳本主入口：蒐集清理項目、確認後執行刪除。

    Script entry point: collect items to clean, ask for confirmation and
    perform the deletions unless in dry-run mode.
    """
    parser = argparse.ArgumentParser(description="純粹的清除腳本：刪除所有子卡片、清空母卡片 JSON 欄位、清理資料庫與媒體資源。")
    parser.add_argument("deck_name", nargs="?", default="日本語::自他動詞", help="根牌組名稱")
    parser.add_argument("--execute", action="store_true", help="正式執行清除 (若無此參數則為 Dry Run 空跑)")
    args = parser.parse_args()
    
    base_deck_name = args.deck_name
    dry_run = not args.execute
    
    client = AnkiClient()
    
    try:
        if dry_run:
            print("\n========================================")
            print("🛡️  目前為 DRY RUN 模式，不會執行任何實際刪除  🛡️")
            print("   (若要正式執行，請加上 --execute)    ")
            print("========================================\n")
            
        print(f"🚀 準備清理根牌組: {base_deck_name}")
        
        # 1. 蒐集需要刪除的 Context 和 Cloze 子卡牌
        notes_to_delete = []
        for subdeck in ["Context", "Cloze"]:
            deck_name = f"{base_deck_name}::{subdeck}"
            print(f"🔍 尋找 {deck_name} 的筆記...")
            notes = await client.find_notes(f'"deck:{deck_name}"')
            if notes:
                notes_to_delete.extend(notes)
                print(f"   找到 {len(notes)} 條來自 {deck_name} 的筆記。")
            else:
                print(f"   {deck_name} 中沒有找到筆記。")
        
        # 2. 蒐集需要清空 JSON 欄位的 Master 筆記
        master_deck = f"{base_deck_name}::Master"
        print(f"\n🔍 尋找 {master_deck} 的筆記...")
        master_notes = await client.find_notes(f'"deck:{master_deck}"')
        if master_notes:
            print(f"   找到 {len(master_notes)} 條 Master 筆記需要清空欄位。")
        else:
            print(f"   {master_deck} 中沒有找到筆記。")
            
        # 3. 蒐集需要刪除的媒體資源
        prefix = "SabbatOfTheWitch_"
        print(f"\n🔍 尋找前綴為 '{prefix}' 的媒體資源...")
        media_files = await client.get_media_files_names(f"{prefix}*")
        if media_files:
            print(f"   找到 {len(media_files)} 個媒體資源。")
        else:
            print(f"   沒有找到前綴為 '{prefix}' 的媒體資源。")
            
        # 4. 資料庫清理 (若有 DB)
        has_db = bool(corpus_async_session_factory)
        
        # 提示確認
        print("\n========================================")
        print("⚠️  即將執行的清理內容總結：")
        print(f"   - 刪除子卡片 (Context/Cloze): {len(notes_to_delete)} 條")
        print(f"   - 清空母卡片 JSON 欄位: {len(master_notes)} 條")
        print(f"   - 刪除媒體資源 ({prefix}*): {len(media_files)} 個")
        print(f"   - 清空 MySQL generated_sentences_log: {'是' if has_db else '否 (無 DB 連線)'}")
        print("========================================\n")
        
        if not dry_run:
            if not notes_to_delete and not master_notes and not media_files and not has_db:
                print("✨ 沒有需要清理的項目。")
                return

            print("🚨 警告：此操作不可逆！將會徹底刪除上述所有內容。")
            user_input = await asyncio.to_thread(input, "確定要繼續執行清除嗎？ [y/N]: ")
            if user_input.strip().lower() != 'y':
                print("❌ 已取消操作。")
                return
                
            print("\n🔥 開始執行清理...")
            
            if notes_to_delete:
                print(f"🗑️ 正在刪除 {len(notes_to_delete)} 條子卡片筆記...")
                await client.delete_notes(notes_to_delete)
                
            if master_notes:
                print(f"📝 正在清空 {len(master_notes)} 條 Master 筆記的 JSON 欄位...")
                for note_id in master_notes:
                    await client.update_note_fields(note_id, {
                        "Intransitive_Data_JSON": "[]",
                        "Transitive_Data_JSON": "[]"
                    })
                    
            if media_files:
                print(f"🗑️ 正在刪除 {len(media_files)} 個媒體資源...")
                for media_file in media_files:
                    await client.delete_media_file(media_file)
                    
            if has_db:
                print(f"🧹 正在清理 MySQL generated_sentences_log 資料表...")
                async with corpus_async_session_factory() as session:
                    log_repo = GeneratedLogRepository()
                    await log_repo.clear_all_records(session, hard_delete=True)
                    
            print("✅ 所有清理作業已順利完成！")

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ 發生錯誤: {e}")
    finally:
        await client.close()
        await dispose_corpus_engine()

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
