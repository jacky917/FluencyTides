"""
牌組遷移腳本：將舊版 Speaking_Coach_Dark_old 遷移為新的 Speaking_Coach_Dark（欄位重排版）。

Deck migration script: migrates legacy Speaking_Coach_Dark_old notes
to the new Speaking_Coach_Dark model (reordered fields), regenerating
Card_ID with UUIDs while copying all other field values and tags.

遷移內容：
    - 來源筆記類型: Speaking_Coach_Dark_old（Card_ID 在第一個欄位）
    - 目標筆記類型: Speaking_Coach_Dark（Card_ID 移至倒數第二個欄位）
    - Card_ID 會重新生成（使用 UUID），不沿用舊值
    - 其餘欄位值與標籤原封不動搬移

用法：
    cd backend
    python -m scripts.migrations.migrate_old_speaking_coach

    # 預覽模式（不實際寫入 Anki，僅列印將要建立的卡片）
    python -m scripts.migrations.migrate_old_speaking_coach --dry-run
"""

import asyncio
import json
import logging
import sys
import uuid
from pathlib import Path

from app.infrastructure.anki.client import AnkiClient, AnkiConnectError
from app.schemas.anki import AnkiNote, AnkiNoteOptions

# ── 遷移設定 ──────────────────────────────────────────────────────
SOURCE_DECK = "日本語::AI點評::面談（Geekly）2026/06/07x"
TARGET_DECK = "日本語::AI點評::面談（Geekly）2026/06/07"

SOURCE_MODEL = "Speaking_Coach_Dark_old"
TARGET_MODEL = "Speaking_Coach_Dark"

# 從舊版搬移的內容欄位（不含 Card_ID，Card_ID 會重新生成）
CONTENT_FIELDS = [
    "Prompt",
    "Prompt_Audios",
    "Context",
    "Recordings",
    "References",
    "TG_Bot",
]

# ── 日誌設定 ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("MigrateOldSpeakingCoach")


def _generate_card_id(seen: set[str]) -> str:
    """產生唯一的 Card_ID。

    使用完整的 UUID4 hex（32 碼，2^122 種可能），不做截斷，
    搭配外部傳入的 seen set 確保同一次執行中絕對不會碰撞。
    格式為 'sc-{32hex}'，總長 35 字元，在 Telegram Deep Link
    的 64 字元 start 參數限制內完全安全。

    Generate a unique Card_ID using a full UUID4 hex, deduplicated
    against the seen set for the current run.

    Args:
        seen: 本次執行已產生過的 Card_ID 集合，用於去重。
            Card_IDs already generated in this run, for dedup.

    Returns:
        格式為 'sc-xxxxxxxx...' 的唯一字串（35 字元）。
        Unique string in 'sc-<32hex>' format (35 chars).
    """
    while True:
        card_id = f"sc-{uuid.uuid4().hex}"
        if card_id not in seen:
            seen.add(card_id)
            return card_id


async def _ensure_target_model_exists(client: AnkiClient) -> None:
    """檢查目標筆記類型是否存在，若不存在則自動從 anki_models 目錄匯入。

    Ensure the target note type exists, importing it automatically
    from the anki_models directory when missing.

    Args:
        client: 已初始化的 AnkiClient 實例。Initialized AnkiClient instance.

    Raises:
        AnkiConnectError: 無法連線或建立模型時。
            When connection or model creation fails.
        FileNotFoundError: 找不到模型定義檔時。When model asset files are missing.
    """
    models = await client.get_model_names()
    if TARGET_MODEL in models:
        logger.info("✅ 目標模型 '%s' 已存在於 Anki 中。", TARGET_MODEL)
        return

    logger.info("⚠️  目標模型 '%s' 不存在，正在自動匯入...", TARGET_MODEL)

    model_dir = Path(__file__).resolve().parent.parent.parent / "app" / "anki_models"
    json_path = model_dir / f"{TARGET_MODEL}.json"
    front_path = model_dir / f"{TARGET_MODEL}_front.html"
    back_path = model_dir / f"{TARGET_MODEL}_back.html"
    css_path = model_dir / f"{TARGET_MODEL}_style.css"

    for path in [json_path, front_path, back_path, css_path]:
        if not path.is_file():
            raise FileNotFoundError(f"找不到模型檔案: {path}")

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        in_order_fields: list[str] = data["inOrderFields"]

    with open(front_path, "r", encoding="utf-8") as f:
        front_html = f.read()
    with open(back_path, "r", encoding="utf-8") as f:
        back_html = f.read()
    with open(css_path, "r", encoding="utf-8") as f:
        css_style = f.read()

    await client.create_model(
        model_name=TARGET_MODEL,
        in_order_fields=in_order_fields,
        css=css_style,
        card_templates=[
            {"Name": "Card 1", "Front": front_html, "Back": back_html}
        ],
    )
    logger.info("✅ 模型 '%s' 匯入完成（欄位: %s）。", TARGET_MODEL, in_order_fields)


async def migrate() -> None:
    """執行遷移主流程。

    Run the main migration flow: ensure the target model and deck
    exist, copy each source note with a regenerated Card_ID, and
    print the summary.
    """
    import argparse
    parser = argparse.ArgumentParser(description="遷移舊版 Speaking Coach 卡片到新版")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="預覽模式：僅列印將要建立的卡片，不實際寫入 Anki",
    )
    args = parser.parse_args()
    dry_run: bool = args.dry_run

    client = AnkiClient()
    logger.info("=" * 60)
    logger.info("FluencyTides 筆記類型遷移工具")
    logger.info("=" * 60)
    logger.info("來源: %s [%s]", SOURCE_DECK, SOURCE_MODEL)
    logger.info("目標: %s [%s]", TARGET_DECK, TARGET_MODEL)
    if dry_run:
        logger.info("⚠️  預覽模式 (--dry-run)：不會實際寫入 Anki")
    logger.info("-" * 60)

    try:
        # Step 0: 確保目標模型與牌組存在
        await _ensure_target_model_exists(client)
        await client.create_deck(TARGET_DECK)
        logger.info("✅ 目標牌組 '%s' 已就緒。", TARGET_DECK)

        # Step 1: 查詢來源筆記
        query = f'"deck:{SOURCE_DECK}" "note:{SOURCE_MODEL}"'
        note_ids = await client.find_notes(query)

        if not note_ids:
            logger.warning("來源牌組中沒有找到任何 %s 筆記，遷移中止。", SOURCE_MODEL)
            return

        logger.info("找到 %d 筆筆記，開始遷移...", len(note_ids))

        # Step 2: 取得筆記詳細資訊
        notes_info = await client.get_notes_info(notes=note_ids)

        success_count = 0
        fail_count = 0
        seen_ids: set[str] = set()  # 追蹤本次遷移已產生的 ID，防止碰撞

        for i, note in enumerate(notes_info, start=1):
            fields_data = note.fields
            tags = note.tags

            # 取出內容欄位（不含 Card_ID）
            new_fields: dict[str, str] = {}
            for f_name in CONTENT_FIELDS:
                val = fields_data.get(f_name, {})
                text_val = val.get("value", "") if isinstance(val, dict) else str(val)
                new_fields[f_name] = text_val

            # 重新生成 Card_ID
            new_card_id = _generate_card_id(seen_ids)
            new_fields["Card_ID"] = new_card_id

            prompt_preview = new_fields["Prompt"][:50]
            if len(new_fields["Prompt"]) > 50:
                prompt_preview += "..."
            logger.info(
                "[%d/%d] %s  (新 Card_ID: %s)",
                i, len(notes_info), prompt_preview, new_card_id,
            )

            # Step 3: 組裝新筆記
            new_note = AnkiNote(
                deckName=TARGET_DECK,
                modelName=TARGET_MODEL,
                fields=new_fields,
                tags=tags,
                options=AnkiNoteOptions(
                    allowDuplicate=True,
                    duplicateScope="deck",
                ),
            )

            if dry_run:
                logger.info("        [DRY-RUN] 標籤: %s (跳過寫入)", tags)
                success_count += 1
                continue

            try:
                new_note_id = await client.add_note(new_note)
                if new_note_id:
                    logger.info("        ✅ 建立成功 → Note ID: %d", new_note_id)
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
        logger.info("  ❌ 失敗: %d", fail_count)
        logger.info("  📊 總計: %d", len(notes_info))
        if not dry_run:
            logger.info("")
            logger.info("  ℹ️  舊卡片仍然保留在 '%s'。", SOURCE_DECK)
            logger.info("     請在 Anki 中確認新卡片無誤後，再手動刪除舊牌組。")

        if not dry_run and success_count > 0:
            try:
                await client.sync(force=True)
                logger.info("  🔄 已觸發 Anki 同步")
            except AnkiConnectError as e:
                logger.warning("  ⚠️ 同步失敗: %s", e)

    except AnkiConnectError as e:
        logger.error("AnkiConnect 連線失敗: %s", e)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(migrate())
