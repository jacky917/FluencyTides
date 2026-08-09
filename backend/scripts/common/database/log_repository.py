"""
generated_sentences_log 資料表的 CRUD 操作封裝。
負責查詢是否已產生卡片，以及寫入/更新產生紀錄。

CRUD wrapper for the generated_sentences_log table. Responsible for
checking whether cards have been generated and for inserting/updating
generation records.
"""

import logging
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

class GeneratedLogRepository:
    """generated_sentences_log 資料表的資料存取層。

    Data-access layer for the generated_sentences_log table.
    """

    async def get_record(self, session: AsyncSession, script_id: int, verb_lemma: str) -> dict | None:
        """取得特定句子的生成紀錄。

        Fetch the generation record for a specific sentence.

        Args:
            session: 非同步資料庫連線 session。Async database session.
            script_id: 來源台詞 ID。Source script (dialogue) ID.
            verb_lemma: 動詞字典形。Dictionary form of the verb.

        Returns:
            dict | None: 紀錄摘要；查無資料時回傳 None。
            Record summary, or None when no record exists.
        """
        query = text("""
            SELECT id, is_deleted, delete_count, failure_count, master_note_id
            FROM generated_sentences_log 
            WHERE script_id = :script_id AND verb_lemma = :verb_lemma
        """)
        result = await session.execute(query, {"script_id": script_id, "verb_lemma": verb_lemma})
        row = result.fetchone()
        
        if row:
            return {
                "id": row[0],
                "is_deleted": bool(row[1]),
                "delete_count": int(row[2]),
                "failure_count": int(row[3]) if row[3] is not None else 0,
                "has_been_generated": row[4] is not None
            }
        return None

    async def get_generated_script_ids(self, session: AsyncSession, verb_lemma: str) -> list[int]:
        """取得指定動詞「已成功生成且未被軟刪除」的所有 script_id（唯讀）。

        Read-only list of every script_id that was successfully generated
        and not soft-deleted for the given verb.

        供 JP_CoreVerb 的增量平衡（docs/14_Core_Verb_Card_Plan.md §6.5）使用：
        腳本重跑時以此清單反查 MySQL 原句並分桶，把已生成句計入桶佔用，
        讓後續生成自動優先填補空桶。

        判定「已成功生成」的條件為 context_note_id 或 cloze_note_id 至少一者非空
        （僅失敗紀錄兩者皆為 NULL，不計入佔用）。

        Args:
            session: 非同步資料庫連線 session。Async database session.
            verb_lemma: 動詞字典形（去標音，如「見る」）。Dictionary form
                of the verb (no reading marks, e.g. "見る").

        Returns:
            list[int]: 依 script_id 遞增排序的清單。List sorted by
            script_id in ascending order.
        """
        query = text("""
            SELECT script_id
            FROM generated_sentences_log
            WHERE verb_lemma = :verb_lemma
              AND is_deleted = FALSE
              AND (context_note_id IS NOT NULL OR cloze_note_id IS NOT NULL)
            ORDER BY script_id ASC
        """)
        result = await session.execute(query, {"verb_lemma": verb_lemma})
        return [int(row[0]) for row in result.fetchall()]

    async def get_generated_records(self, session: AsyncSession, verb_lemma: str) -> list[dict]:
        """取得指定動詞「已成功生成且未被軟刪除」的紀錄（含子卡 note id，唯讀）。

        Read-only records (with child-card note ids) that were
        successfully generated and not soft-deleted for the given verb.

        供 JP_CoreVerb 增量平衡與 Anki 對帳使用：呼叫端可用
        context_note_id / cloze_note_id 與 Anki 實際存在的子卡交叉比對，
        以 Anki 為準決定哪些紀錄仍計入桶佔用。

        Args:
            session: 非同步資料庫連線 session。Async database session.
            verb_lemma: 動詞字典形（去標音，如「見る」）。Dictionary form
                of the verb (no reading marks, e.g. "見る").

        Returns:
            list[dict]: 依 script_id 遞增排序，每項含
            ``script_id / context_note_id / cloze_note_id``（note id 可為 None）。
            Sorted by script_id ascending; note ids may be None.
        """
        query = text("""
            SELECT script_id, context_note_id, cloze_note_id
            FROM generated_sentences_log
            WHERE verb_lemma = :verb_lemma
              AND is_deleted = FALSE
              AND (context_note_id IS NOT NULL OR cloze_note_id IS NOT NULL)
            ORDER BY script_id ASC
        """)
        result = await session.execute(query, {"verb_lemma": verb_lemma})
        return [
            {
                "script_id": int(row[0]),
                "context_note_id": int(row[1]) if row[1] is not None else None,
                "cloze_note_id": int(row[2]) if row[2] is not None else None,
            }
            for row in result.fetchall()
        ]

    async def get_logged_keys(
        self, session: AsyncSession, verb_lemma: str, source: str | None = None
    ) -> set[tuple[int, str]]:
        """取得指定動詞（可選限定 source）**全部**生成紀錄的鍵集合（唯讀）。

        Read-only key set of ALL generation records for the given verb
        (optionally restricted to one source).

        供 JP_CoreVerb 漏斗在 ES 查詢後的過濾層直接篩掉已有紀錄的句子：
        ``(script_id, verb_lemma, source, chapter)`` 全等即排除。

        **刻意不看 is_deleted**——句子被軟刪除代表使用者不想再生成該句，
        同樣排除；失敗紀錄（note id 皆空）亦排除，避免重複撞失敗句。

        Args:
            session: 非同步資料庫連線 session。Async database session.
            verb_lemma: 動詞字典形（去標音）。Dictionary form of the verb.
            source: 來源遊戲名稱；``None`` 時不限定 source。Source game
                name; ``None`` means no source restriction.

        Returns:
            set[tuple[int, str]]: ``(script_id, chapter)`` 集合
            （chapter 為 NULL 時以空字串表示）。Set of ``(script_id,
            chapter)``; NULL chapters are represented as empty strings.
        """
        sql = """
            SELECT script_id, chapter
            FROM generated_sentences_log
            WHERE verb_lemma = :verb_lemma
        """
        params: dict = {"verb_lemma": verb_lemma}
        if source is not None:
            sql += " AND source = :source"
            params["source"] = source
        result = await session.execute(text(sql), params)
        return {(int(row[0]), row[1] or "") for row in result.fetchall()}

    async def increment_failure_count(self, session: AsyncSession, script_id: int, verb_lemma: str, source: str, chapter: str, master_note_id: int, llm_model: str) -> None:
        """記錄生成失敗，遞增 failure_count。若紀錄不存在則建立一筆空紀錄。

        Record a generation failure by incrementing failure_count; an
        empty record is created if none exists yet.

        Args:
            session: 非同步資料庫連線 session。Async database session.
            script_id: 來源台詞 ID。Source script ID.
            verb_lemma: 動詞字典形。Dictionary form of the verb.
            source: 來源遊戲名稱。Source game name.
            chapter: 章節名稱。Chapter name.
            master_note_id: 觸發生成的 Anki 母卡 ID。Triggering master
                Anki note ID.
            llm_model: 使用的 LLM 模型名稱。LLM model name used.
        """
        query = text("""
            INSERT INTO generated_sentences_log 
            (script_id, verb_lemma, source, chapter, master_note_id, llm_model, failure_count)
            VALUES (:script_id, :verb_lemma, :source, :chapter, :master_note_id, :llm_model, 1)
            ON DUPLICATE KEY UPDATE 
                failure_count = failure_count + 1,
                llm_model = VALUES(llm_model),
                updated_at = CURRENT_TIMESTAMP
        """)
        await session.execute(query, {
            "script_id": script_id, 
            "verb_lemma": verb_lemma,
            "source": source,
            "chapter": chapter,
            "master_note_id": master_note_id,
            "llm_model": llm_model
        })
        await session.commit()

    async def create_or_restore_record(self, session: AsyncSession, record_data: dict) -> None:
        """新增紀錄。若存在且為軟刪除狀態，則解除軟刪除並遞增 delete_count，
        同時重置 failure_count 為 0。

        Insert a record. If it already exists in a soft-deleted state,
        restore it, increment delete_count, and reset failure_count to 0.

        Args:
            session: 非同步資料庫連線 session。Async database session.
            record_data: 紀錄欄位字典（含 script_id、verb_lemma、source、
                chapter、note id 與 llm_model）。Dict of record fields
                (script_id, verb_lemma, source, chapter, note ids,
                llm_model).
        """
        query = text("""
            INSERT INTO generated_sentences_log 
            (script_id, verb_lemma, source, chapter, master_note_id, context_note_id, cloze_note_id, llm_model, failure_count)
            VALUES 
            (:script_id, :verb_lemma, :source, :chapter, :master_note_id, :context_note_id, :cloze_note_id, :llm_model, 0)
            ON DUPLICATE KEY UPDATE 
                context_note_id = VALUES(context_note_id),
                cloze_note_id = VALUES(cloze_note_id),
                master_note_id = VALUES(master_note_id),
                llm_model = VALUES(llm_model),
                delete_count = delete_count + 1,
                failure_count = 0,
                is_deleted = FALSE,
                updated_at = CURRENT_TIMESTAMP
        """)
        
        params = {
            "script_id": record_data["script_id"],
            "verb_lemma": record_data["verb_lemma"],
            "source": record_data["source"],
            "chapter": record_data["chapter"],
            "master_note_id": record_data["master_note_id"],
            "context_note_id": record_data.get("context_note_id"),
            "cloze_note_id": record_data.get("cloze_note_id"),
            "llm_model": record_data["llm_model"],
        }
        
        await session.execute(query, params)
        await session.commit()
        
    async def soft_delete_record(self, session: AsyncSession, script_id: int, verb_lemma: str) -> None:
        """將特定紀錄標記為軟刪除。

        Mark a specific record as soft-deleted.

        Args:
            session: 非同步資料庫連線 session。Async database session.
            script_id: 來源台詞 ID。Source script ID.
            verb_lemma: 動詞字典形。Dictionary form of the verb.
        """
        query = text("""
            UPDATE generated_sentences_log 
            SET is_deleted = TRUE, updated_at = CURRENT_TIMESTAMP 
            WHERE script_id = :script_id AND verb_lemma = :verb_lemma
        """)
        await session.execute(query, {"script_id": script_id, "verb_lemma": verb_lemma})
        await session.commit()

    async def clear_all_records(self, session: AsyncSession, hard_delete: bool = False) -> None:
        """清除所有紀錄。

        Clear all records.

        若 hard_delete 為 True，則使用 TRUNCATE TABLE 清空資料表並重置自增 ID。
        否則使用 UPDATE 標記為軟刪除 (is_deleted = TRUE)。

        Args:
            session: 非同步資料庫連線 session。Async database session.
            hard_delete: True 時 TRUNCATE 清空並重置自增 ID；否則僅軟刪除。
                If True, TRUNCATE the table and reset auto-increment IDs;
                otherwise mark all rows as soft-deleted.
        """
        if hard_delete:
            query = text("TRUNCATE TABLE generated_sentences_log")
        else:
            query = text("UPDATE generated_sentences_log SET is_deleted = TRUE, updated_at = CURRENT_TIMESTAMP")
        
        await session.execute(query)
        await session.commit()

    async def smart_delete_by_note_id(self, session: AsyncSession, note_id: int, is_cloze: bool = False, dry_run: bool = False) -> str:
        """根據子卡片 ID (context 或 cloze) 進行智能刪除。

        Smart delete keyed by a child-card note ID (context or cloze).

        Args:
            session: 非同步資料庫連線 session。Async database session.
            note_id: 子卡片 note ID。Child-card note ID.
            is_cloze: True 表示以 cloze_note_id 比對，否則用 context_note_id。
                If True match on cloze_note_id, otherwise context_note_id.
            dry_run: True 時僅模擬，不實際刪除。If True, simulate without
                deleting.

        Returns:
            str: 執行的動作名稱 ("preserved", "hard_deleted", "not_found")。
            Name of the action performed.
        """
        column_name = "cloze_note_id" if is_cloze else "context_note_id"
        
        # 1. 查詢該紀錄的目前的 is_deleted 狀態
        query = text(f"SELECT is_deleted FROM generated_sentences_log WHERE {column_name} = :note_id")
        result = await session.execute(query, {"note_id": note_id})
        row = result.fetchone()
        
        if not row:
            return "not_found"
            
        is_deleted = bool(row[0])
        
        if is_deleted:
            # 已經是軟刪除，保留不硬刪
            return "preserved"
        else:
            # 原本是活躍狀態，代表使用者手動刪除，因此硬刪除
            if not dry_run:
                delete_query = text(f"DELETE FROM generated_sentences_log WHERE {column_name} = :note_id")
                await session.execute(delete_query, {"note_id": note_id})
                await session.commit()
            return "hard_deleted"

