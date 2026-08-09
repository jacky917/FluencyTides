"""Speaking Trilingual Dark 樣板測試卡片驗證器。

Speaking Trilingual Dark template test-card validator: performs
structural checks on the model/sample JSON and inserts fully populated
sample cards into Anki for UI and deep-link preview.

用途：
    建立一張包含完整假資料的 Speaking_Trilingual_Dark 卡片，
    在 Anki 中確認：正面（完整 Prompt＋Context＋三顆錄音按鈕、無答案內容）、
    Prompt_Audios 國旗徽章、背面三語區段預設折疊/展開、各 Deep Link 按鈕。

    另做結構檢查：11 欄位齊全且順序正確、七個 JSON 欄位可解析、
    Prompt_Audios 每語言 ≤1 條且 lang ∈ {ZH, JA, EN}。

使用範例：
    python scripts/common/template_validators/speaking_trilingual_dark_validator.py --overwrite
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

# 加入 backend 到 Python Path 並載入 .env
backend_dir = Path(__file__).resolve().parents[3]
sys.path.append(str(backend_dir))
import scripts.common.env  # noqa

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

EXPECTED_FIELDS = [
    "Prompt",
    "Prompt_Audios",
    "Context",
    "Recordings_ZH",
    "Recordings_JA",
    "Recordings_EN",
    "References_ZH",
    "References_JA",
    "References_EN",
    "Card_ID",
    "TG_Bot",
]
JSON_FIELDS = EXPECTED_FIELDS[1:2] + EXPECTED_FIELDS[3:9]  # Prompt_Audios + 六個三語欄位
VALID_LANGS = {"ZH", "JA", "EN"}


class SpeakingTrilingualDarkValidator:
    """Speaking Trilingual Dark 卡片單一插入驗證器。

    Single-insert validator for Speaking Trilingual Dark cards.
    """

    def __init__(self):
        """初始化模型/牌組名稱與範例資料路徑。

        Initialize model/deck names and the sample data path.
        """
        self.model_name = "Speaking_Trilingual_Dark"
        self.deck_name = "テスト::Speaking_Trilingual"
        self.tg_bot = "Jacky917_bot"
        self.sample_path = (
            backend_dir / "scripts" / "common" / "samples" / "speaking_trilingual_sample.json"
        )

    def validate_model_json(self) -> None:
        """檢查 model JSON 的欄位齊全且順序正確。

        Verify the model JSON contains all fields in the expected order.

        Raises:
            AssertionError: 欄位不符時拋出。Raised on field mismatch.
        """
        model_dir = backend_dir / "app" / "anki_models"
        with open(model_dir / f"{self.model_name}.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        fields = data["inOrderFields"]
        assert fields == EXPECTED_FIELDS, (
            f"欄位不符：期望 {EXPECTED_FIELDS}，實際 {fields}"
        )
        logger.info("✅ model JSON：11 欄位齊全且順序正確。")

    def validate_sample(self) -> list[dict]:
        """檢查 sample JSON 的結構並回傳卡片清單。

        Validate the sample JSON structure and return the card list.

        Returns:
            list[dict]: 通過檢查的卡片清單。List of validated cards.

        Raises:
            AssertionError: 結構不符時拋出。Raised on structural errors.
        """
        with open(self.sample_path, "r", encoding="utf-8") as f:
            cards = json.load(f)
        seen_ids: set[str] = set()
        for card in cards:
            fields = card["fields"]
            for key in JSON_FIELDS:
                value = fields.get(key, [])
                items = json.loads(value) if isinstance(value, str) else value
                assert isinstance(items, list), f"{key} 必須是 JSON 陣列"
            # Prompt_Audios：每語言 ≤1、lang 合法
            pa = fields.get("Prompt_Audios", [])
            pa_items = json.loads(pa) if isinstance(pa, str) else pa
            langs = [item.get("lang") for item in pa_items]
            assert all(l in VALID_LANGS for l in langs), f"Prompt_Audios 含非法 lang: {langs}"
            assert len(langs) == len(set(langs)), f"Prompt_Audios 同語言多條: {langs}"
            # Card_ID 唯一
            card_id = fields.get("Card_ID", "")
            assert card_id and card_id not in seen_ids, f"Card_ID 缺失或重複: {card_id}"
            seen_ids.add(card_id)
        logger.info(f"✅ sample JSON：{len(cards)} 筆結構檢查通過。")
        return cards

    async def _import_anki_model(self, ac) -> None:
        """從 backend/app/anki_models 讀取檔案並匯入模型。

        Read the model files from backend/app/anki_models and import the
        model into Anki.

        Args:
            ac: Anki 連線客戶端。AnkiConnect client instance.
        """
        model_dir = backend_dir / "app" / "anki_models"
        with open(model_dir / f"{self.model_name}.json", "r", encoding="utf-8") as f:
            in_order_fields = json.load(f)["inOrderFields"]
        with open(model_dir / f"{self.model_name}_front.html", "r", encoding="utf-8") as f:
            front_html = f.read()
        with open(model_dir / f"{self.model_name}_back.html", "r", encoding="utf-8") as f:
            back_html = f.read()
        with open(model_dir / f"{self.model_name}_style.css", "r", encoding="utf-8") as f:
            css_style = f.read()

        logger.info(f"👉 模型 {self.model_name} 尚未安裝，正在自動匯入...")
        await ac.create_model(
            model_name=self.model_name,
            in_order_fields=in_order_fields,
            css=css_style,
            card_templates=[
                {"Name": "Card 1", "Front": front_html, "Back": back_html}
            ],
        )
        logger.info(f"✅ {self.model_name} 匯入完成。")

    async def create_sample_cards(self, ac, cards: list[dict]) -> None:
        """建立測試卡片（附帶假資料，含各語言錄音/範本）。

        Create test cards with fake data, including per-language
        recordings and reference examples.

        Args:
            ac: Anki 連線客戶端。AnkiConnect client instance.
            cards: 已驗證的卡片資料清單。List of validated card data.
        """
        from app.schemas.anki import AnkiNote, AnkiNoteOptions

        models = await ac.get_model_names()
        if self.model_name not in models:
            await self._import_anki_model(ac)
        else:
            logger.info(f"✅ 模型 '{self.model_name}' 已存在。")

        await ac.create_deck(self.deck_name)

        for card in cards:
            raw_fields = card["fields"]
            fields = {
                key: (json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v)
                for key, v in raw_fields.items()
            }
            fields["TG_Bot"] = self.tg_bot
            note = AnkiNote(
                deckName=self.deck_name,
                modelName=self.model_name,
                fields=fields,
                tags=["Speaking_Trilingual", "テスト"],
                options=AnkiNoteOptions(allowDuplicate=False, duplicateScope="deck"),
            )
            note_id = await ac.add_note(note)
            if note_id is not None:
                logger.info(f"🎉 測試卡片建立成功！Card_ID: {fields['Card_ID']}, Note ID: {note_id}")
            else:
                logger.warning("⚠️ 卡片建立失敗（可能重複），請加 --overwrite 重新執行。")
        logger.info(f"   請在 Anki 開啟牌組 {self.deck_name} 預覽卡片樣式。")

    async def execute(self, overwrite: bool, no_anki: bool) -> None:
        """入口：結構檢查 →（可選）建卡。

        Entry point: run structural checks, then optionally create cards.

        Args:
            overwrite: True 時先刪除既有測試卡再重建。If True, delete
                existing test cards before rebuilding.
            no_anki: True 時僅做結構檢查，不連 Anki。If True, only run
                structural checks without connecting to Anki.
        """
        self.validate_model_json()
        cards = self.validate_sample()
        if no_anki:
            logger.info("🧪 --no-anki：僅做結構檢查，不連 Anki。")
            return

        from app.infrastructure.anki.client import AnkiClient  # 延遲 import：--no-anki 不需依賴

        ac = AnkiClient()
        try:
            if overwrite:
                try:
                    existing = await ac.find_notes(f'"deck:{self.deck_name}" tag:テスト')
                    if existing:
                        logger.info(f"⚠️ 發現已存在 {len(existing)} 張測試卡片，正在刪除...")
                        await ac.delete_notes(existing)
                except Exception as e:
                    logger.warning(f"覆蓋檢查時發生錯誤 (可忽略): {e}")
            await self.create_sample_cards(ac, cards)
        finally:
            await ac.close()


async def main() -> None:
    """解析命令列參數並執行驗證器。

    Parse command-line arguments and run the validator.
    """
    parser = argparse.ArgumentParser(description="Speaking_Trilingual_Dark 樣板驗證器")
    parser.add_argument("--overwrite", action="store_true", help="刪除既有測試卡後重建")
    parser.add_argument("--no-anki", action="store_true", help="僅做結構檢查，不連 Anki")
    args = parser.parse_args()
    await SpeakingTrilingualDarkValidator().execute(args.overwrite, args.no_anki)


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
