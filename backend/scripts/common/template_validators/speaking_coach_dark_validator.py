"""
Speaking Coach Dark 樣板測試卡片驗證器。

Speaking Coach Dark template test-card validator: inserts fully
populated sample cards into Anki to preview the template UI and
deep-link buttons.

用途：
    單一插入測試入口。建立一張包含完整假資料的 Speaking_Coach_Dark 卡片，
    用來在 Anki 中確認正面/背面/CSS 以及各種 Deep Link 按鈕的效果是否正確。

使用範例：
    # 確保已啟動 Poetry 或虛擬環境
    # 確保 PYTHONPATH 包含 backend 資料夾
    
    # 刪除既有同 ID 卡片並重新建立 (常用於修改後重新測試 UI 效果)
    python scripts/template_validators/speaking_coach_dark_validator.py --overwrite
"""

import asyncio
import json
import logging
import sys
from pathlib import Path

# 加入 backend 到 Python Path
sys.path.append(str(Path(__file__).resolve().parent.parent.parent))

from app.core.config import settings
from app.infrastructure.anki.client import AnkiClient
from app.schemas.anki import AnkiNote, AnkiNoteOptions, AnkiStoreMediaParams

logger = logging.getLogger(__name__)


class SpeakingCoachDarkValidator:
    """Speaking Coach Dark 卡片單一插入驗證器類別。

    Single-insert validator class for Speaking Coach Dark cards.
    """

    def __init__(self):
        """初始化模型/牌組名稱、媒體檔名並載入場景假資料。

        Initialize model/deck names, media filenames, and load the
        scenario fake data from data_ja.json.
        """
        self.model_name = "Speaking_Coach_Dark"
        self.deck_name = "テスト::Speaking_Coach"
        self.tg_bot = "Jacky917_bot"

        self.audio_1 = "example1.wav"
        self.avatar_1 = "example1.jpg"
        self.audio_2 = "example2.wav"
        self.avatar_2 = "example2.jpg"
        self.speaker_audio = "speaker.wav"

        # 讀取外部的 10 個場景資料
        data_path = Path(__file__).resolve().parent / "data_ja.json"
        with open(data_path, "r", encoding="utf-8") as f:
            self.scenarios = json.load(f)

    async def _import_anki_model(self, ac: AnkiClient) -> None:
        """從 backend/app/anki_models 讀取檔案並匯入模型。

        Read the model files from backend/app/anki_models and import the
        model into Anki.

        Args:
            ac: Anki 連線客戶端。AnkiConnect client instance.

        Raises:
            FileNotFoundError: 模型檔案缺失時拋出。Raised when any model
                file is missing.
        """
        model_dir = Path(__file__).resolve().parent.parent.parent / "app" / "anki_models"
        json_path = model_dir / f"{self.model_name}.json"
        front_path = model_dir / f"{self.model_name}_front.html"
        back_path = model_dir / f"{self.model_name}_back.html"
        css_path = model_dir / f"{self.model_name}_style.css"

        for path in [json_path, front_path, back_path, css_path]:
            if not path.is_file():
                raise FileNotFoundError(f"找不到模型檔案: {path}")

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            in_order_fields = data["inOrderFields"]

        with open(front_path, "r", encoding="utf-8") as f:
            front_html = f.read()
        with open(back_path, "r", encoding="utf-8") as f:
            back_html = f.read()
        with open(css_path, "r", encoding="utf-8") as f:
            css_style = f.read()

        logger.info(f"👉 模型 {self.model_name} 尚未安裝，正在自動匯入...")
        await ac.create_model(
            model_name=self.model_name,
            in_order_fields=in_order_fields,
            css=css_style,
            card_templates=[
                {"Name": "Card 1", "Front": front_html, "Back": back_html}
            ]
        )
        logger.info(f"✅ {self.model_name} 匯入完成。")

    async def create_sample_card(self, ac: AnkiClient):
        """建立一張完整的 Speaking_Coach_Dark 測試卡片。

        Create complete Speaking_Coach_Dark test cards: ensure the model
        and deck exist, upload media, then add one note per scenario.

        Args:
            ac: Anki 連線客戶端。AnkiConnect client instance.
        """
        logger.info(f"開始建立 {self.model_name} 測試卡片...")

        # 0. 檢查並匯入模型
        try:
            models = await ac.get_model_names()
            if self.model_name not in models:
                await self._import_anki_model(ac)
            else:
                logger.info(f"✅ 模型 '{self.model_name}' 已存在。")
        except Exception as e:
            logger.error(f"模型檢查或匯入失敗: {e}")
            return

        # 1. 確認牌組存在
        try:
            await ac.create_deck(self.deck_name)
            logger.info(f"✅ 牌組 '{self.deck_name}' 已就緒。")
        except Exception as e:
            logger.error(f"建立牌組失敗: {e}")
            return

        # 2. 上傳媒體檔案到 Anki
        assets_dir = Path(__file__).resolve().parent / "assets"

        # 如果 speaker.wav 不存在，回退使用 example1.wav 作為替代
        speaker_path = assets_dir / self.speaker_audio
        if not speaker_path.exists():
            logger.warning(f"找不到 {self.speaker_audio}，使用 {self.audio_1} 替代。")
            speaker_path = assets_dir / self.audio_1

        media_files = {
            self.audio_1: assets_dir / self.audio_1,
            self.avatar_1: assets_dir / self.avatar_1,
            self.audio_2: assets_dir / self.audio_2,
            self.avatar_2: assets_dir / self.avatar_2,
            self.speaker_audio: speaker_path,
        }

        for name, fpath in media_files.items():
            if not fpath.exists():
                logger.error(f"找不到媒體檔案: {fpath}")
                return

        try:
            for name, fpath in media_files.items():
                await ac.store_media_file(AnkiStoreMediaParams(filename=name, path=str(fpath)))
                logger.info(f"✅ 已上傳: {name}")
        except Exception as e:
            logger.error(f"上傳媒體檔案失敗: {e}")
            return

        # 3. 讀取並建立 10 張卡片
        success_count = 0
        for scenario in self.scenarios:
            card_id = scenario["card_id"]
            prompt_text = scenario["prompt"]
            context_text = scenario["context"]
            prompt_audios = json.dumps(scenario["prompt_audios"], ensure_ascii=False)
            recordings = json.dumps(scenario["recordings"], ensure_ascii=False)
            references = json.dumps(scenario["references"], ensure_ascii=False)

            note = AnkiNote(
                deckName=self.deck_name,
                modelName=self.model_name,
                fields={
                    "Card_ID": card_id,
                    "Prompt": prompt_text,
                    "Prompt_Audios": prompt_audios,
                    "Context": context_text,
                    "Recordings": recordings,
                    "References": references,
                    "TG_Bot": self.tg_bot
                },
                tags=["Speaking_Coach", "テスト"],
                options=AnkiNoteOptions(
                    allowDuplicate=False,
                    duplicateScope="deck"
                )
            )

            try:
                note_id = await ac.add_note(note)
                if note_id is not None:
                    logger.info(f"🎉 測試卡片建立成功！Card_ID: {card_id}, Note ID: {note_id}")
                    success_count += 1
                else:
                    logger.warning(f"⚠️ 卡片建立失敗 ({card_id})，可能是因為重複。請加上 --overwrite 參數重新執行。")
            except Exception as e:
                logger.error(f"❌ 卡片建立失敗 ({card_id}): {e}")

        if success_count > 0:
            logger.info(f"   共成功建立 {success_count} 張卡片，牌組: {self.deck_name}")
            logger.info("   請在 Anki 中開啟此牌組預覽卡片樣式。")

    async def execute(self, overwrite: bool):
        """含覆蓋邏輯的入口函式。

        Entry point with overwrite logic: optionally delete existing
        test cards before recreating them.

        Args:
            overwrite: True 時先刪除既有測試卡再重建。If True, delete
                existing test cards before rebuilding.
        """
        ac = AnkiClient()
        try:
            if overwrite:
                try:
                    query = f'"deck:{self.deck_name}" tag:テスト'
                    existing = await ac.find_notes(query)
                    if existing:
                        logger.info(f"⚠️ 發現已存在 {len(existing)} 張測試卡片，正在刪除...")
                        await ac.delete_notes(existing)
                except Exception as e:
                    logger.warning(f"覆蓋檢查時發生錯誤 (可忽略): {e}")

            await self.create_sample_card(ac)
        finally:
            await ac.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(message)s')
    
    validator = SpeakingCoachDarkValidator()
    # 直接寫死覆蓋參數，不使用 argparse
    asyncio.run(validator.execute(overwrite=True))
