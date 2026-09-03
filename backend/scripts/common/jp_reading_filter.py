"""生卡腳本的讀音查表過濾（只讀 jp_verb_reading_judgments，不呼叫 LLM）。

Judgment-table filter for the generation pipelines: read-only lookups into
jp_verb_reading_judgments, no LLM calls.

規則（docs/wip/verb_reading_judgments_FEAT_2026-09-02.md §3.3）：
- 表層不在多讀表 → 完全不查，零成本。
- 有判斷且 = 本母卡讀音 → 放行。
- 有判斷且 ≠ 本母卡讀音（含空字串「無法判定」）→ 跳過，**不寫任何紀錄**。
- 無判斷 → 放行（與判斷表為空時的行為相同），計入「未判讀」統計以提醒
  使用者跑 ``JP_Common/judge_verb_readings.py``。

兩條管線的接法不同，因此提供兩個入口（兩者共用同一份快取與統計）：

* :meth:`ReadingFilter.apply` —— 呼叫端手上已有候選列（JP_VerbPair 的 ES
  結果），逐列過濾並統計「未判讀」。
* :meth:`ReadingFilter.excluded_ids` —— 呼叫端把排除清單交給下游元件
  （JP_CoreVerb 的漏斗以 ``exclude_script_ids`` 在抓取階段就排除），避免
  配額浪費在讀音不符的句子上。

本模組與專案無關：專案差異只透過 ``ProjectProfile`` 進入
（:meth:`ReadingFilter.create`）。
"""

import logging
from dataclasses import dataclass, field
from typing import Callable, Sequence, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.anki.client import AnkiClient
from scripts.common.database.reading_judgment_repository import ReadingJudgmentRepository
from scripts.common.jp_homograph_table import HomographEntry, load_homograph_table
from scripts.local_anki.common.deletion.profiles import ProjectProfile

logger = logging.getLogger(__name__)

PASS, SKIP, UNJUDGED = "pass", "skip", "unjudged"

T = TypeVar("T")


def verdict(judged_reading: str | None, master_reading: str) -> str:
    """單句判定：``pass`` / ``skip`` / ``unjudged``。

    Decide for one sentence. ``judged_reading`` is None when no judgment
    exists; "" means the judge could not decide (treated as skip).

    Args:
        judged_reading: 判斷表中的讀音；``None`` 表示尚未判讀。The judged
            reading, or None when unjudged.
        master_reading: 本母卡的讀音。This master card's reading.

    Returns:
        str: ``PASS`` / ``SKIP`` / ``UNJUDGED``。
    """
    if judged_reading is None:
        return UNJUDGED
    return PASS if judged_reading == master_reading else SKIP


@dataclass
class ReadingFilter:
    """同表層多讀的候選過濾器。Candidate filter for multi-reading surfaces.

    Attributes:
        table: 多讀表（``load_homograph_table`` 的結果）。Homograph table.
        stats: 本輪統計：``skipped``（讀音不符跳過）、``unjudged``（未判讀
            放行）、``excluded``（交下游排除的 script_id 數）。Run counters.
    """

    table: dict[str, HomographEntry]
    stats: dict[str, int] = field(
        default_factory=lambda: {"skipped": 0, "unjudged": 0, "excluded": 0}
    )
    _cache: dict[str, dict[int, str]] = field(default_factory=dict)
    _repo: ReadingJudgmentRepository = field(default_factory=ReadingJudgmentRepository)

    # ------------------------------------------------------------------
    # 建構
    # ------------------------------------------------------------------

    @classmethod
    async def create(
        cls, anki_client: AnkiClient, profile: ProjectProfile, *, quiet: bool = False,
    ) -> "ReadingFilter":
        """掃該專案母卡建多讀表並回傳過濾器（無多讀表層時為 no-op 過濾器）。

        Build the homograph table from the project's master cards and return
        a filter; a project with no multi-reading surfaces yields a no-op.

        Args:
            anki_client: Anki 連線客戶端。Anki client.
            profile: 專案 profile。Project profile.
            quiet: True 時不輸出表層清單。Suppress the summary log line.

        Returns:
            ReadingFilter: 過濾器實例。The filter.
        """
        table = await load_homograph_table(anki_client, profile)
        if table and not quiet:
            logger.info(
                f"🔤 同表層多讀表層 {len(table)} 個："
                + "、".join(f"{s}({'/'.join(e.candidates)})" for s, e in sorted(table.items()))
            )
        return cls(table)

    # ------------------------------------------------------------------
    # 查詢
    # ------------------------------------------------------------------

    def is_homograph(self, surface: str) -> bool:
        """該表層是否需要讀音判定。Whether the surface needs judging."""
        return surface in self.table

    def reading_for_master(self, surface: str, master_note_id: int) -> str:
        """查該母卡在此表層的讀音（呼叫端不必自行解析標音）。

        The reading this master card carries for the surface.

        Args:
            surface: 表層（正規表記）。Canonical surface.
            master_note_id: 母卡 note id。Master note id.

        Returns:
            str: 讀音；非多讀表層或查無對應時回空字串。The reading, or "".
        """
        entry = self.table.get(surface)
        if not entry:
            return ""
        for reading, ids in entry.readings.items():
            if int(master_note_id) in ids:
                return reading
        return ""

    async def _judgments(self, session: AsyncSession, surface: str) -> dict[int, str]:
        """該表層的全部判斷（每表層只從 DB 載入一次）。Cached judgments."""
        if surface not in self._cache:
            rows = await self._repo.get_by_surface(session, surface)
            self._cache[surface] = {sid: row.reading for sid, row in rows.items()}
        return self._cache[surface]

    # ------------------------------------------------------------------
    # 兩種接法
    # ------------------------------------------------------------------

    async def apply(
        self,
        session: AsyncSession,
        surface: str,
        master_reading: str,
        rows: Sequence[T],
        *,
        key: Callable[[T], int] | None = None,
    ) -> list[T]:
        """過濾呼叫端手上的候選（順序不變），並累計統計。

        Filter candidates already in hand, preserving order.

        Args:
            session: 非同步 session。Async session.
            surface: 母卡表層（正規表記）。Canonical surface.
            master_reading: 本母卡的讀音。This master's reading.
            rows: 候選序列。Candidate sequence.
            key: 從候選取出 ``script_id``；預設讀 ``row["script_id"]``。
                Extractor for the script id.

        Returns:
            list[T]: 放行的候選。Kept candidates.
        """
        if not self.is_homograph(surface) or not master_reading:
            return list(rows)
        get_id = key or (lambda row: int(row["script_id"]))
        judgments = await self._judgments(session, surface)
        kept: list[T] = []
        for row in rows:
            sid = int(get_id(row))
            state = verdict(judgments.get(sid), master_reading)
            if state == SKIP:
                self.stats["skipped"] += 1
                logger.info(
                    f"   🔤 讀音判斷：script_id={sid} 判為 "
                    f"'{judgments[sid] or '無法判定'}'，非本母卡 '{master_reading}'，跳過"
                )
                continue
            if state == UNJUDGED:
                self.stats["unjudged"] += 1
            kept.append(row)
        return kept

    async def excluded_ids(
        self, session: AsyncSession, surface: str, master_reading: str,
    ) -> set[int]:
        """已知屬於其他讀音（或判不出）的 script_id，交下游在抓取階段排除。

        Script ids known to belong to another reading (or undetermined),
        for downstream components that filter during fetching.

        與 :meth:`apply` 的差別：這裡看不到候選，因此只統計 ``excluded``，
        不統計「未判讀」——未判讀的句子本來就不在排除清單內、自然放行。

        Args:
            session: 非同步 session。Async session.
            surface: 母卡表層。Canonical surface.
            master_reading: 本母卡的讀音。This master's reading.

        Returns:
            set[int]: 應排除的 script_id；非多讀表層時為空集合。
        """
        if not self.is_homograph(surface) or not master_reading:
            return set()
        judgments = await self._judgments(session, surface)
        excluded = {
            sid for sid, reading in judgments.items()
            if verdict(reading, master_reading) == SKIP
        }
        self.stats["excluded"] += len(excluded)
        if excluded:
            logger.info(
                f"   🔤 讀音查表：'{surface}'（本母卡 {master_reading}）"
                f"已知他讀 {len(excluded)} 句，交漏斗排除"
            )
        return excluded

    # ------------------------------------------------------------------
    # 報告
    # ------------------------------------------------------------------

    def log_summary(self) -> None:
        """輸出本輪查表統計（無多讀表層時不輸出）。Log the run summary."""
        if not self.table:
            return
        parts = [f"讀音不符跳過 {self.stats['skipped']} 句"]
        if self.stats["excluded"]:
            parts.append(f"交漏斗排除 {self.stats['excluded']} 句")
        parts.append(f"未判讀放行 {self.stats['unjudged']} 句")
        logger.info("\n   [同表層多讀查表] " + "；".join(parts))
        if self.stats["unjudged"]:
            logger.info(
                "   → 未判讀的句子可能掛錯母卡，建議先跑 "
                "scripts/fastapi_client/JP_Common/judge_verb_readings.py"
            )
