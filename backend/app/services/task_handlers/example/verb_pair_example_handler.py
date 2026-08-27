"""日文自動詞/他動詞對範例處理器（測試示範用）模組。

展示以 LLM 一次生成母卡、Context 卡與 Cloze 卡的完整流程，
並示範母卡 JSON 綁定與 SQLite 圖譜關聯的建立。

Example handler module for Japanese verb pairs (for testing/demo).

Demonstrates the full flow of generating master, context, and cloze
cards from one LLM call, master-card JSON binding, and creating SQLite
graph relations.
"""

import json
import logging
from typing import override, TYPE_CHECKING

from app.services.task_handlers.base import BaseHandler
from app.services.task_handlers.registry import register_handler
from app.infrastructure.utils.id_generator import generate_unique_card_id
from app.core.dependencies import get_template_engine
from app.infrastructure.llm.factory import create_llm_client
from app.schemas.llm.example.verb_pair import VerbPairGenerationResult
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager

if TYPE_CHECKING:
    from app.services.card_service import CardService
    from app.services.relation_service import RelationService

logger = logging.getLogger(__name__)

@register_handler
class VerbPairExampleHandler(BaseHandler):
    """日文自動詞他動詞對專屬處理器 (測試範例用)。

    Handler for Japanese verb pairs (testing/demo example).
    """

    @property
    @override
    def handler_name(self) -> str:
        """處理器名稱。

        Handler name.
        """
        return "verb_pair_example"

    @property
    @override
    def supported_models(self) -> list[str]:
        """支援的 Anki 模型名稱清單。

        Supported Anki model names.
        """
        return ["JP_VerbPair_Master_Dark", "JP_Context_Dark", "JP_VerbPair_Cloze_Dark"]

    @override
    def get_input_schema(self) -> dict:
        """回傳前端需要的參數 Schema。

        Return the parameter JSON schema required by the frontend.
        """
        return {
            "type": "object",
            "properties": {
                "intransitive": {"type": "string"},
                "transitive": {"type": "string"},
                "context_text": {"type": "string"},
                "llm_result": {"type": "object"}
            },
            "required": ["intransitive", "transitive"]
        }

    async def execute_generate(self, parameters: dict) -> VerbPairGenerationResult:
        """呼叫 LLM 產生動詞對卡片內容，不寫入 Anki。

        Call the LLM to generate verb-pair card content without writing
        to Anki.

        Args:
            parameters: 需含 intransitive / transitive，可選
                context_text。Requires 'intransitive' / 'transitive';
                optional 'context_text'.

        Returns:
            VerbPairGenerationResult 結構化物件。Structured
            VerbPairGenerationResult object.
        """
        intransitive = parameters.get("intransitive", "")
        transitive = parameters.get("transitive", "")
        context_text = parameters.get("context_text", "")
        
        template_engine = get_template_engine()
        prompt_text = template_engine.render(
            "prompts/anki/example/JP_VerbPair.j2",
            intransitive=intransitive,
            transitive=transitive,
            context_text=context_text
        )

        system_prompt = "You are an expert Japanese language curriculum designer. Always output strict JSON."
        llm_client = create_llm_client()
        schema_dict = VerbPairGenerationResult.model_json_schema()
        
        result = await llm_client.generate_structured_data(
            system_prompt=system_prompt,
            user_prompt=prompt_text,
            response_schema=schema_dict
        )
        return VerbPairGenerationResult.model_validate(result.parsed_data)

    @override
    async def execute_create(
        self,
        card_service: "CardService",
        relation_service: "RelationService",
        deck_name: str,
        model_name: str,
        parameters: dict
    ) -> int | list[int]:
        """一次建立母卡、Context 卡與 Cloze 卡並綁定關聯。

        Create the master, context, and cloze cards in one pass and bind
        their relations.

        Args:
            card_service: 卡片服務。Card service.
            relation_service: 關聯服務。Relation service.
            deck_name: 母卡目標牌組（可為空使用預設）。Master deck (falls
                back to a default when empty).
            model_name: 模型名稱（此處固定使用動詞對模型）。Model name
                (verb-pair models are used regardless).
            parameters: 需含 intransitive / transitive，可選 llm_result。
                Requires 'intransitive' / 'transitive'; optional
                'llm_result'.

        Returns:
            建立成功的 Note ID 列表。List of created note IDs.
        """
        intransitive = parameters.get("intransitive", "")
        transitive = parameters.get("transitive", "")
        llm_result_raw = parameters.get("llm_result")
        
        if llm_result_raw:
            if isinstance(llm_result_raw, VerbPairGenerationResult):
                generation = llm_result_raw
            else:
                generation = VerbPairGenerationResult.model_validate(llm_result_raw)
        else:
            generation = await self.execute_generate(parameters)

        master_card_id = generate_unique_card_id(prefix="vp-m")
        
        master_fields = {
            "Card_ID": master_card_id,
            "Intransitive_Word": intransitive,
            "Transitive_Word": transitive,
            "Intransitive_Data_JSON": "[]",
            "Transitive_Data_JSON": "[]"
        }
        
        base_tags = ["VerbPair", "HandlerGenerated"]
        master_deck = deck_name or "日本語::動詞對::母卡片"
        master_note_id = await card_service.create_note(
            deck_name=master_deck,
            model_name="JP_VerbPair_Master_Dark",
            fields=master_fields,
            tags=base_tags
        )
        
        created_ids = [master_note_id]
        
        context_card_id = generate_unique_card_id(prefix="vp-ctx")
        dialog_dicts = [t.model_dump() for t in generation.context.dialog]
        context_fields = {
            "Card_ID": context_card_id,
            "Master_Note_ID": str(master_note_id), # 雙向綁定母卡片的真實 note_id
            "Summary": generation.context.summary,
            "Dialog_JSON": json.dumps(dialog_dicts, ensure_ascii=False)
        }
        context_deck = "日本語::動詞對::情境"
        if deck_name:
            context_deck = deck_name.replace("母卡片", "情境").replace("Master", "Context")
        context_note_id = await card_service.create_note(
            deck_name=context_deck,
            model_name="JP_Context_Dark",
            fields=context_fields,
            tags=base_tags + ["Context"]
        )
        created_ids.append(context_note_id)
        
        cloze_card_id = generate_unique_card_id(prefix="vp-clz")
        # 紅底線高亮
        full_html = generation.cloze.full_sentence.replace(
            generation.cloze.target_particle_verb,
            f'<u class="error-line">{generation.cloze.target_particle_verb}</u>'
        )
        cloze_fields = {
            "Card_ID": cloze_card_id,
            "Master_Note_ID": str(master_note_id),
            "Cloze_Sentence": generation.cloze.cloze_sentence,
            "Full_Sentence_HTML": full_html,
            "Translation": generation.cloze.translation,
            "Target_Particle_Verb": generation.cloze.target_particle_verb,
            "Audio": "",
            "Speaker": generation.cloze.speaker,
            "Avatar": generation.cloze.avatar
        }
        cloze_deck = "日本語::動詞對::克漏字"
        if deck_name:
            cloze_deck = deck_name.replace("母卡片", "克漏字").replace("Master", "Cloze")
        cloze_note_id = await card_service.create_note(
            deck_name=cloze_deck,
            model_name="JP_VerbPair_Cloze_Dark",
            fields=cloze_fields,
            tags=base_tags + ["Cloze"]
        )
        created_ids.append(cloze_note_id)
        
        # 將 Context/Cloze 卡片綁定到母卡片的對應 JSON 陣列中
        example_item = {
            "text": generation.cloze.full_sentence,
            "audio": "",
            "speaker": "",
            "avatar": "none",
            "context_note_id": context_note_id,
            "cloze_note_id": cloze_note_id
        }
        
        if generation.cloze.verb_type_used == "intransitive":
            target_field = "Intransitive_Data_JSON"
        else:
            target_field = "Transitive_Data_JSON"
            
        await AnkiJsonFieldManager.update_field(card_service, master_note_id, target_field, [example_item])
        
        # 在 SQLite 建立關聯
        if relation_service:
            from app.schemas.relation import CardRelationCreate
            master_label = f"{intransitive} / {transitive}"
            relations_to_create = [
                CardRelationCreate(
                    source_label=master_label,
                    target_label="情境: " + generation.context.summary[:20],
                    relation_type="context",
                    source_note_id=master_note_id,
                    target_note_id=context_note_id
                ),
                CardRelationCreate(
                    source_label=master_label,
                    target_label="克漏字: " + generation.cloze.target_particle_verb,
                    relation_type="cloze",
                    source_note_id=master_note_id,
                    target_note_id=cloze_note_id
                )
            ]
            await relation_service.batch_create_relations(relations_to_create)
        
        return created_ids

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
            parameters: 需含 action；add_audio 時另需 field_name 與
                audio。Must contain 'action'; add_audio also requires
                'field_name' and 'audio'.

        Raises:
            ValueError: 參數缺漏或不支援的 action。On missing params or
                unsupported action.
        """
        action = parameters.get("action")

        if action == "add_audio":
            field_name = parameters.get("field_name")
            audio = parameters.get("audio")
            if not field_name or not audio:
                raise ValueError("add_audio 必須包含 field_name 和 audio")
                
            items = await AnkiJsonFieldManager.safe_read_list(card_service, note_id, field_name)
            
            text = parameters.get("text", "")
            speaker = parameters.get("speaker", "")
            avatar = parameters.get("avatar", "none")
            
            items.append({
                "audio": audio,
                "text": text,
                "speaker": speaker,
                "avatar": avatar
            })
            await AnkiJsonFieldManager.update_field(card_service, note_id, field_name, items)
            logger.info("已新增語音與例句至 %s 欄位", field_name)
            return

        raise ValueError(f"不支援的更新操作: {action}")

    @override
    async def execute_delete(self, card_service: "CardService", relation_service: "RelationService", note_id: int) -> None:
        """刪除指定的卡片。

        Delete the given card.

        Args:
            card_service: 卡片服務。Card service.
            relation_service: 關聯服務。Relation service.
            note_id: 要刪除的 note id。Note ID to delete.
        """
        await card_service.delete_note(note_id)

    @override
    async def execute_read_list(self, card_service: "CardService", relation_service: "RelationService", deck_name: str | None = None) -> list[dict]:
        """讀取卡片清單（目前未實作，回傳空清單）。

        Read the card list (not implemented; returns an empty list).
        """
        return []

    @override
    async def execute_read_graph(self, card_service: "CardService", relation_service: "RelationService", deck_name: str | None = None) -> dict:
        """讀取卡片關聯圖譜（目前未實作，回傳空結構）。

        Read the relation graph (not implemented; returns empty struct).
        """
        return {"nodes": [], "links": []}
