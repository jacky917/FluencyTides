"""JP_VerbPair 完整性檢查與自動修復腳本。

JP_VerbPair integrity check and auto-repair script that cross-checks the
MySQL database (generated_sentences_log) against Anki decks across four
dimensions (broken links, orphans, stale JSON links, orphan media).

此腳本會掃描 MySQL 資料庫 (generated_sentences_log) 與 Anki 牌組，
交叉比對以下四個維度的完整性：
1. DB → Anki 斷鏈：資料庫紀錄指向已不存在的卡片
2. Anki → DB 孤兒：Anki 中存在的子卡片沒有對應的資料庫紀錄
3. 母卡片 JSON 失效連結：JSON 中指向已刪除子卡片的殘留 ID
4. 媒體資源孤兒：Anki Media 中存在但無任何卡片引用的檔案

預設為 Dry-Run 模式（不修改任何資料，僅印出診斷報告與預計操作）。
加上 --execute 參數後才會實際執行修復。

Example:
    # 純診斷 (Dry-Run)
    $ python check_integrity.py

    # 執行自動修復
    $ python check_integrity.py --execute
"""

import os
import sys
import asyncio
import logging
import json
import html
import re
import argparse
from pathlib import Path
from collections import defaultdict

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

backend_dir = str(Path(__file__).resolve().parents[3])
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)
import scripts.common.env  # noqa

from sqlalchemy import text
from app.infrastructure.anki.client import AnkiClient
from app.infrastructure.database.corpus_database import corpus_async_session_factory, dispose_corpus_engine
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager
from app.core.config import settings

# 設定日誌
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# 格式化輸出用的色彩
class Colors:
    """終端機 ANSI 色彩常數，用於美化輸出。

    Terminal ANSI color constants used to prettify console output.
    """
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

async def get_all_notes(client: AnkiClient, query: str) -> list[dict]:
    """批次取得所有符合查詢條件的 Anki 筆記資訊。

    Fetch info for all Anki notes matching the query in batches.

    為避免單次請求筆記數量過多導致 AnkiConnect 超時，
    採用 500 筆一組的分頁策略。
    Uses 500-note pagination to avoid AnkiConnect timeouts on large requests.

    Args:
        client: AnkiConnect 客戶端。AnkiConnect client instance.
        query: Anki 搜尋查詢語句。Anki search query string.

    Returns:
        包含所有符合條件筆記詳細資訊的字典列表。
        A list of dicts with detailed info for every matching note.
    """
    notes = await client._invoke('findNotes', query=query)
    if not notes:
        return []
    
    all_info: list[dict] = []
    chunk_size = 500
    for i in range(0, len(notes), chunk_size):
        chunk = notes[i:i+chunk_size]
        info = await client._invoke('notesInfo', notes=chunk)
        all_info.extend(info)
    return all_info


def _extract_audio_file_key(audio_field_value: str, source_game: str) -> str:
    """從 Cloze 卡片的 Audio 欄位值中萃取出對應 scripts 表的 audio_file 鍵值。

    Extract the scripts-table audio_file key from a cloze card's Audio field.

    Cloze 卡片 Audio 欄位格式範例: 'SabbatOfTheWitch_hid002_040.mp3'
    scripts 表 audio_file 欄位格式: 'hid002_040' (不含前綴與副檔名)

    為什麼不直接用完整檔名查詢：scripts 表的 audio_file 只儲存不含
    遊戲前綴與副檔名的純粹音檔識別碼，這是原始 VN 腳本匯入時的設計。
    The scripts table stores only the bare audio identifier without the game
    prefix or extension, per the original VN import design.

    Args:
        audio_field_value: Cloze 卡片 Audio 欄位值。The cloze Audio field value.
        source_game: 遊戲來源前綴 (如 'SabbatOfTheWitch')。Game source prefix.

    Returns:
        可用於查詢 scripts.audio_file 的鍵值字串。若解析失敗則回傳空字串。
        A key usable to query scripts.audio_file, or an empty string on failure.
    """
    if not audio_field_value:
        return ""
    # 移除前綴 "SabbatOfTheWitch_"
    key = audio_field_value.replace(f"{source_game}_", "", 1)
    # 移除副檔名 ".mp3"
    if key.endswith(".mp3"):
        key = key[:-4]
    return key


async def main() -> None:
    """腳本主入口。

    Script entry point.

    解析命令列參數，依序執行 4 階段的完整性檢查。
    若帶有 --fix 參數，則會在檢查過程中同步執行修復操作。
    Parses CLI arguments and runs the staged integrity checks; with --execute
    the repair operations are applied during the checks.
    """
    parser = argparse.ArgumentParser(description="JP_VerbPair 完整性檢查與修復腳本")
    parser.add_argument("--execute", action="store_true", help="實際執行修復操作 (預設為 Dry-Run 純檢查)")
    args = parser.parse_args()
    
    is_execute = args.execute
    mode_label = "🔧 EXECUTE 模式" if is_execute else "👁️ DRY-RUN 模式"
    
    client = AnkiClient()
    issues: dict[str, list[str]] = defaultdict(list)
    # 用於收集需要在 Fix 模式下的待辦操作（每項是一個 dict 描述操作細節）
    fix_actions: list[dict] = []
    
    source_game = settings.JP_VERB_PAIR_SOURCE_GAME
    game_name_jp = settings.JP_VERB_PAIR_GAME_NAME_JP
    
    logger.info(f"{Colors.HEADER}=================================================={Colors.ENDC}")
    logger.info(f"{Colors.BOLD}🔍 JP_VerbPair 資料完整性診斷 ({mode_label}){Colors.ENDC}")
    logger.info(f"{Colors.HEADER}=================================================={Colors.ENDC}\n")

    try:
        # ==========================================
        # 1. 環境預檢查 (Environment Check)
        # ==========================================
        logger.info(f"{Colors.OKCYAN}[1/7] 正在執行環境預檢查...{Colors.ENDC}")
        # 檢查 AnkiConnect
        try:
            version = await client._invoke("version")
            logger.info(f"   => ✅ AnkiConnect 連線正常 (API 版本: {version})")
        except Exception as e:
            logger.error(f"   => ❌ AnkiConnect 連線失敗: {e}")
            raise RuntimeError("環境預檢查失敗: 無法連接 AnkiConnect")

        # 檢查 MySQL
        async with corpus_async_session_factory() as session:
            try:
                # 簡單查詢驗證連線與資料表是否存在
                await session.execute(text("SELECT 1 FROM scripts LIMIT 1"))
                logger.info("   => ✅ MySQL `scripts` 表存在且可查詢")
                await session.execute(text("SELECT 1 FROM generated_sentences_log LIMIT 1"))
                logger.info("   => ✅ MySQL `generated_sentences_log` 表存在且可查詢")
            except Exception as e:
                logger.error(f"   => ❌ MySQL 資料庫查詢失敗: {e}")
                raise RuntimeError("環境預檢查失敗: MySQL 連線異常或資料表遺失")
        
        # ==========================================
        # 2. 取得 MySQL 資料庫紀錄
        # ==========================================
        logger.info(f"{Colors.OKCYAN}[2/7] 正在讀取 MySQL generated_sentences_log...{Colors.ENDC}")
        db_masters: set[int] = set()
        db_contexts: set[int] = set()
        db_clozes: set[int] = set()
        db_sources: set[str] = set()
        db_rows: list[dict] = []
        
        async with corpus_async_session_factory() as session:
            result = await session.execute(text(
                "SELECT id, source, master_note_id, context_note_id, cloze_note_id "
                "FROM generated_sentences_log WHERE is_deleted = FALSE"
            ))
            for row in result.fetchall():
                r_id, r_source, r_master, r_context, r_cloze = row
                db_sources.add(r_source)
                db_masters.add(r_master)
                if r_context: db_contexts.add(r_context)
                if r_cloze: db_clozes.add(r_cloze)
                db_rows.append({
                    "id": r_id, "source": r_source, 
                    "master": r_master, "context": r_context, "cloze": r_cloze
                })
        logger.info(f"   => 取得 {len(db_rows)} 筆活躍紀錄。")

        # ==========================================
        # 3. 取得 Anki 所有相關卡片
        # ==========================================
        logger.info(f"{Colors.OKCYAN}[3/7] 正在從 Anki 讀取所有關聯卡片...{Colors.ENDC}")
        
        m_notes = await get_all_notes(client, 'note:"JP_VerbPair_Master_Dark"')
        ctx_notes = await get_all_notes(client, 'note:"JP_Context_Dark"')
        clz_notes = await get_all_notes(client, 'note:"JP_VerbPair_Cloze_Dark"')
        
        anki_masters: dict[int, dict] = {n["noteId"]: n for n in m_notes}
        anki_contexts: dict[int, dict] = {n["noteId"]: n for n in ctx_notes}
        anki_clozes: dict[int, dict] = {n["noteId"]: n for n in clz_notes}
        
        logger.info(f"   => 母卡片: {len(anki_masters)} 張")
        logger.info(f"   => Context 子卡片: {len(anki_contexts)} 張")
        logger.info(f"   => Cloze 子卡片: {len(anki_clozes)} 張")

        # ==========================================
        # 4. DB → Anki 斷鏈檢查
        # ==========================================
        logger.info(f"{Colors.OKCYAN}[4/7] 正在進行 DB → Anki 斷鏈檢查...{Colors.ENDC}")
        
        db_broken_row_ids: list[int] = []
        for row in db_rows:
            db_id = row["id"]
            broken = False
            if row["master"] not in anki_masters:
                issues["db_missing_master"].append(f"DB Log ID={db_id} 指向不存在的母卡片 {row['master']}")
                broken = True
            if row["context"] and row["context"] not in anki_contexts:
                issues["db_missing_context"].append(f"DB Log ID={db_id} 指向不存在的 Context 卡片 {row['context']}")
                broken = True
            if row["cloze"] and row["cloze"] not in anki_clozes:
                issues["db_missing_cloze"].append(f"DB Log ID={db_id} 指向不存在的 Cloze 卡片 {row['cloze']}")
                broken = True
            if broken:
                db_broken_row_ids.append(db_id)
        
        # 對斷鏈的 DB 紀錄執行軟刪除
        if db_broken_row_ids:
            if is_execute:
                async with corpus_async_session_factory() as session:
                    for row_id in db_broken_row_ids:
                        await session.execute(text(
                            "UPDATE generated_sentences_log "
                            "SET is_deleted = TRUE, updated_at = CURRENT_TIMESTAMP "
                            "WHERE id = :row_id"
                        ), {"row_id": row_id})
                    await session.commit()
                logger.info(f"   ✅ 已軟刪除 {len(db_broken_row_ids)} 筆斷鏈的 DB 紀錄。")
            else:
                for row_id in db_broken_row_ids:
                    fix_actions.append({
                        "type": "db_soft_delete",
                        "msg": f"預計執行：UPDATE generated_sentences_log SET is_deleted=TRUE WHERE id={row_id}"
                    })

        # ==========================================
        # 5. Anki → DB 孤兒卡片檢查與修復
        # ==========================================
        logger.info(f"{Colors.OKCYAN}[5/7] 正在檢查孤兒卡片並嘗試修復...{Colors.ENDC}")
        
        # 找出所有孤兒 Context 與 Cloze
        orphan_context_ids: set[int] = set()
        orphan_cloze_ids: set[int] = set()
        
        for nid in anki_contexts:
            if nid not in db_contexts:
                orphan_context_ids.add(nid)
        for nid in anki_clozes:
            if nid not in db_clozes:
                orphan_cloze_ids.add(nid)
        
        # 嘗試配對孤兒 Context 與 Cloze
        # 策略：以 Cloze 卡片為主，因為 Cloze 擁有最完整的元資料 (Audio, Verb_Pair_JSON, Master_Note_ID, Context_Note_ID)
        orphan_cloze_by_master: dict[int, list[int]] = defaultdict(list)
        for nid in orphan_cloze_ids:
            info = anki_clozes[nid]
            master_id_str = info.get("fields", {}).get("Master_Note_ID", {}).get("value", "")
            if master_id_str:
                try:
                    master_id = int(master_id_str)
                    orphan_cloze_by_master[master_id].append(nid)
                except ValueError:
                    pass
        
        orphan_context_by_master: dict[int, list[int]] = defaultdict(list)
        for nid in orphan_context_ids:
            info = anki_contexts[nid]
            master_id_str = info.get("fields", {}).get("Master_Note_ID", {}).get("value", "")
            if master_id_str:
                try:
                    master_id = int(master_id_str)
                    orphan_context_by_master[master_id].append(nid)
                except ValueError:
                    pass
        
        # 嘗試從 Cloze 身上的 Context_Note_ID 找到配對的 Context
        paired_repairs: list[dict] = []
        unpaired_orphans: list[int] = []
        unpaired_reasons: dict[int, str] = {}
        repaired_context_ids: set[int] = set()
        repaired_cloze_ids: set[int] = set()
        
        for cloze_nid in orphan_cloze_ids:
            cloze_info = anki_clozes[cloze_nid]
            cloze_fields = cloze_info.get("fields", {})
            ctx_id_str = cloze_fields.get("Context_Note_ID", {}).get("value", "")
            master_id_str = cloze_fields.get("Master_Note_ID", {}).get("value", "")
            
            if not ctx_id_str or not master_id_str:
                unpaired_orphans.append(cloze_nid)
                unpaired_reasons[cloze_nid] = "缺少 Master_Note_ID 或 Context_Note_ID 欄位"
                continue
            
            try:
                ctx_id = int(ctx_id_str)
                master_id = int(master_id_str)
            except ValueError:
                unpaired_orphans.append(cloze_nid)
                unpaired_reasons[cloze_nid] = "Master_Note_ID 或 Context_Note_ID 格式不正確"
                continue
            
            # 檢查配對的 Context 是否在 Anki 中存活
            if ctx_id not in anki_contexts:
                unpaired_orphans.append(cloze_nid)
                unpaired_reasons[cloze_nid] = f"配對的 Context 卡片 ({ctx_id}) 在 Anki 中不存在"
                continue
            
            # 雙方都活著，視為可修復
            paired_repairs.append({
                "cloze_nid": cloze_nid,
                "context_nid": ctx_id,
                "master_nid": master_id,
            })
            repaired_cloze_ids.add(cloze_nid)
            repaired_context_ids.add(ctx_id)
        
        # 沒有配對到的 Context 孤兒
        for ctx_nid in orphan_context_ids:
            if ctx_nid not in repaired_context_ids:
                unpaired_orphans.append(ctx_nid)
                unpaired_reasons[ctx_nid] = "沒有對應的 Cloze 卡片指向此 Context"
        
        # ---------- 處理可修復的配對 ----------
        if paired_repairs:
            logger.info(f"   => 發現 {len(paired_repairs)} 組可修復的孤兒子卡片配對，正在驗證...")
            async with corpus_async_session_factory() as session:
                for pair in paired_repairs:
                    cloze_nid = pair["cloze_nid"]
                    ctx_nid = pair["context_nid"]
                    master_nid = pair["master_nid"]
                    cloze_fields = anki_clozes[cloze_nid].get("fields", {})
                    
                    # 萃取 Verb_Pair_JSON
                    verb_pair_str = cloze_fields.get("Verb_Pair_JSON", {}).get("value", "")
                    verb_lemma = ""
                    if verb_pair_str:
                        try:
                            vp = json.loads(verb_pair_str)
                            used_type = vp.get("used", "")
                            if used_type == "intransitive":
                                verb_lemma = vp.get("intransitive", "")
                            elif used_type == "transitive":
                                verb_lemma = vp.get("transitive", "")
                        except json.JSONDecodeError:
                            pass
                    
                    if not verb_lemma:
                        issues["orphan_repair_fail"].append(
                            f"Cloze {cloze_nid}: 無法從 Verb_Pair_JSON 取得 verb_lemma，略過修復"
                        )
                        # 修復失敗，從已修復集合中移除，讓不可修復邏輯接手
                        repaired_cloze_ids.discard(cloze_nid)
                        repaired_context_ids.discard(ctx_nid)
                        unpaired_orphans.append(cloze_nid)
                        unpaired_reasons[cloze_nid] = "修復失敗：無法從 Verb_Pair_JSON 取得 verb_lemma"
                        if ctx_nid not in db_contexts:
                            unpaired_orphans.append(ctx_nid)
                            unpaired_reasons[ctx_nid] = "修復失敗：對應的 Cloze 卡片缺少 verb_lemma"
                        continue
                    
                    # 萃取 Audio 欄位，反查 scripts 表
                    audio_value = cloze_fields.get("Audio", {}).get("value", "")
                    audio_key = _extract_audio_file_key(audio_value, source_game)
                    
                    script_id = None
                    script_source = ""
                    script_chapter = ""
                    
                    # 1. 優先以台詞 (dialogue) 查詢
                    target_dialogue = ""
                    context_fields = anki_contexts[ctx_nid].get("fields", {})
                    dialog_json_str = context_fields.get("Dialog_JSON", {}).get("value", "")
                    if dialog_json_str:
                        try:
                            # Dialog_JSON 在存入時有用 html.escape，先 unescape
                            import html
                            dialog_json_str = html.unescape(dialog_json_str)
                            dialog_list = json.loads(dialog_json_str)
                            for turn in dialog_list:
                                if turn.get("is_target"):
                                    target_dialogue = turn.get("text", "")
                                    break
                        except Exception:
                            pass
                    clean_dialogue = target_dialogue.replace(" ", "").replace("\u3000", "").replace("\n", "").replace("\r", "")
                    
                    if target_dialogue:
                        r = await session.execute(text(
                            "SELECT id, source, chapter FROM scripts "
                            "WHERE source = :game_name_jp "
                            "AND REPLACE(REPLACE(REPLACE(REPLACE(dialogue, '\r', ''), '\n', ''), ' ', ''), '　', '') = :dialogue "
                            "LIMIT 1"
                        ), {"game_name_jp": game_name_jp, "dialogue": clean_dialogue})
                        script_row = r.fetchone()
                        if script_row:
                            script_id = int(script_row[0])
                            script_source = str(script_row[1])
                            script_chapter = str(script_row[2])
                            
                    # 2. 若 dialogue 查不到，嘗試以 audio_file 作為備案查詢 (因為旁白必定沒有語音)
                    if script_id is None and audio_key:
                        r = await session.execute(text(
                            "SELECT id, source, chapter FROM scripts "
                            "WHERE audio_file = :audio_key LIMIT 1"
                        ), {"audio_key": audio_key})
                        script_row = r.fetchone()
                        if script_row:
                            script_id = int(script_row[0])
                            script_source = str(script_row[1])
                            script_chapter = str(script_row[2])
                    
                    if script_id is None:
                        issues["orphan_repair_fail"].append(
                            f"Cloze {cloze_nid}: 台詞='{target_dialogue}' 與 Audio='{audio_value}' 皆無法在 scripts 表中找到對應紀錄，略過修復"
                        )
                        # 修復失敗，從已修復集合中移除，讓不可修復邏輯接手
                        repaired_cloze_ids.discard(cloze_nid)
                        repaired_context_ids.discard(ctx_nid)
                        unpaired_orphans.append(cloze_nid)
                        unpaired_reasons[cloze_nid] = f"修復失敗：台詞與 Audio 皆無法在 scripts 查到"
                        if ctx_nid not in db_contexts:
                            unpaired_orphans.append(ctx_nid)
                            unpaired_reasons[ctx_nid] = "修復失敗：對應的 Cloze 卡片找不到對應台詞"
                        continue
                    
                    # 檢查 generated_sentences_log 是否已有紀錄 (可能是軟刪除的)
                    r2 = await session.execute(text(
                        "SELECT id, is_deleted FROM generated_sentences_log "
                        "WHERE script_id = :script_id AND verb_lemma = :verb_lemma"
                    ), {"script_id": script_id, "verb_lemma": verb_lemma})
                    existing_row = r2.fetchone()
                    
                    if existing_row:
                        existing_id = existing_row[0]
                        existing_deleted = bool(existing_row[1])
                        if existing_deleted:
                            # 情況 A：軟刪除復原
                            action_msg = (
                                f"Cloze {cloze_nid} + Context {ctx_nid} → "
                                f"UPDATE generated_sentences_log SET is_deleted=FALSE, "
                                f"context_note_id={ctx_nid}, cloze_note_id={cloze_nid} "
                                f"WHERE id={existing_id} (軟刪除復原)"
                            )
                            if is_execute:
                                await session.execute(text(
                                    "UPDATE generated_sentences_log "
                                    "SET is_deleted = FALSE, "
                                    "    context_note_id = :ctx_nid, "
                                    "    cloze_note_id = :cloze_nid, "
                                    "    master_note_id = :master_nid, "
                                    "    updated_at = CURRENT_TIMESTAMP "
                                    "WHERE id = :row_id"
                                ), {
                                    "ctx_nid": ctx_nid,
                                    "cloze_nid": cloze_nid,
                                    "master_nid": master_nid,
                                    "row_id": existing_id,
                                })
                                await session.commit()
                                logger.info(f"   ✅ 已復原軟刪除紀錄: Log ID={existing_id}")
                            else:
                                fix_actions.append({"type": "db_restore", "msg": f"預計執行：{action_msg}"})
                                issues["orphan_context"].append(f"Context 卡片 {ctx_nid} 在資料庫中沒有對應的紀錄 (可修復)")
                                issues["orphan_cloze"].append(f"Cloze 卡片 {cloze_nid} 在資料庫中沒有對應的紀錄 (可修復)")
                        else:
                            # 紀錄存在且非軟刪除 → 只需更新 note ID 以防漂移
                            action_msg = (
                                f"Cloze {cloze_nid} + Context {ctx_nid} → "
                                f"UPDATE generated_sentences_log SET "
                                f"context_note_id={ctx_nid}, cloze_note_id={cloze_nid} "
                                f"WHERE id={existing_id} (ID 同步)"
                            )
                            if is_execute:
                                await session.execute(text(
                                    "UPDATE generated_sentences_log "
                                    "SET context_note_id = :ctx_nid, "
                                    "    cloze_note_id = :cloze_nid, "
                                    "    master_note_id = :master_nid, "
                                    "    updated_at = CURRENT_TIMESTAMP "
                                    "WHERE id = :row_id"
                                ), {
                                    "ctx_nid": ctx_nid,
                                    "cloze_nid": cloze_nid,
                                    "master_nid": master_nid,
                                    "row_id": existing_id,
                                })
                                await session.commit()
                                logger.info(f"   ✅ 已同步 Note ID: Log ID={existing_id}")
                            else:
                                fix_actions.append({"type": "db_sync", "msg": f"預計執行：{action_msg}"})
                    else:
                        # 情況 B：全新插入
                        action_msg = (
                            f"Cloze {cloze_nid} + Context {ctx_nid} → "
                            f"INSERT INTO generated_sentences_log "
                            f"(script_id={script_id}, verb_lemma='{verb_lemma}', "
                            f"source='{source_game}', chapter='{script_chapter}')"
                        )
                        if is_execute:
                            result = await session.execute(text(
                                "INSERT INTO generated_sentences_log "
                                "(script_id, verb_lemma, source, chapter, "
                                " master_note_id, context_note_id, cloze_note_id) "
                                "VALUES (:script_id, :verb_lemma, :source, :chapter, "
                                "        :master_nid, :ctx_nid, :cloze_nid)"
                            ), {
                                "script_id": script_id,
                                "verb_lemma": verb_lemma,
                                "source": source_game,
                                "chapter": script_chapter,
                                "master_nid": master_nid,
                                "ctx_nid": ctx_nid,
                                "cloze_nid": cloze_nid,
                            })
                            new_id = result.lastrowid
                            await session.commit()
                            logger.info(f"   ✅ 已插入新紀錄 (ID={new_id}): script_id={script_id}, verb={verb_lemma}, 台詞={clean_dialogue}")
                        else:
                            fix_actions.append({"type": "db_insert", "msg": f"預計執行：{action_msg}"})
                            issues["orphan_context"].append(f"Context 卡片 {ctx_nid} 在資料庫中沒有對應的紀錄 (可修復)")
                            issues["orphan_cloze"].append(f"Cloze 卡片 {cloze_nid} 在資料庫中沒有對應的紀錄 (可修復)")
        
        # ---------- 處理不可修復的孤兒 ----------
        # 去重
        unpaired_orphans_unique = list(set(unpaired_orphans) - repaired_context_ids - repaired_cloze_ids)
        
        for nid in unpaired_orphans_unique:
            reason = unpaired_reasons.get(nid, "未知原因")
            if nid in anki_contexts:
                issues["orphan_context"].append(f"Context 卡片 {nid} 在資料庫中沒有對應的紀錄 (將刪除 - 原因：{reason})")
                if is_execute:
                    await client.delete_notes([nid])
                    logger.info(f"   🗑️ 已刪除孤兒 Context 卡片: {nid} ({reason})")
                else:
                    fix_actions.append({
                        "type": "delete_note",
                        "msg": f"預計執行：deleteNotes([{nid}]) — 孤兒 Context 卡片 ({reason})"
                    })
            elif nid in anki_clozes:
                issues["orphan_cloze"].append(f"Cloze 卡片 {nid} 在資料庫中沒有對應的紀錄 (將刪除 - 原因：{reason})")
                if is_execute:
                    await client.delete_notes([nid])
                    logger.info(f"   🗑️ 已刪除孤兒 Cloze 卡片: {nid} ({reason})")
                else:
                    fix_actions.append({
                        "type": "delete_note",
                        "msg": f"預計執行：deleteNotes([{nid}]) — 孤兒 Cloze 卡片 ({reason})"
                    })

        # ==========================================
        # 6. 母卡片 JSON 失效連結清理
        # ==========================================
        logger.info(f"{Colors.OKCYAN}[6/7] 正在檢查母卡片 JSON 失效連結...{Colors.ENDC}")
        
        required_media: set[str] = set()
        
        for nid, info in anki_masters.items():
            fields = info.get("fields", {})
            
            for json_field_name in ("Intransitive_Data_JSON", "Transitive_Data_JSON"):
                raw_str = fields.get(json_field_name, {}).get("value", "")
                items = AnkiJsonFieldManager.parse_field_string(raw_str)
                
                valid_items: list[dict] = []
                has_invalid = False
                
                for item in items:
                    ctx_id = item.get("context_note_id")
                    clz_id = item.get("cloze_note_id")
                    
                    # 檢查子卡片是否仍然存在
                    ctx_ok = (ctx_id is None) or (ctx_id in anki_contexts)
                    clz_ok = (clz_id is None) or (clz_id in anki_clozes)
                    
                    if ctx_ok and clz_ok:
                        valid_items.append(item)
                        # 收集媒體清單
                        audio = item.get("audio", "")
                        avatar = item.get("avatar", "")
                        if audio and audio != "none": required_media.add(audio)
                        if avatar and avatar != "none": required_media.add(avatar)
                    else:
                        has_invalid = True
                        if not ctx_ok:
                            issues["json_missing_context"].append(
                                f"母卡片 {nid} 的 {json_field_name} 指向不存在的 Context {ctx_id}"
                            )
                        if not clz_ok:
                            issues["json_missing_cloze"].append(
                                f"母卡片 {nid} 的 {json_field_name} 指向不存在的 Cloze {clz_id}"
                            )
                
                if has_invalid:
                    if is_execute:
                        # 寫回過濾乾淨的 JSON
                        new_json_str = json.dumps(valid_items, ensure_ascii=False)
                        new_json_str = html.escape(new_json_str)
                        await client.update_note_fields(nid, {json_field_name: new_json_str})
                        logger.info(f"   ✅ 已清理母卡片 {nid} 的 {json_field_name} (移除 {len(items) - len(valid_items)} 筆失效連結)")
                    else:
                        fix_actions.append({
                            "type": "json_cleanup",
                            "msg": (
                                f"預計執行：清理母卡片 {nid} 的 {json_field_name}，"
                                f"移除 {len(items) - len(valid_items)} 筆失效連結"
                            )
                        })
                else:
                    # 所有項目都有效，直接收集媒體 (上面 valid_items loop 已收集)
                    pass
                    
        # 同步檢查子卡片身上的媒體 (做二次確認)
        for nid, info in anki_clozes.items():
            fields = info.get("fields", {})
            audio = fields.get("Audio", {}).get("value", "")
            avatar = fields.get("Avatar", {}).get("value", "")
            if audio and audio != "none": required_media.add(audio)
            if avatar and avatar != "none": required_media.add(avatar)

        # 針對 Context 卡片，必須解析其 Dialog_JSON 以取得所有對話音檔與圖片！
        for nid, info in anki_contexts.items():
            fields = info.get("fields", {})
            dialog_str = fields.get("Dialog_JSON", {}).get("value", "")
            if dialog_str:
                dialog_list = AnkiJsonFieldManager.parse_field_string(dialog_str)
                for turn in dialog_list:
                    d_audio = turn.get("audio", "")
                    d_avatar = turn.get("avatar", "")
                    if d_audio and d_audio != "none": required_media.add(d_audio)
                    if d_avatar and d_avatar != "none": required_media.add(d_avatar)

        # ==========================================
        # 7. 媒體資源檢查
        # ==========================================
        logger.info(f"{Colors.OKCYAN}[7/7] 正在掃描 Anki Media 資源...{Colors.ENDC}")
        logger.info(f"   => 本系統預期需要使用 {len(required_media)} 個媒體檔案")
        
        all_anki_media = set(await client._invoke("getMediaFilesNames", pattern="*"))
        logger.info(f"   => Anki Media 庫總共有 {len(all_anki_media)} 個檔案")
        
        # 檢查缺失的媒體
        for m in required_media:
            if m not in all_anki_media:
                issues["missing_media"].append(f"遺失媒體檔案: {m}")
                
        # 檢查多餘的媒體 (利用遊戲 source 前綴)
        prefixes = [f"{s}_" for s in db_sources] if db_sources else [f"{source_game}_"]
        project_media_in_anki: set[str] = set()
        for f in all_anki_media:
            for p in prefixes:
                if f.startswith(p):
                    project_media_in_anki.add(f)
                    break
                    
        extra_media = project_media_in_anki - required_media
        for m in extra_media:
            issues["extra_media"].append(f"多餘媒體檔案: {m}")
            if is_execute:
                await client._invoke("deleteMediaFile", filename=m)
            else:
                fix_actions.append({
                    "type": "delete_media",
                    "msg": f"預計執行：deleteMediaFile('{m}')"
                })
        
        if is_execute and extra_media:
            logger.info(f"   🗑️ 已刪除 {len(extra_media)} 個多餘媒體檔案。")

        # ==========================================
        # 產出報告
        # ==========================================
        logger.info(f"\n{Colors.HEADER}=================================================={Colors.ENDC}")
        logger.info(f"{Colors.BOLD}📋 診斷報告 (Diagnostic Report) — {mode_label}{Colors.ENDC}")
        logger.info(f"{Colors.HEADER}=================================================={Colors.ENDC}")
        
        total_issues = sum(len(v) for v in issues.values())
        if total_issues == 0:
            logger.info(f"\n{Colors.OKGREEN}✨ 完美！資料庫、Anki 欄位、卡片連結與資源全部保持一致，沒有發現任何問題！{Colors.ENDC}\n")
        else:
            logger.info(f"\n{Colors.FAIL}❌ 發現 {total_issues} 個完整性問題！請參考以下詳細清單：{Colors.ENDC}\n")
            
            error_groups = [
                ("db_missing_master", "🔗 資料庫紀錄指向不存在的母卡片 (DB -> Master)"),
                ("db_missing_context", "🔗 資料庫紀錄指向不存在的 Context 卡片 (DB -> Context)"),
                ("db_missing_cloze", "🔗 資料庫紀錄指向不存在的 Cloze 卡片 (DB -> Cloze)"),
                ("orphan_context", "👻 孤兒 Context 卡片 (Anki 中存在但 DB 沒有紀錄)"),
                ("orphan_cloze", "👻 孤兒 Cloze 卡片 (Anki 中存在但 DB 沒有紀錄)"),
                ("orphan_repair_fail", "⚠️ 孤兒卡片修復失敗"),
                ("json_missing_context", "📃 母卡片 JSON 指向不存在的 Context 卡片"),
                ("json_missing_cloze", "📃 母卡片 JSON 指向不存在的 Cloze 卡片"),
                ("missing_media", "⚠️ 遺失的媒體檔案 (卡片有參照但 Anki Media 不存在)"),
                ("extra_media", "🗑️ 多餘的媒體檔案 (存在 Anki Media 中但沒有任何卡片使用)")
            ]
            
            for key, title in error_groups:
                if issues[key]:
                    logger.info(f"{Colors.WARNING}{title} ({len(issues[key])}):{Colors.ENDC}")
                    for msg in issues[key][:10]:
                        logger.info(f"   - {msg}")
                    if len(issues[key]) > 10:
                        logger.info(f"   ... 及其他 {len(issues[key]) - 10} 筆錯誤。")
                    logger.info("")
        
        # Dry-Run 模式下印出預計執行的操作
        if not is_execute and fix_actions:
            logger.info(f"\n{Colors.HEADER}=================================================={Colors.ENDC}")
            logger.info(f"{Colors.BOLD}🔧 預計修復操作 (加上 --execute 參數以實際執行){Colors.ENDC}")
            logger.info(f"{Colors.HEADER}=================================================={Colors.ENDC}\n")
            
            action_type_labels = {
                "db_soft_delete": "📝 DB 軟刪除",
                "db_restore": "♻️ DB 紀錄復原",
                "db_sync": "🔄 DB Note ID 同步",
                "db_insert": "➕ DB 紀錄新增",
                "delete_note": "🗑️ 刪除 Anki 卡片",
                "json_cleanup": "🧹 母卡片 JSON 清理",
                "delete_media": "🗑️ 刪除媒體檔案",
            }
            
            # 依類型分組顯示
            from collections import Counter
            type_counts = Counter(a["type"] for a in fix_actions)
            
            for action_type, count in type_counts.items():
                label = action_type_labels.get(action_type, action_type)
                logger.info(f"{Colors.OKBLUE}{label} ({count} 筆):{Colors.ENDC}")
                actions_of_type = [a for a in fix_actions if a["type"] == action_type]
                for a in actions_of_type[:5]:
                    logger.info(f"   - {a['msg']}")
                if len(actions_of_type) > 5:
                    logger.info(f"   ... 及其他 {len(actions_of_type) - 5} 筆。")
                logger.info("")

    except Exception as e:
        logger.error(f"{Colors.FAIL}執行過程發生錯誤: {e}{Colors.ENDC}")
        import traceback
        traceback.print_exc()
    finally:
        await client.close()
        await dispose_corpus_engine()

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
