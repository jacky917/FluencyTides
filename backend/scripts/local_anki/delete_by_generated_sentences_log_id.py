"""以資料表 `generated_sentences_log` 的主鍵 id 刪除整組卡片（兩種卡片類型通用）。

Delete complete card sets keyed by the primary-key ``id`` of the MySQL
table ``generated_sentences_log``, working across both card projects
(JP_VerbPair 自他動詞 / JP_CoreVerb 核心動詞).

============================================================
這個腳本在做什麼
============================================================
`generated_sentences_log`（yuzusoft 資料庫）是兩個卡片專案共用的
「生成紀錄／去重」表，每筆紀錄對應一組已生成的卡片：

    id               ← 你在本腳本輸入的就是這個主鍵
    project          ← 'jp_verb_pair' 或 'jp_core_verb'（自動辨識的依據）
    verb_lemma       ← 動詞原型（報告顯示用）
    master_note_id   ← Anki 母卡
    cloze_note_id    ← Anki 克漏字子卡
    context_note_id  ← Anki 上下文子卡
    is_deleted       ← 軟刪除標記

你只要給 id，腳本會：

1. 到 `generated_sentences_log` 撈出每筆 id 的 project 與三個 note id。
2. 依 `project` 欄**自動**選擇對應的專案設定（筆記類型、母卡 JSON 欄位），
   不需要、也不能手動指定卡片類型——不存在填錯類型的空間。
3. 按專案分組後調用共用刪除核心，對每組卡片依序執行：
   a. 驗證三張卡存在且筆記類型與該專案相符（類型不符整筆拒絕）
   b. 從母卡 JSON 欄位移除該筆紀錄（欄位已備份，可還原）
   c. 標記 MySQL 紀錄——預設軟刪除＝該句永不再生成；
      `--allow-regen` 改硬刪除＝該句回到生成候選池（先不 commit）
   d. 最後才刪除 cloze + context 兩張子卡（唯一不可逆的一步）
   e. 子卡確定刪除成功後才 commit MySQL；任一步失敗則全部回滾
4. 全部做完後，對受影響的專案各跑一次完整性檢查（與本次相同的
   Dry Run / 真實模式）。

邊界情況的處理：
- 查無此 id → 跳過並警告。
- 「無卡可刪」的紀錄——純失敗紀錄（子卡 note id 為 NULL），或子卡已
  不存在於 Anki——依模式分流：
    * 預設（軟刪除語意）：跳過並警告。紀錄留在 DB 本來就會繼續擋住
      重新生成，動它沒有意義。
    * ``--allow-regen``（硬刪除語意）：**直接硬刪該筆 DB 紀錄**。
      卡片側沒有東西可刪，清掉 DB 列才能真正讓句子回到生成候選池。
- DB 已軟刪除但卡片仍存在 → 警告後照常處理（把卡片刪乾淨）。

============================================================
id 的寫法（三種可混用）
============================================================
    555            單一 id
    555,600,777    逗號分隔多個 id
    439-450        閉區間範圍（含頭尾）

============================================================
使用範例
============================================================
    # Dry Run（預設）：只預覽將發生的事，不修改 Anki 與資料庫
    python delete_by_generated_sentences_log_id.py 555

    # 混合寫法 + 真實執行
    python delete_by_generated_sentences_log_id.py 555 600,601 439-450 --execute

    # 真實執行且允許同句之後重新生成（DB 紀錄硬刪除）
    python delete_by_generated_sentences_log_id.py 555 --execute --allow-regen

相關工具：
- 想以「Anki 母卡」為單位刪除 → 各專案目錄的 delete_child_cards.py
- 想清理斷鏈/孤兒/無用媒體   → 各專案目錄的 check_integrity.py
- 想整個專案打掉重來          → 各專案目錄的 cleanup_script.py
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# 確保 sys.path 包含 backend 根目錄，並載入 .env（DB/AnkiConnect 連線設定）
_backend_dir = Path(__file__).resolve().parents[2]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))
import scripts.common.env  # noqa

from app.infrastructure.database.corpus_database import dispose_corpus_engine
from scripts.local_anki.common.deletion.id_deleter import (
    parse_id_tokens,
    run_deletion_by_log_ids,
)

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


async def main() -> None:
    """腳本主入口：解析 id 標記並調用以 id 為入口的通用刪除核心。

    Script entry point: parse the id tokens and invoke the id-keyed
    deletion core (scripts.local_anki.common.deletion.id_deleter).
    """
    parser = argparse.ArgumentParser(
        description=(
            "以 generated_sentences_log 的主鍵 id 刪除整組卡片"
            "（母卡 JSON 紀錄 + cloze/context 子卡 + DB 紀錄；"
            "卡片類型由每筆紀錄的 project 欄自動辨識）"
        )
    )
    parser.add_argument(
        "ids", nargs="+",
        help="generated_sentences_log 的紀錄 id，支援 555 / 555,600 / 439-450 三種寫法混用"
    )
    parser.add_argument(
        "--execute", action="store_true",
        help="實際執行刪除操作。未加上此參數時預設為 Dry Run 模式 (不實際修改 Anki 或資料庫)"
    )
    parser.add_argument(
        "--allow-regen", action="store_true",
        help="硬刪除 MySQL 去重紀錄，讓對應句子可被重新生成（預設為軟刪除＝永不再生成）"
    )
    args = parser.parse_args()

    try:
        ids = parse_id_tokens(args.ids)
    except ValueError as e:
        logger.error(f"❌ 參數錯誤: {e}")
        return

    logger.info(f"📥 共解析出 {len(ids)} 個 id: {ids if len(ids) <= 20 else str(ids[:20])[:-1] + ', ...]'}")

    try:
        await run_deletion_by_log_ids(
            ids,
            dry_run=not args.execute,
            allow_regen=args.allow_regen,
        )
    finally:
        await dispose_corpus_engine()


if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
