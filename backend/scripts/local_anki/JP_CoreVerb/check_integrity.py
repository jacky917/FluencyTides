"""JP_CoreVerb 完整性檢查與自動修復腳本（薄包裝）。

JP_CoreVerb integrity check and auto-repair script (thin wrapper around
scripts.local_anki.common.deletion.integrity).

交叉比對 MySQL (generated_sentences_log 中本專案的紀錄) 與 Anki 牌組的
四個維度：DB 斷鏈、孤兒卡片、母卡 JSON 失效連結、孤兒媒體。
DB 讀寫以 project 欄過濾、共用的 Context 模型先做歸屬分流、
孤兒媒體以全專案引用聯集判定——不會誤傷 JP_VerbPair 的資料。

孤兒修復的 verb_lemma 取自母卡 Word 欄（去標音），與生成時寫入
DB 的鍵一致；母卡已不存在的孤兒不做自動重建，只回報或刪卡。

預設為 Dry-Run 模式（不修改任何資料，僅印出診斷報告與預計操作）。
加上 --execute 參數後才會實際執行修復。

Example:
    # 純診斷 (Dry-Run)
    $ python check_integrity.py

    # 執行自動修復
    $ python check_integrity.py --execute
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

backend_dir = str(Path(__file__).resolve().parents[3])
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)
import scripts.common.env  # noqa

from scripts.common.database.log_repository import PROJECT_JP_CORE_VERB
from scripts.local_anki.common.deletion.integrity import run_integrity_check
from scripts.local_anki.common.deletion.profiles import get_profile

logging.basicConfig(level=logging.INFO, format="%(message)s")


async def main() -> None:
    """腳本主入口：解析參數並調用共用完整性檢查核心。

    Script entry point: parse arguments and invoke the shared integrity
    core.
    """
    parser = argparse.ArgumentParser(description="JP_CoreVerb 完整性檢查與修復腳本")
    parser.add_argument("--execute", action="store_true", help="實際執行修復操作 (預設為 Dry-Run 純檢查)")
    args = parser.parse_args()

    from app.infrastructure.database.corpus_database import dispose_corpus_engine
    try:
        await run_integrity_check(get_profile(PROJECT_JP_CORE_VERB), is_execute=args.execute)
    except Exception as e:
        import traceback
        traceback.print_exc()
        logging.getLogger(__name__).error(f"執行過程發生錯誤: {e}")
    finally:
        await dispose_corpus_engine()


if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
