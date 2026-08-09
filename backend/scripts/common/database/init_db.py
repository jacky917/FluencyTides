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
    except Exception as e:
        logger.error(f"❌ 建立資料表時發生錯誤: {e}")
    finally:
        await dispose_corpus_engine()

if __name__ == "__main__":
    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8')
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(init_generated_sentences_log())
