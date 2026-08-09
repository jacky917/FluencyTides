"""外語糾錯任務處理器 (Expression Correction Handler) 模組。

負責「外語糾錯」母子卡片群組的生成與更新：呼叫 LLM 產生糾錯結果、
建立 Expression_Master_Dark 母卡片與 Expression_Micro_Dark 子卡片，
並支援音檔欄位的 JSON 更新。

Expression correction handler module.

Generates and updates master/micro card groups for foreign-language
correction: calls the LLM to produce correction results, creates
Expression_Master_Dark master cards and Expression_Micro_Dark micro
cards, and supports JSON updates of audio fields.
"""

import json
import logging
from typing import override, TYPE_CHECKING

from app.services.task_handlers.base import BaseHandler
from app.services.task_handlers.registry import register_handler
from app.infrastructure.utils.id_generator import generate_unique_card_id
from app.core.dependencies import get_template_engine
from app.infrastructure.llm.client import LLMClient
from app.schemas.llm.expression import LLMExpressionCorrectionResult
from app.core.config import settings
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager

if TYPE_CHECKING:
    from app.services.card_service import CardService
    from app.services.relation_service import RelationService

logger = logging.getLogger(__name__)

@register_handler
class ExpressionCorrectionHandler(BaseHandler):
    """外語糾錯與原子化知識點處理器。

    Handler for foreign-language correction and atomic knowledge points.
    """

    @property
    @override
    def handler_name(self) -> str:
        """處理器名稱。

        Handler name.
        """
        return "expression_correction"

    @property
    @override
    def supported_models(self) -> list[str]:
        """支援的 Anki 模型名稱清單。

        Supported Anki model names.
        """
        return ["Expression_Master_Dark", "Expression_Micro_Dark"]

    @override
    def get_input_schema(self) -> dict:
        """回傳前端需要的參數 Schema。

        Return the parameter JSON schema required by the frontend.
        """
        return {
            "type": "object",
            "properties": {
                "native_language": {"type": "string"},
                "target_language": {"type": "string"},
                "original_text": {"type": "string"},
                "context": {"type": "string"},
                "source_tag": {"type": "string"},
                "user_grammar_correction": {"type": "string"},
                "user_reorganization": {"type": "string"},
                "tg_bot": {"type": "string"},
            },
            "required": ["native_language", "target_language", "original_text"]
        }

    async def execute_generate(
        self,
        parameters: dict[str, str]
    ) -> LLMExpressionCorrectionResult:
        """僅呼叫 LLM 產生糾錯結果，不寫入 Anki。

        Call the LLM to generate the correction result only, without
        writing to Anki.

        此方法用於在 TG 流程中先行產生預覽結果，讓使用者
        確認後再決定要寫入哪些子卡片。將「LLM 生成」與
        「Anki 寫入」解耦，遵循單一職責原則。

        Used in the Telegram flow to produce a preview first, so the user
        can pick which micro cards to write, decoupling LLM generation
        from Anki writes (single responsibility).

        Args:
            parameters: 包含語言設定、原文、情境等參數的字典。Dict with
                language settings, original text, context, etc.

        Returns:
            LLMExpressionCorrectionResult 結構化物件。Structured
            LLMExpressionCorrectionResult object.

        Raises:
            ValueError: LLM 回傳格式異常或解析失敗。When the LLM output is
                malformed or parsing fails.
        """
        native_lang = parameters.get("native_language", "中文")
        target_lang = parameters.get("target_language", "日文")
        original_text = parameters.get("original_text", "")
        context_raw = parameters.get("context", "")
        
        import re
        # 以連續五個以上的「ー」為分隔符切分對話
        context_parts = [p.strip() for p in re.split(r'ー{5,}', context_raw) if p.strip()]
        user_grammar_correction = parameters.get("user_grammar_correction", "")
        user_reorganization = parameters.get("user_reorganization", "")

        # 1. 渲染 Prompt
        template_engine = get_template_engine()
        prompt_text = template_engine.render(
            "prompts/anki/expression_correction.j2",
            native_language=native_lang,
            target_language=target_lang,
            original_text=original_text,
            context_parts=context_parts,
            user_grammar_correction=user_grammar_correction,
            user_reorganization=user_reorganization
        )

        system_prompt = "You are a strict JSON data extractor and native language coach."

        # 2. 呼叫 LLM
        llm_client = LLMClient()
        schema_dict = LLMExpressionCorrectionResult.model_json_schema()

        result = await llm_client.generate_structured_data(
            system_prompt=system_prompt,
            user_prompt=prompt_text,
            response_schema=schema_dict
        )

        return LLMExpressionCorrectionResult.model_validate(result.parsed_data)

    @override
    async def execute_create(
        self,
        card_service: "CardService",
        relation_service: "RelationService",
        deck_name: str,
        model_name: str,
        parameters: dict
    ) -> int | list[int]:
        """建立母子卡片群組。

        Create the master/micro card group.

        支援兩種模式：
        1. 完整模式：不提供 llm_result，內部自動呼叫 LLM 生成。
        2. 預覽模式：提供 llm_result（已生成的結果）+ selected_indices
           來控制只寫入使用者選擇的子卡片。

        Two modes: full mode (no llm_result; the LLM is called
        internally) and preview mode (a pre-generated llm_result plus
        selected_indices controls which micro cards are written).

        Args:
            card_service: Anki 卡片操作服務。Anki card service.
            relation_service: 關聯服務（目前未使用）。Relation service
                (currently unused).
            deck_name: 母卡片目標牌組。Target deck for the master card.
            model_name: 母卡片模型名稱。Master card model name.
            parameters: 任務參數字典，可額外包含：Task parameter dict,
                optionally with:
                - llm_result: 預先生成的 LLMExpressionCorrectionResult
                  物件。Pre-generated LLMExpressionCorrectionResult.
                - selected_indices: 要寫入的子卡片索引列表（基於統一編號，
                  None 表示全部寫入）。Indices of micro cards to write
                  (unified numbering; None writes all).

        Returns:
            寫入成功的 Note ID 列表。List of created note IDs.
        """
        original_text = parameters.get("original_text", "")
        context_raw = parameters.get("context", "")
        import re
        context_parts = [p.strip() for p in re.split(r'ー{5,}', context_raw) if p.strip()]
        context_json = json.dumps([{"text": p} for p in context_parts], ensure_ascii=False)
        
        source_tag = parameters.get("source_tag", "")
        tg_bot = parameters.get("tg_bot", settings.TG_BOT_USERNAME or "")

        # 取得 LLM 結果：優先使用外部傳入的預生成結果
        llm_result = parameters.get("llm_result")
        if llm_result is None:
            correction_result = await self.execute_generate(parameters)
        elif isinstance(llm_result, LLMExpressionCorrectionResult):
            correction_result = llm_result
        else:
            # 從序列化字典還原
            correction_result = LLMExpressionCorrectionResult.model_validate(llm_result)

        # 取得使用者選擇的子卡片索引（None = 全部）
        selected_indices: list[int] | None = parameters.get("selected_indices")

        # 1. 準備 JSON 音訊欄位
        answer_audios = json.dumps([{"audio": "", "speaker": "AI", "avatar": "none"}], ensure_ascii=False)
        empty_audios = json.dumps([], ensure_ascii=False)

        # 2. 建立母卡片
        master_card_id = generate_unique_card_id(prefix="ec-m")

        # 序列化 JSON 解說 (由子卡片的 error_hint 組合而成)
        detailed_exp = []
        for mp in correction_result.grammar_micro_points:
            detailed_exp.append({"point": mp.target_phrase, "explanation": mp.error_hint})
        for mp in correction_result.reorganized_micro_points:
            detailed_exp.append({"point": mp.target_phrase, "explanation": mp.error_hint})
        detailed_exp_json = json.dumps(detailed_exp, ensure_ascii=False)

        master_fields = {
            "My_Original": original_text,
            "Context": context_json,
            "Error_Comparison": correction_result.error_comparison,
            "Correct_Answer": correction_result.grammar_correction,
            "Reorganized_Expression": correction_result.reorganized_expression,
            "Answer_Audios": answer_audios,
            "Detailed_Explanation": detailed_exp_json,
            "Child_Cards_Data": empty_audios,  # 預留用，暫時空陣列
            "Card_ID": master_card_id,
            "TG_Bot": tg_bot
        }

        master_deck = "日本語::外語糾錯::母卡片"
        base_tags = ["Expression_Correction"]
        if source_tag:
            base_tags.append(source_tag)
            
        allow_duplicate = parameters.get("allow_duplicate", False)

        master_note_id = await card_service.create_note(
            deck_name=master_deck,
            model_name="Expression_Master_Dark",
            fields=master_fields,
            tags=base_tags + ["HandlerGenerated"],
            allow_duplicate=allow_duplicate
        )

        created_ids = [master_note_id]

        # 3. 建立統一編號的子卡片列表
        # 統一編號規則：grammar_micro_points 在前，reorganized_micro_points 在後，
        # 按照 LLM 回傳順序從 1 開始連續編號。
        all_micro_entries: list[tuple[str, str, object]] = []
        for mp in correction_result.grammar_micro_points:
            all_micro_entries.append(("Grammar", "文法修正", mp))
        for mp in correction_result.reorganized_micro_points:
            all_micro_entries.append(("Reorganized", "重新組織", mp))

        base_micro_deck = "日本語::外語糾錯::子卡片"
        micro_tags = base_tags + ["MicroPoint"]

        for idx, (card_type, sub_deck_name, mp) in enumerate(all_micro_entries):
            # 使用 1-based 編號
            card_number = idx + 1

            # 如果有指定篩選，且此卡片不在選擇列表中，則跳過
            if selected_indices is not None and card_number not in selected_indices:
                continue

            deck_name_full = f"{base_micro_deck}::{sub_deck_name}"
            micro_card_id = generate_unique_card_id(prefix="ec-s")

            phrase_audios = json.dumps([{"audio": "", "speaker": "AI", "avatar": "none"}], ensure_ascii=False)

            micro_fields = {
                "Target_Phrase": mp.target_phrase,
                "Native_Translation": mp.native_translation,
                "Context_Hint": mp.context_hint,
                "Context_Sentence": mp.context_sentence,
                "Phrase_Audios": phrase_audios,
                "Error_Hint": mp.error_hint,
                "Master_Note_ID": str(master_note_id),
                "Card_ID": micro_card_id,
                "Card_Type": card_type,
                "TG_Bot": tg_bot
            }

            micro_note_id = await card_service.create_note(
                deck_name=deck_name_full,
                model_name="Expression_Micro_Dark",
                fields=micro_fields,
                tags=micro_tags
            )
            created_ids.append(micro_note_id)

        return created_ids

    @override
    async def execute_read_list(
        self,
        card_service: "CardService",
        relation_service: "RelationService",
        deck_name: str | None = None
    ) -> list[dict]:
        """讀取卡片清單（目前未實作，回傳空清單）。

        Read the card list (not implemented; returns an empty list).
        """
        return []

    @override
    async def execute_read_graph(
        self,
        card_service: "CardService",
        relation_service: "RelationService",
        deck_name: str | None = None
    ) -> dict:
        """讀取知識圖譜（目前未實作，回傳空結構）。

        Read the knowledge graph (not implemented; returns empty struct).
        """
        return {"nodes": [], "links": []}

    @override
    async def execute_update(
        self,
        card_service: "CardService",
        relation_service: "RelationService",
        note_id: int,
        parameters: dict
    ) -> None:
        """客製化更新（目前僅支援 add_audio 動作）。

        Task-specific update (currently only the add_audio action).

        Args:
            card_service: 卡片服務。Card service.
            relation_service: 關聯服務。Relation service.
            note_id: 目標 Note ID。Target note ID.
            parameters: 需含 action，add_audio 時另需 field_name 與
                audio。Must contain 'action'; add_audio also requires
                'field_name' and 'audio'.

        Raises:
            ValueError: 參數缺漏或不支援的 action。On missing params or
                unsupported action.
        """
        action = parameters.get("action")
        if action == "add_audio":
            field_name = parameters.get("field_name")
            audio_filename = parameters.get("audio")

            if not field_name or not audio_filename:
                raise ValueError("add_audio 需要 field_name 和 audio 參數")

            items = await AnkiJsonFieldManager.safe_read_list(card_service, note_id, field_name)

            # 替換音檔
            if items and isinstance(items[0], dict) and items[0].get("speaker") == "AI":
                items[0]["audio"] = audio_filename
            else:
                items.append({"audio": audio_filename, "speaker": "AI", "avatar": "none"})

            await AnkiJsonFieldManager.update_field(card_service, note_id, field_name, items)
            logger.info("已新增音檔 %s 到 %s", audio_filename, field_name)
            return

        raise ValueError(f"不支援的更新操作: {action}")

    @override
    async def execute_delete(
        self,
        card_service: "CardService",
        relation_service: "RelationService",
        note_id: int
    ) -> None:
        """刪除卡片。

        Delete the note.

        Args:
            card_service: 卡片服務。Card service.
            relation_service: 關聯服務。Relation service.
            note_id: 目標 Note ID。Target note ID.
        """
        await card_service.delete_note(note_id)
