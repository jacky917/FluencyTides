"""jp_verb_reading_judgments 資料表的存取層（讀音判斷快取）。

Data-access layer for jp_verb_reading_judgments, the reading-judgment cache.

表的語意：「這句台詞（script_id）裡的這個表層（verb_surface）讀什麼」。
它是台詞本身的屬性，與母卡無關；不進任何去重鍵、``prepare_generation``
不讀它。判斷永久有效——固定台詞的讀音不會變，會變的只有「判對沒」，
所以重判必須是明確動作（計畫 §3.1）。
"""

import logging
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

TABLE = "jp_verb_reading_judgments"


@dataclass(frozen=True)
class ReadingJudgmentRow:
    """一筆判斷。One judgment row."""

    script_id: int
    verb_surface: str
    reading: str
    llm_model: str | None


class ReadingJudgmentRepository:
    """讀音判斷快取的 CRUD。CRUD for the judgment cache."""

    async def get_by_surface(self, session: AsyncSession, surface: str) -> dict[int, ReadingJudgmentRow]:
        """取得某表層的全部判斷（生卡腳本每表層載入一次）。

        Fetch every judgment for one surface.

        Args:
            session: 非同步 session。Async session.
            surface: 表層。Surface.

        Returns:
            dict[int, ReadingJudgmentRow]: ``{script_id: row}``。
        """
        result = await session.execute(text(
            f"SELECT script_id, verb_surface, reading, llm_model FROM {TABLE} WHERE verb_surface = :s"
        ), {"s": surface})
        return {
            int(r[0]): ReadingJudgmentRow(int(r[0]), r[1], r[2] or "", r[3])
            for r in result.fetchall()
        }

    async def select_for_rejudge(
        self, session: AsyncSession, surface: str, *, empty_only: bool = False, model: str | None = None,
    ) -> set[int]:
        """列出某表層符合重判條件的 script_id。

        Script ids of one surface matching a re-judge condition.

        Args:
            session: 非同步 session。Async session.
            surface: 表層。Surface.
            empty_only: 只選 ``reading = ''``。Only undetermined rows.
            model: 只選 ``llm_model`` 等於此值。Only rows judged by this model.

        Returns:
            set[int]: script_id 集合；無條件時為空集合。Empty when no condition.
        """
        if not empty_only and model is None:
            return set()
        sql = f"SELECT script_id FROM {TABLE} WHERE verb_surface = :s"
        params: dict = {"s": surface}
        if empty_only:
            sql += " AND reading = ''"
        if model is not None:
            sql += " AND llm_model = :m"
            params["m"] = model
        result = await session.execute(text(sql), params)
        return {int(r[0]) for r in result.fetchall()}

    async def upsert_many(self, session: AsyncSession, rows: list[ReadingJudgmentRow]) -> int:
        """寫入或覆寫判斷（重判即覆寫，不留歷史版本）。

        Insert or overwrite judgments; re-judging overwrites in place.

        Args:
            session: 非同步 session。Async session.
            rows: 要寫入的判斷。Rows to upsert.

        Returns:
            int: 寫入筆數。Number of rows written.
        """
        if not rows:
            return 0
        query = text(f"""
            INSERT INTO {TABLE} (script_id, verb_surface, reading, llm_model)
            VALUES (:script_id, :verb_surface, :reading, :llm_model)
            ON DUPLICATE KEY UPDATE
                reading = VALUES(reading),
                llm_model = VALUES(llm_model),
                created_at = CURRENT_TIMESTAMP
        """)
        await session.execute(query, [
            {"script_id": r.script_id, "verb_surface": r.verb_surface, "reading": r.reading, "llm_model": r.llm_model}
            for r in rows
        ])
        await session.commit()
        return len(rows)

    async def delete_by_surface(self, session: AsyncSession, surface: str) -> int:
        """刪除某表層的全部判斷（``--rejudge`` 整表層重來）。

        Delete every judgment of one surface.

        Args:
            session: 非同步 session。Async session.
            surface: 表層。Surface.

        Returns:
            int: 刪除筆數。Rows deleted.
        """
        result = await session.execute(text(f"DELETE FROM {TABLE} WHERE verb_surface = :s"), {"s": surface})
        await session.commit()
        return result.rowcount

    async def count_by_surface(self, session: AsyncSession) -> dict[str, tuple[int, int]]:
        """各表層的判斷筆數與其中無法判定的筆數（報告用）。

        Per-surface totals and undetermined counts, for reports.

        Returns:
            dict[str, tuple[int, int]]: ``{表層: (總數, 空字串數)}``。
        """
        result = await session.execute(text(
            f"SELECT verb_surface, COUNT(*), SUM(reading = '') FROM {TABLE} GROUP BY verb_surface"
        ))
        return {r[0]: (int(r[1]), int(r[2] or 0)) for r in result.fetchall()}
