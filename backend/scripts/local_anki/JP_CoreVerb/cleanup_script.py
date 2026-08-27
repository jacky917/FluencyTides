"""JP_CoreVerb 清除腳本（薄包裝）：刪除子卡片、清空母卡片 JSON、清理資料庫與媒體。

Cleanup script for JP_CoreVerb (thin wrapper around
scripts.local_anki.common.deletion.cleanup): delete all child cards, blank
the master-card Word_Data_JSON field, purge project media and clear the
project's rows in the MySQL dedup log.

媒體前綴取自 settings（JP_CORE_VERB_SOURCE_GAME，預設沿用
JP_VERB_PAIR_SOURCE_GAME），且只刪「所有已註冊專案皆未引用」的檔案——
JP_VerbPair 仍在使用的媒體不受影響。

Usage:
    # Dry Run
    python cleanup_script.py

    # 正式執行（會再次要求確認）
    python cleanup_script.py --execute

    # 指定其他根牌組
    python cleanup_script.py "日本語::核心動詞" --execute
"""

import argparse
import asyncio
import sys
from pathlib import Path

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

# 確保 sys.path 包含 backend 根目錄並載入 .env
backend_dir = str(Path(__file__).resolve().parents[3])
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)
import scripts.common.env  # noqa

from scripts.common.database.log_repository import PROJECT_JP_CORE_VERB
from scripts.local_anki.common.deletion.cleanup import run_cleanup
from scripts.local_anki.common.deletion.profiles import get_profile


async def main() -> None:
    """腳本主入口：解析參數並調用共用清除核心。

    Script entry point: parse arguments and invoke the shared cleanup
    core.
    """
    parser = argparse.ArgumentParser(description="純粹的清除腳本：刪除所有子卡片、清空母卡片 JSON 欄位、清理資料庫與媒體資源。")
    parser.add_argument("deck_name", nargs="?", default=None, help="根牌組名稱（預設取 profile 設定）")
    parser.add_argument("--execute", action="store_true", help="正式執行清除 (若無此參數則為 Dry Run 空跑)")
    args = parser.parse_args()

    await run_cleanup(
        get_profile(PROJECT_JP_CORE_VERB),
        dry_run=not args.execute,
        deck_name=args.deck_name,
    )


if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
