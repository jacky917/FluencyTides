"""
牌組遷移腳本：面談（Geekly）new → 面談（Geekly）2026/06/07。

將原始的 Q&A 筆記類型遷移為 Speaking_Coach_Dark 口說教練卡片。

Deck migration script: migrates raw Q&A notes from the Geekly
interview deck into Speaking_Coach_Dark speaking-coach cards,
mapping Question to Prompt and Answer to the first Reference entry.

欄位映射規則：
    - Question → Prompt（卡片正面提示語）
    - Answer   → References[0].content（第一條參考回覆，無語音、無頭像）

用法：
    cd backend
    python -m scripts.migrations.migrate_geekly_to_speaking_coach

    # 預覽模式（不實際寫入 Anki，僅列印將要建立的卡片）
    python -m scripts.migrations.migrate_geekly_to_speaking_coach --dry-run
"""

import asyncio
import json
import logging
import sys
import uuid
from datetime import datetime, timezone

from app.core.config import settings
from app.infrastructure.anki.client import AnkiClient, AnkiConnectError
from app.schemas.anki import AnkiNote, AnkiNoteOptions

# ── 遷移設定 ──────────────────────────────────────────────────────
# 來源牌組與目標牌組名稱
SOURCE_DECK = "日本語::面談（Geekly）new"
TARGET_DECK = "日本語::面談（Geekly）2026/06/07"

# 目標筆記類型
TARGET_MODEL = "Speaking_Coach_Dark"

# ── 日誌設定 ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("GeeklyMigration")


def _build_references_json(answer_text: str) -> str:
    """將 Answer 欄位的純文字轉為 Speaking_Coach_Dark 的 References JSON。

    Convert the plain-text Answer field into the Speaking_Coach_Dark
    References JSON: one enabled reference entry without audio/avatar.

    Args:
        answer_text: 原始 Answer 欄位內容。Raw Answer field content.

    Returns:
        JSON 字串，格式為 list[ReferenceItem]。
        JSON string in list[ReferenceItem] format.
    """
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    reference = {
        "date": today,
        "content": answer_text,
        "status": 1,
        "audios": [],
    }
    return json.dumps([reference], ensure_ascii=False)


def _generate_card_id() -> str:
    """產生唯一的 Card_ID（與專案中其他地方一致的 UUID 格式）。

    Generate a Card_ID in the project's UUID-based format.

    Returns:
        格式為 'sc-xxxxxxxx' 的唯一字串。
        Unique string in 'sc-xxxxxxxx' format.
    """
    return f"sc-{uuid.uuid4().hex[:8]}"


async def migrate() -> None:
    """執行遷移主流程。

    Run the main migration flow: query source notes, build new
    Speaking_Coach_Dark notes, add them to the target deck, and
    print the summary.

    步驟：
    1. 從來源牌組查詢所有筆記 ID
    2. 取得筆記詳細資訊（Question & Answer）
    3. 逐一建立 Speaking_Coach_Dark 筆記到目標牌組
    4. 輸出統計結果
    """
    # 解析 CLI 參數
    import argparse
    parser = argparse.ArgumentParser(description="遷移 Geekly 面談卡片到 Speaking_Coach_Dark")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="預覽模式：僅列印將要建立的卡片，不實際寫入 Anki",
    )
    args = parser.parse_args()
    dry_run: bool = args.dry_run

    client = AnkiClient()
    logger.info("=" * 60)
    logger.info("FluencyTides 牌組遷移工具")
    logger.info("=" * 60)
    logger.info("來源牌組: %s", SOURCE_DECK)
    logger.info("目標牌組: %s", TARGET_DECK)
    logger.info("目標模型: %s", TARGET_MODEL)
    if dry_run:
        logger.info("⚠️  預覽模式 (--dry-run)：不會實際寫入 Anki")
    logger.info("-" * 60)

    try:
        # Step 1: 查詢來源牌組中的所有筆記
        query = f'"deck:{SOURCE_DECK}"'
        note_ids = await client.find_notes(query)

        if not note_ids:
            logger.warning("來源牌組中沒有找到任何筆記，遷移中止。")
            return

        logger.info("找到 %d 筆筆記，開始讀取...", len(note_ids))

        # Step 2: 取得筆記詳細資訊
        notes_info = await client.get_notes_info(notes=note_ids)

        success_count = 0
        skip_count = 0
        fail_count = 0

        for i, note in enumerate(notes_info, start=1):
            fields = note.fields

            # 讀取 Question 和 Answer 欄位
            question = fields.get("Question", {})
            answer = fields.get("Answer", {})

            # AnkiConnect 回傳的 fields 格式為 {"fieldName": {"value": "...", "order": N}}
            question_text = question.get("value", "") if isinstance(question, dict) else str(question)
            answer_text = answer.get("value", "") if isinstance(answer, dict) else str(answer)

            if not question_text.strip():
                logger.warning("[%d/%d] 跳過空白 Question 的筆記 (noteId=%d)", i, len(notes_info), note.noteId)
                skip_count += 1
                continue

            # Step 3: 組裝新的 Speaking_Coach_Dark 筆記
            card_id = _generate_card_id()
            references_json = _build_references_json(answer_text)

            new_note = AnkiNote(
                deckName=TARGET_DECK,
                modelName=TARGET_MODEL,
                fields={
                    "Card_ID": card_id,
                    "Prompt": question_text,
                    "Prompt_Audios": "[]",
                    "Context": "",
                    "Recordings": "[]",
                    "References": references_json,
                    "TG_Bot": "Jacky917_bot",
                },
                tags=["TelegramBot", "Speaking_Coach", "Migration_Geekly"],
                options=AnkiNoteOptions(
                    allowDuplicate=False,
                    duplicateScope="deck",
                ),
            )

            logger.info(
                "[%d/%d] Question: %s",
                i,
                len(notes_info),
                question_text[:80] + ("..." if len(question_text) > 80 else ""),
            )
            logger.info(
                "        Answer → References: %s",
                answer_text[:80] + ("..." if len(answer_text) > 80 else ""),
            )

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
                    logger.warning("        ⚠️ 建立失敗（AnkiConnect 回傳 None）")
                    fail_count += 1
            except AnkiConnectError as e:
                logger.error("        ❌ 建立失敗: %s", e)
                fail_count += 1

        # Step 4: 輸出統計結果
        logger.info("-" * 60)
        logger.info("遷移完成！")
        logger.info("  ✅ 成功: %d", success_count)
        logger.info("  ⏭️  跳過: %d", skip_count)
        logger.info("  ❌ 失敗: %d", fail_count)
        logger.info("  📊 總計: %d", len(notes_info))

        if not dry_run and success_count > 0:
            # 嘗試同步
            try:
                await client.sync()
                logger.info("  🔄 已觸發 Anki 同步")
            except AnkiConnectError as e:
                logger.warning("  ⚠️ 同步失敗（不影響已建立的卡片）: %s", e)

    except AnkiConnectError as e:
        logger.error("AnkiConnect 連線失敗: %s", e)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(migrate())
