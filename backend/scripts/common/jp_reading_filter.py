"""生卡腳本的讀音查表過濾（只讀 jp_verb_reading_judgments，不呼叫 LLM）。

Judgment-table filter for the generation scripts: read-only lookup into
jp_verb_reading_judgments, no LLM calls.

規則（docs/wip/verb_reading_judgments_FEAT_2026-09-02.md §3.3）：
- 表層不在多讀表 → 完全不查，零成本。
- 有判斷且 = 本母卡讀音 → 放行。
- 有判斷且 ≠ 本母卡讀音（含空字串「無法判定」）→ 跳過，**不寫任何紀錄**。
- 無判斷 → 放行（與判斷表不存在時的行為相同），計入「未判讀」統計以提醒
  使用者跑 ``JP_Common/judge_verb_readings.py``。
"""

import logging
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from scripts.common.database.reading_judgment_repository import ReadingJudgmentRepository
from scripts.common.jp_homograph_table import HomographEntry

logger = logging.getLogger(__name__)

PASS, SKIP, UNJUDGED = "pass", "skip", "unjudged"


def verdict(judged_reading: str | None, master_reading: str) -> str:
    """單句判定：``pass`` / ``skip`` / ``unjudged``。

    Decide for one sentence. ``judged_reading`` is None when no judgment
    exists; "" means the judge could not decide (treated as skip).
    """
    if judged_reading is None:
        return UNJUDGED
    return PASS if judged_reading == master_reading else SKIP


@dataclass
class ReadingFilter:
    """同表層多讀的候選過濾器。Candidate filter for multi-reading surfaces.

    Attributes:
        table: 多讀表（``load_homograph_table`` 的結果）。Homograph table.
        stats: ``{"skipped": n, "unjudged": n}`` 本輪統計。Run counters.
    """

    table: dict[str, HomographEntry]
    stats: dict[str, int] = field(default_factory=lambda: {"skipped": 0, "unjudged": 0})
    _cache: dict[str, dict[int, str]] = field(default_factory=dict)
    _repo: ReadingJudgmentRepository = field(default_factory=ReadingJudgmentRepository)

    def is_homograph(self, surface: str) -> bool:
        return surface in self.table

    async def _judgments(self, session: AsyncSession, surface: str) -> dict[int, str]:
        if surface not in self._cache:
            rows = await self._repo.get_by_surface(session, surface)
            self._cache[surface] = {sid: row.reading for sid, row in rows.items()}
        return self._cache[surface]

    async def apply(
        self, session: AsyncSession, surface: str, master_reading: str, rows: list[dict],
    ) -> list[dict]:
        """過濾 ES 候選列（每列含 ``script_id``）。Filter ES candidate rows.

        Args:
            session: 非同步 session。Async session.
            surface: 母卡表層（正規表記）。Canonical surface.
            master_reading: 本母卡的讀音。This master's reading.
            rows: ES 候選（dict 含 ``script_id``）。Candidate rows.

        Returns:
            list[dict]: 放行的候選（順序不變）。Kept rows in original order.
        """
        if not self.is_homograph(surface) or not master_reading:
            return rows
        judgments = await self._judgments(session, surface)
        kept: list[dict] = []
        for row in rows:
            sid = int(row["script_id"])
            v = verdict(judgments.get(sid), master_reading)
            if v == SKIP:
                self.stats["skipped"] += 1
                logger.info(
                    f"   🔤 讀音判斷：script_id={sid} 判為 '{judgments[sid] or '無法判定'}'，"
                    f"非本母卡 '{master_reading}'，跳過"
                )
                continue
            if v == UNJUDGED:
                self.stats["unjudged"] += 1
            kept.append(row)
        return kept
