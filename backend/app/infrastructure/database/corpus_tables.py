"""語料庫 ``fluencytides_corpus`` 的表名常數與關係圖的表結構。

Table-name constants for the ``fluencytides_corpus`` corpus database, plus
the SQLAlchemy Core definitions of the relation-graph tables.

表名集中在這裡，程式各處不寫死（docs/wip/corpus_db_rebuild_FEAT_2026-10-31.md D0
的命名規則）。建表 SQL 的唯一來源是 ``backend/scripts/database/corpus_rebuild/schema.sql``；
這裡的 ``Table`` 只描述網站讀寫 ``rel_types`` / ``rel_links`` 會用到的欄位，
不負責在 MySQL 建表（測試用 SQLite 時才以 ``corpus_metadata.create_all`` 建）。

Table names live here so no module hard-codes them (naming rules in D0 of
the corpus rebuild plan). ``schema.sql`` is the single source of the DDL;
the ``Table`` objects below only describe the columns the website reads and
writes, and are created with ``corpus_metadata.create_all`` only in SQLite
tests.

前綴與用途：``game_`` 遊戲內容、``nlp_`` 語言標注、``card_`` 建卡帳本、
``rel_`` 通用關係圖、``story_`` 劇情流程圖。
Prefixes: ``game_`` content, ``nlp_`` annotations, ``card_`` card ledger,
``rel_`` relation graph, ``story_`` story flowchart.
"""

import re

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    func,
)

# ============================================================ game_
GAME_PROFILES = "game_profiles"
GAME_SCRIPT_FILES = "game_script_files"
GAME_SCENES = "game_scenes"
GAME_SCENE_LINKS = "game_scene_links"
GAME_SPEAKERS = "game_speakers"
GAME_LINES = "game_lines"
GAME_BGM_TRACKS = "game_bgm_tracks"
GAME_LINE_BGM = "game_line_bgm"
GAME_LINE_REVISIONS = "game_line_revisions"
GAME_LEGACY_SCRIPTS = "game_legacy_scripts"

# ============================================================ nlp_
NLP_LINE_TERMS = "nlp_line_terms"
NLP_LINE_READINGS = "nlp_line_readings"

# ============================================================ card_
CARD_GEN_LOG = "card_gen_log"

# ============================================================ rel_
REL_ENTITY_TYPES = "rel_entity_types"
REL_TYPES = "rel_types"
REL_LINKS = "rel_links"

# ============================================================ story_
STORY_BUILDS = "story_builds"
STORY_NODE_LINES = "story_node_lines"

# ============================================================ 值域 / value sets

# 進建卡與搜尋的台詞條件（D2 game_lines、D6）：選項畫面（choice）不是台詞，不進。
# Corpus lines used by card generation and search; choice screens are excluded.
CORPUS_LINE_KINDS = ("line", "bgv")

# rel_entity_types 的端點類型。Endpoint types registered in rel_entity_types.
ENTITY_NOTE = "note"
ENTITY_SCRIPT_LINE = "script_line"
ENTITY_TERM = "term"

# 系統關係（D8 rel_types 初始值）。System relation types seeded by the migration.
REL_HAS_CONTEXT = "core.has_context"
REL_HAS_CLOZE = "core.has_cloze"
REL_EXAMPLE_FROM = "core.example_from"
REL_TEACHES = "core.teaches"
CORE_CARD_RELATIONS = (REL_HAS_CONTEXT, REL_HAS_CLOZE, REL_EXAMPLE_FROM, REL_TEACHES)

REL_STATUS_ACTIVE = "active"
REL_STATUS_DELETED = "deleted"

# 詞的鍵：``<BCP 47 語言代碼>:<原形>``（D8：term 先不建表，直接用這個字串當 ID）。
# Term key: ``<BCP 47 language tag>:<lemma>``.
LANG_JA_JP = "ja-JP"
# BCP 47 語言代碼的大致形狀（``ja-JP``、``zh-Hant``）。Rough shape of a BCP 47 tag.
_LANG_TAG = re.compile(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*")


def term_key(lemma: str, lang: str = LANG_JA_JP) -> str:
    """由原形組出詞的鍵（``見る`` → ``ja-JP:見る``）。

    Build the term key from a lemma.

    Args:
        lemma: 詞的原形。The lemma.
        lang: BCP 47 語言代碼。The BCP 47 language tag.

    Returns:
        str: ``<lang>:<lemma>``。
    """
    return f"{lang}:{lemma}"


def parse_term_key(key: str) -> tuple[str, str]:
    """把詞的鍵拆成 ``(語言代碼, 原形)``（``ja-JP:見る`` → ``("ja-JP", "見る")``）。

    Split a term key into ``(language tag, lemma)``.

    詞的鍵格式的唯一解析處；``lemma_of`` 與劇情流程圖的讀取都經過這裡。以第一個 ``:`` 切開，
    原形本身可以含 ``:``。
    The single parser of the term-key format; splits at the first ``:``, so
    the lemma itself may contain ``:``.

    Args:
        key: 詞的鍵。The term key.

    Returns:
        tuple[str, str]: ``(語言代碼, 原形)``。``(lang, lemma)``.

    Raises:
        ValueError: 不是 ``<BCP 47 語言代碼>:<原形>``（沒有 ``:``、語言代碼不合格式、原形空白）。
            When the key is not ``<BCP 47 tag>:<lemma>``.
    """
    lang, sep, lemma = key.partition(":") if isinstance(key, str) else ("", "", "")
    if not sep or not _LANG_TAG.fullmatch(lang) or not lemma.strip():
        raise ValueError(f"詞的鍵格式不對（應為 <語言代碼>:<原形>，例 {LANG_JA_JP}:見る）：{key!r}")
    return lang, lemma


def lemma_of(key: str, lang: str = LANG_JA_JP) -> str:
    """從詞的鍵取回原形；鍵格式不對或不是這個語言時報錯（不會把整個鍵當成原形）。

    Recover the lemma from a term key; raises on a malformed key or another
    language instead of silently treating the whole key as the lemma.

    Args:
        key: 詞的鍵。The term key.
        lang: 預期的語言代碼。The expected language tag.

    Returns:
        str: 原形。The lemma.

    Raises:
        ValueError: 鍵格式不對（見 :func:`parse_term_key`），或語言不是 ``lang``。When the key
            is malformed or of another language.
    """
    key_lang, lemma = parse_term_key(key)
    if key_lang != lang:
        raise ValueError(f"詞的鍵 {key!r} 的語言是 {key_lang}，預期 {lang}")
    return lemma


# ============================================================ rel_ 表結構（網站用）

corpus_metadata = MetaData()

# SQLite 只有 INTEGER PRIMARY KEY 會自動編號，所以 BIGINT 主鍵在 SQLite 改用 INTEGER。
# SQLite only auto-increments INTEGER PRIMARY KEY.
_BIGINT_PK = BigInteger().with_variant(Integer(), "sqlite")

rel_types_table = Table(
    REL_TYPES,
    corpus_metadata,
    Column("relation_type", String(64), primary_key=True),
    Column("owner", String(64), nullable=False),
    Column("name_ja_jp", String(64), nullable=False),
    Column("name_zh_hant", String(64), nullable=False),
    Column("src_types", JSON, nullable=False),
    Column("dst_types", JSON, nullable=False),
    Column("directed", Integer, nullable=False, server_default="1"),
    Column("inverse_type", String(64), nullable=True),
    Column("style", JSON, nullable=True),
    Column("created_at", DateTime, nullable=False, server_default=func.now()),
)

rel_links_table = Table(
    REL_LINKS,
    corpus_metadata,
    Column("relation_id", _BIGINT_PK, primary_key=True, autoincrement=True),
    Column("relation_type", String(64), nullable=False),
    Column("src_type", String(32), nullable=False),
    Column("src_id", String(64), nullable=True),
    Column("src_label", String(200), nullable=True),
    Column("dst_type", String(32), nullable=False),
    Column("dst_id", String(64), nullable=True),
    Column("dst_label", String(200), nullable=True),
    Column("attrs", JSON, nullable=True),
    Column("status", String(16), nullable=False, server_default=REL_STATUS_ACTIVE),
    Column("created_by", String(64), nullable=False),
    Column("created_at", DateTime, nullable=False, server_default=func.now()),
    Column("updated_at", DateTime, nullable=False, server_default=func.now(), onupdate=func.now()),
)
