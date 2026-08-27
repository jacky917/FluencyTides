"""
generated_sentences_log 資料表的 CRUD 操作封裝。
負責查詢是否已產生卡片，以及寫入/更新產生紀錄。

CRUD wrapper for the generated_sentences_log table. Responsible for
checking whether cards have been generated and for inserting/updating
generation records.

此資料表由多個卡片專案共用（JP_VerbPair / JP_CoreVerb），以 `project`
欄位隔離；所有方法皆要求呼叫端明確傳入 project，避免跨專案誤讀誤刪。
The table is shared by multiple card projects (JP_VerbPair / JP_CoreVerb)
and partitioned by the `project` column; every method requires an explicit
project from the caller to prevent cross-project reads or deletions.
"""

import logging
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# generated_sentences_log.project 的合法值。
# Valid values for generated_sentences_log.project.
PROJECT_JP_VERB_PAIR = "jp_verb_pair"
PROJECT_JP_CORE_VERB = "jp_core_verb"
KNOWN_PROJECTS = (PROJECT_JP_VERB_PAIR, PROJECT_JP_CORE_VERB)


def _validate_project(project: str) -> None:
    """驗證 project 值合法，擋下打錯字造成的靜默空結果。

    Validate the project value to catch typos that would otherwise
    silently return empty result sets.

    Args:
        project: 專案識別字串。Project identifier.

    Raises:
        ValueError: project 不在 KNOWN_PROJECTS 中時。If project is not
            one of KNOWN_PROJECTS.
    """
    if project not in KNOWN_PROJECTS:
        raise ValueError(
            f"未知的 project 值: {project!r}（合法值: {KNOWN_PROJECTS}）"
        )


class GeneratedLogRepository:
    """generated_sentences_log 資料表的資料存取層。

    Data-access layer for the generated_sentences_log table.
    """

    async def get_record(self, session: AsyncSession, script_id: int, verb_lemma: str, *, project: str) -> dict | None:
        """取得特定句子的生成紀錄。

        Fetch the generation record for a specific sentence.

        Args:
            session: 非同步資料庫連線 session。Async database session.
            script_id: 來源台詞 ID。Source script (dialogue) ID.
            verb_lemma: 動詞字典形。Dictionary form of the verb.
            project: 專案識別（見 KNOWN_PROJECTS）。Project identifier.

        Returns:
            dict | None: 紀錄摘要；查無資料時回傳 None。
            Record summary, or None when no record exists.
        """
        _validate_project(project)
        query = text("""
            SELECT id, is_deleted, delete_count, failure_count, master_note_id
            FROM generated_sentences_log
            WHERE script_id = :script_id AND verb_lemma = :verb_lemma
              AND project = :project
        """)
        result = await session.execute(query, {"script_id": script_id, "verb_lemma": verb_lemma, "project": project})
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

    async def get_generated_script_ids(self, session: AsyncSession, verb_lemma: str, *, project: str) -> list[int]:
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
            project: 專案識別。Project identifier.

        Returns:
            list[int]: 依 script_id 遞增排序的清單。List sorted by
            script_id in ascending order.
        """
        _validate_project(project)
        query = text("""
            SELECT script_id
            FROM generated_sentences_log
            WHERE verb_lemma = :verb_lemma
              AND project = :project
              AND is_deleted = FALSE
              AND (context_note_id IS NOT NULL OR cloze_note_id IS NOT NULL)
            ORDER BY script_id ASC
        """)
        result = await session.execute(query, {"verb_lemma": verb_lemma, "project": project})
        return [int(row[0]) for row in result.fetchall()]

    async def get_generated_records(self, session: AsyncSession, verb_lemma: str, *, project: str) -> list[dict]:
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
            project: 專案識別。Project identifier.

        Returns:
            list[dict]: 依 script_id 遞增排序，每項含
            ``script_id / context_note_id / cloze_note_id``（note id 可為 None）。
            Sorted by script_id ascending; note ids may be None.
        """
        _validate_project(project)
        query = text("""
            SELECT script_id, context_note_id, cloze_note_id
            FROM generated_sentences_log
            WHERE verb_lemma = :verb_lemma
              AND project = :project
              AND is_deleted = FALSE
              AND (context_note_id IS NOT NULL OR cloze_note_id IS NOT NULL)
            ORDER BY script_id ASC
        """)
        result = await session.execute(query, {"verb_lemma": verb_lemma, "project": project})
        return [
            {
                "script_id": int(row[0]),
                "context_note_id": int(row[1]) if row[1] is not None else None,
                "cloze_note_id": int(row[2]) if row[2] is not None else None,
            }
            for row in result.fetchall()
        ]

    async def get_logged_keys(
        self, session: AsyncSession, verb_lemma: str, source: str | None = None, *, project: str
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
            project: 專案識別。Project identifier.

        Returns:
            set[tuple[int, str]]: ``(script_id, chapter)`` 集合
            （chapter 為 NULL 時以空字串表示）。Set of ``(script_id,
            chapter)``; NULL chapters are represented as empty strings.
        """
        _validate_project(project)
        sql = """
            SELECT script_id, chapter
            FROM generated_sentences_log
            WHERE verb_lemma = :verb_lemma
              AND project = :project
        """
        params: dict = {"verb_lemma": verb_lemma, "project": project}
        if source is not None:
            sql += " AND source = :source"
            params["source"] = source
        result = await session.execute(text(sql), params)
        return {(int(row[0]), row[1] or "") for row in result.fetchall()}

    async def increment_failure_count(self, session: AsyncSession, script_id: int, verb_lemma: str, source: str, chapter: str, master_note_id: int, llm_model: str, *, project: str) -> None:
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
            project: 專案識別。Project identifier.
        """
        _validate_project(project)
        query = text("""
            INSERT INTO generated_sentences_log
            (script_id, verb_lemma, project, source, chapter, master_note_id, llm_model, failure_count)
            VALUES (:script_id, :verb_lemma, :project, :source, :chapter, :master_note_id, :llm_model, 1)
            ON DUPLICATE KEY UPDATE
                failure_count = failure_count + 1,
                llm_model = VALUES(llm_model),
                updated_at = CURRENT_TIMESTAMP
        """)
        await session.execute(query, {
            "script_id": script_id,
            "verb_lemma": verb_lemma,
            "project": project,
            "source": source,
            "chapter": chapter,
            "master_note_id": master_note_id,
            "llm_model": llm_model
        })
        await session.commit()

    async def create_or_restore_record(self, session: AsyncSession, record_data: dict, *, project: str) -> None:
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
            project: 專案識別。Project identifier.
        """
        _validate_project(project)
        query = text("""
            INSERT INTO generated_sentences_log
            (script_id, verb_lemma, project, source, chapter, master_note_id, context_note_id, cloze_note_id, llm_model, failure_count)
            VALUES
            (:script_id, :verb_lemma, :project, :source, :chapter, :master_note_id, :context_note_id, :cloze_note_id, :llm_model, 0)
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
            "project": project,
            "source": record_data["source"],
            "chapter": record_data["chapter"],
            "master_note_id": record_data["master_note_id"],
            "context_note_id": record_data.get("context_note_id"),
            "cloze_note_id": record_data.get("cloze_note_id"),
            "llm_model": record_data["llm_model"],
        }

        await session.execute(query, params)
        await session.commit()

    async def soft_delete_record(self, session: AsyncSession, script_id: int, verb_lemma: str, *, project: str) -> None:
        """將特定紀錄標記為軟刪除。

        Mark a specific record as soft-deleted.

        Args:
            session: 非同步資料庫連線 session。Async database session.
            script_id: 來源台詞 ID。Source script ID.
            verb_lemma: 動詞字典形。Dictionary form of the verb.
            project: 專案識別。Project identifier.
        """
        _validate_project(project)
        query = text("""
            UPDATE generated_sentences_log
            SET is_deleted = TRUE, updated_at = CURRENT_TIMESTAMP
            WHERE script_id = :script_id AND verb_lemma = :verb_lemma
              AND project = :project
        """)
        await session.execute(query, {"script_id": script_id, "verb_lemma": verb_lemma, "project": project})
        await session.commit()

    async def clear_all_records(self, session: AsyncSession, *, project: str, hard_delete: bool = False) -> None:
        """清除**指定專案**的所有紀錄。

        Clear all records belonging to one project.

        若 hard_delete 為 True，使用 DELETE 移除該專案全部紀錄；
        否則使用 UPDATE 標記為軟刪除 (is_deleted = TRUE)。
        不再提供 TRUNCATE 整表——資料表由多專案共用，整表清空會誤刪他專案資料。

        Args:
            session: 非同步資料庫連線 session。Async database session.
            project: 專案識別。Project identifier.
            hard_delete: True 時 DELETE 該專案全部紀錄；否則僅軟刪除。
                If True, DELETE all rows of the project; otherwise mark
                them as soft-deleted.
        """
        _validate_project(project)
        if hard_delete:
            query = text("DELETE FROM generated_sentences_log WHERE project = :project")
        else:
            query = text(
                "UPDATE generated_sentences_log "
                "SET is_deleted = TRUE, updated_at = CURRENT_TIMESTAMP "
                "WHERE project = :project"
            )

        await session.execute(query, {"project": project})
        await session.commit()

    async def delete_record_by_note_ids(
        self,
        session: AsyncSession,
        master_note_id: int,
        cloze_note_id: int,
        context_note_id: int,
        *,
        project: str,
        hard: bool = False,
        commit: bool = True,
    ) -> int:
        """以三個 note ID 全匹配的方式刪除一筆去重紀錄。

        Delete one dedup record matched by all three note IDs.

        供刪卡工具使用：預設軟刪除（該句不再生成，delete_count+1）；
        ``hard=True`` 時硬刪除（該句回到生成候選池）。
        For the deletion tools: soft delete by default (the sentence is
        never regenerated, delete_count+1); ``hard=True`` hard-deletes it
        (the sentence returns to the candidate pool).

        Args:
            session: 非同步資料庫連線 session。Async database session.
            master_note_id: 母卡 note ID。Master note ID.
            cloze_note_id: Cloze 子卡 note ID。Cloze child note ID.
            context_note_id: Context 子卡 note ID。Context child note ID.
            project: 專案識別。Project identifier.
            hard: True 時硬刪除。If True, hard-delete the row.
            commit: False 時不 commit，交由呼叫端控制交易邊界（供刪卡
                工具把 commit 留到 Anki 操作成功後）。If False, skip the
                commit so the caller controls the transaction boundary.

        Returns:
            int: 影響的筆數。Number of affected rows.
        """
        _validate_project(project)
        where = (
            "WHERE master_note_id = :master_nid "
            "AND cloze_note_id = :cloze_nid "
            "AND context_note_id = :context_nid "
            "AND project = :project"
        )
        if hard:
            query = text(f"DELETE FROM generated_sentences_log {where}")
        else:
            query = text(
                f"UPDATE generated_sentences_log "
                f"SET is_deleted = TRUE, delete_count = delete_count + 1, "
                f"    updated_at = CURRENT_TIMESTAMP "
                f"{where} AND is_deleted = FALSE"
            )
        result = await session.execute(query, {
            "master_nid": master_note_id,
            "cloze_nid": cloze_note_id,
            "context_nid": context_note_id,
            "project": project,
        })
        if commit:
            await session.commit()
        return result.rowcount

    async def reset_auto_increment(self, session: AsyncSession) -> None:
        """把 AUTO_INCREMENT 收斂回 max(id)+1，避免硬刪除後尾端留下大段空號。

        Clamp AUTO_INCREMENT back to max(id)+1 so hard deletions at the
        tail do not leave a large id gap for the next insert.

        InnoDB 對 ``AUTO_INCREMENT = 1`` 的語意是「設為不小於目前最大 id+1
        的最小值」，因此固定設 1 即可，不需要先查 max(id)；表中間的空號
        不受影響（也不應被重用）。
        InnoDB clamps ``AUTO_INCREMENT = 1`` up to max(id)+1, so no
        max-lookup is needed; gaps in the middle are unaffected.

        僅供硬刪除路徑（--allow-regen / cleanup）呼叫；ALTER TABLE 屬 DDL，
        MySQL 會隱式 commit。
        Called only by hard-delete paths; ALTER TABLE is DDL and commits
        implicitly in MySQL.

        Args:
            session: 非同步資料庫連線 session。Async database session.
        """
        await session.execute(text("ALTER TABLE generated_sentences_log AUTO_INCREMENT = 1"))
        await session.commit()

    async def count_record_by_note_ids(
        self,
        session: AsyncSession,
        master_note_id: int,
        cloze_note_id: int,
        context_note_id: int,
        *,
        project: str,
    ) -> int:
        """計數三 ID 全匹配且未軟刪除的紀錄（Dry Run 預覽用）。

        Count active records matched by all three note IDs (for dry-run
        previews).

        Args:
            session: 非同步資料庫連線 session。Async database session.
            master_note_id: 母卡 note ID。Master note ID.
            cloze_note_id: Cloze 子卡 note ID。Cloze child note ID.
            context_note_id: Context 子卡 note ID。Context child note ID.
            project: 專案識別。Project identifier.

        Returns:
            int: 符合的活躍筆數。Number of matching active rows.
        """
        _validate_project(project)
        query = text(
            "SELECT COUNT(*) FROM generated_sentences_log "
            "WHERE master_note_id = :master_nid "
            "AND cloze_note_id = :cloze_nid "
            "AND context_note_id = :context_nid "
            "AND project = :project AND is_deleted = FALSE"
        )
        result = await session.execute(query, {
            "master_nid": master_note_id,
            "cloze_nid": cloze_note_id,
            "context_nid": context_note_id,
            "project": project,
        })
        return int(result.scalar() or 0)
