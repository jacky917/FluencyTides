"""generated_sentences_log.project 欄位的存量資料歸屬回填腳本。

Backfill script that assigns existing generated_sentences_log rows to
their owning project (jp_verb_pair / jp_core_verb).

歸屬策略（docs/wip/child_card_deletion_toolkit_FEAT_2026-08-27.md §D1）：
1. 以 master_note_id 反查 Anki——屬於 JP_VerbPair_Master_Dark 的歸
   jp_verb_pair，屬於 JP_CoreVerb_Master_Dark 的歸 jp_core_verb。
2. 母卡已不存在（斷鏈）的紀錄無從反查，維持預設 jp_verb_pair
   （CoreVerb 為新專案），並在報告中列出供人工抽查。

預設 Dry Run 只印報告；加上 --execute 才實際 UPDATE。

Usage:
    # Dry Run (預覽歸屬結果)
    python backfill_project.py

    # 實際回填
    python backfill_project.py --execute
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# 確保 sys.path 包含 backend 根目錄並載入 .env
_backend_dir = Path(__file__).resolve().parents[3]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))
import scripts.common.env  # noqa

from sqlalchemy import text

from app.infrastructure.anki.client import AnkiClient
from app.infrastructure.database.corpus_database import (
    corpus_async_session_factory,
    dispose_corpus_engine,
)
from scripts.common.database.log_repository import (
    PROJECT_JP_CORE_VERB,
    PROJECT_JP_VERB_PAIR,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# 母卡模型 → 專案的歸屬對照。Master model to project mapping.
MASTER_MODEL_PROJECTS: dict[str, str] = {
    "JP_VerbPair_Master_Dark": PROJECT_JP_VERB_PAIR,
    "JP_CoreVerb_Master_Dark": PROJECT_JP_CORE_VERB,
}


async def main() -> None:
    """腳本主入口：反查歸屬、產出報告、（--execute 時）回填 project 欄。

    Script entry point: resolve ownership, print the report and, with
    --execute, backfill the project column.
    """
    parser = argparse.ArgumentParser(
        description="generated_sentences_log.project 存量歸屬回填"
    )
    parser.add_argument(
        "--execute", action="store_true",
        help="實際執行 UPDATE。未加上此參數時為 Dry Run（只印報告）"
    )
    args = parser.parse_args()
    dry_run: bool = not args.execute

    if dry_run:
        logger.info("⚠️ DRY-RUN 模式：只產出歸屬報告，不會修改資料庫（真實執行請加 --execute）")
    else:
        logger.warning("🚨 已啟用真實執行模式！將 UPDATE generated_sentences_log.project。")

    anki_client = AnkiClient()
    try:
        # ── 1. 從 Anki 取得各專案母卡的 note id 集合 ──
        master_owner: dict[int, str] = {}
        for model_name, project in MASTER_MODEL_PROJECTS.items():
            note_ids = await anki_client.find_notes(f'note:"{model_name}"')
            for nid in note_ids:
                master_owner[nid] = project
            logger.info(f"📇 {model_name}: {len(note_ids)} 張母卡 → {project}")

        # ── 2. 讀取全部紀錄並計算目標歸屬 ──
        async with corpus_async_session_factory() as session:
            result = await session.execute(text(
                "SELECT id, master_note_id, project FROM generated_sentences_log"
            ))
            rows = result.fetchall()
            logger.info(f"🗃️ generated_sentences_log 共 {len(rows)} 筆紀錄。")

            to_update: dict[str, list[int]] = {}
            unresolved: list[tuple[int, int]] = []  # (row_id, master_note_id)
            already_ok = 0

            for row_id, master_nid, current_project in rows:
                desired = master_owner.get(int(master_nid))
                if desired is None:
                    # 母卡已死，無從反查 → 維持預設歸屬，列入人工抽查清單
                    unresolved.append((int(row_id), int(master_nid)))
                    continue
                if desired == current_project:
                    already_ok += 1
                else:
                    to_update.setdefault(desired, []).append(int(row_id))

            # ── 3. 報告 ──
            logger.info("=" * 50)
            logger.info("📊 歸屬報告")
            logger.info("=" * 50)
            logger.info(f"✅ 歸屬已正確: {already_ok} 筆")
            for project, ids in to_update.items():
                logger.info(f"🔧 需改為 {project}: {len(ids)} 筆")
            logger.info(
                f"❓ 母卡已不存在、維持現值不動: {len(unresolved)} 筆"
                + ("（清單如下，請人工抽查）" if unresolved else "")
            )
            for row_id, master_nid in unresolved[:20]:
                logger.info(f"   - Log ID={row_id}, master_note_id={master_nid}")
            if len(unresolved) > 20:
                logger.info(f"   ... 及其他 {len(unresolved) - 20} 筆。")

            # ── 4. 執行 ──
            if not to_update:
                logger.info("✨ 沒有需要回填的紀錄。")
                return

            if dry_run:
                logger.info("👁️ Dry Run 結束，未修改任何資料。")
                return

            for project, ids in to_update.items():
                # 一次 UPDATE 一批，避免 IN 清單過長
                chunk_size = 500
                for i in range(0, len(ids), chunk_size):
                    chunk = ids[i:i + chunk_size]
                    placeholders = ", ".join(str(x) for x in chunk)
                    await session.execute(text(
                        f"UPDATE generated_sentences_log "
                        f"SET project = :project "
                        f"WHERE id IN ({placeholders})"
                    ), {"project": project})
                logger.info(f"✅ 已回填 {project}: {len(ids)} 筆")
            await session.commit()
            logger.info("🎉 回填完成。")

    except Exception as e:
        logger.error(f"💥 執行失敗: {e}")
        raise
    finally:
        await anki_client.close()
        await dispose_corpus_engine()


if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
