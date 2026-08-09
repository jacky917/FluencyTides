"""
專屬 Speaking_Coach_Dark 的 Anki 卡片匯入腳本。

Anki card import script dedicated to the Speaking_Coach_Dark model.

這是一個直接透過程式碼定義內容並匯入的入口，不透過外部檔案或命令列參數傳入。
可用於快速測試、手動批次匯入等情境。
Card contents are defined directly in code rather than via external files or
CLI arguments; useful for quick tests and manual batch imports.

用法：
    cd backend
    python -m scripts.Speaking_Coach_Dark.import_cards
"""

import asyncio
import json
import logging
import sys
import uuid
from datetime import datetime
from typing import Dict, List, Union

from app.infrastructure.anki.client import AnkiClient, AnkiConnectError
from app.schemas.anki import AnkiNote, AnkiNoteOptions

# ── 日誌設定 ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("ImportSpeakingCoachDark")

from app.infrastructure.utils.id_generator import generate_unique_card_id


def _generate_card_id() -> str:
    """產生唯一的 Card_ID。

    Generate a unique Card_ID.

    Returns:
        str: 帶有前綴 "sc-"、時間戳與隨機字串的唯一 ID。
            A unique ID with the "sc-" prefix, timestamp and random suffix.
    """
    return generate_unique_card_id(prefix="sc")


async def import_cards(
    deck_name: str,
    cards_data: List[Dict[str, Union[str, List[str]]]],
    dry_run: bool = False
) -> None:
    """
    匯入卡片資料到指定的 Anki 牌組中。

    Import card data into the specified Anki deck.

    此函數負責遍歷提供的卡片資料，驗證必要欄位（Prompt），並為 Speaking_Coach_Dark
    模型構建 AnkiNote 實例。最後將卡片逐一匯入 Anki 並回報統計結果。
    Iterates over the provided card data, validates the required Prompt field,
    builds AnkiNote instances for the Speaking_Coach_Dark model, imports them
    one by one and reports summary statistics.

    Args:
        deck_name: 目標牌組名稱。Target deck name.
        cards_data: 包含卡片資料的字典列表。List of card-data dictionaries.
            支援的欄位包含 "Prompt", "Context", "Prompt_Audios", "Recordings",
            "References", "TG_Bot", "tags" 等。Supported keys include "Prompt",
            "Context", "Prompt_Audios", "Recordings", "References", "TG_Bot", "tags".
        dry_run: 是否為預覽模式。若為 True，僅印出執行計畫而不實際寫入 Anki。
            Preview mode; if True, only print the plan without writing to Anki.
    """
    target_model = "Speaking_Coach_Dark"
    
    client = AnkiClient()
    logger.info("=" * 60)
    logger.info("Speaking_Coach_Dark 專屬卡片匯入工具")
    logger.info("=" * 60)
    logger.info("目標牌組: %s", deck_name)
    logger.info("目標模型: %s", target_model)
    logger.info("預計匯入卡片數: %d", len(cards_data))
    if dry_run:
        logger.info("⚠️  預覽模式 (dry_run=True)：不會實際寫入 Anki")
    logger.info("-" * 60)

    success_count = 0
    fail_count = 0

    try:
        for idx, card in enumerate(cards_data, 1):
            # 取得各欄位，因為 typing 要求嚴格，這裡需要做型別轉換或判斷
            raw_prompt = card.get("Prompt", "")
            prompt = str(raw_prompt).strip() if isinstance(raw_prompt, str) else ""
            
            if not prompt:
                logger.warning("跳過空白 Prompt 的卡片 (Index %d)", idx)
                continue

            # 若有指定 Card_ID 則使用，否則自動生成
            raw_card_id = card.get("Card_ID")
            card_id = str(raw_card_id) if isinstance(raw_card_id, str) and raw_card_id else _generate_card_id()
            
            # 其他文字欄位
            raw_prompt_audios = card.get("Prompt_Audios", "[]")
            prompt_audios = str(raw_prompt_audios) if isinstance(raw_prompt_audios, str) else "[]"
            
            raw_context = card.get("Context", "")
            context = str(raw_context).strip() if isinstance(raw_context, str) else ""
            
            raw_recordings = card.get("Recordings", "[]")
            recordings = str(raw_recordings) if isinstance(raw_recordings, str) else "[]"
            
            raw_references = card.get("References", "[]")
            references = str(raw_references) if isinstance(raw_references, str) else "[]"
            
            raw_tg_bot = card.get("TG_Bot", "Jacky917_bot")
            tg_bot = str(raw_tg_bot) if isinstance(raw_tg_bot, str) else "Jacky917_bot"
            
            # 標籤欄位
            raw_tags = card.get("tags", [])
            tags = raw_tags if isinstance(raw_tags, list) else []
            # 確保標籤內都是字串
            valid_tags = [str(t) for t in tags]

            new_note = AnkiNote(
                deckName=deck_name,
                modelName=target_model,
                fields={
                    "Card_ID": card_id,
                    "Prompt": prompt,
                    "Prompt_Audios": prompt_audios,
                    "Context": context,
                    "Recordings": recordings,
                    "References": references,
                    "TG_Bot": tg_bot,
                },
                tags=valid_tags,
                options=AnkiNoteOptions(
                    allowDuplicate=False,
                    duplicateScope="deck",
                ),
            )

            logger.info("[%d] Prompt: %s", idx, prompt[:80] + ("..." if len(prompt) > 80 else ""))
            if context:
                logger.info("    Context: %s", context[:80] + ("..." if len(context) > 80 else ""))
            logger.info("    Tags: %s", valid_tags)

            if dry_run:
                logger.info("    [DRY-RUN] 將建立 Card_ID=%s (跳過寫入)", card_id)
                success_count += 1
                continue

            try:
                new_note_id = await client.add_note(new_note)
                if new_note_id:
                    logger.info("    ✅ 建立成功 → Note ID: %d, Card_ID: %s", new_note_id, card_id)
                    success_count += 1
                else:
                    logger.warning("    ⚠️ 建立失敗（可能已有重複的 Prompt）")
                    fail_count += 1
            except AnkiConnectError as e:
                logger.error("    ❌ 建立失敗: %s", e)
                fail_count += 1

        # 輸出統計結果
        logger.info("-" * 60)
        logger.info("匯入完成！")
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
    # ── 在這裡定義要匯入的資料 ──────────────────────────────────
    
    # 請在此處指定您的目標牌組名稱
    TARGET_DECK = "日本語::AI點評::面接（2026/06/13）"
    
    # 預覽模式：若設為 False 則會實際將卡片寫入 Anki 中
    DRY_RUN = False
    
    # 在這裡填寫您的卡片資料
    #
    # 支援的欄位包含:
    # - Prompt (字串，必填)
    # - Context (字串)
    # - Prompt_Audios (字串，預設: "[]")
    # - Recordings (字串，預設: "[]")
    # - References (字串，預設: "[]")
    # - TG_Bot (字串，預設: "Jacky917_bot")
    # - tags (字串列表，預設: [])
    # - Card_ID (字串，選填，若無則自動產生)
    
    today_str = datetime.now().strftime("%Y-%m-%d")

    CARDS_TO_IMPORT: List[Dict[str, Union[str, List[str]]]] = [
        # {
        #     "Prompt": "なぜ今の会社を辞めて、弊社を志望されたのですか？",
        #     "Context": "【アドバイス】\n退職理由はネガティブなものではなく、ポジティブなキャリアアップとして伝えましょう。",
        #     "Prompt_Audios": "[]",
        #     "Recordings": "[]",
        #     "References": "[]",
        #     "TG_Bot": "Jacky917_bot",
        #     "tags": ["TelegramBot", "Speaking_Coach", "面接準備", "転職理由"],
        # },
        {
            "Prompt": "開発で何を重視するかの判断軸",
            "Context": "開発で重視する観点を3～5つ決め、優先の考え方を言語化しておく（例：ユーザー価値、品質、納期、運用性、コスト）。",
            "References": json.dumps([{
                "date": today_str,
                "content": "私が開発において最も重視する判断軸は、『安定性と品質』です。理由は、御社のような宿泊予約システムでは、データの連携ミスが『オーバーブッキング（超賣）』といった顧客の致命的な損失に直結するからです。外部連携では通信エラーがつきものです。\
                そのため、リトライ処理を徹底して、『絶対にデータを取りこぼさない設計』を最優先します。もし納期が厳しい状況になっても、この『安定性』を妥協してスピードを上げることは絶対にしない、というのが私のエンジニアとしての信念です。",
                "status": 1,
                "audios": []
            }], ensure_ascii=False),
            "TG_Bot": "Jacky917_bot",
            "tags": ["面接準備", "temairazu"],
            # 其餘欄位若省略，腳本內會自動代入預設值或自動生成（如 Card_ID）
        }
    ]

    asyncio.run(import_cards(
        deck_name=TARGET_DECK, 
        cards_data=CARDS_TO_IMPORT, 
        dry_run=DRY_RUN
    ))
