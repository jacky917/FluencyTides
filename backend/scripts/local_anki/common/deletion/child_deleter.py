"""子卡片批量刪除核心（專案參數化）。

Project-parameterized batch child-card deletion core.

針對每筆展開後的任務依序執行（順序刻意讓不可逆操作留在最後）：
1. 驗證卡片存在性：母卡、cloze、context 三者皆存在於 Anki。
2. 從母卡片 JSON 移除紀錄（可還原：欄位已備份）。
3. 標記 MySQL 去重紀錄（預設軟刪除＝該句不再生成；--allow-regen 硬刪除
   ＝該句回到候選池），**先不 commit**。
4. 刪除子卡片（deleteNotes，不可逆）。
5. commit MySQL——deleteNotes 失敗時 rollback + 還原母卡欄位，
   所有已做的變更完整退回。
The irreversible deleteNotes runs last: the master-JSON edit is backed up
and the MySQL change stays uncommitted until the Anki deletion succeeds,
so any failure rolls everything back cleanly.

全部任務結束後自動以同一 dry_run 設定調用完整性檢查。
An integrity check runs afterwards with the same dry_run setting.
"""

import json
import logging
from pathlib import Path

from app.core.config import settings
from app.infrastructure.anki.client import AnkiClient
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager
from app.infrastructure.database.corpus_database import (
    corpus_async_session_factory,
    dispose_corpus_engine,
)
from app.services.anki_model_manager import AnkiModelManager
from app.services.card_service import CardService
from scripts.common.database.log_repository import GeneratedLogRepository
from scripts.local_anki.common.deletion.integrity import run_integrity_check
from scripts.local_anki.common.deletion.profiles import ProjectProfile

logger = logging.getLogger(__name__)

# scripts/ 的上層即 backend 根目錄（models_dir 解析用）。
_BACKEND_DIR = Path(__file__).resolve().parents[4]


async def _get_note_fields(
    anki_client: AnkiClient, note_id: int, expected_model: str | None = None
) -> dict:
    """從 Anki 讀取卡片內容並回傳 fields 字典，可同時驗證筆記類型。

    Read a note from Anki and return its fields dictionary, optionally
    validating its note model.

    類型驗證是防呆的一環：三個 nid 都手填的 JSON 精確模式下，
    使用者可能把他專案的卡片誤填進本專案的清單；類型不符直接拒絕，
    避免用錯誤的腳本刪掉別的筆記類型的卡。
    The model check guards against pasting another project's note ids
    into this project's task list.

    Args:
        anki_client: AnkiConnect 非同步客戶端。Async AnkiConnect client.
        note_id: 要讀取的筆記 ID。The note ID to read.
        expected_model: 預期的筆記類型名；None 時不驗證。Expected note
            model name; skipped when None.

    Returns:
        dict: 該筆記的 fields 字典。The note's fields dict.

    Raises:
        Exception: 筆記不存在，或筆記類型與預期不符時。
            If the note is missing or its model does not match.
    """
    notes_info = await anki_client.get_notes_info([note_id])
    if not notes_info:
        raise Exception(f"找不到筆記 ID: {note_id}")
    note = notes_info[0]
    if expected_model and expected_model not in note.modelName:
        raise Exception(
            f"筆記 {note_id} 的類型為 '{note.modelName}'，"
            f"與本腳本鎖定的 '{expected_model}' 不符——是否誤填了其他專案的卡片？"
        )
    return note.fields


async def _expand_tasks(
    profile: ProjectProfile,
    anki_client: AnkiClient,
    card_service: CardService,
    raw_targets: list[dict],
) -> list[dict[str, int]]:
    """把任務清單展開為 (master, cloze, context) 三元組列表。

    Expand the raw target list into (master, cloze, context) triples.

    僅含 master_nid 的條目視為「清除該母卡片下的所有子卡片」，
    會掃描母卡的全部 JSON 欄位動態提取。
    Entries with only master_nid mean "delete every child of this master";
    the master's JSON fields are scanned to enumerate them.

    Args:
        profile: 專案描述子。Project profile.
        anki_client: AnkiConnect 客戶端。AnkiConnect client.
        card_service: 卡片服務（JSON 欄位讀取用）。Card service used for
            JSON-field reads.
        raw_targets: 原始任務清單。Raw target list.

    Returns:
        list[dict[str, int]]: 展開後的任務。Expanded tasks.
    """
    tasks: list[dict[str, int]] = []

    logger.info("🔍 正在解析並展開任務清單...")
    for target in raw_targets:
        master_nid = target.get("master_nid")
        if not master_nid:
            continue

        cloze_nid = target.get("cloze_nid")
        context_nid = target.get("context_nid")

        if cloze_nid and context_nid:
            tasks.append({
                "master_nid": master_nid,
                "cloze_nid": cloze_nid,
                "context_nid": context_nid
            })
            continue

        # 僅有母卡片，視為清除該母卡片所有子卡片
        logger.info(f"   => 僅指定母卡片 (NID: {master_nid})，嘗試動態提取其所有子卡片...")
        try:
            notes_info = await anki_client.get_notes_info([master_nid])
            if not notes_info:
                logger.error(f"      ❌ 找不到母卡片 ID: {master_nid}，跳過。")
                continue
            master_note = notes_info[0]

            if profile.master_model not in master_note.modelName:
                logger.error(
                    f"      ❌ 筆記類型錯誤: '{master_note.modelName}'"
                    f"（預期 {profile.master_model}）。跳過。"
                )
                continue

            extracted_count = 0
            for f_name in profile.master_json_fields:
                json_list = await AnkiJsonFieldManager.safe_read_list(
                    card_service, master_nid, f_name
                )
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

    return tasks


async def run_child_deletion(
    profile: ProjectProfile,
    *,
    dry_run: bool,
    allow_regen: bool = False,
    master_nid: int | None = None,
    config_path: Path | None = None,
    tasks: list[dict] | None = None,
) -> None:
    """執行批量子卡片刪除（含事後完整性檢查與資源清理）。

    Run the batch child-card deletion, followed by an integrity check.

    Args:
        profile: 專案描述子。Project profile.
        dry_run: True 時僅預覽，不做任何實質變更。Preview only when True.
        allow_regen: True 時硬刪除 DB 紀錄（該句可重新生成）；預設軟刪除
            （該句不再生成）。Hard-delete the DB record when True so the
            sentence may be regenerated; default is soft delete.
        master_nid: 指定單張母卡片，刪除其下所有子卡片（優先於 JSON）。
            Master note whose children are all deleted (wins over JSON).
        config_path: JSON 任務清單路徑；master_nid 未指定時使用。Task-list
            JSON path, used when master_nid is absent.
        tasks: 呼叫端預先組好的任務清單（每項含 master_nid / cloze_nid /
            context_nid），優先於 master_nid 與 config_path——供
            id_deleter 這類已完成解析的上游使用。Pre-built task triples,
            taking precedence over master_nid and config_path; used by
            upstream callers (e.g. id_deleter) that already resolved them.
    """
    if dry_run:
        logger.info("⚠️ 預設啟用 DRY-RUN 模式，將不會對 Anki 或資料庫進行實質變更 (若要真實執行請加上 --execute 參數)")
    else:
        logger.warning("🚨 注意：已啟用真實執行模式！將直接修改 Anki 與資料庫。")
    if allow_regen:
        logger.info("♻️ --allow-regen：DB 紀錄將被硬刪除，對應句子會回到生成候選池。")
    else:
        logger.info("🧷 預設語意：DB 紀錄軟刪除，對應句子永不再生成（要重生成請加 --allow-regen）。")

    anki_client = AnkiClient()
    repo = GeneratedLogRepository()

    try:
        models_dir_path = _BACKEND_DIR / settings.ANKI_MODELS_DIR
        model_manager = AnkiModelManager(anki_client, model_dir=models_dir_path)
        card_service = CardService(anki_client, model_manager)

        # ── 讀取與解析任務清單 ──
        raw_targets: list[dict] = []

        if tasks is not None:
            logger.info(f"📥 使用呼叫端預組任務清單 (共 {len(tasks)} 筆)")
        elif master_nid:
            raw_targets.append({"master_nid": master_nid})
            logger.info(f"📥 使用參數輸入: --master-nid {master_nid}")
        elif config_path and config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    raw_targets = json.load(f)
                logger.info(f"📥 使用 JSON 設定檔輸入 (共 {len(raw_targets)} 筆目標)")
            except Exception as e:
                logger.error(f"❌ 讀取 JSON 失敗: {e}")
                return

        if tasks is None:
            if not raw_targets:
                logger.warning("📭 任務清單為空！請加上 `--master-nid` 參數，或是編輯 delete_child_cards.json。")
                return
            tasks = await _expand_tasks(profile, anki_client, card_service, raw_targets)
        if not tasks:
            logger.info("📭 展開後沒有任何需要刪除的子卡片任務。")
            return

        success_count = 0
        failed_count = 0
        mysql_affected_count = 0

        async with corpus_async_session_factory() as corpus_session:
            for idx, task in enumerate(tasks, 1):
                logger.info("=" * 50)
                logger.info(f"🗑️ 開始處理刪除任務 [{idx}/{len(tasks)}]")

                t_master: int = task["master_nid"]
                t_cloze: int = task["cloze_nid"]
                t_context: int = task["context_nid"]

                logger.info(f"   母卡片: {t_master}")
                logger.info(f"   子卡片: cloze={t_cloze}, context={t_context}")

                original_master_fields: dict | None = None
                master_json_modified = False

                try:
                    # ── 步驟 0: 驗證所有卡片存在且筆記類型正確 + 備份母卡欄位 ──
                    logger.info("   🔍 驗證卡片存在性與筆記類型...")
                    master_fields = await _get_note_fields(
                        anki_client, t_master, profile.master_model
                    )
                    original_master_fields = master_fields.copy()
                    await _get_note_fields(anki_client, t_cloze, profile.cloze_model)
                    await _get_note_fields(anki_client, t_context, profile.context_model)
                    logger.info("      ✅ 所有卡片皆存在且類型正確，開始刪除流程")
                except Exception as e:
                    logger.error(f"   ❌ 卡片驗證失敗，跳過此任務: {e}")
                    failed_count += 1
                    continue

                try:
                    # ── 步驟 1: 從母卡片的 JSON 中移除對應紀錄（可還原） ──
                    # 逐一掃描 profile 定義的全部 JSON 欄位：使用者可能只提供
                    # master_nid，無從得知子卡片歸屬於哪個欄位。
                    logger.info("   📦 正在從母卡片移除 JSON 紀錄...")
                    removed = False
                    for f_name in profile.master_json_fields:
                        json_list = await AnkiJsonFieldManager.safe_read_list(
                            card_service, t_master, f_name
                        )
                        found_idx = -1
                        for i, item in enumerate(json_list):
                            if item.get("cloze_note_id") == t_cloze:
                                found_idx = i
                                break
                        if found_idx != -1:
                            if not dry_run:
                                await AnkiJsonFieldManager.remove_from_list(
                                    card_service, t_master, f_name, found_idx
                                )
                                master_json_modified = True
                            logger.info(
                                f"      ✅ 成功從 {f_name} 移除紀錄 (Index: {found_idx})"
                            )
                            removed = True
                            break

                    if not removed:
                        logger.warning(
                            "      ⚠️ 在母卡片中找不到對應的 JSON 紀錄，繼續執行刪除子卡片..."
                        )

                    # ── 步驟 2: 標記 MySQL 去重紀錄（先不 commit） ──
                    action = "硬刪除" if allow_regen else "軟刪除"
                    logger.info(f"   🗃️ 正在{action} MySQL 去重紀錄...")
                    if dry_run:
                        count = await repo.count_record_by_note_ids(
                            corpus_session, t_master, t_cloze, t_context,
                            project=profile.project_key,
                        )
                        if count == 0:
                            logger.warning(f"      ⚠️ 該筆任務預計{action} MySQL 紀錄 0 筆 (找不到符合的去重紀錄，目前為 Dry Run)")
                        else:
                            logger.info(f"      ✅ 該筆任務預計精準{action} MySQL 紀錄 {count} 筆 (目前為 Dry Run，未實際執行)")
                        rowcount = count
                    else:
                        rowcount = await repo.delete_record_by_note_ids(
                            corpus_session, t_master, t_cloze, t_context,
                            project=profile.project_key,
                            hard=allow_regen,
                            commit=False,
                        )
                        if rowcount == 0:
                            logger.warning(f"      ⚠️ MySQL {action}影響了 0 筆 (找不到符合的紀錄)")
                        else:
                            logger.info(f"      ✅ MySQL 紀錄{action}完成 (精準影響 {rowcount} 筆，待 Anki 刪除成功後 commit)")

                    # ── 步驟 3: 刪除子卡片 (cloze + context，不可逆，最後執行) ──
                    logger.info("   🗑️ 正在刪除子卡片...")
                    if not dry_run:
                        await anki_client.delete_notes([t_cloze, t_context])
                    logger.info(
                        f"      ✅ 子卡片刪除完成 (cloze={t_cloze}, context={t_context})"
                    )

                    # ── 步驟 4: 子卡片確定刪除後才 commit MySQL ──
                    if not dry_run:
                        await corpus_session.commit()

                    logger.info(f"🎉 任務 [{idx}/{len(tasks)}] 刪除順利完成！\n")
                    success_count += 1
                    mysql_affected_count += rowcount

                except Exception as e:
                    failed_count += 1
                    logger.error(
                        f"💥 任務 [{idx}/{len(tasks)}] 發生錯誤，觸發安全回滾機制: {e}"
                    )

                    if not dry_run:
                        # 1. MySQL 回滾（步驟 2 尚未 commit，直接退回）
                        await corpus_session.rollback()
                        logger.info("      🔄 MySQL 交易已回滾")

                        # 2. Anki 回滾（還原母卡片 JSON 欄位）
                        # 子卡片刪除是原子性操作：要嘛刪成功（此時不會再拋錯），
                        # 要嘛 AnkiConnect 報錯（卡片仍在），因此只需還原母卡。
                        if master_json_modified and original_master_fields:
                            logger.info("      🔄 正在還原母卡片 JSON 欄位...")
                            try:
                                await anki_client.update_note_fields(
                                    t_master,
                                    {k: v["value"] for k, v in original_master_fields.items()}
                                )
                                logger.info("      ✅ 母卡片狀態還原成功")
                            except Exception as rollback_e:
                                logger.error(
                                    f"      ⚠️ 母卡片狀態還原失敗，請手動檢查: {rollback_e}"
                                )

                    logger.info("      ⏭️ 跳過此任務，繼續處理下一筆...\n")
                    continue

            # ── 硬刪除後收斂 AUTO_INCREMENT，避免尾端留下大段空號 ──
            if not dry_run and allow_regen and success_count > 0:
                await repo.reset_auto_increment(corpus_session)
                logger.info("🔢 已重置 AUTO_INCREMENT（收斂回 max(id)+1）")

        # ── 總結報告 ──
        logger.info("=" * 50)
        logger.info("📊 執行總結報告")
        logger.info("=" * 50)
        logger.info(f"總計任務數: {len(tasks)}")
        logger.info(f"✅ 成功完成: {success_count} 筆")
        logger.info(f"❌ 失敗跳過: {failed_count} 筆")
        logger.info(f"🗑️ Anki 子卡片刪除: {success_count * 2} 張 (每組含 cloze + context)")
        db_action = "硬刪除" if allow_regen else "軟刪除"
        if dry_run:
            logger.info(f"🗃️ 預計共{db_action} MySQL 去重紀錄: {mysql_affected_count} 筆 (未實際執行)")
        else:
            logger.info(f"🗃️ 實際共{db_action} MySQL 去重紀錄: {mysql_affected_count} 筆")
        logger.info("=" * 50)

        # ── 事後完整性檢查（同進程直接調用，dry_run 設定同步傳遞） ──
        # 刪除操作涉及 Anki JSON、子卡片、MySQL 三方同步，任何一環出錯都可能
        # 導致資料不一致；自動跑一次完整性檢查能即時發現並提醒使用者。
        logger.info("")
        logger.info("=" * 50)
        logger.info("🔍 正在進行刪除後完整性驗證...")
        logger.info("=" * 50)
        try:
            await run_integrity_check(profile, is_execute=not dry_run, client=anki_client)
        except Exception as integrity_e:
            logger.error(f"❌ 完整性檢查時發生錯誤: {integrity_e}")

    except Exception as fatal_e:
        logger.error(f"💥 發生致命錯誤，腳本中止: {fatal_e}")
    finally:
        await anki_client.close()
        await dispose_corpus_engine()
        logger.info("🏁 資源已清理，腳本結束。")
