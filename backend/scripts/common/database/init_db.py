"""
初始化 JP_VerbPair 生成日誌資料庫 (generated_sentences_log)。
可單獨執行此腳本來建立或更新資料表結構。

Initialize the JP_VerbPair generation log database
(generated_sentences_log). This script can be run standalone to create
or update the table schema.
"""

import asyncio
import logging
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# 確保 sys.path 包含 backend 根目錄並載入 .env
_backend_dir = Path(__file__).resolve().parents[3]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))
import scripts.common.env  # noqa

from app.infrastructure.database.corpus_database import corpus_async_session_factory, dispose_corpus_engine

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

async def init_generated_sentences_log():
    """建立（或確認存在）generated_sentences_log 資料表。

    Create the generated_sentences_log table if it does not already
    exist, then dispose of the corpus database engine.
    """
    logger.info("🔧 開始檢查/建立 generated_sentences_log 資料表...")
    
    if not corpus_async_session_factory:
        logger.error("❌ 語料庫資料庫連線尚未初始化，請檢查 .env 設定。")
        return

    ddl = """
    CREATE TABLE IF NOT EXISTS generated_sentences_log (
        id INT AUTO_INCREMENT PRIMARY KEY,
        
        -- 核心關聯鍵 (複合 Unique 基礎)
        script_id BIGINT UNSIGNED NOT NULL COMMENT '來源台詞 ID (對應 scripts.id)',
        verb_lemma VARCHAR(255) NOT NULL COMMENT '動詞正規表記：母卡標準表層去標音（非搜尋關鍵字）',
        project VARCHAR(32) NOT NULL DEFAULT 'jp_verb_pair' COMMENT '所屬卡片專案 (jp_verb_pair / jp_core_verb)',

        -- 關聯還原資訊 (方便 JOIN 與追溯)
        source VARCHAR(255) NOT NULL COMMENT '遊戲來源名稱',
        chapter VARCHAR(255) NOT NULL COMMENT '章節名稱',
        
        -- Anki 產物關聯
        master_note_id BIGINT NOT NULL COMMENT '觸發生成的 Anki 母卡片 ID',
        context_note_id BIGINT DEFAULT NULL COMMENT '生成的 Context 子卡片 ID',
        cloze_note_id BIGINT DEFAULT NULL COMMENT '生成的 Cloze 子卡片 ID',
        
        -- 狀態與時間戳
        is_deleted BOOLEAN NOT NULL DEFAULT FALSE COMMENT '軟刪除標記，TRUE 表示卡片已被刪除，允許重新生成',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '首次生成時間',
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最後更新(如軟刪除)時間',
        delete_count INT NOT NULL DEFAULT 0 COMMENT '被反覆刪除的次數統計',
        failure_count INT NOT NULL DEFAULT 0 COMMENT 'LLM 生成連續失敗次數，達門檻則永久跳過',
        llm_model VARCHAR(255) DEFAULT NULL COMMENT '最後一次生成使用的 LLM 模型名稱',

        -- 索引與約束
        UNIQUE KEY uk_script_verb_project (script_id, verb_lemma, project),
        INDEX idx_verb (verb_lemma),
        INDEX idx_master (master_note_id),
        INDEX idx_project (project),
        FOREIGN KEY (script_id) REFERENCES scripts(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """

    try:
        async with corpus_async_session_factory() as session:
            await session.execute(text(ddl))
            await session.commit()
            logger.info("✅ generated_sentences_log 資料表建立成功 (或已存在)。")
            # DDL 用的是 CREATE TABLE IF NOT EXISTS，既有資料表不會因為改了
            # DDL 就長出新欄位，因此必須再跑一次冪等的欄位補齊（S007）。
            # The DDL uses CREATE TABLE IF NOT EXISTS, so an existing table
            # never gains newly added columns; an idempotent column backfill
            # must run as well (S007).
            await _ensure_columns(session)
            # 既有資料表的 unique key 也可能停留在舊版 (script_id, verb_lemma)，
            # 需冪等地遷移到含 project 的新 key。
            # An existing table may still carry the old (script_id, verb_lemma)
            # unique key; migrate it idempotently to the project-aware one.
            await _ensure_unique_key(session)
            # 已退役的欄位：search_keyword 是 2026-09 存量拼寫修復期的安全網，
            # 任務完成後移除（docs/wip/verb_reading_judgments_FEAT_2026-09-02.md §2.2）。
            await _drop_legacy_columns(session)
            # 讀音判斷快取表（與 generated_sentences_log 無關聯，計畫 §2.1）。
            await _ensure_reading_judgments_table(session)
    except Exception as e:
        logger.error(f"❌ 建立資料表時發生錯誤: {e}")
    finally:
        await dispose_corpus_engine()


async def _ensure_columns(session: AsyncSession) -> None:
    """為既有資料表補上 DDL 後來新增的欄位（冪等）。

    Backfill columns added to the DDL after the table already existed
    (idempotent).

    MySQL 8 不支援 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`，因此先查
    information_schema 取得現有欄位，再只對缺少者執行 ALTER。

    MySQL 8 has no `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, so existing
    columns are read from information_schema first and only the missing ones
    are altered in.

    Args:
        session: 語料庫資料庫的非同步 Session。Async session for the corpus
            database.
    """
    result = await session.execute(
        text(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = 'generated_sentences_log'"
        )
    )
    existing = {row[0] for row in result.all()}

    # 欄位名 -> ALTER 子句（型別依 log_repository.py 的實際用法推得）
    # Column name -> ALTER clause (types derived from log_repository.py usage).
    required: dict[str, str] = {
        "failure_count": (
            "ADD COLUMN failure_count INT NOT NULL DEFAULT 0 "
            "COMMENT 'LLM 生成連續失敗次數，達門檻則永久跳過'"
        ),
        "llm_model": (
            "ADD COLUMN llm_model VARCHAR(255) DEFAULT NULL "
            "COMMENT '最後一次生成使用的 LLM 模型名稱'"
        ),
        "project": (
            "ADD COLUMN project VARCHAR(32) NOT NULL DEFAULT 'jp_verb_pair' "
            "COMMENT '所屬卡片專案 (jp_verb_pair / jp_core_verb)' "
            "AFTER verb_lemma, ADD INDEX idx_project (project)"
        ),
    }

    missing = {name: clause for name, clause in required.items() if name not in existing}
    if not missing:
        logger.info("✅ 欄位檢查完成，無需補齊。")
        return

    for name, clause in missing.items():
        await session.execute(text(f"ALTER TABLE generated_sentences_log {clause}"))
        logger.info("🔧 已補上缺少的欄位: %s", name)
    await session.commit()


async def _ensure_unique_key(session: AsyncSession) -> None:
    """把 unique key 從舊版 (script_id, verb_lemma) 遷移到含 project 的新版（冪等）。

    Idempotently migrate the unique key from the legacy
    (script_id, verb_lemma) to the project-aware
    (script_id, verb_lemma, project).

    順序刻意「先加新、後刪舊」：舊 key 比新 key 更嚴格，加新 key 不可能
    因重複而失敗；反過來先刪舊 key 則會有一段沒有唯一約束的空窗。
    The new key is added before the old one is dropped: the old key is
    strictly tighter, so adding the new one can never fail on duplicates,
    while dropping first would leave a window with no uniqueness at all.

    Args:
        session: 語料庫資料庫的非同步 Session。Async session for the corpus
            database.
    """
    result = await session.execute(
        text(
            "SELECT DISTINCT INDEX_NAME FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = 'generated_sentences_log'"
        )
    )
    index_names = {row[0] for row in result.all()}

    if "uk_script_verb_project" not in index_names:
        await session.execute(text(
            "ALTER TABLE generated_sentences_log "
            "ADD UNIQUE KEY uk_script_verb_project (script_id, verb_lemma, project)"
        ))
        logger.info("🔧 已建立新 unique key: uk_script_verb_project")

    if "uk_script_verb" in index_names:
        await session.execute(text(
            "ALTER TABLE generated_sentences_log DROP INDEX uk_script_verb"
        ))
        logger.info("🔧 已移除舊 unique key: uk_script_verb")

    if "uk_script_verb_project" in index_names and "uk_script_verb" not in index_names:
        logger.info("✅ unique key 檢查完成，無需遷移。")
    else:
        await session.commit()

async def _drop_legacy_columns(session: AsyncSession) -> None:
    """移除已退役的欄位（冪等：存在才刪）。

    Drop retired columns idempotently (only when present).

    ``search_keyword``：2026-09 存量拼寫修復期用來保留被改寫原值的安全網，
    修復完成並逐筆驗證後失去用途；無任何讀取方、且不在任何索引內，DROP
    零風險。

    Args:
        session: 語料庫資料庫的非同步 Session。Async session.
    """
    result = await session.execute(text(
        "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'generated_sentences_log'"
    ))
    existing = {row[0] for row in result.all()}
    for column in ("search_keyword",):
        if column in existing:
            await session.execute(text(f"ALTER TABLE generated_sentences_log DROP COLUMN {column}"))
            logger.info("🔧 已移除退役欄位: %s", column)
    await session.commit()


async def _ensure_reading_judgments_table(session: AsyncSession) -> None:
    """建立（或確認存在）jp_verb_reading_judgments 讀音判斷快取表。

    Create the jp_verb_reading_judgments table if it does not exist.

    表的語意：「這句台詞裡的這個表層讀什麼」——台詞本身的屬性，與母卡無關；
    不進任何去重鍵。由 scripts/fastapi_client/JP_Common/judge_verb_readings.py
    寫入、生卡腳本只讀（docs/wip/verb_reading_judgments_FEAT_2026-09-02.md §2.1）。

    Args:
        session: 語料庫資料庫的非同步 Session。Async session.
    """
    await session.execute(text("""
    CREATE TABLE IF NOT EXISTS jp_verb_reading_judgments (
        script_id    BIGINT UNSIGNED NOT NULL COMMENT '台詞 ID（對應 scripts.id）',
        verb_surface VARCHAR(32)     NOT NULL COMMENT '同表層多讀的表層，如 汚す',
        reading      VARCHAR(32)     NOT NULL COMMENT 'LLM 判定的讀音（平假名）；無法判定為空字串',
        llm_model    VARCHAR(255)    DEFAULT NULL COMMENT '判讀所用模型標籤（取自後端回應）',
        created_at   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (script_id, verb_surface),
        FOREIGN KEY (script_id) REFERENCES scripts(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """))
    await session.commit()
    logger.info("✅ jp_verb_reading_judgments 資料表建立成功 (或已存在)。")


if __name__ == "__main__":
    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8')
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(init_generated_sentences_log())
