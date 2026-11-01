"""卡片生成紀錄 ``card_gen_log`` 的 CRUD 操作封裝（原 ``generated_sentences_log``）。

CRUD wrapper for the ``card_gen_log`` table (formerly
``generated_sentences_log``). Responsible for checking whether cards have
been generated and for inserting/updating generation records.

此資料表由多個卡片類型共用（JP_VerbPair / JP_CoreVerb），以 ``card_type``
欄位（舊 ``project``）隔離；所有方法皆要求呼叫端明確傳入 project，避免跨類型誤讀誤刪。
The table is shared by several card types and partitioned by ``card_type``
(the old ``project``); every method requires an explicit project.

新庫的欄位對照（docs/wip/corpus_db_rebuild_FEAT_2026-10-31.md D2、D9 的 B）：
``script_id`` → ``line_id``、``verb_lemma`` → ``term_key``（``ja-JP:<原形>``）、
``project`` → ``card_type``、``source`` → ``game_id``；``chapter`` 拿掉，需要時由台詞查。
方法名稱與參數名照舊：呼叫端照樣傳 ``script_id`` 與原形，``term_key`` 與 ``game_id``
由這裡組出與查出，呼叫端不再傳遊戲與章節。
Method names and parameters are unchanged: callers still pass the line id
and the bare lemma; the term key and game id are derived here.

``verb_lemma`` 的語意是「母卡標準表層去標音」（``scripts.common.verb_lemma
.canonical_verb_lemma``），**不是**命中的搜尋關鍵字。寫入不同拼寫會讓唯一鍵
失效、同句重複生成（docs/archive/dedup_canonical_lemma_FIX_2026-09-02.md）。
``verb_lemma`` is the master card's furigana-stripped standard surface,
never the matched search keyword.

**卡片關聯**：建立紀錄時在同一個交易寫入 ``rel_links``（母卡→情境子卡
``core.has_context``、母卡→填空子卡 ``core.has_cloze``、子卡→台詞 ``core.example_from``、
母卡與子卡→詞 ``core.teaches``）；刪除紀錄（軟刪或硬刪）時把對應關係標成 ``deleted``，
``rel_links`` 本身不刪列（D7：紀錄表不清空）。
Relations: inserting a record writes its ``rel_links`` rows in the same
transaction; deleting a record (soft or hard) marks them ``deleted``.
"""

import json
import logging
from dataclasses import dataclass

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.corpus_tables import (
    CARD_GEN_LOG,
    CORE_CARD_RELATIONS,
    ENTITY_NOTE,
    ENTITY_SCRIPT_LINE,
    ENTITY_TERM,
    GAME_LINES,
    REL_EXAMPLE_FROM,
    REL_HAS_CLOZE,
    REL_HAS_CONTEXT,
    REL_LINKS,
    REL_STATUS_ACTIVE,
    REL_STATUS_DELETED,
    REL_TEACHES,
    lemma_of,
    term_key,
)
from scripts.common.verb_lemma import is_non_canonical_lemma

logger = logging.getLogger(__name__)

# card_gen_log.card_type 的合法值（與卡片 handler、rel_links.attrs.card_type 一致）。
# Valid values for card_gen_log.card_type.
PROJECT_JP_VERB_PAIR = "jp_verb_pair"
PROJECT_JP_CORE_VERB = "jp_core_verb"
KNOWN_PROJECTS = (PROJECT_JP_VERB_PAIR, PROJECT_JP_CORE_VERB)

# 建卡管線寫入的關係標成這個建立者。``created_by`` of relations written by the pipeline.
RELATION_CREATED_BY = "pipeline"

# 一次 UPDATE 帶多少個筆記 ID。Note ids per batched UPDATE.
_LINK_BATCH = 500

# 「成功生成」的紀錄：至少一張子卡。A successfully generated record has a child note.
_GENERATED = "(context_note_id IS NOT NULL OR cloze_note_id IS NOT NULL)"


def _null_safe_eq(column: str, param: str) -> str:
    """NULL 也算相等的比對（MySQL 的 ``<=>``）；SQLite 沒有 ``<=>``，所以寫成兩邊都通用的形式。

    NULL-safe equality (MySQL's ``<=>``) written portably, since the SQLite
    test database has no ``<=>``.

    子卡可能只有一張（另一欄是 NULL），``col = :x`` 遇到 NULL 永遠不成立，那筆紀錄就刪不到。
    A record may have only one child note; ``col = :x`` never matches NULL.

    Args:
        column: 欄位名。Column name.
        param: 綁定參數名（不含冒號）。Bind parameter name, without the colon.

    Returns:
        str: SQL 條件。The SQL condition.
    """
    return f"({column} = :{param} OR ({column} IS NULL AND :{param} IS NULL))"


# 刪卡工具以三個 note id 找一筆紀錄的條件。Match one record by its three note ids.
_NOTE_IDS_MATCH = (
    "master_note_id = :master_nid "
    f"AND {_null_safe_eq('cloze_note_id', 'cloze_nid')} "
    f"AND {_null_safe_eq('context_note_id', 'context_nid')} "
    "AND card_type = :card_type"
)


def _validate_project(project: str) -> None:
    """驗證 project 值合法，擋下打錯字造成的靜默空結果。

    Validate the project value to catch typos that would otherwise
    silently return empty result sets.

    Args:
        project: 卡片類型識別字串。Card-type identifier.

    Raises:
        ValueError: project 不在 KNOWN_PROJECTS 中時。If project is not
            one of KNOWN_PROJECTS.
    """
    if project not in KNOWN_PROJECTS:
        raise ValueError(
            f"未知的 project 值: {project!r}（合法值: {KNOWN_PROJECTS}）"
        )


def _int_or_none(value) -> int | None:
    """資料庫值轉 int，NULL 保持 None。Convert a DB value to int, keeping NULL."""
    return int(value) if value is not None else None


@dataclass(frozen=True)
class _Link:
    """一條要寫入或撤銷的關係。One relation to write or retire."""

    relation_type: str
    src_type: str
    src_id: str
    src_label: str | None
    dst_type: str
    dst_id: str
    dst_label: str | None


def _record_links(
    *, line_id: int, key: str, master: int, context: int | None, cloze: int | None,
    include_master_term: bool = True,
) -> list[_Link]:
    """一筆紀錄對應的 ``rel_links``（D8 的四種系統關係）。

    The ``rel_links`` rows implied by one record (the four system relations).

    標籤沿用遷移回填與網站的寫法：母卡是原形，子卡是 ``<原形>_context`` / ``<原形>_cloze``，
    詞是原形，台詞沒有標籤。
    Labels follow the backfill and the website: the lemma for the master,
    ``<lemma>_context`` / ``<lemma>_cloze`` for children.

    Args:
        line_id: 台詞 ID。Line id.
        key: 詞的鍵。Term key.
        master: 母卡 note id。Master note id.
        context: 情境子卡 note id。Context note id.
        cloze: 填空子卡 note id。Cloze note id.
        include_master_term: 是否包含「母卡→詞」。Whether to include master → term.

    Returns:
        list[_Link]: 關係清單。The relations.
    """
    lemma = lemma_of(key)
    links: list[_Link] = []
    for relation, child, suffix in ((REL_HAS_CONTEXT, context, "context"), (REL_HAS_CLOZE, cloze, "cloze")):
        if child is None:
            continue
        child_label = f"{lemma}_{suffix}"
        links.append(_Link(relation, ENTITY_NOTE, str(master), lemma, ENTITY_NOTE, str(child), child_label))
        links.append(_Link(REL_EXAMPLE_FROM, ENTITY_NOTE, str(child), child_label, ENTITY_SCRIPT_LINE, str(line_id), None))
        links.append(_Link(REL_TEACHES, ENTITY_NOTE, str(child), child_label, ENTITY_TERM, key, lemma))
    if include_master_term:
        links.append(_Link(REL_TEACHES, ENTITY_NOTE, str(master), lemma, ENTITY_TERM, key, lemma))
    return links


_LINK_MATCH = (
    "relation_type = :relation_type AND src_type = :src_type AND src_id = :src_id "
    "AND dst_type = :dst_type AND dst_id = :dst_id"
)


def _link_params(link: _Link) -> dict:
    """關係的比對參數。Match parameters of a relation."""
    return {
        "relation_type": link.relation_type,
        "src_type": link.src_type,
        "src_id": link.src_id,
        "dst_type": link.dst_type,
        "dst_id": link.dst_id,
    }


async def _ensure_link(session: AsyncSession, link: _Link, card_type: str) -> None:
    """確保一條關係是 active（已有就補上 ``attrs.card_type``，沒有就新增）。

    Make sure one relation exists and is active.

    網站的卡片 handler 建卡時已經寫了母卡→子卡（``core.has_context`` 等），所以這裡先找現有的
    active 列，不重複新增；``rel_links`` 沒有唯一鍵，靠這個查詢維持一條關係一列。
    The card handler may already have written master → child; an existing
    active row is reused, never duplicated.

    Args:
        session: 語料庫 async session。Corpus async session.
        link: 關係。The relation.
        card_type: 卡片類型（寫進 ``attrs``）。Card type stored in ``attrs``.
    """
    params = _link_params(link)
    row = (await session.execute(text(
        f"SELECT relation_id, attrs FROM {REL_LINKS} WHERE {_LINK_MATCH} AND status = :active LIMIT 1"
    ), {**params, "active": REL_STATUS_ACTIVE})).fetchone()
    attrs = {"card_type": card_type}
    if row is not None:
        existing = row[1]
        if isinstance(existing, str):
            try:
                existing = json.loads(existing)
            except json.JSONDecodeError:
                logger.warning(
                    "rel_links relation_id=%s 的 attrs 不是合法 JSON，改寫成 %s（原值：%r）",
                    row[0], attrs, existing,
                )
                existing = None
        if not isinstance(existing, dict) or existing.get("card_type") != card_type:
            merged = {**(existing if isinstance(existing, dict) else {}), **attrs}
            await session.execute(text(
                f"UPDATE {REL_LINKS} SET attrs = :attrs WHERE relation_id = :relation_id"
            ), {"attrs": json.dumps(merged, ensure_ascii=False), "relation_id": row[0]})
        return
    await session.execute(text(
        f"INSERT INTO {REL_LINKS} "
        "(relation_type, src_type, src_id, src_label, dst_type, dst_id, dst_label, attrs, status, created_by) "
        "VALUES (:relation_type, :src_type, :src_id, :src_label, :dst_type, :dst_id, :dst_label, "
        ":attrs, :active, :created_by)"
    ), {
        **params,
        "src_label": link.src_label,
        "dst_label": link.dst_label,
        "attrs": json.dumps(attrs, ensure_ascii=False),
        "active": REL_STATUS_ACTIVE,
        "created_by": RELATION_CREATED_BY,
    })


async def _retire_link(session: AsyncSession, link: _Link) -> None:
    """把一條關係的 active 列標成 deleted。Mark a relation's active rows deleted."""
    await session.execute(text(
        f"UPDATE {REL_LINKS} SET status = :deleted WHERE {_LINK_MATCH} AND status = :active"
    ), {**_link_params(link), "deleted": REL_STATUS_DELETED, "active": REL_STATUS_ACTIVE})


async def _retire_record_links(
    session: AsyncSession, *, card_type: str, line_id: int, key: str,
    master: int, context: int | None, cloze: int | None,
) -> None:
    """撤銷一筆紀錄的子卡關係；母卡已沒有這個詞的其他有效紀錄時，連「母卡→詞」一起撤銷。

    Retire a record's child relations; master → term goes too once the master
    has no other live record for the term.

    必須在 ``card_gen_log`` 已經改完（軟刪或硬刪）之後呼叫，才算得出「其他有效紀錄」。
    Call after the ``card_gen_log`` row has been changed.

    Args:
        session: 語料庫 async session。Corpus async session.
        card_type: 卡片類型。Card type.
        line_id: 台詞 ID。Line id.
        key: 詞的鍵。Term key.
        master: 母卡 note id。Master note id.
        context: 情境子卡 note id。Context note id.
        cloze: 填空子卡 note id。Cloze note id.
    """
    for link in _record_links(
        line_id=line_id, key=key, master=master, context=context, cloze=cloze, include_master_term=False,
    ):
        await _retire_link(session, link)
    remaining = (await session.execute(text(
        f"SELECT COUNT(*) FROM {CARD_GEN_LOG} WHERE master_note_id = :master AND term_key = :key "
        f"AND card_type = :card_type AND is_deleted = FALSE AND {_GENERATED}"
    ), {"master": master, "key": key, "card_type": card_type})).scalar()
    if not remaining:
        lemma = lemma_of(key)
        await _retire_link(session, _Link(REL_TEACHES, ENTITY_NOTE, str(master), lemma, ENTITY_TERM, key, lemma))


async def _line_game_id(session: AsyncSession, line_id: int) -> str:
    """台詞所屬的遊戲。The game a line belongs to.

    Args:
        session: 語料庫 async session。Corpus async session.
        line_id: 台詞 ID。Line id.

    Returns:
        str: ``game_id``。

    Raises:
        LookupError: ``game_lines`` 沒有這句台詞。When the line does not exist.
    """
    row = (await session.execute(text(
        f"SELECT game_id FROM {GAME_LINES} WHERE id = :line_id"
    ), {"line_id": line_id})).fetchone()
    if row is None:
        raise LookupError(f"{GAME_LINES} 沒有台詞 id={line_id}")
    return row[0]


class GeneratedLogRepository:
    """``card_gen_log`` 資料表的資料存取層。

    Data-access layer for the ``card_gen_log`` table.
    """

    async def get_record(self, session: AsyncSession, script_id: int, verb_lemma: str, *, project: str) -> dict | None:
        """取得特定句子的生成紀錄。

        Fetch the generation record for a specific sentence.

        Args:
            session: 非同步資料庫連線 session。Async database session.
            script_id: 來源台詞 ID（``line_id``）。Source line ID.
            verb_lemma: 動詞字典形。Dictionary form of the verb.
            project: 卡片類型（見 KNOWN_PROJECTS）。Card type.

        Returns:
            dict | None: 紀錄摘要；查無資料時回傳 None。
            Record summary, or None when no record exists.
        """
        _validate_project(project)
        query = text(f"""
            SELECT id, is_deleted, delete_count, failure_count, master_note_id
            FROM {CARD_GEN_LOG}
            WHERE line_id = :line_id AND term_key = :term_key
              AND card_type = :card_type
        """)
        result = await session.execute(
            query, {"line_id": script_id, "term_key": term_key(verb_lemma), "card_type": project}
        )
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
        """取得指定動詞「已成功生成且未被軟刪除」的所有台詞 ID（唯讀）。

        Read-only list of every line id that was successfully generated and
        not soft-deleted for the given verb.

        供 JP_CoreVerb 的增量平衡使用：腳本重跑時以此清單反查原句並分桶，
        把已生成句計入桶佔用。判定「已成功生成」的條件為 context_note_id 或
        cloze_note_id 至少一者非空（僅失敗紀錄兩者皆為 NULL，不計入佔用）。

        Args:
            session: 非同步資料庫連線 session。Async database session.
            verb_lemma: 動詞字典形（去標音，如「見る」）。Dictionary form
                of the verb (no reading marks, e.g. "見る").
            project: 卡片類型。Card type.

        Returns:
            list[int]: 依台詞 ID 遞增排序的清單。Sorted ascending.
        """
        _validate_project(project)
        query = text(f"""
            SELECT line_id
            FROM {CARD_GEN_LOG}
            WHERE term_key = :term_key
              AND card_type = :card_type
              AND is_deleted = FALSE
              AND {_GENERATED}
            ORDER BY line_id ASC
        """)
        result = await session.execute(query, {"term_key": term_key(verb_lemma), "card_type": project})
        return [int(row[0]) for row in result.fetchall()]

    async def get_generated_records(self, session: AsyncSession, verb_lemma: str, *, project: str) -> list[dict]:
        """取得指定動詞「已成功生成且未被軟刪除」的紀錄（含子卡 note id，唯讀）。

        Read-only records (with child-card note ids) that were
        successfully generated and not soft-deleted for the given verb.

        供 JP_CoreVerb 增量平衡與 Anki 對帳使用：呼叫端可用
        context_note_id / cloze_note_id 與 Anki 實際存在的子卡交叉比對。

        Args:
            session: 非同步資料庫連線 session。Async database session.
            verb_lemma: 動詞字典形（去標音）。Dictionary form of the verb.
            project: 卡片類型。Card type.

        Returns:
            list[dict]: 依台詞 ID 遞增排序，每項含
            ``id / script_id / master_note_id / context_note_id / cloze_note_id``（子卡 note id 可為 None）。
            Sorted by line id ascending; child note ids may be None.
        """
        _validate_project(project)
        query = text(f"""
            SELECT line_id, context_note_id, cloze_note_id, id, master_note_id
            FROM {CARD_GEN_LOG}
            WHERE term_key = :term_key
              AND card_type = :card_type
              AND is_deleted = FALSE
              AND {_GENERATED}
            ORDER BY line_id ASC
        """)
        result = await session.execute(query, {"term_key": term_key(verb_lemma), "card_type": project})
        return [
            {
                "script_id": int(row[0]),
                "context_note_id": _int_or_none(row[1]),
                "cloze_note_id": _int_or_none(row[2]),
                "id": int(row[3]),
                "master_note_id": int(row[4]),
            }
            for row in result.fetchall()
        ]

    async def get_logged_keys(
        self, session: AsyncSession, verb_lemma: str, game_id: str | None = None, *, project: str
    ) -> set[tuple[int, str]]:
        """取得指定動詞（可選限定遊戲）**全部**生成紀錄的鍵集合（唯讀）。

        Read-only key set of ALL generation records for the given verb
        (optionally restricted to one game).

        供 JP_CoreVerb 漏斗在 ES 查詢後的過濾層直接篩掉已有紀錄的句子：
        ``(台詞 ID, 章節)`` 全等即排除。章節取自台詞的 ``chapter_label``，與漏斗的
        ``metadata_fetcher`` 同一個來源。

        **刻意不看 is_deleted**——句子被軟刪除代表使用者不想再生成該句，
        同樣排除；失敗紀錄（note id 皆空）亦排除，避免重複撞失敗句。

        Args:
            session: 非同步資料庫連線 session。Async database session.
            verb_lemma: 動詞字典形（去標音）。Dictionary form of the verb.
            game_id: 遊戲代號；``None`` 時不限定。Game id; ``None`` means
                no restriction.
            project: 卡片類型。Card type.

        Returns:
            set[tuple[int, str]]: ``(台詞 ID, 章節)`` 集合
            （章節為 NULL 時以空字串表示）。NULL chapters are empty strings.
        """
        _validate_project(project)
        sql = f"""
            SELECT g.line_id, l.chapter_label
            FROM {CARD_GEN_LOG} g
            JOIN {GAME_LINES} l ON l.id = g.line_id
            WHERE g.term_key = :term_key
              AND g.card_type = :card_type
        """
        params: dict = {"term_key": term_key(verb_lemma), "card_type": project}
        if game_id is not None:
            sql += " AND g.game_id = :game_id"
            params["game_id"] = game_id
        result = await session.execute(text(sql), params)
        return {(int(row[0]), row[1] or "") for row in result.fetchall()}

    async def get_logged_dialogues(self, session: AsyncSession, verb_lemma: str, *, project: str) -> list[str]:
        """取得指定動詞**全部**生成紀錄對應的台詞原文（唯讀，JOIN ``game_lines``）。

        Read-only raw dialogue text of ALL generation records for the given
        verb (joined from ``game_lines``).

        供句子文字層去重：候選句正規化後若與這裡任一句相同，即視為同文
        異 id 的分身而跳過。與 ``get_logged_keys`` 同理**刻意不看
        is_deleted**；失敗紀錄亦納入。
        Like ``get_logged_keys`` this deliberately ignores is_deleted.

        Args:
            session: 非同步資料庫連線 session。Async database session.
            verb_lemma: 動詞正規表記。Canonical verb lemma.
            project: 卡片類型。Card type.

        Returns:
            list[str]: 台詞原文清單（帶振假名標記，未正規化，呼叫端自行處理）。
            Raw dialogue strings; normalization is the caller's job.
        """
        _validate_project(project)
        query = text(f"""
            SELECT COALESCE(l.ruby_ja_jp, l.text_ja_jp)
            FROM {CARD_GEN_LOG} g
            JOIN {GAME_LINES} l ON l.id = g.line_id
            WHERE g.term_key = :term_key
              AND g.card_type = :card_type
        """)
        result = await session.execute(query, {"term_key": term_key(verb_lemma), "card_type": project})
        return [row[0] for row in result.fetchall() if row[0]]

    async def find_non_canonical_lemmas(
        self, session: AsyncSession, keyword_map: dict[str, dict[str, str]], *, project: str
    ) -> list[tuple[str, int, int]]:
        """列出仍以非正規拼寫存放的詞（帶標音、或是該母卡的擴展關鍵字）。

        List terms still stored in a non-canonical spelling (with furigana,
        or equal to one of that master's search keywords).

        生成腳本的啟動防線：這類紀錄用正規拼寫查不到，新程式碼會把已生成
        的句子當新句重做一張。判斷**按母卡**進行（見
        ``verb_lemma.is_non_canonical_lemma``）。
        Startup guard for the generators; the check is per master.

        Args:
            session: 非同步資料庫連線 session。Async database session.
            keyword_map: ``{母卡 nid: {關鍵字: 標準表層}}``。Per-master
                keyword map.
            project: 卡片類型。Card type.

        Returns:
            list[tuple[str, int, int]]: ``(原形, master_note_id, 筆數)``，
            依原形排序；全部正規時為空。Sorted triples; empty when
            everything is canonical.
        """
        _validate_project(project)
        result = await session.execute(text(
            f"SELECT term_key, master_note_id, COUNT(*) FROM {CARD_GEN_LOG} "
            "WHERE card_type = :card_type GROUP BY term_key, master_note_id ORDER BY term_key"
        ), {"card_type": project})
        return [
            (lemma_of(row[0]), int(row[1]), int(row[2]))
            for row in result.fetchall()
            if is_non_canonical_lemma(lemma_of(row[0]), row[1], keyword_map)
        ]

    async def increment_failure_count(
        self, session: AsyncSession, script_id: int, verb_lemma: str,
        master_note_id: int, llm_model: str, *, project: str,
    ) -> None:
        """記錄生成失敗，遞增 failure_count。若紀錄不存在則建立一筆空紀錄。

        Record a generation failure by incrementing failure_count; an
        empty record is created if none exists yet.

        失敗紀錄沒有子卡，不寫 ``rel_links``。Failure records carry no child
        notes, so no relations are written.

        Args:
            session: 非同步資料庫連線 session。Async database session.
            script_id: 來源台詞 ID。Source line ID.
            verb_lemma: 動詞正規表記（母卡標準表層去標音）。Canonical
                verb lemma.
            master_note_id: 觸發生成的 Anki 母卡 ID。Triggering master
                Anki note ID.
            llm_model: 使用的 LLM 模型名稱。LLM model name used.
            project: 卡片類型。Card type.
        """
        _validate_project(project)
        game_id = await _line_game_id(session, script_id)
        query = text(f"""
            INSERT INTO {CARD_GEN_LOG}
            (game_id, line_id, term_key, card_type, master_note_id, llm_model, failure_count)
            VALUES (:game_id, :line_id, :term_key, :card_type, :master_note_id, :llm_model, 1)
            ON DUPLICATE KEY UPDATE
                failure_count = failure_count + 1,
                llm_model = VALUES(llm_model),
                updated_at = CURRENT_TIMESTAMP
        """)
        await session.execute(query, {
            "game_id": game_id,
            "line_id": script_id,
            "term_key": term_key(verb_lemma),
            "card_type": project,
            "master_note_id": master_note_id,
            "llm_model": llm_model,
        })
        await session.commit()

    async def create_or_restore_record(self, session: AsyncSession, record_data: dict, *, project: str) -> None:
        """新增紀錄並寫入它的卡片關聯；若已存在（軟刪除或失敗紀錄）則覆寫 note id、
        解除軟刪除並遞增 delete_count，同時重置 failure_count 為 0。

        Insert a record together with its relations. If it already exists
        (soft-deleted or failure-only), overwrite the note ids, restore it,
        increment delete_count and reset failure_count to 0.

        紀錄與 ``rel_links`` 在同一個交易提交。既有紀錄若指向別的子卡（重新生成），舊子卡的
        關係先標成 ``deleted``。
        The record and its relations commit together; relations of replaced
        child notes are retired first.

        Args:
            session: 非同步資料庫連線 session。Async database session.
            record_data: 紀錄欄位字典：``script_id``、``verb_lemma``、
                ``master_note_id``、``context_note_id``、``cloze_note_id``、
                ``llm_model``。Dict of record fields.
            project: 卡片類型。Card type.
        """
        _validate_project(project)
        line_id = int(record_data["script_id"])
        key = term_key(record_data["verb_lemma"])
        master = int(record_data["master_note_id"])
        context = _int_or_none(record_data.get("context_note_id"))
        cloze = _int_or_none(record_data.get("cloze_note_id"))
        game_id = await _line_game_id(session, line_id)

        previous = (await session.execute(text(
            f"SELECT master_note_id, context_note_id, cloze_note_id FROM {CARD_GEN_LOG} "
            "WHERE line_id = :line_id AND term_key = :term_key AND card_type = :card_type"
        ), {"line_id": line_id, "term_key": key, "card_type": project})).fetchone()

        query = text(f"""
            INSERT INTO {CARD_GEN_LOG}
            (game_id, line_id, term_key, card_type, master_note_id, context_note_id, cloze_note_id, llm_model, failure_count)
            VALUES
            (:game_id, :line_id, :term_key, :card_type, :master_note_id, :context_note_id, :cloze_note_id, :llm_model, 0)
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
        await session.execute(query, {
            "game_id": game_id,
            "line_id": line_id,
            "term_key": key,
            "card_type": project,
            "master_note_id": master,
            "context_note_id": context,
            "cloze_note_id": cloze,
            "llm_model": record_data["llm_model"],
        })

        if previous is not None:
            old_master, old_context, old_cloze = int(previous[0]), _int_or_none(previous[1]), _int_or_none(previous[2])
            if (old_master, old_context, old_cloze) != (master, context, cloze):
                await _retire_record_links(
                    session, card_type=project, line_id=line_id, key=key,
                    master=old_master, context=old_context, cloze=old_cloze,
                )
        for link in _record_links(line_id=line_id, key=key, master=master, context=context, cloze=cloze):
            await _ensure_link(session, link, project)
        await session.commit()

    async def soft_delete_record(self, session: AsyncSession, script_id: int, verb_lemma: str, *, project: str) -> None:
        """將特定紀錄標記為軟刪除，並撤銷它的卡片關聯。

        Mark a specific record as soft-deleted and retire its relations.

        Args:
            session: 非同步資料庫連線 session。Async database session.
            script_id: 來源台詞 ID。Source line ID.
            verb_lemma: 動詞字典形。Dictionary form of the verb.
            project: 卡片類型。Card type.
        """
        _validate_project(project)
        params = {"line_id": script_id, "term_key": term_key(verb_lemma), "card_type": project}
        rows = await self._select_records(
            session, "line_id = :line_id AND term_key = :term_key AND card_type = :card_type", params
        )
        await session.execute(text(f"""
            UPDATE {CARD_GEN_LOG}
            SET is_deleted = TRUE, updated_at = CURRENT_TIMESTAMP
            WHERE line_id = :line_id AND term_key = :term_key
              AND card_type = :card_type
        """), params)
        await self._retire_rows(session, rows)
        await session.commit()

    async def clear_all_records(self, session: AsyncSession, *, project: str, hard_delete: bool = False) -> None:
        """清除**指定卡片類型**的所有紀錄，並撤銷這些卡片的系統關係。

        Clear all records of one card type and retire the cards' system
        relations.

        若 hard_delete 為 True，使用 DELETE 移除該類型全部紀錄；
        否則使用 UPDATE 標記為軟刪除 (is_deleted = TRUE)。
        不提供 TRUNCATE 整表——資料表由多類型共用。``rel_links`` 一律只標成 deleted。

        Args:
            session: 非同步資料庫連線 session。Async database session.
            project: 卡片類型。Card type.
            hard_delete: True 時 DELETE 該類型全部紀錄；否則僅軟刪除。
                If True, DELETE all rows of the card type; otherwise mark
                them as soft-deleted.
        """
        _validate_project(project)
        result = await session.execute(text(
            f"SELECT master_note_id, context_note_id, cloze_note_id FROM {CARD_GEN_LOG} "
            "WHERE card_type = :card_type"
        ), {"card_type": project})
        note_ids = sorted({str(int(nid)) for row in result.fetchall() for nid in row if nid is not None})

        if hard_delete:
            query = text(f"DELETE FROM {CARD_GEN_LOG} WHERE card_type = :card_type")
        else:
            query = text(
                f"UPDATE {CARD_GEN_LOG} "
                "SET is_deleted = TRUE, updated_at = CURRENT_TIMESTAMP "
                "WHERE card_type = :card_type"
            )
        await session.execute(query, {"card_type": project})

        retire = text(
            f"UPDATE {REL_LINKS} SET status = :deleted "
            "WHERE status = :active AND src_type = :note AND relation_type IN :types AND src_id IN :ids"
        ).bindparams(bindparam("types", expanding=True), bindparam("ids", expanding=True))
        for start in range(0, len(note_ids), _LINK_BATCH):
            await session.execute(retire, {
                "deleted": REL_STATUS_DELETED,
                "active": REL_STATUS_ACTIVE,
                "note": ENTITY_NOTE,
                "types": list(CORE_CARD_RELATIONS),
                "ids": note_ids[start:start + _LINK_BATCH],
            })
        await session.commit()

    async def delete_record_by_note_ids(
        self,
        session: AsyncSession,
        master_note_id: int,
        cloze_note_id: int | None,
        context_note_id: int | None,
        *,
        project: str,
        hard: bool = False,
        commit: bool = True,
    ) -> int:
        """以三個 note ID 全匹配的方式刪除一筆去重紀錄，並撤銷它的卡片關聯。

        Delete one dedup record matched by all three note IDs and retire its
        relations.

        供刪卡工具使用：預設軟刪除（該句不再生成，delete_count+1）；
        ``hard=True`` 時硬刪除（該句回到生成候選池）。關係的撤銷與紀錄在同一個交易。
        For the deletion tools: soft delete by default; ``hard=True``
        hard-deletes the row. Relations are retired in the same transaction.

        子卡 note id 可為 None（紀錄只有一張子卡）：以 NULL 也算相等的方式比對。
        A child note id may be None; it is compared NULL-safely.

        Args:
            session: 非同步資料庫連線 session。Async database session.
            master_note_id: 母卡 note ID。Master note ID.
            cloze_note_id: Cloze 子卡 note ID（可為 None）。Cloze child note ID, or None.
            context_note_id: Context 子卡 note ID（可為 None）。Context child note ID, or None.
            project: 卡片類型。Card type.
            hard: True 時硬刪除。If True, hard-delete the row.
            commit: False 時不 commit，交由呼叫端控制交易邊界（供刪卡
                工具把 commit 留到 Anki 操作成功後）。If False, skip the
                commit so the caller controls the transaction boundary.

        Returns:
            int: 影響的筆數。Number of affected rows.
        """
        _validate_project(project)
        where = _NOTE_IDS_MATCH
        params = {
            "master_nid": master_note_id,
            "cloze_nid": cloze_note_id,
            "context_nid": context_note_id,
            "card_type": project,
        }
        if hard:
            rows = await self._select_records(session, where, params)
            query = text(f"DELETE FROM {CARD_GEN_LOG} WHERE {where}")
        else:
            rows = await self._select_records(session, f"{where} AND is_deleted = FALSE", params)
            query = text(
                f"UPDATE {CARD_GEN_LOG} "
                f"SET is_deleted = TRUE, delete_count = delete_count + 1, "
                f"    updated_at = CURRENT_TIMESTAMP "
                f"WHERE {where} AND is_deleted = FALSE"
            )
        result = await session.execute(query, params)
        await self._retire_rows(session, rows)
        if commit:
            await session.commit()
        return result.rowcount

    async def reset_auto_increment(self, session: AsyncSession) -> None:
        """把 AUTO_INCREMENT 收斂回 max(id)+1，避免硬刪除後尾端留下大段空號。

        Clamp AUTO_INCREMENT back to max(id)+1 so hard deletions at the
        tail do not leave a large id gap for the next insert.

        InnoDB 對 ``AUTO_INCREMENT = 1`` 的語意是「設為不小於目前最大 id+1
        的最小值」，因此固定設 1 即可；表中間的空號不受影響。
        僅供硬刪除路徑（--allow-regen / cleanup）呼叫；ALTER TABLE 屬 DDL，
        MySQL 會隱式 commit。
        Called only by hard-delete paths; ALTER TABLE is DDL and commits
        implicitly in MySQL.

        Args:
            session: 非同步資料庫連線 session。Async database session.
        """
        await session.execute(text(f"ALTER TABLE {CARD_GEN_LOG} AUTO_INCREMENT = 1"))
        await session.commit()

    async def count_record_by_note_ids(
        self,
        session: AsyncSession,
        master_note_id: int,
        cloze_note_id: int | None,
        context_note_id: int | None,
        *,
        project: str,
    ) -> int:
        """計數三 ID 全匹配且未軟刪除的紀錄（Dry Run 預覽用；子卡 id 以 NULL 也算相等比對）。

        Count active records matched by all three note IDs (for dry-run
        previews).

        Args:
            session: 非同步資料庫連線 session。Async database session.
            master_note_id: 母卡 note ID。Master note ID.
            cloze_note_id: Cloze 子卡 note ID。Cloze child note ID.
            context_note_id: Context 子卡 note ID。Context child note ID.
            project: 卡片類型。Card type.

        Returns:
            int: 符合的活躍筆數。Number of matching active rows.
        """
        _validate_project(project)
        query = text(
            f"SELECT COUNT(*) FROM {CARD_GEN_LOG} WHERE {_NOTE_IDS_MATCH} AND is_deleted = FALSE"
        )
        result = await session.execute(query, {
            "master_nid": master_note_id,
            "cloze_nid": cloze_note_id,
            "context_nid": context_note_id,
            "card_type": project,
        })
        return int(result.scalar() or 0)

    async def get_record_ids_by_note_ids(
        self,
        session: AsyncSession,
        master_note_id: int,
        cloze_note_id: int | None,
        context_note_id: int | None,
        *,
        project: str,
    ) -> list[int]:
        """三個 note ID 全匹配的紀錄 id（不分是否軟刪除；刪卡工具出錯時記錄用）。

        Ids of the records matched by all three note ids (soft-deleted or not),
        so the deletion tool can name them when something goes wrong.

        Args:
            session: 非同步資料庫連線 session。Async database session.
            master_note_id: 母卡 note ID。Master note ID.
            cloze_note_id: Cloze 子卡 note ID（可為 None）。Cloze child note ID.
            context_note_id: Context 子卡 note ID（可為 None）。Context child note ID.
            project: 卡片類型。Card type.

        Returns:
            list[int]: 紀錄 id，遞增排序。Record ids, ascending.
        """
        _validate_project(project)
        result = await session.execute(text(
            f"SELECT id FROM {CARD_GEN_LOG} WHERE {_NOTE_IDS_MATCH} ORDER BY id"
        ), {
            "master_nid": master_note_id,
            "cloze_nid": cloze_note_id,
            "context_nid": context_note_id,
            "card_type": project,
        })
        return [int(row[0]) for row in result.fetchall()]

    # ------------------------------------------------------------------
    # 刪卡工具、完整性檢查與維運腳本用的方法
    # Methods for the deletion toolkit, integrity check and maintenance scripts
    # ------------------------------------------------------------------

    async def get_records_by_ids(self, session: AsyncSession, ids: list[int]) -> list[dict]:
        """依紀錄主鍵取紀錄（不限卡片類型，供以紀錄 id 刪卡的工具自動辨識類型）。

        Fetch records by primary key regardless of card type.

        Args:
            session: 非同步資料庫連線 session。Async database session.
            ids: 紀錄 id。Record ids.

        Returns:
            list[dict]: 每筆含 ``id / project / verb_lemma / is_deleted / script_id /
            master_note_id / context_note_id / cloze_note_id``。One dict per record.
        """
        if not ids:
            return []
        query = text(
            "SELECT id, card_type, term_key, is_deleted, master_note_id, context_note_id, cloze_note_id, line_id "
            f"FROM {CARD_GEN_LOG} WHERE id IN :ids"
        ).bindparams(bindparam("ids", expanding=True))
        result = await session.execute(query, {"ids": [int(i) for i in ids]})
        return [
            {
                "id": int(r[0]),
                "project": r[1],
                "verb_lemma": lemma_of(r[2]),
                "is_deleted": bool(r[3]),
                "master_note_id": _int_or_none(r[4]),
                "context_note_id": _int_or_none(r[5]),
                "cloze_note_id": _int_or_none(r[6]),
                "script_id": _int_or_none(r[7]),
            }
            for r in result.fetchall()
        ]

    async def hard_delete_by_ids(self, session: AsyncSession, ids: list[int]) -> int:
        """依紀錄主鍵硬刪除紀錄，並撤銷它們的卡片關聯（同一個交易，最後 commit）。

        Hard-delete records by primary key and retire their relations.

        Args:
            session: 非同步資料庫連線 session。Async database session.
            ids: 紀錄 id。Record ids.

        Returns:
            int: 刪除的筆數。Rows deleted.
        """
        if not ids:
            return 0
        params = {"ids": [int(i) for i in ids]}
        rows = await self._select_records(session, "id IN :ids", params, expanding=("ids",))
        result = await session.execute(
            text(f"DELETE FROM {CARD_GEN_LOG} WHERE id IN :ids").bindparams(bindparam("ids", expanding=True)),
            params,
        )
        await self._retire_rows(session, rows)
        await session.commit()
        return result.rowcount

    async def mark_skipped(
        self, session: AsyncSession, script_id: int, verb_lemma: str, *, project: str, failure_count: int = 9,
    ) -> int:
        """把既有紀錄的 failure_count 設成固定值，讓之後的生成永久跳過這句。

        Pin a record's failure_count so future runs skip the sentence.

        Args:
            session: 非同步資料庫連線 session。Async database session.
            script_id: 台詞 ID。Line id.
            verb_lemma: 動詞正規表記。Canonical verb lemma.
            project: 卡片類型。Card type.
            failure_count: 要寫入的值。Value to write.

        Returns:
            int: 符合的筆數；0 表示沒有這筆紀錄（呼叫端應警告）。Rows matched; 0 means
            no such record (callers should warn).
        """
        _validate_project(project)
        result = await session.execute(text(
            f"UPDATE {CARD_GEN_LOG} SET failure_count = :failure_count "
            "WHERE line_id = :line_id AND term_key = :term_key AND card_type = :card_type"
        ), {
            "failure_count": failure_count,
            "line_id": script_id,
            "term_key": term_key(verb_lemma),
            "card_type": project,
        })
        await session.commit()
        return result.rowcount

    async def get_active_records(self, session: AsyncSession, *, project: str) -> list[dict]:
        """某卡片類型全部未軟刪除的紀錄（完整性檢查用）。

        Every record of a card type that is not soft-deleted.

        Args:
            session: 非同步資料庫連線 session。Async database session.
            project: 卡片類型。Card type.

        Returns:
            list[dict]: 每筆含 ``id / game_id / master / context / cloze``。One dict per record.
        """
        _validate_project(project)
        result = await session.execute(text(
            "SELECT id, game_id, master_note_id, context_note_id, cloze_note_id "
            f"FROM {CARD_GEN_LOG} WHERE is_deleted = FALSE AND card_type = :card_type"
        ), {"card_type": project})
        return [
            {
                "id": int(r[0]),
                "game_id": r[1],
                "master": _int_or_none(r[2]),
                "context": _int_or_none(r[3]),
                "cloze": _int_or_none(r[4]),
            }
            for r in result.fetchall()
        ]

    async def soft_delete_by_ids(self, session: AsyncSession, ids: list[int], *, project: str) -> None:
        """依紀錄主鍵軟刪除並撤銷關聯（完整性檢查清掉斷鏈紀錄用，最後 commit）。

        Soft-delete records by primary key and retire their relations.

        Args:
            session: 非同步資料庫連線 session。Async database session.
            ids: 紀錄 id。Record ids.
            project: 卡片類型（只動這個類型的紀錄）。Card type.
        """
        _validate_project(project)
        if not ids:
            return
        params = {"ids": [int(i) for i in ids], "card_type": project}
        where = "id IN :ids AND card_type = :card_type"
        rows = await self._select_records(session, where, params, expanding=("ids",))
        await session.execute(text(
            f"UPDATE {CARD_GEN_LOG} SET is_deleted = TRUE, updated_at = CURRENT_TIMESTAMP WHERE {where}"
        ).bindparams(bindparam("ids", expanding=True)), params)
        await self._retire_rows(session, rows)
        await session.commit()

    async def repair_record(
        self, session: AsyncSession, row_id: int, *, master_note_id: int, context_note_id: int,
        cloze_note_id: int, restore: bool, project: str,
    ) -> int:
        """完整性修復：把紀錄的 note id 對回 Anki 實際的卡片（``restore`` 時一併解除軟刪除）。

        Integrity repair: point a record at the notes that exist in Anki,
        optionally restoring it from soft deletion.

        舊 note id 的關係撤銷，新 note id 的關係寫入；同一個交易，最後 commit。
        Relations of the old note ids are retired and new ones written.

        Args:
            session: 非同步資料庫連線 session。Async database session.
            row_id: 紀錄 id。Record id.
            master_note_id: 母卡 note id。Master note id.
            context_note_id: 情境子卡 note id。Context note id.
            cloze_note_id: 填空子卡 note id。Cloze note id.
            restore: True 時解除軟刪除。Restore from soft deletion when True.
            project: 卡片類型。Card type.

        Returns:
            int: 更新的筆數；0 表示這個卡片類型沒有這筆紀錄（呼叫端應警告）。Rows updated;
            0 means no such record for the card type (callers should warn).
        """
        _validate_project(project)
        rows = await self._select_records(session, "id = :row_id AND card_type = :card_type",
                                          {"row_id": row_id, "card_type": project})
        restore_sql = "is_deleted = FALSE, " if restore else ""
        result = await session.execute(text(
            f"UPDATE {CARD_GEN_LOG} SET {restore_sql}context_note_id = :ctx_nid, cloze_note_id = :cloze_nid, "
            "master_note_id = :master_nid, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = :row_id AND card_type = :card_type"
        ), {
            "ctx_nid": context_note_id,
            "cloze_nid": cloze_note_id,
            "master_nid": master_note_id,
            "row_id": row_id,
            "card_type": project,
        })
        new_ids = (master_note_id, context_note_id, cloze_note_id)
        await self._retire_rows(
            session, [row for row in rows if (row["master"], row["context"], row["cloze"]) != new_ids]
        )
        for row in rows:
            for link in _record_links(
                line_id=row["line_id"], key=row["term_key"], master=master_note_id,
                context=context_note_id, cloze=cloze_note_id,
            ):
                await _ensure_link(session, link, project)
        await session.commit()
        return result.rowcount

    async def insert_repaired_record(
        self, session: AsyncSession, *, script_id: int, verb_lemma: str, master_note_id: int,
        context_note_id: int, cloze_note_id: int, project: str,
    ) -> int:
        """完整性修復：為 Anki 裡有、紀錄裡沒有的卡片組補一筆紀錄與它的關聯。

        Integrity repair: add the missing record (and relations) for a card
        set that exists in Anki.

        Args:
            session: 非同步資料庫連線 session。Async database session.
            script_id: 台詞 ID。Line id.
            verb_lemma: 動詞正規表記。Canonical verb lemma.
            master_note_id: 母卡 note id。Master note id.
            context_note_id: 情境子卡 note id。Context note id.
            cloze_note_id: 填空子卡 note id。Cloze note id.
            project: 卡片類型。Card type.

        Returns:
            int: 新紀錄的 id。The new record id.
        """
        _validate_project(project)
        key = term_key(verb_lemma)
        game_id = await _line_game_id(session, script_id)
        result = await session.execute(text(
            f"INSERT INTO {CARD_GEN_LOG} "
            "(game_id, line_id, term_key, card_type, master_note_id, context_note_id, cloze_note_id) "
            "VALUES (:game_id, :line_id, :term_key, :card_type, :master_nid, :ctx_nid, :cloze_nid)"
        ), {
            "game_id": game_id,
            "line_id": script_id,
            "term_key": key,
            "card_type": project,
            "master_nid": master_note_id,
            "ctx_nid": context_note_id,
            "cloze_nid": cloze_note_id,
        })
        new_id = result.lastrowid
        for link in _record_links(
            line_id=script_id, key=key, master=master_note_id, context=context_note_id, cloze=cloze_note_id,
        ):
            await _ensure_link(session, link, project)
        await session.commit()
        return new_id

    # ------------------------------------------------------------------

    async def _select_records(
        self, session: AsyncSession, where: str, params: dict, *, expanding: tuple[str, ...] = (),
    ) -> list[dict]:
        """取出即將被改動的紀錄（撤銷關聯需要它們的台詞、詞與 note id）。

        Read the records about to change; retiring relations needs their
        line, term and note ids.
        """
        query = text(
            "SELECT id, card_type, line_id, term_key, master_note_id, context_note_id, cloze_note_id "
            f"FROM {CARD_GEN_LOG} WHERE {where}"
        )
        if expanding:
            query = query.bindparams(*(bindparam(name, expanding=True) for name in expanding))
        result = await session.execute(query, params)
        return [
            {
                "id": int(r[0]),
                "card_type": r[1],
                "line_id": int(r[2]),
                "term_key": r[3],
                "master": int(r[4]),
                "context": _int_or_none(r[5]),
                "cloze": _int_or_none(r[6]),
            }
            for r in result.fetchall()
        ]

    async def _retire_rows(self, session: AsyncSession, rows: list[dict]) -> None:
        """撤銷多筆紀錄的關聯（不 commit）。Retire the relations of several records."""
        for row in rows:
            await _retire_record_links(
                session, card_type=row["card_type"], line_id=row["line_id"], key=row["term_key"],
                master=row["master"], context=row["context"], cloze=row["cloze"],
            )
