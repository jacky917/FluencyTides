"""以 generated_sentences_log 的 id 為入口的通用卡片刪除核心。

Card-type-agnostic deletion core keyed by generated_sentences_log row ids.

使用者只需提供 DB 紀錄的 id（支援複數與範圍），工具會：
1. 查出每筆紀錄的 project / master / cloze / context note id。
2. 依 project 欄自動掛上對應的 ProjectProfile（JP_VerbPair / JP_CoreVerb），
   不需要也不能手動指定卡片類型。
3. 按專案分組後調用 child_deleter 共用核心——筆記類型驗證、
   「不可逆操作留最後」的步驟順序、失敗回滾、事後完整性檢查全部繼承。

「無卡可刪」的紀錄（純失敗紀錄，或子卡已不存在於 Anki）依模式分流：
- 預設（軟刪除語意＝該句不再生成）：跳過並警告——紀錄留在 DB 本來就能
  繼續擋住重新生成，動它沒有意義。
- ``--allow-regen``（硬刪除語意＝該句回到候選池）：**直接硬刪 DB 列**。
  卡片側本來就沒有東西，只有清掉 DB 紀錄才能真正讓句子重新生成。

Rows with nothing to delete on the Anki side (failure-only records, or
records whose child cards are already gone) are skipped under the default
soft-delete semantics, but hard-deleted directly from the DB under
``--allow-regen`` — that is the only way those sentences can actually
return to the generation pool.
"""

import logging

from sqlalchemy import text

from app.infrastructure.anki.client import AnkiClient
from app.infrastructure.database.corpus_database import corpus_async_session_factory
from scripts.common.database.log_repository import GeneratedLogRepository
from scripts.local_anki.common.deletion.child_deleter import run_child_deletion
from scripts.local_anki.common.deletion.profiles import build_registry

logger = logging.getLogger(__name__)


def parse_id_tokens(tokens: list[str]) -> list[int]:
    """把命令列的 id 標記解析為去重排序後的 id 清單。

    Parse CLI id tokens into a deduplicated, sorted id list.

    支援三種寫法，可混用：``555``（單一）、``555,600``（逗號分隔）、
    ``439-450``（閉區間範圍）。
    Supports single ids, comma-separated lists and inclusive ranges,
    freely mixed.

    Args:
        tokens: 命令列傳入的原始標記。Raw CLI tokens.

    Returns:
        list[int]: 去重且遞增排序的 id。Deduplicated ascending ids.

    Raises:
        ValueError: 標記格式錯誤，或範圍起點大於終點時。On malformed
            tokens or inverted ranges.
    """
    ids: set[int] = set()
    for token in tokens:
        for part in token.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                lo_str, sep, hi_str = part.partition("-")
                try:
                    lo, hi = int(lo_str), int(hi_str)
                except ValueError:
                    raise ValueError(f"無法解析的 id 範圍: '{part}'（格式: 起-迄，如 439-450）")
                if lo > hi:
                    raise ValueError(f"id 範圍起點大於終點: '{part}'")
                ids.update(range(lo, hi + 1))
            else:
                try:
                    ids.add(int(part))
                except ValueError:
                    raise ValueError(f"無法解析的 id: '{part}'")
    return sorted(ids)


async def resolve_log_rows(session, ids: list[int]) -> tuple[list[dict], list[int]]:
    """從 generated_sentences_log 撈出指定 id 的紀錄。

    Fetch the requested rows from generated_sentences_log.

    Args:
        session: 非同步資料庫連線 session。Async database session.
        ids: 要查詢的紀錄 id。Row ids to fetch.

    Returns:
        tuple[list[dict], list[int]]: (查到的紀錄, 查無資料的 id)。
        (Found rows, missing ids.)
    """
    if not ids:
        return [], []
    placeholders = ", ".join(str(int(i)) for i in ids)
    result = await session.execute(text(
        "SELECT id, project, verb_lemma, is_deleted, "
        "       master_note_id, context_note_id, cloze_note_id "
        f"FROM generated_sentences_log WHERE id IN ({placeholders})"
    ))
    rows = [
        {
            "id": int(r[0]),
            "project": r[1],
            "verb_lemma": r[2],
            "is_deleted": bool(r[3]),
            "master_note_id": int(r[4]) if r[4] is not None else None,
            "context_note_id": int(r[5]) if r[5] is not None else None,
            "cloze_note_id": int(r[6]) if r[6] is not None else None,
        }
        for r in result.fetchall()
    ]
    found_ids = {row["id"] for row in rows}
    missing = [i for i in ids if i not in found_ids]
    return rows, missing


async def run_deletion_by_log_ids(
    ids: list[int],
    *,
    dry_run: bool,
    allow_regen: bool = False,
) -> None:
    """依 DB 紀錄 id 刪除對應的卡片組（自動辨識卡片專案）。

    Delete the card sets behind the given generated_sentences_log ids,
    auto-detecting each row's project.

    Args:
        ids: generated_sentences_log 的紀錄 id 清單。Row ids to delete.
        dry_run: True 時僅預覽。Preview only when True.
        allow_regen: True 時 DB 紀錄硬刪除（該句可重新生成）。Hard-delete
            DB records so the sentences may be regenerated.
    """
    if not ids:
        logger.warning("📭 未指定任何 id。")
        return

    registry = build_registry()

    # ── 1. 解析 id → 紀錄 ──
    async with corpus_async_session_factory() as session:
        rows, missing = await resolve_log_rows(session, ids)

    for i in missing:
        logger.warning(f"⚠️ id={i}: 查無此紀錄，跳過。")

    # ── 2. 逐筆分類：完整卡片組 vs 無卡可刪 ──
    # 無卡可刪的兩種情況（純失敗紀錄／子卡已不存在於 Anki）：
    # 軟刪除語意下留著 DB 紀錄本來就能擋重新生成，跳過即可；
    # --allow-regen（硬刪除語意）下必須硬刪 DB 列，句子才會回到候選池。
    tasks_by_project: dict[str, list[dict]] = {}
    db_only_ids: list[int] = []

    anki_client = AnkiClient()
    try:
        for row in rows:
            rid = row["id"]
            label = f"id={rid} ({row['project']}, {row['verb_lemma']})"

            if row["project"] not in registry:
                logger.warning(f"⚠️ {label}: 未知的 project 值，跳過。")
                continue

            if row["cloze_note_id"] is None or row["context_note_id"] is None:
                if allow_regen:
                    logger.info(f"🧹 {label}: 純失敗紀錄（無卡片），--allow-regen → 直接硬刪 DB 列。")
                    db_only_ids.append(rid)
                else:
                    logger.warning(
                        f"⚠️ {label}: 純失敗紀錄（無卡片），軟刪除模式下跳過"
                        "（要清掉並允許重新生成請加 --allow-regen）。"
                    )
                continue

            # 子卡存活檢查（不存在的卡片會被 get_notes_info 過濾掉）
            alive = {
                n.noteId for n in await anki_client.get_notes_info(
                    [row["cloze_note_id"], row["context_note_id"]]
                )
            }
            children_alive = (
                row["cloze_note_id"] in alive and row["context_note_id"] in alive
            )

            if not children_alive:
                if allow_regen:
                    logger.info(f"🧹 {label}: 子卡已不存在於 Anki，--allow-regen → 直接硬刪 DB 列。")
                    db_only_ids.append(rid)
                else:
                    logger.warning(
                        f"⚠️ {label}: 子卡已不存在於 Anki，軟刪除模式下跳過"
                        "（要清掉並允許重新生成請加 --allow-regen）。"
                    )
                continue

            if row["is_deleted"]:
                logger.warning(f"⚠️ {label}: DB 已是軟刪除狀態，但卡片仍存在，照常處理。")

            tasks_by_project.setdefault(row["project"], []).append({
                "master_nid": row["master_note_id"],
                "cloze_nid": row["cloze_note_id"],
                "context_nid": row["context_note_id"],
            })
            logger.info(
                f"🎯 {label}: master={row['master_note_id']}, "
                f"cloze={row['cloze_note_id']}, context={row['context_note_id']}"
            )
    finally:
        await anki_client.close()

    # ── 3. 無卡可刪的紀錄：硬刪 DB 列（僅 --allow-regen 會走到這裡） ──
    if db_only_ids:
        if dry_run:
            logger.info(f"🗃️ 預計硬刪除 {len(db_only_ids)} 筆無卡紀錄: {db_only_ids} (目前為 Dry Run，未實際執行)")
        else:
            placeholders = ", ".join(str(int(i)) for i in db_only_ids)
            async with corpus_async_session_factory() as session:
                result = await session.execute(text(
                    f"DELETE FROM generated_sentences_log WHERE id IN ({placeholders})"
                ))
                await session.commit()
                # 硬刪除後收斂 AUTO_INCREMENT，避免尾端留下大段空號
                await GeneratedLogRepository().reset_auto_increment(session)
            logger.info(f"🗃️ 已硬刪除 {result.rowcount} 筆無卡紀錄: {db_only_ids}（AUTO_INCREMENT 已重置）")

    if not tasks_by_project:
        if not db_only_ids:
            logger.info("📭 沒有可執行的刪除任務。")
        return

    # ── 4. 逐專案調用共用刪除核心 ──
    # 各專案使用各自的 profile：筆記類型驗證/母卡 JSON 欄位/DB project
    # 全部自動對應，事後完整性檢查也只跑受影響的專案。
    for project_key, tasks in tasks_by_project.items():
        profile = registry[project_key]
        logger.info("")
        logger.info("=" * 50)
        logger.info(f"🚀 開始處理 {profile.display_name} 的 {len(tasks)} 筆刪除任務")
        logger.info("=" * 50)
        await run_child_deletion(
            profile,
            dry_run=dry_run,
            allow_regen=allow_regen,
            tasks=tasks,
        )
