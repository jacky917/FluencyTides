"""
從 JSON 檔案生成面試題卡片的工具腳本。

Utility script that generates interview-question cards from a JSON file.

用法：
    cd backend
    python -m scripts.generate_interview_cards

    # 預覽模式（不實際寫入 Anki，僅列印將要建立的卡片）
    python -m scripts.generate_interview_cards --dry-run
"""

import asyncio
import json
import logging
import sys
import uuid
from pathlib import Path

from app.infrastructure.anki.client import AnkiClient, AnkiConnectError
from app.schemas.anki import AnkiNote, AnkiNoteOptions

# ── 設定 ──────────────────────────────────────────────────────
JSON_FILE_PATH = Path("./2026.06/08_interview.json")
TARGET_DECK = "日本語::面接（2026/06/07）"
TARGET_MODEL = "Speaking_Coach_Dark"

# ── 日誌設定 ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("GenerateInterviewCards")

from app.infrastructure.utils.id_generator import generate_unique_card_id


def _generate_card_id() -> str:
    """產生唯一的 Card_ID。

    Generate a unique Card_ID with the "sc-" prefix.

    Returns:
        str: 唯一卡片 ID 字串。A unique card ID string.
    """
    return generate_unique_card_id(prefix="sc")


async def generate() -> None:
    """讀取 JSON 檔並逐張建立面試口說卡片。

    Read the interview JSON file and create speaking cards one by one,
    supporting a --dry-run preview mode.
    """
    # 解析 CLI 參數
    import argparse
    parser = argparse.ArgumentParser(description="從 JSON 生成面試口說卡片")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="預覽模式：僅列印將要建立的卡片，不實際寫入 Anki",
    )
    args = parser.parse_args()
    dry_run: bool = args.dry_run

    client = AnkiClient()
    logger.info("=" * 60)
    logger.info("FluencyTides 面試卡片生成工具")
    logger.info("=" * 60)
    logger.info("讀取檔案: %s", JSON_FILE_PATH)
    logger.info("目標牌組: %s", TARGET_DECK)
    logger.info("目標模型: %s", TARGET_MODEL)
    if dry_run:
        logger.info("⚠️  預覽模式 (--dry-run)：不會實際寫入 Anki")
    logger.info("-" * 60)

    if not JSON_FILE_PATH.exists():
        logger.error("找不到 JSON 檔案: %s", JSON_FILE_PATH)
        return

    try:
        with open(JSON_FILE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.error("讀取或解析 JSON 失敗: %s", e)
        return

    sections = data.get("interview_preparation", [])
    if not sections:
        logger.warning("JSON 檔案中沒有找到 interview_preparation 區塊。")
        return

    success_count = 0
    fail_count = 0

    try:
        for section_idx, section in enumerate(sections, 1):
            base_tags = ["TelegramBot", "Speaking_Coach", "面接準備"]
            section_tags = section.get("tags", [])
            # 組合所有標籤並過濾掉空白的
            tags = [t for t in (base_tags + section_tags) if t.strip()]

            items = section.get("items", [])
            for item_idx, item in enumerate(items, 1):
                question = item.get("question", "").strip()
                advice = item.get("advice", "").strip()

                if not question:
                    logger.warning("跳過空白 Question 的項目 (Section %d, Item %d)", section_idx, item_idx)
                    continue

                card_id = _generate_card_id()
                
                new_note = AnkiNote(
                    deckName=TARGET_DECK,
                    modelName=TARGET_MODEL,
                    fields={
                        "Card_ID": card_id,
                        "Prompt": question,
                        "Prompt_Audios": "[]",
                        "Context": advice,
                        "Recordings": "[]",
                        "References": "[]",
                        "TG_Bot": "Jacky917_bot",
                    },
                    tags=tags,
                    options=AnkiNoteOptions(
                        allowDuplicate=False,
                        duplicateScope="deck",
                    ),
                )

                logger.info(
                    "[%d-%d] Question: %s",
                    section_idx,
                    item_idx,
                    question[:80] + ("..." if len(question) > 80 else ""),
                )
                if advice:
                    logger.info(
                        "        Advice → Context: %s",
                        advice[:80] + ("..." if len(advice) > 80 else ""),
                    )
                logger.info("        Tags: %s", tags)

                if dry_run:
                    logger.info("        [DRY-RUN] 將建立 Card_ID=%s (跳過寫入)", card_id)
                    success_count += 1
                    continue

                try:
                    new_note_id = await client.add_note(new_note)
                    if new_note_id:
                        logger.info("        ✅ 建立成功 → Note ID: %d, Card_ID: %s", new_note_id, card_id)
                        success_count += 1
                    else:
                        logger.warning("        ⚠️ 建立失敗（可能已有重複的 Prompt）")
                        fail_count += 1
                except AnkiConnectError as e:
                    logger.error("        ❌ 建立失敗: %s", e)
                    fail_count += 1

        # 輸出統計結果
        logger.info("-" * 60)
        logger.info("生成完成！")
        logger.info("  ✅ 成功: %d", success_count)
        logger.info("  ❌ 失敗: %d", fail_count)

        if not dry_run and success_count > 0:
            # 強制觸發同步
            try:
                await client.sync(force=True)
                logger.info("  🔄 已觸發 Anki 同步")
            except AnkiConnectError as e:
                logger.warning("  ⚠️ 同步失敗（不影響已建立的卡片）: %s", e)

    except AnkiConnectError as e:
        logger.error("AnkiConnect 連線失敗: %s", e)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(generate())
