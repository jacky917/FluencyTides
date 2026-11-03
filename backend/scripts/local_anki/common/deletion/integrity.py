"""卡片專案完整性檢查與自動修復核心（專案參數化）。

Project-parameterized integrity check and auto-repair core.

以 ProjectProfile 驅動，交叉比對 MySQL (card_gen_log 中
**該卡片類型**的紀錄) 與 Anki 牌組的四個維度：
1. DB → Anki 斷鏈：資料庫紀錄指向已不存在的卡片
2. Anki → DB 孤兒：Anki 中存在的子卡片沒有對應的資料庫紀錄
3. 母卡片 JSON 失效連結：JSON 中指向已刪除子卡片的殘留 ID
4. 媒體資源孤兒：Anki Media 中存在但**任何專案**皆未引用的檔案

跨專案安全（docs/archive/child_card_deletion_toolkit_FEAT_2026-08-27.md）：
- DB 讀寫一律以 card_type 欄過濾，他專案紀錄不會被判為斷鏈；DB 的寫入一律經
  GeneratedLogRepository，卡片關聯（rel_links）跟著紀錄一起改。
- 媒體前綴是 game_profiles 裡每一款遊戲的 game_id，不再從 .env 讀。
- Context 模型共用，先以卡上的 Master_Note_ID 反查母卡歸屬分流；
  無法歸屬的 Context 只回報、不刪除。
- 孤兒媒體以「所有已註冊專案」的引用聯集判定，不會誤刪他專案媒體。
Cross-project safety: DB access is project-filtered, shared context notes
are attributed via Master_Note_ID (unattributable ones are report-only),
and orphan media is judged against the union of every registered
project's references.
"""

import html
import json
import logging
from collections import Counter, defaultdict

from sqlalchemy import text

from app.infrastructure.anki.client import AnkiClient
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager
from app.infrastructure.database.corpus_database import corpus_async_session_factory
from app.infrastructure.database.corpus_tables import CARD_GEN_LOG, GAME_LINES
from scripts.common.database.corpus_repository import (
    find_excluded_line,
    find_line_by_text,
    find_line_by_voice,
    list_game_ids,
)
from scripts.common.database.log_repository import GeneratedLogRepository
from scripts.local_anki.common.deletion.media_scan import (
    collect_required_media,
    get_all_notes,
    guard_unreferenced,
)
from scripts.local_anki.common.deletion.profiles import (
    ProjectProfile,
    _field_value,
    build_registry,
)

logger = logging.getLogger(__name__)


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


def _extract_audio_file_key(audio_field_value: str, game_ids: list[str]) -> tuple[str | None, str]:
    """從 Cloze 卡片的 Audio 欄位值中萃取出遊戲與 ``game_lines.voice_file`` 的鍵值。

    Extract the game and the ``game_lines.voice_file`` key from a cloze
    card's Audio field.

    Cloze 卡片 Audio 欄位格式範例: 'SabbatOfTheWitch_hid002_040.mp3'
    game_lines.voice_file 欄位格式: 'hid002_040' (不含前綴與副檔名)

    Args:
        audio_field_value: Cloze 卡片 Audio 欄位值。The cloze Audio field value.
        game_ids: game_profiles 裡全部遊戲的代號（媒體前綴）。Every game id.

    Returns:
        tuple[str | None, str]: ``(遊戲代號, 鍵值)``；沒有已知前綴時遊戲為 None，
        解析失敗時鍵值為空字串。The game (None without a known prefix) and the key.
    """
    if not audio_field_value:
        return None, ""
    game_id = next((g for g in game_ids if audio_field_value.startswith(f"{g}_")), None)
    key = audio_field_value[len(game_id) + 1:] if game_id else audio_field_value
    if key.endswith(".mp3"):
        key = key[:-4]
    return game_id, key


def _parse_note_id(fields: dict, name: str) -> int | None:
    """從欄位字典解析 note id 整數；缺欄或格式錯誤回傳 None。

    Parse an integer note id from the fields dict; None when the field is
    missing or malformed.

    Args:
        fields: AnkiConnect 原始 fields 字典。Raw AnkiConnect fields dict.
        name: 欄位名稱。Field name.

    Returns:
        int | None: 解析結果。The parsed id, or None.
    """
    raw = _field_value(fields, name)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


async def run_integrity_check(
    profile: ProjectProfile,
    is_execute: bool,
    client: AnkiClient | None = None,
) -> int:
    """執行單一專案的四維度完整性檢查（可選自動修復）。

    Run the four-dimension integrity check for one project, optionally
    applying repairs.

    Args:
        profile: 專案描述子。Project profile.
        is_execute: True 時實際執行修復；否則為 Dry-Run 純診斷。Apply
            repairs when True; otherwise diagnose only.
        client: 可重用的 AnkiConnect 客戶端；None 時自建自關。Reusable
            AnkiConnect client; created (and closed) internally if None.

    Returns:
        int: 發現的完整性問題總數。Total number of issues found.
    """
    own_client = client is None
    if own_client:
        client = AnkiClient()

    mode_label = "🔧 EXECUTE 模式" if is_execute else "👁️ DRY-RUN 模式"
    issues: dict[str, list[str]] = defaultdict(list)
    fix_actions: list[dict] = []

    project = profile.project_key
    repo = GeneratedLogRepository()

    logger.info(f"{Colors.HEADER}=================================================={Colors.ENDC}")
    logger.info(f"{Colors.BOLD}🔍 {profile.display_name} 資料完整性診斷 ({mode_label}){Colors.ENDC}")
    logger.info(f"{Colors.HEADER}=================================================={Colors.ENDC}\n")

    try:
        # ==========================================
        # 1. 環境預檢查 (Environment Check)
        # ==========================================
        logger.info(f"{Colors.OKCYAN}[1/7] 正在執行環境預檢查...{Colors.ENDC}")
        try:
            version = await client._invoke("version")
            logger.info(f"   => ✅ AnkiConnect 連線正常 (API 版本: {version})")
        except Exception as e:
            logger.error(f"   => ❌ AnkiConnect 連線失敗: {e}")
            raise RuntimeError("環境預檢查失敗: 無法連接 AnkiConnect")

        async with corpus_async_session_factory() as session:
            try:
                await session.execute(text(f"SELECT 1 FROM {GAME_LINES} LIMIT 1"))
                logger.info(f"   => ✅ MySQL `{GAME_LINES}` 表存在且可查詢")
                await session.execute(text(f"SELECT 1 FROM {CARD_GEN_LOG} LIMIT 1"))
                logger.info(f"   => ✅ MySQL `{CARD_GEN_LOG}` 表存在且可查詢")
                game_ids = await list_game_ids(session)
                logger.info(f"   => ✅ 遊戲（媒體前綴）: {game_ids}")
            except Exception as e:
                logger.error(f"   => ❌ MySQL 資料庫查詢失敗: {e}")
                raise RuntimeError("環境預檢查失敗: MySQL 連線異常或資料表遺失")

        # ==========================================
        # 2. 取得 MySQL 資料庫紀錄（僅限本專案）
        # ==========================================
        logger.info(f"{Colors.OKCYAN}[2/7] 正在讀取 {CARD_GEN_LOG} (card_type={project})...{Colors.ENDC}")
        db_contexts: set[int] = set()
        db_clozes: set[int] = set()
        db_rows: list[dict] = []

        async with corpus_async_session_factory() as session:
            for row in await repo.get_active_records(session, project=project):
                if row["context"]: db_contexts.add(row["context"])
                if row["cloze"]: db_clozes.add(row["cloze"])
                db_rows.append(row)
        logger.info(f"   => 取得 {len(db_rows)} 筆活躍紀錄。")

        # ==========================================
        # 3. 取得 Anki 所有相關卡片
        # ==========================================
        logger.info(f"{Colors.OKCYAN}[3/7] 正在從 Anki 讀取所有關聯卡片...{Colors.ENDC}")

        m_notes = await get_all_notes(client, f'note:"{profile.master_model}"')
        all_ctx_notes = await get_all_notes(client, f'note:"{profile.context_model}"')
        clz_notes = await get_all_notes(client, f'note:"{profile.cloze_model}"')

        anki_masters: dict[int, dict] = {n["noteId"]: n for n in m_notes}
        anki_clozes: dict[int, dict] = {n["noteId"]: n for n in clz_notes}

        # Context 模型可能被多個專案共用，需先做專案歸屬分流：
        # 1. 卡上的 Master_Note_ID 指向本專案母卡 → 本專案。
        # 2. 否則若本專案 DB 紀錄的 context_note_id 有此卡 → 本專案（母卡已死的兜底）。
        # 3. 否則指向他專案母卡 → 排除；兩者皆查無 → 無法歸屬（只回報，不處理）。
        # The shared context model requires attribution before any repair
        # or deletion; unattributable notes are report-only.
        registry = build_registry()
        other_master_ids: set[int] = set()
        for other in registry.values():
            if other.project_key == project or other.context_model != profile.context_model:
                continue
            other_notes = await client.find_notes(f'note:"{other.master_model}"')
            other_master_ids.update(other_notes)

        anki_contexts: dict[int, dict] = {}
        unattributed_contexts: list[int] = []
        for n in all_ctx_notes:
            nid = n["noteId"]
            master_ref = _parse_note_id(n.get("fields", {}), "Master_Note_ID")
            if master_ref is not None and master_ref in anki_masters:
                anki_contexts[nid] = n
            elif nid in db_contexts:
                anki_contexts[nid] = n
            elif master_ref is not None and master_ref in other_master_ids:
                continue  # 明確屬於他專案
            else:
                unattributed_contexts.append(nid)

        for nid in unattributed_contexts:
            issues["context_unattributed"].append(
                f"Context 卡片 {nid} 無法歸屬到任何專案 (Master_Note_ID 失效且無 DB 紀錄)，僅回報不處理"
            )

        logger.info(f"   => 母卡片: {len(anki_masters)} 張")
        logger.info(f"   => Context 子卡片 (歸屬本專案): {len(anki_contexts)} 張 / 全部 {len(all_ctx_notes)} 張")
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

        if db_broken_row_ids:
            if is_execute:
                async with corpus_async_session_factory() as session:
                    await repo.soft_delete_by_ids(session, db_broken_row_ids, project=project)
                logger.info(f"   ✅ 已軟刪除 {len(db_broken_row_ids)} 筆斷鏈的 DB 紀錄。")
            else:
                for row_id in db_broken_row_ids:
                    fix_actions.append({
                        "type": "db_soft_delete",
                        "msg": f"預計執行：UPDATE {CARD_GEN_LOG} SET is_deleted=TRUE WHERE id={row_id}（並撤銷 rel_links）"
                    })

        # ==========================================
        # 5. Anki → DB 孤兒卡片檢查與修復
        # ==========================================
        logger.info(f"{Colors.OKCYAN}[5/7] 正在檢查孤兒卡片並嘗試修復...{Colors.ENDC}")

        orphan_context_ids: set[int] = {nid for nid in anki_contexts if nid not in db_contexts}
        orphan_cloze_ids: set[int] = {nid for nid in anki_clozes if nid not in db_clozes}

        paired_repairs: list[dict] = []
        unpaired_orphans: list[int] = []
        unpaired_reasons: dict[int, str] = {}
        repaired_context_ids: set[int] = set()
        repaired_cloze_ids: set[int] = set()

        for cloze_nid in orphan_cloze_ids:
            cloze_fields = anki_clozes[cloze_nid].get("fields", {})
            ctx_id = _parse_note_id(cloze_fields, "Context_Note_ID")
            master_id = _parse_note_id(cloze_fields, "Master_Note_ID")

            if ctx_id is None or master_id is None:
                unpaired_orphans.append(cloze_nid)
                unpaired_reasons[cloze_nid] = "缺少 Master_Note_ID 或 Context_Note_ID 欄位（或格式不正確）"
                continue

            if master_id not in anki_masters:
                unpaired_orphans.append(cloze_nid)
                unpaired_reasons[cloze_nid] = f"配對的母卡片 ({master_id}) 在 Anki 中不存在"
                continue

            if ctx_id not in anki_contexts:
                unpaired_orphans.append(cloze_nid)
                unpaired_reasons[cloze_nid] = f"配對的 Context 卡片 ({ctx_id}) 在 Anki 中不存在"
                continue

            paired_repairs.append({
                "cloze_nid": cloze_nid,
                "context_nid": ctx_id,
                "master_nid": master_id,
            })
            repaired_cloze_ids.add(cloze_nid)
            repaired_context_ids.add(ctx_id)

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
                    master_fields = anki_masters[master_nid].get("fields", {})

                    # 萃取 verb_lemma（策略由 profile 提供，兩專案不同）
                    verb_lemma = profile.extract_verb_lemma(cloze_fields, master_fields)

                    if not verb_lemma:
                        issues["orphan_repair_fail"].append(
                            f"Cloze {cloze_nid}: 無法取得 verb_lemma，略過修復"
                        )
                        repaired_cloze_ids.discard(cloze_nid)
                        repaired_context_ids.discard(ctx_nid)
                        unpaired_orphans.append(cloze_nid)
                        unpaired_reasons[cloze_nid] = "修復失敗：無法取得 verb_lemma"
                        if ctx_nid not in db_contexts:
                            unpaired_orphans.append(ctx_nid)
                            unpaired_reasons[ctx_nid] = "修復失敗：對應的 Cloze 卡片缺少 verb_lemma"
                        continue

                    # 萃取 Audio 欄位（前綴決定遊戲），反查 game_lines
                    audio_value = _field_value(cloze_fields, "Audio")
                    audio_game, audio_key = _extract_audio_file_key(audio_value, game_ids)

                    script_id = None

                    # 1. 優先以台詞查詢（比對遊戲顯示的文字，去掉空白與換行）
                    target_dialogue = ""
                    context_fields = anki_contexts[ctx_nid].get("fields", {})
                    dialog_json_str = _field_value(context_fields, "Dialog_JSON")
                    if dialog_json_str:
                        try:
                            # Dialog_JSON 在存入時有用 html.escape，先 unescape
                            dialog_list = json.loads(html.unescape(dialog_json_str))
                            for turn in dialog_list:
                                if turn.get("is_target"):
                                    target_dialogue = turn.get("text", "")
                                    break
                        except Exception:
                            pass
                    clean_dialogue = target_dialogue.replace(" ", "").replace("　", "").replace("\n", "").replace("\r", "")

                    if target_dialogue:
                        line = await find_line_by_text(session, clean_dialogue, audio_game)
                        if line:
                            script_id = line.id

                    # 2. 若台詞查不到，嘗試以語音檔名作為備案查詢 (因為旁白必定沒有語音)
                    if script_id is None and audio_key:
                        line = await find_line_by_voice(session, audio_key, audio_game)
                        if line:
                            script_id = line.id

                    if script_id is None:
                        # 區分「game_lines 根本沒有」與「有，但不進語料庫（in_corpus=0 等）」
                        excluded = await find_excluded_line(
                            session, normalized_text=clean_dialogue or None, voice_file=audio_key or None,
                            game_id=audio_game,
                        )
                        if excluded:
                            line_id, line_status, in_corpus, kind = excluded
                            why = (
                                f"{GAME_LINES} 有這句（id={line_id}），但不在語料庫"
                                f"（status={line_status}, in_corpus={in_corpus}, kind={kind}）"
                            )
                        else:
                            why = f"台詞與 Audio 皆無法在 {GAME_LINES} 查到"
                        issues["orphan_repair_fail"].append(
                            f"Cloze {cloze_nid}: 台詞='{target_dialogue}' Audio='{audio_value}'：{why}，略過修復"
                        )
                        repaired_cloze_ids.discard(cloze_nid)
                        repaired_context_ids.discard(ctx_nid)
                        unpaired_orphans.append(cloze_nid)
                        unpaired_reasons[cloze_nid] = f"修復失敗：{why}"
                        if ctx_nid not in db_contexts:
                            unpaired_orphans.append(ctx_nid)
                            unpaired_reasons[ctx_nid] = "修復失敗：對應的 Cloze 卡片找不到對應台詞"
                        continue

                    # 檢查 card_gen_log 是否已有紀錄 (可能是軟刪除的)
                    existing = await repo.get_record(session, script_id, verb_lemma, project=project)

                    if existing:
                        existing_id = existing["id"]
                        existing_deleted = existing["is_deleted"]
                        if existing_deleted:
                            # 情況 A：軟刪除復原
                            action_msg = (
                                f"Cloze {cloze_nid} + Context {ctx_nid} → "
                                f"UPDATE {CARD_GEN_LOG} SET is_deleted=FALSE, "
                                f"context_note_id={ctx_nid}, cloze_note_id={cloze_nid} "
                                f"WHERE id={existing_id} (軟刪除復原，rel_links 一併改寫)"
                            )
                            if is_execute:
                                updated = await repo.repair_record(
                                    session, existing_id, master_note_id=master_nid,
                                    context_note_id=ctx_nid, cloze_note_id=cloze_nid,
                                    restore=True, project=project,
                                )
                                if updated:
                                    logger.info(f"   ✅ 已復原軟刪除紀錄: Log ID={existing_id}")
                                else:
                                    logger.warning(
                                        f"   ⚠️ 沒有更新到紀錄: Log ID={existing_id}（{project} 已沒有這筆，可能被同時刪除）"
                                    )
                                    issues["orphan_repair_fail"].append(
                                        f"Cloze {cloze_nid}: Log ID={existing_id} 更新 0 筆，修復未生效"
                                    )
                            else:
                                fix_actions.append({"type": "db_restore", "msg": f"預計執行：{action_msg}"})
                                issues["orphan_context"].append(f"Context 卡片 {ctx_nid} 在資料庫中沒有對應的紀錄 (可修復)")
                                issues["orphan_cloze"].append(f"Cloze 卡片 {cloze_nid} 在資料庫中沒有對應的紀錄 (可修復)")
                        else:
                            # 紀錄存在且非軟刪除 → 只需更新 note ID 以防漂移
                            action_msg = (
                                f"Cloze {cloze_nid} + Context {ctx_nid} → "
                                f"UPDATE {CARD_GEN_LOG} SET "
                                f"context_note_id={ctx_nid}, cloze_note_id={cloze_nid} "
                                f"WHERE id={existing_id} (ID 同步，rel_links 一併改寫)"
                            )
                            if is_execute:
                                updated = await repo.repair_record(
                                    session, existing_id, master_note_id=master_nid,
                                    context_note_id=ctx_nid, cloze_note_id=cloze_nid,
                                    restore=False, project=project,
                                )
                                if updated:
                                    logger.info(f"   ✅ 已同步 Note ID: Log ID={existing_id}")
                                else:
                                    logger.warning(
                                        f"   ⚠️ 沒有更新到紀錄: Log ID={existing_id}（{project} 已沒有這筆，可能被同時刪除）"
                                    )
                                    issues["orphan_repair_fail"].append(
                                        f"Cloze {cloze_nid}: Log ID={existing_id} 更新 0 筆，修復未生效"
                                    )
                            else:
                                fix_actions.append({"type": "db_sync", "msg": f"預計執行：{action_msg}"})
                    else:
                        # 情況 B：全新插入
                        action_msg = (
                            f"Cloze {cloze_nid} + Context {ctx_nid} → "
                            f"INSERT INTO {CARD_GEN_LOG} "
                            f"(line_id={script_id}, term_key='ja-JP:{verb_lemma}', "
                            f"card_type='{project}')，並寫入 rel_links"
                        )
                        if is_execute:
                            new_id = await repo.insert_repaired_record(
                                session, script_id=script_id, verb_lemma=verb_lemma,
                                master_note_id=master_nid, context_note_id=ctx_nid,
                                cloze_note_id=cloze_nid, project=project,
                            )
                            logger.info(f"   ✅ 已插入新紀錄 (ID={new_id}): line_id={script_id}, verb={verb_lemma}, 台詞={clean_dialogue}")
                        else:
                            fix_actions.append({"type": "db_insert", "msg": f"預計執行：{action_msg}"})
                            issues["orphan_context"].append(f"Context 卡片 {ctx_nid} 在資料庫中沒有對應的紀錄 (可修復)")
                            issues["orphan_cloze"].append(f"Cloze 卡片 {cloze_nid} 在資料庫中沒有對應的紀錄 (可修復)")

        # ---------- 處理不可修復的孤兒 ----------
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

        for nid, info in anki_masters.items():
            fields = info.get("fields", {})

            for json_field_name in profile.master_json_fields:
                raw_str = _field_value(fields, json_field_name)
                items = AnkiJsonFieldManager.parse_field_string(raw_str)

                valid_items: list[dict] = []
                has_invalid = False

                for item in items:
                    ctx_id = item.get("context_note_id")
                    clz_id = item.get("cloze_note_id")

                    ctx_ok = (ctx_id is None) or (ctx_id in anki_contexts)
                    clz_ok = (clz_id is None) or (clz_id in anki_clozes)

                    if ctx_ok and clz_ok:
                        valid_items.append(item)
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
                        new_json_str = html.escape(json.dumps(valid_items, ensure_ascii=False))
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

        # ==========================================
        # 7. 媒體資源檢查（跨專案保護）
        # ==========================================
        logger.info(f"{Colors.OKCYAN}[7/7] 正在掃描 Anki Media 資源...{Colors.ENDC}")

        # 遺失檢查看「本專案」引用；多餘檢查看「所有專案」聯集，
        # 避免把他專案仍在使用的檔案誤判為孤兒。
        from scripts.local_anki.common.deletion.media_scan import (
            collect_required_media_from_notes,
        )
        required_own = collect_required_media_from_notes(
            profile, anki_masters.values(), anki_clozes.values(), anki_contexts.values()
        )
        required_all = await collect_required_media(client, registry.values())
        logger.info(f"   => 本專案引用媒體 {len(required_own)} 個；全專案聯集 {len(required_all)} 個")

        all_anki_media = set(await client._invoke("getMediaFilesNames", pattern="*"))
        logger.info(f"   => Anki Media 庫總共有 {len(all_anki_media)} 個檔案")

        for m in required_own:
            if m not in all_anki_media:
                issues["missing_media"].append(f"遺失媒體檔案: {m}")

        # 帶任何一款遊戲前綴（game_profiles 的 game_id）的檔案才在檢查範圍內
        prefixes = tuple(f"{game_id}_" for game_id in game_ids)
        if not prefixes:
            # 沒有前綴時檢查範圍是空的，看起來會像「沒有多餘媒體」；明確報錯並略過這項檢查
            logger.error(
                "   => ❌ game_profiles 沒有任何遊戲（媒體前綴），略過多餘媒體檢查；"
                "請確認 MYSQL_DATABASE 指向語料庫"
            )
            issues["media_check_skipped"].append("game_profiles 沒有任何遊戲，無法判斷哪些媒體屬於本工具，未檢查多餘媒體")
            extra_candidates: set[str] = set()
        else:
            project_media_in_anki = {f for f in all_anki_media if f.startswith(prefixes)}
            extra_candidates = project_media_in_anki - required_all

        # 最後防線：孤兒候選逐檔對整個集合（不限筆記類型）全文搜尋，
        # 任何卡片仍引用就攔下——防範未註冊筆記類型引用同前綴檔案。
        extra_media, guard_blocked = await guard_unreferenced(client, sorted(extra_candidates))
        for fname, ref_count in guard_blocked.items():
            issues["media_guard_blocked"].append(
                f"媒體 {fname} 未被已註冊專案引用，但全集合搜尋發現 {ref_count} 張卡片引用中，已攔下不刪除"
            )
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
        logger.info(f"{Colors.BOLD}📋 診斷報告 (Diagnostic Report) — {profile.display_name} — {mode_label}{Colors.ENDC}")
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
                ("context_unattributed", "❓ 無法歸屬專案的 Context 卡片 (僅回報)"),
                ("json_missing_context", "📃 母卡片 JSON 指向不存在的 Context 卡片"),
                ("json_missing_cloze", "📃 母卡片 JSON 指向不存在的 Cloze 卡片"),
                ("missing_media", "⚠️ 遺失的媒體檔案 (卡片有參照但 Anki Media 不存在)"),
                ("extra_media", "🗑️ 多餘的媒體檔案 (存在 Anki Media 中但沒有任何專案使用)"),
                ("media_guard_blocked", "🛡️ 全集合交叉驗證攔下的媒體 (未註冊筆記類型仍在引用，不刪除)"),
                ("media_check_skipped", "❌ 未執行的多餘媒體檢查"),
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

        return total_issues

    finally:
        if own_client:
            await client.close()
