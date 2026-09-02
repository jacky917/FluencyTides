"""存量修復：把 generated_sentences_log.verb_lemma 收斂為正規表記。

Data repair: canonicalize generated_sentences_log.verb_lemma.

背景（docs/wip/dedup_canonical_lemma_FIX_2026-09-02.md §2）：生成管線曾把
「命中的搜尋關鍵字」（假名擴展 まとめる、異體 捲る/まくる）以及刪卡工具
鏈曾把「帶標音表層」（纏[まと]める）寫進 ``verb_lemma``，同一句因此被同
一動詞側重複生成。本腳本把每筆紀錄改成母卡標準表層去標音，原值移到
``search_keyword`` 保留追溯。

規則：
1. canonical = 去標音；若值落在該母卡 ``extra_search_keywords.json`` 的
   ``extra_keywords`` 中，映射回其標準表層。
2. 改寫後與同 ``(script_id, project)`` 的另一筆撞鍵時：
   - 至多一筆「活的」（未軟刪除且有子卡）→ 自動合併：保留活的（都不活
     則保留 id 最小者），硬刪其餘，``delete_count``/``failure_count``
     取最大值。
   - 兩筆以上都活 → **不動**並列出，請先用
     ``scripts/local_anki/delete_by_generated_sentences_log_id.py`` 刪掉
     冗餘卡再重跑。
3. 執行後呼叫 ``reset_auto_increment`` 收斂 id。

Usage:
    # 預設 dry-run：只列出將做的改寫/合併/衝突
    python canonicalize_verb_lemma.py

    # 實際寫入
    python canonicalize_verb_lemma.py --execute

前置：先跑 ``init_db.py`` 補上 ``search_keyword`` 欄位。
"""

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import text

_backend_dir = Path(__file__).resolve().parents[3]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))
import scripts.common.env  # noqa

from app.infrastructure.database.corpus_database import (
    corpus_async_session_factory,
    dispose_corpus_engine,
)
from scripts.common.database.log_repository import (
    PROJECT_JP_VERB_PAIR,
    GeneratedLogRepository,
)
from scripts.common.verb_lemma import canonical_verb_lemma

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

_EXTRA_KEYWORDS_PATH = (
    _backend_dir / "scripts" / "fastapi_client" / "JP_VerbPair" / "extra_search_keywords.json"
)


def load_keyword_map(path: Path = _EXTRA_KEYWORDS_PATH) -> dict[str, dict[str, str]]:
    """讀取 extra_search_keywords.json，建立 ``{母卡nid: {關鍵字: 標準表層}}``。

    Build ``{master_nid: {keyword: canonical_surface}}`` from
    extra_search_keywords.json (both the legacy list format and the newer
    dict format are accepted).

    Args:
        path: 設定檔路徑。Config file path.

    Returns:
        dict[str, dict[str, str]]: 映射表；檔案不存在時為空 dict。
    """
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    result: dict[str, dict[str, str]] = {}
    for nid, verbs in raw.items():
        mapping: dict[str, str] = {}
        for surface, value in (verbs or {}).items():
            canonical = canonical_verb_lemma(surface)
            keywords = value if isinstance(value, list) else (value or {}).get("extra_keywords", [])
            for kw in keywords:
                mapping[canonical_verb_lemma(kw)] = canonical
        result[str(nid)] = mapping
    return result


@dataclass
class Row:
    """一筆 generated_sentences_log 紀錄的修復所需欄位。Fields needed for repair."""

    id: int
    script_id: int
    verb_lemma: str
    project: str
    master_note_id: int
    is_deleted: bool
    has_card: bool
    delete_count: int
    failure_count: int
    search_keyword: str | None

    @property
    def is_live(self) -> bool:
        """未軟刪除且有子卡。Not soft-deleted and has a child card."""
        return (not self.is_deleted) and self.has_card


@dataclass
class Plan:
    """修復計畫。The repair plan."""

    # (id, new_lemma, new_search_keyword)
    updates: list[tuple[int, str, str | None]] = field(default_factory=list)
    # (keep_id, new_lemma, new_search_keyword, delete_count, failure_count, [deleted ids])
    merges: list[tuple[int, str, str | None, int, int, list[int]]] = field(default_factory=list)
    # 兩筆以上都活：[(canonical, [ids])]
    conflicts: list[tuple[str, list[int]]] = field(default_factory=list)


def canonical_for(row: Row, keyword_map: dict[str, dict[str, str]]) -> str:
    """算出一筆紀錄的正規 verb_lemma。Compute the canonical lemma for a row.

    Args:
        row: 紀錄。The row.
        keyword_map: ``load_keyword_map`` 的結果。Keyword map.

    Returns:
        str: 正規表記。Canonical lemma.
    """
    stripped = canonical_verb_lemma(row.verb_lemma)
    if row.project == PROJECT_JP_VERB_PAIR:
        return keyword_map.get(str(row.master_note_id), {}).get(stripped, stripped)
    return stripped


def _search_keyword_after(row: Row, new_lemma: str) -> str | None:
    """改寫後 search_keyword 該存什麼：既有值優先，否則存被替換掉的原值。"""
    if row.search_keyword:
        return row.search_keyword
    return row.verb_lemma if row.verb_lemma != new_lemma else None


def plan_canonicalization(rows: list[Row], keyword_map: dict[str, dict[str, str]]) -> Plan:
    """純函式：依規則產出改寫/合併/衝突計畫。

    Pure function: derive the update/merge/conflict plan.

    Args:
        rows: 全部紀錄。All rows.
        keyword_map: 關鍵字映射。Keyword map.

    Returns:
        Plan: 修復計畫。The plan.
    """
    groups: dict[tuple[int, str, str], list[Row]] = {}
    for row in rows:
        key = (row.script_id, canonical_for(row, keyword_map), row.project)
        groups.setdefault(key, []).append(row)

    plan = Plan()
    for (_, new_lemma, _), members in groups.items():
        if len(members) == 1:
            row = members[0]
            if row.verb_lemma != new_lemma:
                plan.updates.append((row.id, new_lemma, _search_keyword_after(row, new_lemma)))
            continue

        live = [m for m in members if m.is_live]
        if len(live) > 1:
            plan.conflicts.append((new_lemma, sorted(m.id for m in members)))
            continue

        keep = live[0] if live else min(members, key=lambda m: m.id)
        others = [m for m in members if m.id != keep.id]
        plan.merges.append((
            keep.id,
            new_lemma,
            _search_keyword_after(keep, new_lemma),
            max(m.delete_count for m in members),
            max(m.failure_count for m in members),
            sorted(m.id for m in others),
        ))
    return plan


async def _load_rows(session) -> list[Row]:
    result = await session.execute(text(
        "SELECT id, script_id, verb_lemma, project, master_note_id, is_deleted, "
        "       (cloze_note_id IS NOT NULL OR context_note_id IS NOT NULL) AS has_card, "
        "       delete_count, failure_count, search_keyword "
        "FROM generated_sentences_log ORDER BY id"
    ))
    return [
        Row(
            id=int(r[0]), script_id=int(r[1]), verb_lemma=r[2], project=r[3],
            master_note_id=int(r[4]), is_deleted=bool(r[5]), has_card=bool(r[6]),
            delete_count=int(r[7] or 0), failure_count=int(r[8] or 0), search_keyword=r[9],
        )
        for r in result.fetchall()
    ]


async def _has_search_keyword_column(session) -> bool:
    result = await session.execute(text(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'generated_sentences_log' "
        "AND COLUMN_NAME = 'search_keyword'"
    ))
    return int(result.scalar() or 0) > 0


async def _apply(session, plan: Plan) -> None:
    """套用計畫：先刪後改，避免改寫途中撞唯一鍵。Delete first, then update."""
    for keep_id, lemma, kw, dcount, fcount, deleted in plan.merges:
        if deleted:
            # id 皆為 int，直接內插安全；MySQL 的 IN 綁定 tuple 在不同驅動下行為不一
            id_list = ",".join(str(int(i)) for i in deleted)
            await session.execute(text(f"DELETE FROM generated_sentences_log WHERE id IN ({id_list})"))
        await session.execute(text(
            "UPDATE generated_sentences_log "
            "SET verb_lemma = :lemma, search_keyword = :kw, delete_count = :dc, failure_count = :fc "
            "WHERE id = :id"
        ), {"lemma": lemma, "kw": kw, "dc": dcount, "fc": fcount, "id": keep_id})
    for row_id, lemma, kw in plan.updates:
        await session.execute(text(
            "UPDATE generated_sentences_log SET verb_lemma = :lemma, search_keyword = :kw WHERE id = :id"
        ), {"lemma": lemma, "kw": kw, "id": row_id})
    await session.commit()


def _print_plan(plan: Plan) -> None:
    logger.info(f"\n📝 單純改寫: {len(plan.updates)} 筆")
    for row_id, lemma, kw in plan.updates:
        logger.info(f"   [{row_id}] verb_lemma → '{lemma}'  (search_keyword='{kw}')")
    logger.info(f"\n🔀 撞鍵合併: {len(plan.merges)} 組")
    for keep_id, lemma, kw, dc, fc, deleted in plan.merges:
        logger.info(
            f"   保留 [{keep_id}] → '{lemma}' (search_keyword='{kw}', delete_count={dc}, "
            f"failure_count={fc})；硬刪 {deleted}"
        )
    logger.info(f"\n🚨 兩筆以上皆活、需先刪冗餘卡: {len(plan.conflicts)} 組")
    for lemma, ids in plan.conflicts:
        logger.info(f"   '{lemma}': ids={ids}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="收斂 generated_sentences_log.verb_lemma 為正規表記")
    parser.add_argument("--execute", action="store_true", help="實際寫入（預設 dry-run）")
    args = parser.parse_args()

    keyword_map = load_keyword_map()
    logger.info(f"🔑 已載入 {sum(len(m) for m in keyword_map.values())} 個關鍵字映射")

    async with corpus_async_session_factory() as session:
        if not await _has_search_keyword_column(session):
            logger.error("❌ 資料表缺少 search_keyword 欄位，請先執行 scripts/common/database/init_db.py")
            await dispose_corpus_engine()
            return
        rows = await _load_rows(session)
        plan = plan_canonicalization(rows, keyword_map)
        _print_plan(plan)

        if not args.execute:
            logger.info("\n🧪 dry-run 結束，未寫入。加 --execute 才會實際修改。")
        elif plan.conflicts:
            logger.error("\n🛑 存在兩筆皆活的衝突，拒絕執行。請先刪除冗餘卡片後重跑。")
        else:
            await _apply(session, plan)
            await GeneratedLogRepository().reset_auto_increment(session)
            logger.info(f"\n✅ 完成：改寫 {len(plan.updates)} 筆、合併 {len(plan.merges)} 組。")
    await dispose_corpus_engine()


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
