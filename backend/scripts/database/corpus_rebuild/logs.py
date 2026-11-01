"""遷移卡片紀錄、詞彙索引、讀音判定，並回填 rel_links（計畫書 D2、D8）。

Migrate the card log, term index and reading judgments from the legacy DB
into the multi-game tables, then register relation types and backfill
``rel_links`` from the card log.

欄位轉換 / column mapping:
- ``script_id`` → ``line_id``；``source`` → ``game_id``；``chapter`` 拿掉；
- ``verb_lemma`` / ``term_lemma`` → ``term_key``（前面加 ``ja-JP:``）；
- ``project`` → ``card_type``。
"""

from __future__ import annotations

import json

from scripts.database.corpus_rebuild.content import GAME_ID, GAME_PROFILE

LANG = "ja-JP"
ENTITY_TYPES = [
    ("note", "Anki ノート", "Anki 筆記", "anki", "int"),
    ("script_line", "台詞", "台詞", "game_lines", "int"),
    ("scene", "シーン", "場景", "game_scenes", "int"),
    ("speaker", "話者", "說話者", "game_speakers", "int"),
    ("bgm_track", "BGM", "BGM", "game_bgm_tracks", "int"),
    ("term", "語", "詞", "nlp_terms", "string"),
]
REL_TYPES = [
    ("core.has_context", "system", "文脈カード", "情境子卡", ["note"], ["note"]),
    ("core.has_cloze", "system", "穴埋めカード", "填空子卡", ["note"], ["note"]),
    ("core.example_from", "system", "例文の出典", "例句出處", ["note"], ["script_line"]),
    ("core.teaches", "system", "学ぶ語", "學習的詞", ["note"], ["term"]),
    # 網站 SQLite relation_types 的使用者類型 / user types from the site's SQLite
    ("user.synonym", "user", "類義", "同義", ["note"], ["note"]),
    ("user.conversation", "user", "会話", "對話", ["note"], ["note"]),
]


def _auto_increment(cur, db: str, table: str) -> int | None:
    cur.execute("SELECT AUTO_INCREMENT FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
                (db, table))
    row = cur.fetchone()
    return row[0] if row else None


def migrate(cur, old_db: str) -> None:
    """在同一個交易中搬移紀錄表並回填關係。Migrate log tables and backfill relations."""
    o = f"`{old_db}`"
    cur.execute(
        f"""INSERT INTO card_gen_log (id, game_id, line_id, term_key, card_type, master_note_id,
                context_note_id, cloze_note_id, is_deleted, delete_count, failure_count, llm_model,
                created_at, updated_at)
            SELECT id, %s, script_id, CONCAT(%s, ':', verb_lemma), project, master_note_id,
                context_note_id, cloze_note_id, is_deleted, delete_count, failure_count, llm_model,
                created_at, updated_at
            FROM {o}.generated_sentences_log""",
        (GAME_ID, LANG),
    )
    cur.execute(
        f"""INSERT INTO nlp_line_terms (id, game_id, line_id, term_key, term_type, frequency_score, model_name)
            SELECT id, %s, script_id, CONCAT(%s, ':', term_lemma), term_type, frequency_score, model_name
            FROM {o}.dialogue_terms_index WHERE source = %s""",
        (GAME_ID, LANG, GAME_PROFILE["legacy_source"]),
    )
    cur.execute(
        f"""INSERT INTO nlp_line_readings (line_id, verb_surface, reading, llm_model, created_at)
            SELECT script_id, verb_surface, reading, llm_model, created_at FROM {o}.jp_verb_reading_judgments"""
    )
    for table, old_table in (("card_gen_log", "generated_sentences_log"), ("nlp_line_terms", "dialogue_terms_index")):
        ai = _auto_increment(cur, old_db, old_table)
        if ai:
            cur.execute(f"ALTER TABLE {table} AUTO_INCREMENT = {int(ai)}")

    cur.executemany(
        "INSERT INTO rel_entity_types (entity_type, name_ja_jp, name_zh_hant, source, id_kind) VALUES (%s,%s,%s,%s,%s)",
        ENTITY_TYPES,
    )
    cur.executemany(
        "INSERT INTO rel_types (relation_type, owner, name_ja_jp, name_zh_hant, src_types, dst_types) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        [(t, ow, ja, zh, json.dumps(s), json.dumps(d)) for t, ow, ja, zh, s, d in REL_TYPES],
    )
    backfill_relations(cur)


def backfill_relations(cur) -> None:
    """由 card_gen_log 回填 rel_links（計畫書 D8）。Backfill rel_links from card_gen_log.

    標籤沿用網站 SQLite 的寫法：母卡是詞，子卡是 ``<詞>_context`` / ``<詞>_cloze``。
    Labels follow the site's SQLite convention.
    """
    lemma = "SUBSTRING(term_key, LOCATE(':', term_key) + 1)"
    status = "IF(is_deleted, 'deleted', 'active')"
    attrs = "JSON_OBJECT('card_type', card_type)"
    ins = ("INSERT INTO rel_links (relation_type, src_type, src_id, src_label, dst_type, dst_id, dst_label, "
           "attrs, status, created_by, created_at) ")
    for child, suffix in (("context_note_id", "context"), ("cloze_note_id", "cloze")):
        cur.execute(ins + f"""
            SELECT 'core.has_{suffix}', 'note', CAST(master_note_id AS CHAR), {lemma},
                   'note', CAST({child} AS CHAR), CONCAT({lemma}, '_{suffix}'),
                   {attrs}, {status}, 'migration', created_at
            FROM card_gen_log WHERE {child} IS NOT NULL""")
        cur.execute(ins + f"""
            SELECT 'core.example_from', 'note', CAST({child} AS CHAR), CONCAT({lemma}, '_{suffix}'),
                   'script_line', CAST(line_id AS CHAR), NULL,
                   {attrs}, {status}, 'migration', created_at
            FROM card_gen_log WHERE {child} IS NOT NULL""")
        cur.execute(ins + f"""
            SELECT 'core.teaches', 'note', CAST({child} AS CHAR), CONCAT({lemma}, '_{suffix}'),
                   'term', term_key, {lemma},
                   {attrs}, {status}, 'migration', created_at
            FROM card_gen_log WHERE {child} IS NOT NULL""")
    # 母卡 → 詞：同一張母卡只寫一次；全部子卡都刪掉時才算刪除
    # Master → term once per (master, term); deleted only if every row is deleted.
    cur.execute(ins + f"""
        SELECT 'core.teaches', 'note', CAST(master_note_id AS CHAR), {lemma},
               'term', term_key, {lemma},
               JSON_OBJECT('card_type', card_type), IF(MIN(is_deleted), 'deleted', 'active'),
               'migration', MIN(created_at)
        FROM card_gen_log GROUP BY master_note_id, term_key, card_type""")
