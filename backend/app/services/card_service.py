"""
核心卡片生成服務模組。

本模組是 FluencyTides 的核心業務邏輯層，串接 LLM 結構化輸出、
AnkiConnect 筆記提交、以及模型管理功能，提供完整的「輸入單字 → 生成卡片」流程。

Phase 2 改進：
- 整合 PromptManager (Jinja2) 自動載入 System Prompt 模板。
- 新增 generate_card() 統一入口，接收 CardGenerateRequest Pydantic 模型。
- 語意化例外包裝：底層 AnkiConnectError 等轉換為 DuplicateCardError 等。
- 支援 extra_fields 合併（使用者手動提供的固定欄位不經 LLM 生成）。

設計原則：
- 此模組嚴格屬於 Service 層，透過建構子注入所有 Infrastructure 依賴。
- 禁止在此處直接建立 httpx 連線或讀取環境變數。
- Web API (FastAPI Router) 與 Telegram Bot 均透過此 Service 執行業務邏輯，
  確保雙端 Controller 共用相同的生成邏輯。

Dependencies:
    - AnkiClient (infrastructure/anki)
    - LLMClient (infrastructure/llm)
    - AnkiModelManager (services/anki_model_manager)
    - PromptManager (services/prompt_manager)
"""

import logging
import base64
import json
from datetime import datetime, timezone
from collections.abc import Awaitable, Callable

from app.core.exceptions import (
    AnkiServiceError,
    DeckNotFoundError,
    DuplicateCardError,
    LLMServiceError,
    ModelFileNotFoundError,
)
from app.infrastructure.anki.client import AnkiClient, AnkiConnectError
from app.infrastructure.llm.client import LLMClient
from app.schemas.anki import AnkiDeckInfo, AnkiModelInfo
from app.schemas.card import CardGenerateRequest, CardGenerateResponse
from app.services.anki_model_manager import AnkiModelManager
from app.services.prompt_manager import PromptManager
from app.services.relation_service import RelationService

logger = logging.getLogger(__name__)


class CardService:
    """核心卡片生成與管理服務。

    此服務是 FluencyTides 的中樞，負責：
    1. 從模型定義檔讀取 LLM JSON Schema。
    2. 從 Jinja2 模板載入或使用自訂 System Prompt。
    3. 呼叫 LLM 生成結構化卡片內容。
    4. 組裝為 AnkiNote 並提交至 Anki。
    5. 提供防重複檢查、牌組存在性驗證、模型列表查詢等輔助功能。

    此類別被設計為由 FastAPI 的 Dependency Injection 或
    Telegram Bot 的 Handler 注入使用，不應直接實例化。

    Attributes:
        _anki_client: AnkiConnect 非同步客戶端。
        _llm_client: LLM 非同步客戶端。
        _model_manager: Anki 模型管理器。
        _prompt_manager: Jinja2 Prompt 模板管理器。
        _relation_service: 卡片關聯服務（用於自動寫入圖譜關聯）。
    """

    def __init__(
        self,
        anki_client: AnkiClient,
        llm_client: LLMClient,
        model_manager: AnkiModelManager,
        prompt_manager: PromptManager,
        relation_service: RelationService,
    ) -> None:
        """初始化 CardService。

        Args:
            anki_client: 注入的 AnkiConnect 非同步客戶端。
            llm_client: 注入的 LLM 非同步客戶端。
            model_manager: 注入的 Anki 模型管理器。
            prompt_manager: 注入的 Jinja2 Prompt 模板管理器。
            relation_service: 注入的卡片關聯服務。
        """
        self._anki_client = anki_client
        self._llm_client = llm_client
        self._model_manager = model_manager
        self._prompt_manager = prompt_manager
        self._relation_service = relation_service

    # ========================================================================
    # Phase 2 統一入口
    # ========================================================================

    async def generate_card(
        self,
        request: CardGenerateRequest,
    ) -> CardGenerateResponse:
        """Phase 2 的統一卡片生成入口。

        接收 CardGenerateRequest Pydantic 模型，執行完整流程：
        1. 確認目標牌組存在。
        2. 防重複檢查。
        3. 取得 LLM JSON Schema。
        4. 載入或使用自訂 System Prompt。
        5. 呼叫 LLM 生成結構化資料。
        6. 合併 extra_fields（若有）。
        7. 組裝 AnkiNote 並提交。

        相較於 Phase 1 的 check_and_generate，此方法：
        - 自動填充預設 System Prompt（從 Jinja2 模板載入）。
        - 支援 extra_fields 合併。
        - 包裝所有例外為語意化的 FluencyTidesError 子類。
        - 回傳 CardGenerateResponse Pydantic 模型而非裸 int。

        Args:
            request: CardGenerateRequest Pydantic 模型實例。

        Returns:
            CardGenerateResponse Pydantic 模型實例。

        Raises:
            DeckNotFoundError: 牌組不存在時。
            DuplicateCardError: 卡片重複時。
            ModelFileNotFoundError: 模型定義檔不存在時。
            LLMServiceError: LLM API 請求失敗時。
            AnkiServiceError: AnkiConnect 回傳錯誤時。
        """
        logger.info(
            "開始生成卡片流程 -> 輸入: '%s', 牌組: '%s', 模型: '%s'",
            request.user_input,
            request.deck_name,
            request.model_name,
        )

        # Step 1: 確認目標牌組存在
        try:
            await self._model_manager.ensure_deck_exists(request.deck_name)
        except RuntimeError as e:
            raise DeckNotFoundError(str(e)) from e
        except AnkiConnectError as e:
            raise AnkiServiceError(
                f"檢查牌組時 Anki 服務異常: {e.message}"
            ) from e

        # Step 2: 防重複檢查
        try:
            can_add = await self._model_manager.can_add_note(
                deck_name=request.deck_name,
                model_name=request.model_name,
                fields={request.primary_field_name: request.user_input},
            )
            if not can_add:
                raise DuplicateCardError(
                    f"卡片 '{request.user_input}' 已存在於 "
                    f"'{request.deck_name}' 中，取消生成以避免重複。"
                )
        except AnkiConnectError as e:
            raise AnkiServiceError(
                f"防重複檢查時 Anki 服務異常: {e.message}"
            ) from e

        # Step 3: 取得 LLM JSON Schema 並動態注入 Graph_Relations
        try:
            import copy
            response_schema = copy.deepcopy(self._model_manager.get_model_schema(
                request.model_file_name
            ))
            
            if "properties" in response_schema:
                response_schema["properties"]["Graph_Relations"] = {
                    "type": "array",
                    "description": "知識圖譜關聯。請根據卡片內容，提取有價值的關聯節點。",
                    "items": {
                        "type": "object",
                        "properties": {
                            "target_label": {"type": "string", "description": "關聯目標名稱 (如另一個單字)"},
                            "relation_type": {"type": "string", "description": "關係名稱 (如 synonym, antonym, collocation, parent 等)"},
                            "direction": {"type": "string", "enum": ["forward", "bidirectional"], "description": "關係方向性"}
                        },
                        "required": ["target_label", "relation_type", "direction"],
                        "additionalProperties": False
                    }
                }
                if "required" in response_schema and isinstance(response_schema["required"], list):
                    response_schema["required"].append("Graph_Relations")
                    
        except FileNotFoundError as e:
            raise ModelFileNotFoundError(str(e)) from e
        except ValueError as e:
            raise ModelFileNotFoundError(str(e)) from e

        # Step 4: 取得 System Prompt
        system_prompt = self._resolve_system_prompt(
            request.system_prompt,
            request.model_name,
        )

        # Step 5: 呼叫 LLM 生成結構化資料
        # LLMServiceError 由 LLMClient 自行拋出，無需在此包裝
        llm_result = await self._llm_client.generate_structured_data(
            system_prompt=system_prompt,
            user_prompt=request.user_input,
            response_schema=response_schema,
        )

        logger.info(
            "LLM 結構化輸出成功（第 %d 次嘗試），正在組裝 AnkiNote...",
            llm_result.attempt_count,
        )

        # Step 6: 合併 extra_fields（使用者手動提供的固定欄位）
        # 這些欄位不經 LLM 生成，直接合併進最終資料中，
        # 典型場景如音檔佔位符 [sound:...]、使用者提供的原文等。
        merged_data = dict(llm_result.parsed_data)
        if request.extra_fields:
            logger.info(
                "合併使用者自訂額外欄位: %s",
                list(request.extra_fields.keys()),
            )
            merged_data.update(request.extra_fields)

        # Step 7: 提取 Graph_Relations 並從給 Anki 的資料中移除
        graph_relations_data = merged_data.pop("Graph_Relations", [])

        # Step 8: 組裝 AnkiNote 並提交
        note = self._model_manager.create_note_from_llm_response(
            deck_name=request.deck_name,
            model_name=request.model_name,
            llm_response=merged_data,
            tags=request.tags if request.tags else ["LLM_Auto_Generated"],
        )

        try:
            note_id = await self._model_manager.submit_note(note)
        except AnkiConnectError as e:
            if "duplicate" in e.message.lower():
                raise DuplicateCardError(e.message) from e
            raise AnkiServiceError(
                f"提交卡片至 Anki 時發生錯誤: {e.message}"
            ) from e
        except RuntimeError as e:
            raise AnkiServiceError(str(e)) from e

        # Step 9: 自動解析並寫入知識圖譜關聯 (Card Relations)
        await self._create_relations_from_llm_data(
            source_note_id=note_id,
            source_label=request.user_input,
            relations_data=graph_relations_data,
        )

        logger.info(
            "✅ 卡片生成完成！筆記 ID: %d, 牌組: '%s'",
            note_id,
            request.deck_name,
        )

        return CardGenerateResponse(
            note_id=note_id,
            deck_name=request.deck_name,
            model_name=request.model_name,
            message=f"卡片 '{request.user_input}' 已成功建立。",
        )

    # ========================================================================
    # Phase 6: RUD 操作 (Read, Update, Delete)
    # ========================================================================

    async def get_card(self, note_id: int) -> dict[str, object]:
        """讀取單一卡片詳細資訊。

        呼叫 AnkiConnect 取得卡片現有欄位。
        
        Args:
            note_id: 筆記 ID。
            
        Returns:
            包含 note_id, model_name, tags, fields 的字典。
            
        Raises:
            AnkiServiceError: 當 AnkiConnect 請求失敗或找不到筆記時。
        """
        try:
            notes = await self._anki_client.get_notes_info(notes=[note_id])
            if not notes:
                raise AnkiServiceError(f"找不到 ID 為 {note_id} 的筆記")
            note = notes[0]
            # 將欄位格式簡化，原本的 {"Expression": {"value": "apple", "order": 0}} 轉為 {"Expression": "apple"}
            simplified_fields = {
                key: str(val.get("value", ""))
                for key, val in note.fields.items()
            }
            return {
                "note_id": note.noteId,
                "model_name": note.modelName,
                "tags": note.tags,
                "fields": simplified_fields
            }
        except AnkiConnectError as e:
            raise AnkiServiceError(f"讀取卡片時 Anki 服務異常: {e.message}") from e

    async def update_card(self, note_id: int, fields: dict[str, str]) -> None:
        """更新卡片欄位。

        呼叫 AnkiConnect 更新欄位，若 Expression 改變，同步更新關聯資料庫。
        
        Args:
            note_id: 筆記 ID。
            fields: 要更新的欄位字典。
            
        Raises:
            AnkiServiceError: 更新失敗時。
        """
        try:
            # 1. 更新 Anki
            await self._anki_client.update_note_fields(note_id, fields)
            
            # 2. 如果使用者修改了 Expression，需要同步更新資料庫關聯表中的 source_label
            # 這裡我們假設主要欄位名稱為 Expression，如果未來有變動可能需要動態判斷
            if "Expression" in fields:
                new_expression = fields["Expression"]
                if new_expression:
                    await self._relation_service.update_source_label(note_id, new_expression)
                    
            logger.info("卡片 %d 更新成功", note_id)
        except AnkiConnectError as e:
            raise AnkiServiceError(f"更新卡片時 Anki 服務異常: {e.message}") from e

    async def delete_card(self, note_id: int) -> None:
        """刪除卡片與其相關聯。

        呼叫 AnkiConnect 刪除實體卡片，並清理資料庫中的關聯紀錄。
        
        Args:
            note_id: 筆記 ID。
            
        Raises:
            AnkiServiceError: 刪除失敗時。
        """
        try:
            # 1. 從 Anki 中刪除
            await self._anki_client.delete_notes([note_id])
            
            # 2. 從關聯資料庫中刪除對應的關聯
            await self._relation_service.delete_relations_for_note(note_id)
            
            logger.info("卡片 %d 刪除成功，並已清理相關關聯", note_id)
        except AnkiConnectError as e:
            raise AnkiServiceError(f"刪除卡片時 Anki 服務異常: {e.message}") from e

    # ========================================================================
    # 查詢輔助方法
    # ========================================================================

    async def process_voice_evaluation(
        self,
        card_id: str,
        audio_data: bytes,
        audio_filename: str,
        audio_evaluator: "BaseAudioEvaluator",
        progress_callback: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, object]:
        """處理語音評估流程。

        從 Anki 取得卡片資訊 -> 呼叫 LLM 評估 -> 寫回 Anki 卡片。

        Args:
            card_id: Anki Card ID。
            audio_data: 語音二進位資料。
            audio_filename: 語音檔名。
            audio_evaluator: 語音評估器實例。
            progress_callback: 用於發送進度訊息的非同步回呼函數。

        Returns:
            包含評估結果與卡片資訊的字典。

        Raises:
            AnkiServiceError: Anki 操作失敗。
            LLMServiceError: LLM 評估失敗。
        """
        async def _notify(msg: str) -> None:
            if progress_callback:
                await progress_callback(msg)

        try:
            await _notify("步驟 2/4: 正在從 Anki 讀取卡片資料...")
            note_ids = await self._anki_client.find_notes(f"Card_ID:{card_id}")
            if not note_ids:
                raise AnkiServiceError(f"找不到 Card ID: {card_id}")

            notes_info = await self._anki_client.get_notes_info(notes=note_ids[:1])
            if not notes_info:
                raise AnkiServiceError("無法取得卡片詳細資訊。")

            note_info = notes_info[0]
            note_id = note_info.noteId

            # 提取 Prompt 文字
            prompt_text = str(note_info.fields.get("Prompt", {}).get("value", "")).strip()

            # 提取 References 並解析為純文字列表
            refs_raw = str(note_info.fields.get("References", {}).get("value", "[]")).strip()
            reference_answers: list[str] = []
            try:
                refs_list = json.loads(refs_raw)
                if isinstance(refs_list, list):
                    reference_answers = [
                        str(ref.get("content", ""))
                        for ref in refs_list
                        if isinstance(ref, dict) and ref.get("content")
                    ]
            except json.JSONDecodeError:
                logger.warning("References 欄位 JSON 解析失敗，視為空。")

            # 提取現有的 Recordings
            recs_raw = str(note_info.fields.get("Recordings", {}).get("value", "[]")).strip()
            try:
                existing_recordings = json.loads(recs_raw)
                if not isinstance(existing_recordings, list):
                    existing_recordings = []
            except json.JSONDecodeError:
                existing_recordings = []

            await _notify(
                f"步驟 3/4: AI 正在分析你的語音...\n"
                f"📝 Prompt: {prompt_text[:50]}...\n"
                f"📚 參考答案: {len(reference_answers)} 筆"
            )

            evaluation = await audio_evaluator.evaluate_audio(
                audio_data=audio_data,
                audio_filename=audio_filename,
                prompt_text=prompt_text,
                reference_answers=reference_answers,
            )

            await _notify("步驟 4/4: 正在將結果寫回 Anki...")

            # 4a. 將音檔存入 Anki collection.media
            from app.schemas.anki import AnkiStoreMediaParams
            audio_b64 = base64.b64encode(audio_data).decode("utf-8")
            await self._anki_client.store_media_file(
                AnkiStoreMediaParams(
                    filename=audio_filename,
                    data=audio_b64,
                    deleteExisting=True,
                )
            )

            # 4b. 組裝新的 RecordingItem
            from app.schemas.speaking import RecordingItem
            today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
            new_recording = RecordingItem(
                date=today,
                score=evaluation.score,
                transcript=evaluation.transcript,
                comment=evaluation.feedback,
                audio=audio_filename,
            )

            # 4c. 將新錄音附加到現有陣列（最新的放在最前面）
            existing_recordings.insert(0, new_recording.model_dump())
            new_recordings_json = json.dumps(existing_recordings, ensure_ascii=False)

            # 4d. 更新 Anki 卡片的 Recordings 欄位
            await self._anki_client.update_note_fields(
                note_id=note_id,
                fields={"Recordings": new_recordings_json},
            )

            return {
                "score": evaluation.score,
                "status_code": evaluation.status_code,
                "transcript": evaluation.transcript,
                "feedback": evaluation.feedback,
                "card_id": card_id,
            }

        except Exception as e:
            logger.error("語音評估流程失敗: %s", e)
            raise
        """列出所有可用的 Anki 模型定義。

        委託 AnkiModelManager 掃描本地模型目錄。

        Returns:
            AnkiModelInfo 模型實例列表。
        """
        return self._model_manager.list_available_models()

    def get_model_detail(
        self, model_file_name: str
    ) -> dict[str, object]:
        """取得單一模型的完整定義資訊。

        Args:
            model_file_name: JSON 檔案名稱。

        Returns:
            模型定義檔的完整 JSON 字典。

        Raises:
            ModelFileNotFoundError: 找不到對應檔案時。
        """
        try:
            return self._model_manager.get_model_detail(model_file_name)
        except (FileNotFoundError, ValueError) as e:
            raise ModelFileNotFoundError(str(e)) from e

    async def list_decks(self) -> list[AnkiDeckInfo]:
        """取得 Anki 中所有牌組的摘要資訊。

        委託 AnkiClient 查詢牌組名稱與 ID。

        Returns:
            AnkiDeckInfo 模型實例列表。

        Raises:
            AnkiServiceError: AnkiConnect 請求失敗時。
        """
        try:
            decks_dict = await self._anki_client.get_deck_names_and_ids()
            return [
                AnkiDeckInfo(deck_name=name, deck_id=deck_id)
                for name, deck_id in decks_dict.items()
            ]
        except AnkiConnectError as e:
            raise AnkiServiceError(
                f"取得牌組列表時 Anki 服務異常: {e.message}"
            ) from e

    # ========================================================================
    # 內部輔助方法
    # ========================================================================

    async def _create_relations_from_llm_data(
        self,
        source_note_id: int,
        source_label: str,
        relations_data: list[dict[str, object]],
    ) -> None:
        """從 LLM 回傳的 Graph_Relations 中提取關聯並寫入資料庫。

        解析 relations_data，轉換為 CardRelationCreate DTO 並批次寫入關聯庫。
        若方向為 bidirectional，將會建立兩筆紀錄 (A->B, B->A)。
        若目標單字尚未建立卡片，將會建立 target_note_id 為 None 的「懸空節點」。

        Args:
            source_note_id: 剛建立完成的起點 Anki Note ID。
            source_label: 起點單字的標籤（如 user_input）。
            relations_data: LLM 回傳的 Graph_Relations 陣列。
        """
        from app.schemas.relation import CardRelationCreate

        relations_to_create = []

        if not isinstance(relations_data, list):
            logger.warning("relations_data 不是列表，略過關聯建立。")
            return

        for rel in relations_data:
            if not isinstance(rel, dict):
                continue
            
            target_label = str(rel.get("target_label", "")).strip()
            relation_type = str(rel.get("relation_type", "")).strip()
            direction = str(rel.get("direction", "forward")).strip()

            if not target_label or not relation_type:
                continue
                
            # Forward edge (source -> target)
            relations_to_create.append(
                CardRelationCreate(
                    source_note_id=source_note_id,
                    target_note_id=None,
                    relation_type=relation_type,
                    source_label=source_label,
                    target_label=target_label,
                )
            )
            
            # If bidirectional, add reverse edge (target -> source)
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
                await self._relation_service.batch_create_relations(relations_to_create)
                logger.info("已為卡片 '%s' 自動寫入 %d 筆關聯", source_label, len(relations_to_create))
            except Exception as e:
                # 寫入關聯失敗不應中斷卡片建立流程，僅記錄錯誤
                logger.error("自動寫入關聯時發生錯誤: %s", e)

    def _resolve_system_prompt(
        self,
        custom_prompt: str | None,
        model_name: str,
    ) -> str:
        """解析 System Prompt 來源。

        優先使用使用者自訂的 Prompt，若未提供則從 Jinja2 模板載入。

        Args:
            custom_prompt: 使用者自訂的 System Prompt（可為 None）。
            model_name: 模型名稱，用於定位模板。

        Returns:
            最終的 System Prompt 字串。

        Raises:
            PromptTemplateNotFoundError: 未提供自訂 Prompt 且找不到模板時。
        """
        if custom_prompt:
            logger.info("使用使用者自訂 System Prompt (長度: %d)", len(custom_prompt))
            return custom_prompt

        logger.info(
            "未提供自訂 Prompt，從 Jinja2 模板載入: %s", model_name
        )
        # PromptTemplateNotFoundError 由 PromptManager 自行拋出
        return self._prompt_manager.render(model_name)

    # ========================================================================
    # Phase 1 向後相容方法（保留供內部測試使用）
    # ========================================================================

    async def generate_and_add_card(
        self,
        user_input: str,
        deck_name: str,
        model_file_name: str,
        model_name: str,
        system_prompt: str,
        tags: list[str] | None = None,
    ) -> int:
        """完整的卡片生成流程：LLM 生成 → 組裝 → 提交。

        此方法是 Phase 1 的原始入口，Phase 2 保留供內部或測試使用。
        新的外部呼叫請使用 generate_card() 方法。

        Args:
            user_input: 使用者輸入的原始文字（例如：一個英文單字或句子）。
            deck_name: 目標牌組名稱。
            model_file_name: 模型定義 JSON 檔名（例如 'TOEIC_Coach_Dark.json'）。
            model_name: Anki 筆記類型名稱（例如 'TOEIC_Coach_Dark'）。
            system_prompt: LLM 系統提示，定義生成行為與角色。
            tags: 附加至卡片的標籤列表。

        Returns:
            成功建立後 Anki 回傳的筆記 ID。

        Raises:
            FileNotFoundError: 模型定義檔不存在時。
            ValueError: LLM 回傳的 JSON 無效時。
            AnkiConnectError: AnkiConnect 回傳錯誤時。
            RuntimeError: 牌組不存在或其他不可恢復錯誤時。
        """
        logger.info(
            "開始生成卡片流程 -> 輸入: '%s', 牌組: '%s', 模型: '%s'",
            user_input,
            deck_name,
            model_name,
        )

        # Step 1: 讀取模型的 LLM JSON Schema
        response_schema = self._model_manager.get_model_schema(
            model_file_name
        )

        # Step 2: 呼叫 LLM 生成結構化資料
        llm_result = await self._llm_client.generate_structured_data(
            system_prompt=system_prompt,
            user_prompt=user_input,
            response_schema=response_schema,
        )

        logger.info("LLM 結構化輸出成功，正在組裝 AnkiNote...")

        # Step 3: 組裝為 AnkiNote
        note = self._model_manager.create_note_from_llm_response(
            deck_name=deck_name,
            model_name=model_name,
            llm_response=llm_result.parsed_data,
            tags=tags,
        )

        # Step 4: 提交至 AnkiConnect
        note_id = await self._model_manager.submit_note(note)

        logger.info(
            "✅ 卡片生成完成！筆記 ID: %d, 牌組: '%s'", note_id, deck_name
        )
        return note_id

    async def check_and_generate(
        self,
        user_input: str,
        deck_name: str,
        model_file_name: str,
        model_name: str,
        system_prompt: str,
        primary_field_name: str = "Expression",
        tags: list[str] | None = None,
    ) -> int:
        """帶有完整前置檢查的卡片生成流程。

        Phase 1 的原始入口，Phase 2 保留供內部或測試使用。
        新的外部呼叫請使用 generate_card() 方法。

        Args:
            user_input: 使用者輸入的原始文字。
            deck_name: 目標牌組名稱。
            model_file_name: 模型定義 JSON 檔名。
            model_name: Anki 筆記類型名稱。
            system_prompt: LLM 系統提示。
            primary_field_name: 主要欄位名稱，用於防重複檢查，預設 'Expression'。
            tags: 附加至卡片的標籤列表。

        Returns:
            成功建立後 Anki 回傳的筆記 ID。

        Raises:
            RuntimeError: 牌組不存在、卡片重複、或其他不可恢復錯誤時。
        """
        # 前置檢查 1: 牌組存在性
        await self._model_manager.ensure_deck_exists(deck_name)

        # 前置檢查 2: 防重複
        can_add = await self._model_manager.can_add_note(
            deck_name=deck_name,
            model_name=model_name,
            fields={primary_field_name: user_input},
        )
        if not can_add:
            raise RuntimeError(
                f"卡片 '{user_input}' 已存在於 '{deck_name}' 中，"
                f"取消生成以避免重複。"
            )

        # 執行完整的生成流程
        return await self.generate_and_add_card(
            user_input=user_input,
            deck_name=deck_name,
            model_file_name=model_file_name,
            model_name=model_name,
            system_prompt=system_prompt,
            tags=tags,
        )
