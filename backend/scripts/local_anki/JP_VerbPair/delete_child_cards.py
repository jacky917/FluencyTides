"""批量刪除 JP_VerbPair 子卡片工具。

Batch deletion tool for JP_VerbPair child cards: removes master-card JSON
records, deletes cloze/context notes, purges MySQL dedup records and runs
an integrity check afterwards.

支援兩種任務輸入模式：
A. JSON 模式：從 configs/delete_child_cards.json 讀取特定待刪除的子卡片清單。
   - 若 JSON 條目僅包含 master_nid（不含 cloze_nid/context_nid），
     則視為清除該母卡片下的「所有」子卡片。
B. 母卡模式：透過 `--master-nid` 參數指定母卡片，腳本會自動掃描並刪除該母卡片下的「所有」子卡片。

兩種模式僅入參方式不同，底層行為完全一致。優先順序：參數 > JSON > 無輸入則警告退出。

針對每筆展開後的任務依序執行以下操作：
1. 驗證卡片存在性：確認母卡片 (master_nid)、克漏字卡片 (cloze_nid)、
   上下文卡片 (context_nid) 三者皆存在於 Anki 中。
2. 從母卡片移除 JSON 紀錄：掃描 Intransitive_Data_JSON 與 Transitive_Data_JSON，
   找到 cloze_note_id 匹配的項目並移除。
3. 刪除子卡片：透過 AnkiConnect 的 deleteNotes 刪除 cloze 與 context 兩張子卡片。
4. 刪除 MySQL 去重紀錄：從 generated_sentences_log 中刪除對應的紀錄
   (條件: master_note_id + cloze_note_id + context_note_id 三者皆符合)。
5. 完整性檢查：刪除完成後自動調用 check_integrity.py 進行資料一致性驗證，
   dry_run 設定同步傳遞（刪除為 Dry Run 時檢查也為 Dry Run）。

安全機制：
- 預設為 Dry Run 模式，僅列印預計操作內容，不進行任何實質變更。
- 每筆任務獨立包裹在 try/except 中，單筆失敗會觸發回滾並跳過，不影響後續任務。
- Anki 欄位變更前會先備份原始狀態，失敗時自動還原。

Usage:
    # Dry Run (預覽模式)
    python delete_child_cards.py

    # 實際執行 JSON 清單刪除
    python delete_child_cards.py --execute

    # 實際執行單張母卡片下所有子卡片的清除
    python delete_child_cards.py --execute --master-nid 1234567890
"""

import argparse
import asyncio
import json
import logging
import subprocess
import sys
from pathlib import Path

# 確保 sys.path 包含 backend 根目錄
_backend_dir = Path(__file__).resolve().parents[3]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))
import scripts.common.env  # noqa

from sqlalchemy import text
from app.infrastructure.anki.client import AnkiClient
from app.infrastructure.database.corpus_database import corpus_async_session_factory, dispose_corpus_engine
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager
from app.services.card_service import CardService
from app.services.anki_model_manager import AnkiModelManager
from app.core.config import settings

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


async def get_note_fields(anki_client: AnkiClient, note_id: int) -> dict:
    """從 Anki 讀取卡片內容並回傳 fields 字典。

    Read a note from Anki and return its fields dictionary.

    Args:
        anki_client: AnkiConnect 非同步客戶端。Async AnkiConnect client.
        note_id: 要讀取的筆記 ID。The note ID to read.

    Returns:
        該筆記的 fields 字典 (key=欄位名, value=AnkiNoteFieldInfo)。
        The note's fields dict (key=field name, value=AnkiNoteFieldInfo).

    Raises:
        Exception: 當 AnkiConnect 回傳空結果 (筆記不存在) 時。
            If AnkiConnect returns an empty result (note not found).
    """
    notes_info = await anki_client.get_notes_info([note_id])
    if not notes_info:
        raise Exception(f"找不到筆記 ID: {note_id}")
    return notes_info[0].fields


async def main() -> None:
    """腳本主入口：解析參數、讀取 JSON、逐筆執行刪除流程。

    Script entry point: parse arguments, load the task JSON and run the
    deletion workflow task by task.
    """
    parser = argparse.ArgumentParser(description="批量刪除 JP_VerbPair 子卡片工具")
    parser.add_argument(
        "--execute", action="store_true",
        help="實際執行刪除操作。未加上此參數時預設為 Dry Run 模式 (不實際修改 Anki 或資料庫)"
    )
    parser.add_argument(
        "--master-nid", type=int,
        help="指定單張母卡片 ID。若提供此參數，將動態提取並刪除該母卡片下的所有子卡片（優先於 json 檔案）"
    )
    args = parser.parse_args()
    
    dry_run: bool = not args.execute

    if dry_run:
        logger.info("⚠️ 預設啟用 DRY-RUN 模式，將不會對 Anki 或資料庫進行實質變更 (若要真實執行請加上 --execute 參數)")
    else:
        logger.warning("🚨 注意：已啟用真實執行模式！將直接修改 Anki 與資料庫。")

    # ── 初始化 Anki 客戶端與 CardService ──
    anki_client = AnkiClient()
    
    try:
        models_dir_path = _backend_dir / settings.ANKI_MODELS_DIR
        model_manager = AnkiModelManager(anki_client, model_dir=models_dir_path)
        card_service = CardService(anki_client, model_manager)

        # ── 讀取與解析任務清單 ──
        raw_targets: list[dict] = []
        
        if args.master_nid:
            raw_targets.append({"master_nid": args.master_nid})
            logger.info(f"📥 使用參數輸入: --master-nid {args.master_nid}")
        else:
            json_path = Path(__file__).parent / "configs" / "delete_child_cards.json"
            if json_path.exists():
                try:
                    with open(json_path, "r", encoding="utf-8") as f:
                        raw_targets = json.load(f)
                    logger.info(f"📥 使用 JSON 設定檔輸入 (共 {len(raw_targets)} 筆目標)")
                except Exception as e:
                    logger.error(f"❌ 讀取 JSON 失敗: {e}")
                    return

        if not raw_targets:
            logger.warning("📭 任務清單為空！請加上 `--master-nid` 參數，或是編輯 delete_child_cards.json。")
            return

        tasks: list[dict[str, int]] = []
        
        logger.info("🔍 正在解析並展開任務清單...")
        for target in raw_targets:
            master_nid = target.get("master_nid")
            if not master_nid:
                continue
                
            cloze_nid = target.get("cloze_nid")
            context_nid = target.get("context_nid")
            
            if cloze_nid and context_nid:
                # 精確指定子卡片
                tasks.append({
                    "master_nid": master_nid,
                    "cloze_nid": cloze_nid,
                    "context_nid": context_nid
                })
            else:
                # 僅有母卡片，視為清除該母卡片所有子卡片
                logger.info(f"   => 僅指定母卡片 (NID: {master_nid})，嘗試動態提取其所有子卡片...")
                try:
                    notes_info = await anki_client.get_notes_info([master_nid])
                    if not notes_info:
                        logger.error(f"      ❌ 找不到母卡片 ID: {master_nid}，跳過。")
                        continue
                    master_note = notes_info[0]
                    
                    if "JP_VerbPair_Master" not in master_note.modelName:
                        logger.error(f"      ❌ 筆記類型錯誤: '{master_note.modelName}'。跳過。")
                        continue
                        
                    extracted_count = 0
                    for f_name in ["Intransitive_Data_JSON", "Transitive_Data_JSON"]:
                        json_list = await AnkiJsonFieldManager.safe_read_list(card_service, master_nid, f_name)
                        for item in json_list:
                            c_nid = item.get("cloze_note_id")
                            ctx_nid = item.get("context_note_id")
                            if c_nid and ctx_nid:
                                tasks.append({
                                    "master_nid": master_nid,
                                    "cloze_nid": c_nid,
                                    "context_nid": ctx_nid
                                })
                                extracted_count += 1
                    logger.info(f"      ✅ 成功提取 {extracted_count} 組子卡片。")
                except Exception as e:
                    logger.error(f"      ❌ 分析母卡片時發生錯誤: {e}")

        if not tasks:
            logger.info("📭 展開後沒有任何需要刪除的子卡片任務。")
            return

        success_count = 0
        failed_count = 0
        mysql_deleted_count = 0
        
        async with corpus_async_session_factory() as corpus_session:
            for idx, task in enumerate(tasks, 1):
                logger.info("=" * 50)
                logger.info(f"🗑️ 開始處理刪除任務 [{idx}/{len(tasks)}]")

                master_nid: int = task["master_nid"]
                cloze_nid: int = task["cloze_nid"]
                context_nid: int = task["context_nid"]

                logger.info(f"   母卡片: {master_nid}")
                logger.info(f"   子卡片: cloze={cloze_nid}, context={context_nid}")

                # ── 備份原始狀態 (用於回滾) ──
                original_master_fields: dict | None = None

                try:
                    # ── 步驟 0: 驗證所有卡片存在 ──
                    logger.info("   🔍 驗證卡片存在性...")
                    master_fields = await get_note_fields(anki_client, master_nid)
                    original_master_fields = master_fields.copy()
                    # 驗證子卡片存在 (只需確認能讀到，不需要備份，因為最終要刪除)
                    await get_note_fields(anki_client, cloze_nid)
                    await get_note_fields(anki_client, context_nid)
                    logger.info("      ✅ 所有卡片皆存在，開始刪除流程")
                except Exception as e:
                    logger.error(f"   ❌ 卡片驗證失敗，跳過此任務: {e}")
                    continue

                try:
                    # ── 步驟 1: 從母卡片的 JSON 中移除對應紀錄 ──
                    # 為什麼要掃描兩個欄位？因為使用者可能只提供 master_nid，
                    # 而不確定該子卡片歸屬於自動詞還是他動詞的 JSON 欄位。
                    logger.info("   📦 正在從母卡片移除 JSON 紀錄...")
                    removed = False
                    for f_name in ["Intransitive_Data_JSON", "Transitive_Data_JSON"]:
                        json_list = await AnkiJsonFieldManager.safe_read_list(
                            card_service, master_nid, f_name
                        )
                        found_idx = -1
                        for i, item in enumerate(json_list):
                            if item.get("cloze_note_id") == cloze_nid:
                                found_idx = i
                                break
                        if found_idx != -1:
                            if not dry_run:
                                await AnkiJsonFieldManager.remove_from_list(
                                    card_service, master_nid, f_name, found_idx
                                )
                            logger.info(
                                f"      ✅ 成功從 {f_name} 移除紀錄 (Index: {found_idx})"
                            )
                            removed = True
                            break

                    if not removed:
                        logger.warning(
                            "      ⚠️ 在母卡片中找不到對應的 JSON 紀錄，繼續執行刪除子卡片..."
                        )

                    # ── 步驟 2: 刪除子卡片 (cloze + context) ──
                    logger.info("   🗑️ 正在刪除子卡片...")
                    if not dry_run:
                        await anki_client.delete_notes([cloze_nid, context_nid])
                    logger.info(
                        f"      ✅ 子卡片刪除完成 (cloze={cloze_nid}, context={context_nid})"
                    )

                    # ── 步驟 3: 刪除 MySQL 去重紀錄 ──
                    # 條件: master_note_id + cloze_note_id + context_note_id 三者完全匹配
                    logger.info("   🗃️ 正在刪除 MySQL 去重紀錄...")
                    delete_log_query = text("""
                        DELETE FROM generated_sentences_log 
                        WHERE master_note_id = :master_nid 
                          AND cloze_note_id = :cloze_nid 
                          AND context_note_id = :context_nid
                    """)
                    if dry_run:
                        check_query = text("""
                            SELECT COUNT(*) FROM generated_sentences_log 
                            WHERE master_note_id = :master_nid 
                              AND cloze_note_id = :cloze_nid 
                              AND context_note_id = :context_nid
                        """)
                        result = await corpus_session.execute(check_query, {
                            "master_nid": master_nid,
                            "cloze_nid": cloze_nid,
                            "context_nid": context_nid,
                        })
                        count = result.scalar()
                        if count == 0:
                            logger.warning("      ⚠️ 該筆任務預計刪除 MySQL 紀錄 0 筆 (找不到符合的去重紀錄，目前為 Dry Run)")
                        else:
                            logger.info(f"      ✅ 該筆任務預計精準刪除 MySQL 紀錄 {count} 筆 (目前為 Dry Run，未實際執行)")
                    else:
                        result = await corpus_session.execute(delete_log_query, {
                            "master_nid": master_nid,
                            "cloze_nid": cloze_nid,
                            "context_nid": context_nid,
                        })
                        rowcount = result.rowcount
                        await corpus_session.commit()
                        
                        if rowcount == 0:
                            logger.warning("      ⚠️ MySQL 刪除執行完畢，但該筆任務影響了 0 筆 (找不到符合的紀錄)")
                        else:
                            logger.info(f"      ✅ 該筆任務之 MySQL 紀錄刪除完成 (精準刪除了 {rowcount} 筆)")

                    logger.info(f"🎉 任務 [{idx}/{len(tasks)}] 刪除順利完成！\n")
                    success_count += 1
                    mysql_deleted_count += (count if dry_run else rowcount)

                except Exception as e:
                    failed_count += 1
                    logger.error(
                        f"💥 任務 [{idx}/{len(tasks)}] 發生錯誤，觸發安全回滾機制: {e}"
                    )

                    if not dry_run:
                        # 1. MySQL 回滾
                        await corpus_session.rollback()
                        logger.info("      🔄 MySQL 交易已回滾")

                        # 2. Anki 回滾 (還原母卡片 JSON 欄位)
                        # 為什麼只回滾母卡片？因為子卡片的刪除是原子性操作：
                        # 要嘛刪成功，要嘛 AnkiConnect 報錯（卡片仍在）。
                        logger.info("      🔄 正在還原母卡片 JSON 欄位...")
                        try:
                            if original_master_fields:
                                await anki_client.update_note_fields(
                                    master_nid,
                                    {k: v["value"] for k, v in original_master_fields.items()}
                                )
                            logger.info("      ✅ 母卡片狀態還原成功")
                        except Exception as rollback_e:
                            logger.error(
                                f"      ⚠️ 母卡片狀態還原失敗，請手動檢查: {rollback_e}"
                            )

                    logger.info("      ⏭️ 跳過此任務，繼續處理下一筆...\n")
                    continue

        # ── 總結報告 ──
        logger.info("=" * 50)
        logger.info("📊 執行總結報告")
        logger.info("=" * 50)
        logger.info(f"總計任務數: {len(tasks)}")
        logger.info(f"✅ 成功完成: {success_count} 筆")
        logger.info(f"❌ 失敗跳過: {failed_count} 筆")
        logger.info(f"🗑️ Anki 子卡片刪除: {success_count * 2} 張 (每組含 cloze + context)")
        if dry_run:
            logger.info(f"🗃️ 預計共刪除 MySQL 去重紀錄: {mysql_deleted_count} 筆 (未實際執行)")
        else:
            logger.info(f"🗃️ 實際共刪除 MySQL 去重紀錄: {mysql_deleted_count} 筆")
        logger.info("=" * 50)

        # ── 步驟 5: 調用 check_integrity.py 進行完整性檢查 ──
        # 為什麼要在刪除後跑完整性檢查？因為刪除操作涉及 Anki JSON、
        # 子卡片、MySQL 三方同步，任何一環出錯都可能導致資料不一致。
        # 透過自動跑一次完整性檢查，能即時發現並提醒使用者處理殘留問題。
        logger.info("")
        logger.info("=" * 50)
        logger.info("🔍 正在調用 check_integrity.py 進行刪除後完整性驗證...")
        logger.info("=" * 50)
        
        integrity_script = Path(__file__).parent / "check_integrity.py"
        integrity_cmd = [sys.executable, str(integrity_script)]
        if not dry_run:
            # 刪除為真實執行時，完整性檢查也啟用自動修復
            integrity_cmd.append("--execute")
        
        try:
            integrity_result = subprocess.run(
                integrity_cmd,
                cwd=str(Path(__file__).parent),
                timeout=300,  # 5 分鐘超時保護
            )
            if integrity_result.returncode != 0:
                logger.warning(f"⚠️ check_integrity.py 回傳非零退出碼: {integrity_result.returncode}")
        except subprocess.TimeoutExpired:
            logger.error("❌ check_integrity.py 執行超時 (超過 5 分鐘)，已強制中止。")
        except Exception as integrity_e:
            logger.error(f"❌ 調用 check_integrity.py 時發生錯誤: {integrity_e}")

    except Exception as fatal_e:
        logger.error(f"💥 發生致命錯誤，腳本中止: {fatal_e}")
    finally:
        await anki_client.close()
        await dispose_corpus_engine()
        logger.info("🏁 資源已清理，腳本結束。")


if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
