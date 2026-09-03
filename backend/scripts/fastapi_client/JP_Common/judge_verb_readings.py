"""日文同表層多讀動詞的讀音判讀腳本（獨立、離線、專案無關）。

Offline, project-agnostic judging script for Japanese verbs whose kanji
surface has multiple readings.

對應計畫 docs/wip/verb_reading_judgments_FEAT_2026-09-02.md §3.1。

它做什麼：
1. 依 ``--project`` 的 ``ProjectProfile`` 掃全部母卡，建同表層多讀表
   （只留讀音數 ≥ 2 的表層，例：汚す → けがす / よごす）。
2. 對每個表層收集待判台詞：ES 依**漢字表層**搜尋的候選（假名寫法的句子
   讀音本身就是明的，不需要判）＋ ``generated_sentences_log`` 該表層已生成
   的存量紀錄；扣掉 ``jp_verb_reading_judgments`` 已判過的。
3. 每批 ``--batch-size`` 句（附前後各 2 行上下文）送後端
   ``POST /api/v1/jp/verb-readings/judge``，結果寫入判斷表。
4. 結尾輸出「歸屬對帳報告」：存量紀錄的判定讀音 ≠ 所屬母卡讀音者逐筆列出，
   交人工複核。

它不做什麼：不生成卡片、不動 ``generated_sentences_log``、不直連 LLM
（模型/深度覆寫透過端點參數傳給後端；標籤以後端回應為準）。

快取規則：是否跳過只看 ``(script_id, 表層)`` 是否已有紀錄，**不比對模型**；
重判必須用 ``--rejudge*`` 明確指定（三者互斥）。

``--batch-size`` 建議值 **20**、硬上限 40：每句附前後各 2 行，20 句約 100 行
對話、數千 token，模型仍能逐句對照；超過 40 句後逐項注意力下降、遺漏或
串位的機率上升；低於 10 句則呼叫次數翻倍、省不到什麼。判讀是「看上下文
選讀音」的分類任務，推薦 ``--effort medium``——難判的句子模型應回空字串
而不是硬猜，深度加大不會讓它更誠實。

Usage:
    python judge_verb_readings.py --project jp_verb_pair --dry-run
    python judge_verb_readings.py --project jp_verb_pair --surface 汚す 止める
    python judge_verb_readings.py --project jp_verb_pair --rejudge-empty --model claude-opus-5 --effort high
"""

import argparse
import asyncio
import logging
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from sqlalchemy import text

_backend_dir = Path(__file__).resolve().parents[3]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))
import scripts.common.env  # noqa

from app.core.config import settings
from app.infrastructure.anki.client import AnkiClient
from app.infrastructure.database.corpus_database import corpus_async_session_factory, dispose_corpus_engine
from app.infrastructure.database.elasticsearch_client import dispose_elasticsearch_client, search_dialogue_by_verb
from scripts.common.database.log_repository import KNOWN_PROJECTS
from scripts.common.database.reading_judgment_repository import ReadingJudgmentRepository, ReadingJudgmentRow
from scripts.common.jp_homograph_table import HomographEntry, load_homograph_table
from scripts.local_anki.common.deletion.profiles import get_profile

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# 每次請求送幾句：推薦 20，硬上限 40（與後端 MAX_ITEMS_PER_REQUEST 一致）。理由見模組 docstring。
DEFAULT_BATCH_SIZE = 20
MAX_BATCH_SIZE = 40
CONTEXT_LINES = 2
ES_PAGE = 100


# ============================================================
# 純函式（可測）
# ============================================================

def validate_rejudge_args(args: argparse.Namespace) -> str | None:
    """檢查 rejudge 參數組合；回傳錯誤訊息或 None。

    Validate the mutually exclusive re-judge options.
    """
    modes = [bool(args.rejudge), bool(args.rejudge_empty), args.rejudge_model is not None]
    if sum(modes) > 1:
        return "--rejudge / --rejudge-empty / --rejudge-model 三者互斥，只能擇一。"
    if args.rejudge and not args.surface:
        return "--rejudge 會刪除整個表層的判斷，必須配 --surface 指定表層。"
    if not (1 <= args.batch_size <= MAX_BATCH_SIZE):
        return f"--batch-size 須在 1..{MAX_BATCH_SIZE}（推薦 {DEFAULT_BATCH_SIZE}）。"
    return None


@dataclass
class SurfacePlan:
    """一個表層的待判計畫。Pending plan for one surface."""

    surface: str
    candidates: list[str]
    es_ids: set[int] = field(default_factory=set)
    existing_ids: set[int] = field(default_factory=set)
    judged_ids: set[int] = field(default_factory=set)
    rejudge_ids: set[int] = field(default_factory=set)

    @property
    def new_ids(self) -> set[int]:
        return (self.es_ids | self.existing_ids) - self.judged_ids

    @property
    def pending_ids(self) -> list[int]:
        return sorted(self.new_ids | self.rejudge_ids)

    def batches(self, size: int) -> int:
        return math.ceil(len(self.pending_ids) / size) if self.pending_ids else 0

    def summary(self, size: int) -> str:
        return (
            f"{self.surface:6} ES 候選 {len(self.es_ids):4} + 存量 {len(self.existing_ids):3} "
            f"− 已判 {len(self.judged_ids):4} = 待判 {len(self.pending_ids):4}"
            f"（新 {len(self.new_ids)} + 重判 {len(self.rejudge_ids)}）→ {self.batches(size)} 批"
        )


def reconcile(
    existing: list[tuple[int, int, int]],  # (log_id, script_id, master_note_id)
    judgments: dict[int, str],             # script_id -> reading
    entry: HomographEntry,
) -> list[tuple[int, int, str, str]]:
    """對帳：存量紀錄的判定讀音 ≠ 所屬母卡讀音者。

    Rows whose judged reading differs from the reading of the master card
    they were filed under. Returns (log_id, script_id, master_reading,
    judged_reading); undetermined ("") is reported too.
    """
    master_reading = {mid: r for r, mids in entry.readings.items() for mid in mids}
    out = []
    for log_id, script_id, master_id in existing:
        expected = master_reading.get(master_id)
        judged = judgments.get(script_id)
        if expected is None or judged is None:
            continue
        if judged != expected:
            out.append((log_id, script_id, expected, judged))
    return out


# ============================================================
# I/O
# ============================================================

async def _fetch_es_ids(surface: str, game_name_jp: str, limit: int) -> set[int]:
    ids: set[int] = set()
    last = 0
    while len(ids) < limit:
        rows = await search_dialogue_by_verb(surface, game_name_jp, limit=min(ES_PAGE, limit - len(ids)), last_script_id=last)
        if not rows:
            break
        for r in rows:
            ids.add(int(r["script_id"]))
        last = max(int(r["script_id"]) for r in rows)
        if len(rows) < ES_PAGE:
            break
    return ids


async def _fetch_existing(session, project: str, surface: str) -> list[tuple[int, int, int]]:
    r = await session.execute(text(
        "SELECT id, script_id, master_note_id FROM generated_sentences_log "
        "WHERE project = :p AND verb_lemma = :s AND is_deleted = FALSE "
        "AND (cloze_note_id IS NOT NULL OR context_note_id IS NOT NULL)"
    ), {"p": project, "s": surface})
    return [(int(a), int(b), int(c)) for a, b, c in r.fetchall()]


async def _fetch_lines_with_context(session, script_ids: list[int]) -> dict[int, dict]:
    """取台詞與前後各 CONTEXT_LINES 行（同 source、依 id 相鄰）。"""
    out: dict[int, dict] = {}
    for sid in script_ids:
        r = await session.execute(text(
            "SELECT id, dialogue, role_name, source FROM scripts "
            "WHERE id BETWEEN :lo AND :hi ORDER BY id"
        ), {"lo": sid - CONTEXT_LINES, "hi": sid + CONTEXT_LINES})
        rows = r.fetchall()
        target = next((x for x in rows if int(x[0]) == sid), None)
        if not target:
            continue
        same = [x for x in rows if x[3] == target[3]]
        fmt = lambda x: f"{x[2]}：{x[1]}" if x[2] and x[2] not in ("-", "none") else str(x[1])
        out[sid] = {
            "line": fmt(target),
            "context_before": [fmt(x) for x in same if int(x[0]) < sid],
            "context_after": [fmt(x) for x in same if int(x[0]) > sid],
        }
    return out


async def _call_judge(client: httpx.AsyncClient, url: str, items: list[dict], model: str | None, effort: str | None) -> dict:
    payload: dict = {"items": items}
    if model:
        payload["model"] = model
    if effort:
        payload["effort"] = effort
    resp = await client.post(url, json=payload)
    if resp.status_code == 404:
        raise SystemExit("❌ 後端回 404：此後端版本尚未包含 /jp/verb-readings/judge 端點，請先部署新映像。")
    if resp.status_code == 422:
        raise SystemExit(f"❌ 後端拒絕請求（422）：{resp.text[:300]}")
    resp.raise_for_status()
    return resp.json()


# ============================================================
# main
# ============================================================

async def _run() -> None:
    parser = argparse.ArgumentParser(description="判讀同表層多讀動詞在台詞中的實際讀音，寫入 jp_verb_reading_judgments")
    parser.add_argument("--project", default="jp_verb_pair", choices=KNOWN_PROJECTS)
    parser.add_argument("--surface", nargs="*", default=None, help="只處理指定表層（可多個）")
    parser.add_argument("--max-surfaces", type=int, default=None, help="本次最多處理幾個表層（順序固定）")
    parser.add_argument("--limit", type=int, default=200, help="每表層 ES 候選上限")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help=f"每次請求送幾句（推薦 {DEFAULT_BATCH_SIZE}，上限 {MAX_BATCH_SIZE}）")
    parser.add_argument("--model", default=None, help="覆寫後端模型（傳給後端驗證，腳本不解析）")
    parser.add_argument("--effort", default=None, help="覆寫思考深度 low/medium/high（僅 claude-code）")
    parser.add_argument("--dry-run", action="store_true", help="只列待判數量與分批計畫，不呼叫 LLM")
    parser.add_argument("--rejudge", action="store_true", help="整個表層砍掉重判（須配 --surface）")
    parser.add_argument("--rejudge-empty", action="store_true", help="只重判上次判不出來（空字串）的紀錄")
    parser.add_argument("--rejudge-model", default=None, help="只重判 llm_model 等於此標籤的紀錄")
    parser.add_argument("--yes", action="store_true", help="跳過正式執行前的規模確認")
    args = parser.parse_args()

    err = validate_rejudge_args(args)
    if err:
        raise SystemExit(f"❌ {err}")

    profile = get_profile(args.project)
    repo = ReadingJudgmentRepository()

    anki = AnkiClient()
    try:
        table = await load_homograph_table(anki, profile)
    finally:
        await anki.close()
    if not table:
        logger.info(f"ℹ️ {profile.display_name} 沒有同表層多讀的動詞，無事可做。")
        return

    surfaces = sorted(table)
    if args.surface:
        unknown = [s for s in args.surface if s not in table]
        if unknown:
            raise SystemExit(f"❌ 不是多讀表層：{unknown}。可用：{surfaces}")
        surfaces = [s for s in surfaces if s in set(args.surface)]
    if args.max_surfaces:
        surfaces = surfaces[: args.max_surfaces]
    logger.info(f"📚 {profile.display_name} 多讀表層 {len(table)} 個，本次處理 {len(surfaces)} 個：{surfaces}")

    plans: list[SurfacePlan] = []
    existing_by_surface: dict[str, list[tuple[int, int, int]]] = {}
    async with corpus_async_session_factory() as session:
        for surface in surfaces:
            entry = table[surface]
            plan = SurfacePlan(surface=surface, candidates=entry.candidates)
            plan.es_ids = await _fetch_es_ids(surface, profile.game_name_jp, args.limit)
            existing = await _fetch_existing(session, args.project, surface)
            existing_by_surface[surface] = existing
            plan.existing_ids = {sid for _, sid, _ in existing}
            if args.rejudge:
                plan.rejudge_ids = set((await repo.get_by_surface(session, surface)).keys())
            else:
                plan.judged_ids = set((await repo.get_by_surface(session, surface)).keys())
                plan.rejudge_ids = await repo.select_for_rejudge(
                    session, surface, empty_only=args.rejudge_empty, model=args.rejudge_model,
                )
            plans.append(plan)

        total_pending = sum(len(p.pending_ids) for p in plans)
        total_batches = sum(p.batches(args.batch_size) for p in plans)
        logger.info("\n========== 規模摘要 ==========")
        for p in plans:
            logger.info("  " + p.summary(args.batch_size))
        logger.info(f"  合計 {len(plans)} 個表層、待判 {total_pending} 句、{total_batches} 次呼叫"
                    + (f"（model={args.model or '後端設定'}, effort={args.effort or '後端設定'}）"))

        if args.dry_run:
            logger.info("\n🧪 dry-run 結束，未呼叫 LLM、未寫入。")
            return
        if total_pending == 0:
            logger.info("\n✅ 沒有待判句子。")
            return
        if not args.yes:
            answer = input("\n以上規模確認執行？[y/N] ").strip().lower()
            if answer != "y":
                logger.info("已取消。")
                return

        base_url = getattr(settings, "SCRIPTS_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
        url = f"{base_url}/api/v1/jp/verb-readings/judge"
        headers: dict[str, str] = {}
        if settings.CF_ACCESS_CLIENT_ID and settings.CF_ACCESS_CLIENT_SECRET:
            headers["CF-Access-Client-Id"] = settings.CF_ACCESS_CLIENT_ID
            headers["CF-Access-Client-Secret"] = settings.CF_ACCESS_CLIENT_SECRET

        written = 0
        async with httpx.AsyncClient(headers=headers, timeout=None) as client:
            for p in plans:
                if args.rejudge and p.rejudge_ids:
                    n = await repo.delete_by_surface(session, p.surface)
                    logger.info(f"🗑️ --rejudge：已刪除 '{p.surface}' 既有判斷 {n} 筆")
                pending = p.pending_ids
                for i in range(0, len(pending), args.batch_size):
                    chunk = pending[i:i + args.batch_size]
                    lines = await _fetch_lines_with_context(session, chunk)
                    items = [
                        {"script_id": sid, "surface": p.surface, "candidates": p.candidates, **lines[sid]}
                        for sid in chunk if sid in lines
                    ]
                    if not items:
                        continue
                    logger.info(f"🧠 '{p.surface}' 第 {i // args.batch_size + 1}/{p.batches(args.batch_size)} 批：{len(items)} 句")
                    data = await _call_judge(client, url, items, args.model, args.effort)
                    rows = [
                        ReadingJudgmentRow(int(r["script_id"]), p.surface, r.get("reading") or "", data.get("llm_model"))
                        for r in data.get("results", [])
                    ]
                    for r in rows:
                        logger.info(f"   script_id={r.script_id}: {r.reading or '（無法判定）'}")
                    written += await repo.upsert_many(session, rows)

        logger.info(f"\n✅ 判讀完成，寫入 {written} 筆。")

        # 歸屬對帳報告
        logger.info("\n========== 歸屬對帳（存量紀錄：判定讀音 ≠ 所屬母卡讀音） ==========")
        mismatches = 0
        for p in plans:
            judged = {sid: row.reading for sid, row in (await repo.get_by_surface(session, p.surface)).items()}
            for log_id, script_id, expected, got in reconcile(existing_by_surface[p.surface], judged, table[p.surface]):
                mismatches += 1
                logger.info(f"  [{log_id}] {p.surface} script_id={script_id}：母卡讀音 {expected}，判定 {got or '（無法判定）'}")
        logger.info(f"  不一致 {mismatches} 筆" if mismatches else "  全部一致 ✅")


async def main() -> None:
    """進入點：確保 session 區塊結束後才釋放 DB 引擎與 ES 客戶端。

    Entry point; resources are disposed only after the session block has
    exited (disposing inside it leaves connections to be collected after
    the event loop is gone).
    """
    try:
        await _run()
    finally:
        await dispose_corpus_engine()
        await dispose_elasticsearch_client()


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
