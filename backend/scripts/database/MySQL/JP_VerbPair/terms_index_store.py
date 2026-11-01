"""LLM 詞彙索引（``nlp_line_terms``，原 ``dialogue_terms_index``）的讀寫。

Reads and writes for the LLM vocabulary index ``nlp_line_terms`` (formerly
``dialogue_terms_index``), shared by ``build_llm_index.py`` and
``build_llm_index_no_context.py``.

台詞經 ``corpus_repository`` 讀（依劇情順序、只取進語料庫的台詞）；索引列的欄位依
docs/wip/corpus_db_rebuild_FEAT_2026-10-31.md D2：``script_id`` → ``line_id``、
``source='サノバウィッチ'`` → ``game_id``、``term_lemma`` → ``term_key``（``ja-JP:<原形>``）。
Lines are read through ``corpus_repository``; index rows use the new
``line_id`` / ``game_id`` / ``term_key`` columns.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.corpus_tables import NLP_LINE_TERMS, term_key
from scripts.common.database.corpus_repository import iter_lines


async def chapter_lines(session: AsyncSession, game_id: str, chapter: str) -> list[tuple[int, str]]:
    """一個章節的語料台詞 ``(台詞 ID, 台詞)``，依劇情順序。

    A chapter's corpus lines as ``(line id, dialogue)`` in story order.

    Args:
        session: 語料庫 async session。Corpus async session.
        game_id: 遊戲代號。Game id.
        chapter: 章節名（``chapter_label``）。Chapter label.

    Returns:
        list[tuple[int, str]]: 台詞清單。The lines.
    """
    return [(line.id, line.dialogue) async for line in iter_lines(session, game_id, chapter)]


async def clear_game(session: AsyncSession, game_id: str) -> None:
    """刪除一款遊戲的全部索引列（從頭重建前）。Delete a game's index rows.

    Args:
        session: 語料庫 async session。Corpus async session.
        game_id: 遊戲代號。Game id.
    """
    await session.execute(text(f"DELETE FROM {NLP_LINE_TERMS} WHERE game_id = :game_id"), {"game_id": game_id})
    await session.commit()


async def indexed_line_ids(session: AsyncSession, game_id: str) -> set[int]:
    """已有索引列的台詞 ID（斷點續跑時跳過）。Line ids that already have index rows.

    Args:
        session: 語料庫 async session。Corpus async session.
        game_id: 遊戲代號。Game id.

    Returns:
        set[int]: 台詞 ID。Line ids.
    """
    result = await session.execute(text(
        f"SELECT DISTINCT line_id FROM {NLP_LINE_TERMS} WHERE game_id = :game_id"
    ), {"game_id": game_id})
    return {int(row[0]) for row in result.fetchall()}


async def insert_terms(
    session: AsyncSession, game_id: str, rows: list[tuple[int, str, str, int, str]],
) -> None:
    """寫入索引列。Insert index rows.

    Args:
        session: 語料庫 async session。Corpus async session.
        game_id: 遊戲代號。Game id.
        rows: ``(台詞 ID, 原形, 詞類, 頻率分數, 模型)``；原形在這裡加上 ``ja-JP:``。
            ``(line id, lemma, term type, score, model)``; the lemma gets the
            ``ja-JP:`` prefix here.
    """
    if not rows:
        return
    await session.execute(text(
        f"INSERT INTO {NLP_LINE_TERMS} (game_id, line_id, term_key, term_type, frequency_score, model_name) "
        "VALUES (:game_id, :line_id, :term_key, :term_type, :score, :model)"
    ), [
        {
            "game_id": game_id,
            "line_id": line_id,
            "term_key": term_key(lemma),
            "term_type": term_type,
            "score": score,
            "model": model,
        }
        for line_id, lemma, term_type, score, model in rows
    ])
    await session.commit()
