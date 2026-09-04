"""清除卡片 JSON 中的身分證欄位（``cardId`` / ``noteId``）。

Clear the identity fields (``cardId`` / ``noteId``) from card JSON files.

``import_cards.py`` 在身分「有但與 Anki 對不上」時會停手並交還給人處理
（見 ``docs/archive/card_identity_writeback_FEAT_2026-08-11.md`` §3.2）。
本工具是那個情況下**唯一**的復原手段：清掉身分後該卡回到「無身分」狀態，
重跑匯入會建一張新卡，或加 ``--adopt-by-prompt`` 重新接管既有卡。

``import_cards.py`` stops and defers to a human whenever an identity is
present but does not match Anki (see the plan document, §3.2). This tool is
the **only** recovery path in that situation: clearing the identity returns
the card to the "no identity" state, so a re-run either creates a new card or,
with ``--adopt-by-prompt``, re-adopts the existing one.

**本工具只改 JSON，完全不碰 Anki。** 清除身分不應該有刪卡的副作用——若也想
刪掉 Anki 那張卡，請自行在 Anki 操作。

**This tool only edits JSON and never touches Anki.** Clearing an identity
must not have the side effect of deleting a card; delete it in Anki yourself
if that is what you want.

【使用方式】

1. 清除單一檔案全部卡片的身分（先預覽）:
   python scripts/local_anki/Speaking_Trilingual_Dark/clear_identity.py \\
       --name "日本語面接/Queen Bee Capital株式会社/逆質問" --dry-run

2. 只清特定一張卡（序號對齊匯入腳本診斷訊息中的 ``#N``）:
   python scripts/local_anki/Speaking_Trilingual_Dark/clear_identity.py \\
       --name "日本語面接/Queen Bee Capital株式会社/逆質問" --index 2

3. 清除整個 jsons/ 目錄（複製整包資料夾去開新公司牌組時必做）:
   python scripts/local_anki/Speaking_Trilingual_Dark/clear_identity.py --all

⚠️ 複製 JSON 檔開新牌組時**務必先執行本工具**。複製出來的檔案會帶著原牌組
卡片的身分，若直接匯入，新牌組的卡會沿著 ``noteId`` 去**更新原牌組的卡片**
——內容被覆寫，而新牌組一張卡都不會建立。這是唯一會造成資料被覆寫的操作路徑。

⚠️ Always run this after copying JSON files to start a new deck. The copies
carry the original cards' identities, so importing them would follow
``noteId`` and **overwrite the original cards** while creating nothing in the
new deck. This is the only operation path that can overwrite data.
"""

import argparse
import logging
import sys
from pathlib import Path

# 確保能載入 backend 模組
_backend_dir = Path(__file__).resolve().parents[3]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from scripts.local_anki.common.card_identity import (
    clear_identity,
    load_cards,
    read_identity,
    save_cards,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

#: 卡片 JSON 的存放根目錄
JSONS_DIR = Path(__file__).parent / "jsons"


def clear_file(file_path: Path, index: int | None, dry_run: bool) -> int:
    """清除單一 JSON 檔中的身分欄位。

    Clear identity fields in a single JSON file.

    Args:
        file_path: 目標 JSON 檔案路徑。Target JSON file path.
        index: 只處理第 N 張卡（1-based）；``None`` 表示整檔。Process only the
            N-th card (1-based); ``None`` means the whole file.
        dry_run: 僅列出將被清除的項目，不寫檔。List what would be cleared
            without writing the file.

    Returns:
        被清除身分的卡片數。The number of cards whose identity was cleared.

    Raises:
        IndexError: ``index`` 超出該檔卡片範圍時拋出。Raised when ``index`` is
            outside the file's card range.
    """
    cards = load_cards(file_path)

    if index is not None and not 1 <= index <= len(cards):
        raise IndexError(f"{file_path.name} 只有 {len(cards)} 張卡，找不到第 {index} 張")

    cleared = 0
    for i, card in enumerate(cards, 1):
        if index is not None and i != index:
            continue
        card_id, note_id = read_identity(card)
        if card_id is None and note_id is None:
            continue
        prefix = "🧪 [DRY-RUN] 將清除" if dry_run else "🧹 已清除"
        logger.info(
            f"   {prefix} [{file_path.name} #{i}]  noteId={note_id}  cardId={card_id}"
        )
        if clear_identity(card):
            cleared += 1

    if cleared and not dry_run:
        save_cards(file_path, cards)

    return cleared


def main() -> int:
    """腳本主入口：解析參數並清除指定範圍的身分欄位。

    Script entry point: parse arguments and clear identities in the requested
    scope.

    Returns:
        行程結束碼；找不到檔案或參數不合法時回傳 ``1``。Process exit code;
        ``1`` when the file is missing or the arguments are invalid.
    """
    parser = argparse.ArgumentParser(
        description="Speaking_Trilingual_Dark 專用: 清除卡片 JSON 的身分證 (cardId / noteId)"
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--name",
        type=str,
        help="jsons 目錄下的 JSON 相對路徑 (不含 .json，子資料夾需一併給，如 '日本語面接/Q社/逆質問')",
    )
    target.add_argument(
        "--all", action="store_true", help="遞迴處理整個 jsons 目錄下的所有 JSON 檔"
    )
    parser.add_argument(
        "--index",
        type=int,
        default=None,
        help="只清除第 N 張卡 (1-based，對齊匯入腳本診斷訊息的 #N)；僅能搭配 --name 使用",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="僅列出將被清除的身分，不實際寫檔"
    )
    args = parser.parse_args()

    if args.index is not None and args.all:
        parser.error("--index 只能搭配 --name 使用（--all 無法指定單一卡片序號）")

    if args.all:
        if not JSONS_DIR.exists():
            logger.error(f"❌ 找不到 jsons 資料夾: {JSONS_DIR}")
            return 1
        targets = sorted(JSONS_DIR.rglob("*.json"))
        if not targets:
            logger.info("ℹ️ jsons 目錄下沒有找到任何 JSON 檔案。")
            return 0
    else:
        file_path = JSONS_DIR / f"{args.name}.json"
        if not file_path.exists():
            logger.error(f"❌ 找不到 JSON 檔案: {file_path}")
            return 1
        targets = [file_path]

    logger.info("=" * 60)
    logger.info("🧹 準備清除卡片 JSON 的身分證 (cardId / noteId)")
    logger.info(f"📄 目標檔案: {len(targets)} 個" + (f"，僅第 {args.index} 張" if args.index else ""))
    if args.dry_run:
        logger.info("⚠️ 目前為 --dry-run 模式，不會實際寫檔")
    logger.info("=" * 60)

    total = 0
    for file_path in targets:
        try:
            total += clear_file(file_path, args.index, args.dry_run)
        except (IndexError, ValueError) as e:
            logger.error(f"❌ {file_path.name}: {e}")
            return 1

    if total:
        verb = "將清除" if args.dry_run else "已清除"
        logger.info(f"📊 {verb} {total} 張卡片的身分。")
        if not args.dry_run:
            logger.info(
                "ℹ️ 這些卡片已回到「無身分」狀態。重跑匯入會建立新卡；"
                "若要接回既有卡片，請加 --adopt-by-prompt。"
            )
    else:
        logger.info("ℹ️ 指定範圍內沒有任何卡片持有身分，未做任何變更。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        logger.info("\n操作已取消")
        sys.exit(130)
