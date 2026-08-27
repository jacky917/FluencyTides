"""批量刪除 JP_CoreVerb 子卡片工具（薄包裝）。

Batch deletion tool for JP_CoreVerb child cards (thin wrapper around
scripts.local_anki.common.deletion.child_deleter).

支援兩種任務輸入模式：
A. JSON 模式：從 configs/delete_child_cards.json 讀取特定待刪除的子卡片清單。
   - 若 JSON 條目僅包含 master_nid（不含 cloze_nid/context_nid），
     則視為清除該母卡片下的「所有」子卡片。
B. 母卡模式：透過 `--master-nid` 參數指定母卡片，腳本會自動掃描並刪除
   該母卡片下的「所有」子卡片。

與 JP_VerbPair 版行為一致，差異只在專案描述子：母卡 JSON 為單欄
Word_Data_JSON、Cloze 模型為 JP_CoreVerb_Cloze_Dark。

刪除流程與安全機制（母卡 JSON 移除 → DB 標記 → 最後才刪子卡、
單筆失敗完整回滾、事後自動完整性檢查）由共用核心提供，
詳見 common/deletion/child_deleter.py。

去重語意：預設對 MySQL 紀錄做**軟刪除**（該句永不再生成）；
加上 `--allow-regen` 才硬刪除（該句回到生成候選池）。

Usage:
    # Dry Run (預覽模式)
    python delete_child_cards.py

    # 實際執行 JSON 清單刪除
    python delete_child_cards.py --execute

    # 實際執行單張母卡片下所有子卡片的清除
    python delete_child_cards.py --execute --master-nid 1234567890

    # 刪除且允許同句重新生成
    python delete_child_cards.py --execute --master-nid 1234567890 --allow-regen
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# 確保 sys.path 包含 backend 根目錄
_backend_dir = Path(__file__).resolve().parents[3]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))
import scripts.common.env  # noqa

from scripts.common.database.log_repository import PROJECT_JP_CORE_VERB
from scripts.local_anki.common.deletion.child_deleter import run_child_deletion
from scripts.local_anki.common.deletion.profiles import get_profile

logging.basicConfig(level=logging.INFO, format='%(message)s')


async def main() -> None:
    """腳本主入口：解析參數並調用共用刪除核心。

    Script entry point: parse arguments and invoke the shared deletion
    core.
    """
    parser = argparse.ArgumentParser(description="批量刪除 JP_CoreVerb 子卡片工具")
    parser.add_argument(
        "--execute", action="store_true",
        help="實際執行刪除操作。未加上此參數時預設為 Dry Run 模式 (不實際修改 Anki 或資料庫)"
    )
    parser.add_argument(
        "--master-nid", type=int,
        help="指定單張母卡片 ID。若提供此參數，將動態提取並刪除該母卡片下的所有子卡片（優先於 json 檔案）"
    )
    parser.add_argument(
        "--allow-regen", action="store_true",
        help="硬刪除 MySQL 去重紀錄，讓對應句子可被重新生成（預設為軟刪除＝永不再生成）"
    )
    args = parser.parse_args()

    await run_child_deletion(
        get_profile(PROJECT_JP_CORE_VERB),
        dry_run=not args.execute,
        allow_regen=args.allow_regen,
        master_nid=args.master_nid,
        config_path=Path(__file__).parent / "configs" / "delete_child_cards.json",
    )


if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
