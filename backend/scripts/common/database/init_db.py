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
        verb_lemma VARCHAR(255) NOT NULL COMMENT '正規化後的動詞原型',
        
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
        UNIQUE KEY uk_script_verb (script_id, verb_lemma),
        INDEX idx_verb (verb_lemma),
        INDEX idx_master (master_note_id),
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
    }

    missing = {name: clause for name, clause in required.items() if name not in existing}
    if not missing:
        logger.info("✅ 欄位檢查完成，無需補齊。")
        return

    for name, clause in missing.items():
        await session.execute(text(f"ALTER TABLE generated_sentences_log {clause}"))
        logger.info("🔧 已補上缺少的欄位: %s", name)
    await session.commit()

if __name__ == "__main__":
    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8')
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(init_generated_sentences_log())
