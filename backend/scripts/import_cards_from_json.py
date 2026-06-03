"""
通用 JSON 卡片匯入工具 (CLI)。

此腳本允許使用者透過標準化的 JSON 檔案，將任意筆記類型的卡片批次匯入 Anki。
腳本會自動驗證欄位是否與模型定義相符，並支援自動安裝缺失的模型，
以及自動序列化巢狀 JSON 資料 (如 list, dict) 為字串。
同時，若 JSON 中帶有关聯資訊 (Synonyms_JSON, Collocations_JSON)，
也會自動寫入知識圖譜資料庫中。

用法：
    cd backend
    python -m scripts.import_cards_from_json --file scripts/samples/speaking_coach_sample.json
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.infrastructure.anki.client import AnkiClient, AnkiConnectError
from app.schemas.anki import AnkiNote, AnkiNoteOptions
from app.services.anki_model_manager import AnkiModelManager
from app.services.relation_service import RelationService

# 設定日誌格式
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("CardImporter")


async def _extract_and_create_relations(
    relation_service: RelationService,
    source_note_id: int,
    source_label: str,
    relations_data: list[dict[str, Any]]
) -> None:
    from app.schemas.relation import CardRelationCreate
    relations_to_create = []

    if not isinstance(relations_data, list):
        return

    for rel in relations_data:
        target_label = str(rel.get("target_label", "")).strip()
        relation_type = str(rel.get("relation_type", "")).strip()
        direction = str(rel.get("direction", "forward")).strip()

        if not target_label or not relation_type:
            continue

        relations_to_create.append(
            CardRelationCreate(
                source_note_id=source_note_id,
                target_note_id=None,
                relation_type=relation_type,
                source_label=source_label,
                target_label=target_label,
            )
        )
        
        if direction == "bidirectional":
            relations_to_create.append(
                CardRelationCreate(
                    source_note_id=None,
                    target_note_id=source_note_id,
                    relation_type=relation_type,
                    source_label=target_label,
                    target_label=source_label,
                )
            )

    if relations_to_create:
        try:
            await relation_service.batch_create_relations(relations_to_create)
            logger.info(f"已為卡片 '{source_label}' 自動寫入 {len(relations_to_create)} 筆關聯")
        except Exception as e:
            logger.error(f"自動寫入關聯時發生錯誤: {e}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="通用的 Anki JSON 卡片匯入工具")
    parser.add_argument(
        "--file",
        type=str,
        required=True,
        help="要匯入的 JSON 檔案路徑",
    )
    parser.add_argument(
        "--db-url",
        type=str,
        default=None,
        help="自訂的資料庫連線字串 (如 sqlite+aiosqlite:///./my_db.db)。若未提供，預設使用環境變數的 DATABASE_URL。",
    )
    args = parser.parse_args()

    file_path = Path(args.file)
    if not file_path.is_file():
        logger.error(f"找不到檔案: {file_path}")
        return

    # 讀取 JSON 檔案
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            notes_data: list[dict[str, Any]] = json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"JSON 格式無效: {e}")
        return

    if not isinstance(notes_data, list):
        logger.error("JSON 檔案的最外層必須是一個陣列 (List)。")
        return

    logger.info("正在初始化基礎設施與資料庫連線...")
    
    # 根據是否傳入 --db-url 決定要使用的 Session Factory
    if args.db_url:
        logger.info(f"使用自訂的資料庫連線: {args.db_url}")
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from sqlmodel import SQLModel
        import app.infrastructure.database.conventions  # noqa: F401
        import app.infrastructure.database.models  # noqa: F401
        
        custom_engine = create_async_engine(args.db_url)
        async with custom_engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        session_factory = async_sessionmaker(custom_engine, class_=AsyncSession, expire_on_commit=False)
    else:
        logger.info(f"使用預設資料庫連線 (DATABASE_URL): {settings.DATABASE_URL}")
        from app.infrastructure.database.database import async_session_factory as session_factory, create_db_and_tables
        # 確保資料表存在
        await create_db_and_tables()

    anki_client = AnkiClient()
    model_manager = AnkiModelManager(
        anki_client=anki_client,
        model_dir=Path(__file__).resolve().parent.parent / "app" / "anki_models",
    )

    try:
        async with session_factory() as session:
            relation_service = RelationService(session)
            
            # 取得 Anki 目前已安裝的模型列表
            installed_models = await anki_client.get_model_names()
            
            success_count = 0
            fail_count = 0

            for index, note_data in enumerate(notes_data):
                logger.info(f"正在處理第 {index + 1}/{len(notes_data)} 張卡片...")
                
                deck_name = note_data.get("deckName")
                model_name = note_data.get("modelName")
                tags = note_data.get("tags", [])
                fields_raw = note_data.get("fields", {})
                relations_raw = note_data.get("relations", [])

                if not deck_name or not model_name:
                    logger.error(f"卡片 {index + 1} 缺少 'deckName' 或 'modelName'，跳過。")
                    fail_count += 1
                    continue

                # 1. 檢查牌組是否存在，不存在則建立
                await model_manager.ensure_deck_exists(deck_name)

                # 2. 檢查模型是否存在，不存在則自動匯入
                if model_name not in installed_models:
                    logger.info(f"👉 模型 '{model_name}' 尚未安裝至 Anki，正在自動匯入...")
                    try:
                        await model_manager.import_model_from_files(model_name)
                        installed_models.append(model_name)
                        logger.info(f"✅ 模型 '{model_name}' 匯入完成。")
                    except Exception as e:
                        logger.error(f"❌ 模型匯入失敗: {e}")
                        fail_count += 1
                        continue

                # 3. 讀取本地模型定義進行嚴格欄位驗證
                try:
                    required_fields = set(
                        model_manager.get_model_fields(f"{model_name}.json")
                    )
                except Exception as e:
                    logger.error(f"❌ 無法讀取本地模型定義 '{model_name}.json': {e}")
                    fail_count += 1
                    continue

                provided_fields = set(fields_raw.keys())
                
                # 嚴格校驗卡片內容欄位
                missing = required_fields - provided_fields
                extra = provided_fields - required_fields

                if missing or extra:
                    logger.error(
                        f"❌ 欄位不匹配！卡片模型為 '{model_name}'。\n"
                        f"   - 缺少欄位: {missing if missing else '無'}\n"
                        f"   - 多餘欄位: {extra if extra else '無'}"
                    )
                    fail_count += 1
                    continue

                # 4. 處理並序列化欄位
                fields_processed: dict[str, str] = {}
                for key, value in fields_raw.items():
                    if isinstance(value, (dict, list)):
                        fields_processed[key] = json.dumps(value, ensure_ascii=False)
                    elif isinstance(value, str):
                        val_stripped = value.strip()
                        if val_stripped.startswith(("[", "{")) and val_stripped.endswith(("]", "}")):
                            try:
                                json.loads(val_stripped)
                            except json.JSONDecodeError:
                                logger.warning(
                                    f"⚠️ 欄位 '{key}' 看似 JSON 字串，但解析失敗。將原樣寫入。"
                                )
                        fields_processed[key] = value
                    else:
                        fields_processed[key] = str(value)

                # 5. 組裝並送出
                note = AnkiNote(
                    deckName=deck_name,
                    modelName=model_name,
                    fields=fields_processed,
                    tags=tags,
                    options=AnkiNoteOptions(
                        allowDuplicate=False,
                        duplicateScope="deck",
                    ),
                )

                try:
                    note_id = await model_manager.submit_note(note)
                    logger.info(f"✅ 成功匯入，Note ID: {note_id}")
                    success_count += 1
                    
                    # 6. 自動寫入圖譜關聯
                    # 嘗試抓取 source_label，順序: 使用者指定的 primary_field_name -> Expression -> Prompt -> 第一個欄位
                    primary_field_name = note_data.get("primary_field_name")
                    if primary_field_name and primary_field_name in fields_processed:
                        source_label = fields_processed[primary_field_name]
                    elif "Expression" in fields_processed:
                        source_label = fields_processed["Expression"]
                    elif "Prompt" in fields_processed:
                        source_label = fields_processed["Prompt"]
                    else:
                        source_label = str(next(iter(fields_processed.values()), ""))
                        
                    await _extract_and_create_relations(
                        relation_service=relation_service,
                        source_note_id=note_id,
                        source_label=source_label,
                        relations_data=relations_raw
                    )
                    
                except AnkiConnectError as e:
                    logger.error(f"❌ 匯入失敗: {e}")
                    fail_count += 1
                except Exception as e:
                    logger.error(f"❌ 發生未預期錯誤: {e}", exc_info=True)
                    fail_count += 1

            logger.info("-" * 40)
            logger.info(
                f"🎉 匯入作業結束！共處理 {len(notes_data)} 張卡片。"
                f"成功: {success_count}, 失敗: {fail_count}"
            )

    finally:
        await anki_client.close()


if __name__ == "__main__":
    settings.setup_logging()
    asyncio.run(main())
