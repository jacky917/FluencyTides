"""批量子卡片搬移工具：將子卡片從舊母卡片搬移至新母卡片。

Batch child-card migration tool that moves cloze/context child cards from an
old master card to a new one, updating Anki JSON fields, MySQL dedup records
and the SQLite relation graph, with rollback on failure.
"""

import argparse
import asyncio
import json
import logging
import re
import sys
from pathlib import Path

# 確保 sys.path 包含 backend 根目錄
_backend_dir = Path(__file__).resolve().parents[3]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))
import scripts.common.env  # noqa

from sqlalchemy import text
from app.infrastructure.anki.client import AnkiClient
from app.infrastructure.database.database import async_session_factory, dispose_engine
from app.infrastructure.database.corpus_database import corpus_async_session_factory, dispose_corpus_engine
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager
from app.services.card_service import CardService
from app.services.anki_model_manager import AnkiModelManager
from app.core.config import settings

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

def to_pure_kana(text_val: str) -> str:
    """去除括號與多餘部分，取得純假名或純字串。

    Strip ruby brackets and extra parts to obtain the plain kana or string.

    Args:
        text_val: 原始欄位文字。Raw field text (may contain Base[Ruby] notation).

    Returns:
        清理後的純假名字串。The cleaned plain kana string.
    """
    if not text_val:
        return ""
    # 取出 Base[Ruby] 中的 Ruby
    clean = re.sub(r'[^\s\[\]]+\[([^\]]+)\]', r'\1', text_val)
    parts = [v.strip() for v in re.split(r'[,、/・]', clean) if v.strip()]
    return parts[0] if parts else clean.strip()

async def get_note_fields(anki_client: AnkiClient, note_id: int) -> dict:
    """從 Anki 讀取卡片內容並回傳 fields 字典。

    Read a note from Anki and return its fields dictionary.

    Args:
        anki_client: AnkiConnect 客戶端。AnkiConnect client instance.
        note_id: 筆記 ID。The note ID to read.

    Returns:
        該筆記的 fields 字典。The note's fields dictionary.

    Raises:
        Exception: 找不到筆記時。If the note does not exist.
    """
    notes_info = await anki_client.get_notes_info([note_id])
    if not notes_info:
        raise Exception(f"找不到筆記 ID: {note_id}")
    return notes_info[0].fields

async def main():
    """腳本主入口：讀取設定檔並逐筆執行搬移任務。

    Script entry point: load the migration config JSON and process each
    migration task with rollback on error.
    """
    parser = argparse.ArgumentParser(description="批量子卡片搬移工具")
    parser.add_argument("--execute", action="store_true", help="實際執行搬移操作 (預設為 Dry Run 模式，不實際修改 Anki 或資料庫)")
    args = parser.parse_args()
    dry_run = not args.execute

    if dry_run:
        logger.info("⚠️ 預設啟用 DRY-RUN 模式，將不會對 Anki 或資料庫進行實質變更 (若要真實執行請加上 --execute 參數)")
    else:
        logger.warning("🚨 注意：已啟用真實執行模式！將直接修改 Anki 與資料庫。")

    json_path = Path(__file__).parent / "configs" / "migrate_child_cards.json"
    if not json_path.exists():
        logger.error(f"❌ 找不到設定檔: {json_path}")
        return

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            tasks = json.load(f)
    except Exception as e:
        logger.error(f"❌ 讀取 JSON 失敗: {e}")
        return

    anki_client = AnkiClient()
    
    # 調用 .env 裡面設置的相對路徑
    models_dir_path = _backend_dir / settings.ANKI_MODELS_DIR
    model_manager = AnkiModelManager(anki_client, model_dir=models_dir_path)
    card_service = CardService(anki_client, model_manager)

    try:
        async with corpus_async_session_factory() as corpus_session, async_session_factory() as app_session:
            for idx, task in enumerate(tasks, 1):
                logger.info("==================================================")
                logger.info(f"🔄 開始處理任務 [{idx}/{len(tasks)}]")
                
                old_master_nid = task["old_master_nid"]
                cloze_nid = task["cloze_nid"]
                context_nid = task["context_nid"]
                target_master_nid = task["target_master_nid"]
                target_field = task["target_field"]
                target_field_type = str(target_field).capitalize() # Intransitive or Transitive

                if target_field_type not in ("Intransitive", "Transitive"):
                    logger.error(f"❌ 未知的 target_field: {target_field_type}")
                    continue
                
                logger.info(f"   參數: old_master={old_master_nid}, target_master={target_master_nid}")
                logger.info(f"   子卡片: cloze={cloze_nid}, context={context_nid}")

                original_old_master = None
                original_target_master = None
                original_cloze = None
                original_context = None
                
                try:
                    # 0. 提前讀取所有卡片的原始狀態 (用於失敗時的 Rollback)
                    old_master_fields = await get_note_fields(anki_client, old_master_nid)
                    target_master_fields = await get_note_fields(anki_client, target_master_nid)
                    cloze_fields = await get_note_fields(anki_client, cloze_nid)
                    context_fields = await get_note_fields(anki_client, context_nid)
                    
                    original_old_master = old_master_fields.copy()
                    original_target_master = target_master_fields.copy()
                    original_cloze = cloze_fields.copy()
                    original_context = context_fields.copy()
                    
                    # 觸發同步，確保本地是最新狀態
                    logger.info("正在觸發 Anki 同步...")
                    await anki_client._invoke("sync")
                    logger.info("✅ Anki 同步完成。")
                except Exception as e:
                    logger.error(f"❌ 卡片讀取失敗: {e}")
                    continue

                def get_field_val(fields_dict, name):
                    field = fields_dict.get(name, fields_dict.get(name.replace("_Word", ""), {}))
                    return field.get("value", "") if isinstance(field, dict) else getattr(field, 'value', '')

                t_intransitive_raw = get_field_val(target_master_fields, "Intransitive_Word")
                t_transitive_raw = get_field_val(target_master_fields, "Transitive_Word")
                
                target_intransitive = to_pure_kana(t_intransitive_raw)
                target_transitive = to_pure_kana(t_transitive_raw)
                target_used_type = target_field_type.lower()

                try:
                    # 1. 從原母卡片解除關聯 (找出 JSON 並移除)
                    logger.info("   🗑️ 正在從舊母卡片尋找並移除紀錄...")
                    extracted_json_item = None
                    
                    for f_name in ["Intransitive_Data_JSON", "Transitive_Data_JSON"]:
                        json_list = await AnkiJsonFieldManager.safe_read_list(card_service, old_master_nid, f_name)
                        found_idx = -1
                        for i, item in enumerate(json_list):
                            if item.get("cloze_note_id") == cloze_nid:
                                found_idx = i
                                extracted_json_item = item
                                break
                        if found_idx != -1:
                            if not dry_run:
                                await AnkiJsonFieldManager.remove_from_list(card_service, old_master_nid, f_name, found_idx)
                            logger.info(f"      ✅ 成功從 {f_name} 移除紀錄 (Index: {found_idx})")
                            break

                    if not extracted_json_item:
                        logger.warning("      ⚠️ 在舊母卡片中找不到對應的 JSON 紀錄！將繼續執行更新子卡片流程...")
                    else:
                        logger.info(f"      📦 提取的紀錄: {extracted_json_item}")

                    # 2. 更新子卡片資訊
                    logger.info("   📝 正在更新子卡片 Master_Note_ID 與 Verb_Pair_JSON...")
                    if not dry_run:
                        await anki_client.update_note_fields(context_nid, {
                            "Master_Note_ID": str(target_master_nid)
                        })
                    
                    verb_pair_data = {
                        "intransitive": target_intransitive,
                        "transitive": target_transitive,
                        "used": target_used_type
                    }
                    verb_pair_json_str = json.dumps(verb_pair_data, ensure_ascii=False)
                    
                    if not dry_run:
                        await anki_client.update_note_fields(cloze_nid, {
                            "Master_Note_ID": str(target_master_nid),
                            "Verb_Pair_JSON": verb_pair_json_str
                        })
                    logger.info(f"      ✅ 子卡片更新完成 (新的 Verb_Pair_JSON: {verb_pair_json_str})")

                    # 3. 目標母卡片建立關聯
                    if extracted_json_item:
                        logger.info(f"   🔗 正在將紀錄加入目標母卡片的 {target_field_type}_Data_JSON...")
                        target_json_field = f"{target_field_type}_Data_JSON"
                        if not dry_run:
                            await AnkiJsonFieldManager.append_to_list(
                                card_service, target_master_nid, target_json_field, extracted_json_item
                            )
                        logger.info("      ✅ 目標母卡片 JSON 紀錄加入成功")

                    # 4. 更新本機資料庫與圖譜關聯
                    logger.info("   🗃️ 正在更新資料庫與圖譜關聯...")
                    
                    # 更新去重紀錄 (存於 MySQL)
                    update_log_query = text("""
                        UPDATE generated_sentences_log 
                        SET master_note_id = :target_master_nid 
                        WHERE cloze_note_id = :cloze_nid AND context_note_id = :context_nid
                    """)
                    if not dry_run:
                        res_log = await corpus_session.execute(update_log_query, {
                            "target_master_nid": target_master_nid,
                            "cloze_nid": cloze_nid,
                            "context_nid": context_nid
                        })
                        rowcount_log = res_log.rowcount
                        await corpus_session.commit()
                    else:
                        rowcount_log = "N/A (Dry Run)"
                    
                    # 更新圖譜關係 (針對 frontend react-force-graph，存於 SQLite)
                    update_relation_query = text("""
                        UPDATE card_relations 
                        SET source_note_id = :target_master_nid 
                        WHERE (target_note_id = :cloze_nid OR target_note_id = :context_nid)
                          AND source_note_id = :old_master_nid
                    """)
                    if not dry_run:
                        res_rel = await app_session.execute(update_relation_query, {
                            "target_master_nid": target_master_nid,
                            "cloze_nid": cloze_nid,
                            "context_nid": context_nid,
                            "old_master_nid": old_master_nid
                        })
                        rowcount_rel = res_rel.rowcount
                        await app_session.commit()
                    else:
                        rowcount_rel = "N/A (Dry Run)"
                        
                    logger.info(f"      ✅ 資料庫更新完成 (影響去重紀錄: {rowcount_log} 筆, 圖譜邊緣: {rowcount_rel} 筆)")
                    
                    logger.info(f"🎉 任務 [{idx}/{len(tasks)}] 搬移順利完成！\n")

                except Exception as e:
                    logger.error(f"💥 任務 [{idx}/{len(tasks)}] 發生非預期錯誤，觸發安全回滾機制: {e}")
                    
                    if not dry_run:
                        # 1. 資料庫回滾
                        await corpus_session.rollback()
                        await app_session.rollback()
                        logger.info("      🔄 資料庫交易已回滾")
                        
                        # 2. Anki 回滾
                        logger.info("      🔄 正在還原 Anki 卡片欄位狀態...")
                        try:
                            if original_old_master:
                                await anki_client.update_note_fields(old_master_nid, {k: v["value"] for k, v in original_old_master.items()})
                            if original_target_master:
                                await anki_client.update_note_fields(target_master_nid, {k: v["value"] for k, v in original_target_master.items()})
                            if original_cloze:
                                await anki_client.update_note_fields(cloze_nid, {k: v["value"] for k, v in original_cloze.items()})
                            if original_context:
                                await anki_client.update_note_fields(context_nid, {k: v["value"] for k, v in original_context.items()})
                            logger.info("      ✅ Anki 狀態還原成功")
                        except Exception as rollback_e:
                            logger.error(f"      ⚠️ Anki 狀態還原失敗，請手動檢查卡片: {rollback_e}")
                    
                    logger.info("      ⏭️ 跳過此任務，繼續處理下一筆...\n")
                    continue

    except Exception as fatal_e:
        logger.error(f"💥 發生致命錯誤，腳本中止: {fatal_e}")
    finally:
        await anki_client.close()
        await dispose_engine()
        await dispose_corpus_engine()
        logger.info("🏁 資源已清理，腳本結束。")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
