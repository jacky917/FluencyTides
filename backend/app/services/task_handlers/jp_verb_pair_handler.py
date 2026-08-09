"""日文自動詞/他動詞對（JP_VerbPair）卡片專屬 Handler 模組。

負責由遊戲/小說台詞生成 Context 與 Cloze 子卡片、
回寫母卡片 JSON 範例，並建立圖譜關聯。

Dedicated handler module for Japanese intransitive/transitive verb pair
(JP_VerbPair) cards.

Generates Context and Cloze child cards from game/novel dialog, writes
example items back into the master card's JSON, and creates graph
relations.
"""

import json
import logging
import re
from typing import override, TYPE_CHECKING

from app.services.task_handlers.base import BaseHandler
from app.services.task_handlers.registry import register_handler
from app.services.task_handlers.shared.cloze_positioning import (
    assemble_dialog_turns,
    position_cloze,
)
from app.infrastructure.utils.id_generator import generate_unique_card_id
from app.core.dependencies import get_template_engine
from app.infrastructure.llm.client import LLMClient
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager
from app.services.task_handlers.shared.anki_transaction import AnkiNoteTransaction
from app.schemas.relation import CardRelationCreate

if TYPE_CHECKING:
    from app.services.card_service import CardService
    from app.services.relation_service import RelationService

logger = logging.getLogger(__name__)

@register_handler
class JPVerbPairHandler(BaseHandler):
    """日文自動詞他動詞對專屬處理器。

    Dedicated handler for Japanese intransitive/transitive verb pairs.
    """

    @property
    @override
    def handler_name(self) -> str:
        """處理器名稱。

        Handler name.
        """
        return "jp_verb_pair"

    @property
    @override
    def supported_models(self) -> list[str]:
        """支援的 Anki 模型名稱清單。

        Supported Anki model names.
        """
        return ["JP_VerbPair_Master_Dark", "JP_Context_Dark", "JP_VerbPair_Cloze_Dark"]

    @override
    def get_input_schema(self) -> dict:
        """回傳建立母卡片所需的輸入參數 JSON Schema。

        Return the input JSON schema for creating the master card.
        """
        return {
            "type": "object",
            "properties": {
                "intransitive": {"type": "string"},
                "transitive": {"type": "string"},
                "context_text": {"type": "string"}
            },
            "required": ["intransitive", "transitive"]
        }

    @override
    async def execute_create(self, card_service: "CardService", relation_service: "RelationService", deck_name: str, model_name: str, parameters: dict) -> int | list[int]:
        """建立空白 Master 母卡片（目前未實作，回傳空清單）。

        Create an empty master card (not implemented; returns []).
        """
        # TODO: Implement creating empty Master card only
        return []

    @override
    async def execute_generate(self, card_service: "CardService", relation_service: "RelationService", parameters: dict) -> dict:
        """根據文本生成 Context 和 Cloze 子卡片，並將結果追加至母卡片 JSON 中。

        Generate Context and Cloze child cards from text and append the
        result into the master card's JSON.

        Args:
            card_service: 卡片服務。Card service.
            relation_service: 關聯服務。Relation service.
            parameters: 生成參數，需包含 master_note_id / deck_name /
                target_verb / source_game / context_dialogue。Generation
                params; requires master_note_id / deck_name / target_verb
                / source_game / context_dialogue.

        Returns:
            dict: 包含 context_note_id、cloze_note_id、kept_dialog、
                llm_model。Dict with context_note_id, cloze_note_id,
                kept_dialog, llm_model.

        Raises:
            ValueError: 參數缺漏、牌組不存在、母卡不存在或 LLM 產出失敗時。
                On missing params, missing deck/master card, or LLM
                failure.
            ClozePositioningError: 挖空定位失敗時。On cloze positioning
                failure.
        """
        master_note_id = parameters.get("master_note_id")
        deck_name = parameters.get("deck_name")
        target_verb = parameters.get("target_verb")
        source_game = parameters.get("source_game")
        context_dialogue = parameters.get("context_dialogue", [])

        if not all([master_note_id, deck_name, target_verb, context_dialogue]):
            raise ValueError("缺少必要的參數：master_note_id, deck_name, target_verb, context_dialogue")

        # 1. 解析 context_dialogue 陣列
        # 從中找出被標記為 target 的句子，並建立提供給 LLM 的乾淨文本
        target_sentence = ""
        # 從上下文尋找目標句的對應資料 (對齊人名、音檔)
        target_speaker = "-"
        target_avatar = "none"
        target_audio = ""
        target_audio_filename = ""
        for block in context_dialogue:
            if block.get("is_target"):
                target_speaker = block.get("speaker", "-")
                target_avatar = block.get("avatar", "none")
                target_audio = block.get("audio", "")
                break
        
        if target_audio:
            sm = re.search(r'\[sound:(.+?)\]', target_audio)
            if sm:
                target_audio_filename = sm.group(1)
        
        clean_lines = []
        for i, block in enumerate(context_dialogue):
            speaker = block.get("speaker", "-")
            text = block.get("text", "")
            is_target = block.get("is_target", False)
            
            clean_lines.append(f"[{i}] {speaker}: {text}")
            
            if is_target:
                target_sentence = text

        if not target_sentence:
            raise ValueError("context_dialogue 陣列中沒有任何被標記為 is_target 的對話。")

        clean_context_text = "\n".join(clean_lines)

        logger.info("成功解析目標句標記，目標句: %s", target_sentence)

        # 2. 預檢邏輯
        decks = await card_service.anki_client.get_deck_names()
        if deck_name not in decks:
            raise ValueError(f"目標牌組 '{deck_name}' 不存在，請確認後再試")
            
        try:
            master_note = await card_service.get_note(master_note_id)
        except Exception as e:
            raise ValueError(f"找不到指定的母卡片 ID: {master_note_id}") from e

        # 3. Jinja2 渲染與 LLM 呼叫
        template_engine = get_template_engine()
        prompt_text = template_engine.render(
            "prompts/anki/JP_VerbPair_Child.j2",
            target_verb=target_verb,
            source_game=source_game,
            context_text=clean_context_text,
            target_sentence=target_sentence
        )

        from app.schemas.llm.jp_verb_pair import ChildCardGenerationResult
        llm_client = LLMClient()
        try:
            schema = ChildCardGenerationResult.model_json_schema()
            raw_result = await llm_client.generate_structured_data(
                system_prompt="你是一位專業的日語教師，專長是將遊戲/小說台詞拆解為克漏字填空與上下文閱讀卡。請嚴格根據指定的 JSON 格式輸出。",
                user_prompt=prompt_text,
                response_schema=schema
            )
            llm_result = ChildCardGenerationResult.model_validate(raw_result.parsed_data)
        except Exception as e:
            raise ValueError(f"LLM 產出失敗: {e}") from e

        # 4. 在 Python 中組裝確定性的對話結構 (Deterministic Dialog)
        # 將 LLM 翻譯映射為 id -> translation，再交由共用模組組裝
        translation_map = {item.id: item.translation for item in llm_result.context.dialog_translations}
        dialog_turns = assemble_dialog_turns(context_dialogue, translation_map)
        # 4. 處理 Cloze 挖空定位（在建立任何卡片之前先驗證）
        # 採用 fail-fast 策略：先確認挖空定位成功，再建立 Context 與 Cloze 卡片。
        # 這樣可以避免當 LLM 產出的 cloze_blanks 無法匹配原文時，
        # 產生孤兒 Context 卡片或內容損壞的 Cloze 卡片。
        #
        # LLM 負責「決定挖空哪裡」(提供要挖空的子字串清單)，
        # Python 負責「精準執行挖空」(在原文中定位並替換)，
        # 徹底消除 LLM 自行改寫原文的風險。
        # 定位策略（右往左定位、助詞剝除 Fallback、整體匹配 Fallback、
        # 全數失敗拋 ClozePositioningError）皆封裝於共用模組
        # shared/cloze_positioning.py，與 JP_CoreVerb Handler 共用。
        cloze_sentence, full_sentence_html = position_cloze(
            target_sentence,
            llm_result.cloze.cloze_blanks,
            llm_result.cloze.target_particle_verb,
            task_name="JP_VerbPair_Cloze",
            model_name=raw_result.model_name,
            prompt_text=prompt_text,
            raw_response=raw_result.raw_content,
        )

        # 5. 建立 Context 子卡片（只有在 Cloze 挖空驗證通過後才會執行到這裡）
        context_deck_name = f"{deck_name}::Context"
        context_card_uuid = generate_unique_card_id()
        context_fields = {
            "Card_ID": context_card_uuid,
            "Master_Note_ID": str(master_note_id),
            "Summary": llm_result.context.summary,
            "Dialog_JSON": json.dumps(dialog_turns, ensure_ascii=False)
        }
        llm_tag = f"LLM::{raw_result.model_name}"
        tags = ["FluencyTides::Generated", f"Game::{source_game}", llm_tag] if source_game else ["FluencyTides::Generated", llm_tag]
        
        # S003 修復：Context 卡、Cloze 卡與母卡 JSON 回寫屬同一組不可分割的
        # 產出。任一步失敗（含 S001 修復後 append_to_list 會對損毀欄位主動
        # 拋錯）時，補償式交易會反序刪除已建立的子卡，避免母卡無引用的孤兒卡。
        # S003 fix: the Context note, the Cloze note and the master JSON
        # write-back form one indivisible unit. If any step fails (including
        # append_to_list now raising on corrupted fields after the S001 fix),
        # the compensating transaction deletes the created child notes in
        # reverse, preventing orphans the master never references.
        async with AnkiNoteTransaction(card_service) as tx:
            new_context_id = await tx.create_note(
                deck_name=context_deck_name,
                model_name="JP_Context_Dark",
                fields=context_fields,
                tags=tags,
                allow_duplicate=True
            )

            # 6. 建立 Cloze 子卡片
            cloze_deck_name = f"{deck_name}::Cloze"
            cloze_card_uuid = generate_unique_card_id()
        
            intransitive_word = str(master_note.get("fields", {}).get("Intransitive_Word", {}).get("value", ""))
            transitive_word = str(master_note.get("fields", {}).get("Transitive_Word", {}).get("value", ""))
            used_type = llm_result.cloze.verb_type_used
            target_particle_verb = llm_result.cloze.target_particle_verb
        
            # 檢查句子實際使用的漢字是否與本次生成的目標動詞 (target_verb) 漢字一致
            # 因為母卡片可能包含多個同義詞 "澄[す]む, 清[す]む"，直接檢查母卡片欄位會導致誤判
            kanji_in_word = set(re.findall(r'[一-龥々]', target_verb))
        
            mismatch = False
            if kanji_in_word:
                for k in kanji_in_word:
                    if k not in target_particle_verb:
                        mismatch = True
                        break
                    
            # 若漢字不一致 (例如目標動詞是 澄む，但句子用平假名 すんだ)
            # 則將面板文字退化為純平假名，避免造成學習者尋找漢字的困惑
            if mismatch:
                def to_pure_kana(text: str) -> str:
                    """把 furigana 標音格式的 Base[Ruby] 退化為純假名 Ruby。

                    Degrade furigana Base[Ruby] notation into pure kana.

                    Args:
                        text: furigana 標音字串，例如 "澄[す]む, 清[す]む"。
                            Furigana string, e.g. "澄[す]む, 清[す]む".

                    Returns:
                        str: 純假名字串，例如 "すむ, すむ"。Pure kana string,
                            e.g. "すむ, すむ".
                    """
                    # 把 Base[Ruby] 替換為 Ruby
                    # 例如 "澄[す]む, 清[す]む" -> "すむ, すむ"
                    return re.sub(r'[^\s\[\]]+\[([^\]]+)\]', r'\1', text)
                
                intransitive_word = to_pure_kana(intransitive_word)
                transitive_word = to_pure_kana(transitive_word)
        
            verb_pair_data = {
                "intransitive": intransitive_word,
                "transitive": transitive_word,
                "used": used_type
            }
        
            cloze_fields = {
                "Cloze_Sentence": cloze_sentence,
                "Full_Sentence_HTML": full_sentence_html,
                "Translation": llm_result.cloze.translation.replace("\n", " "),
                "Conjugation_Explanation": llm_result.cloze.conjugation_explanation,
                "Verb_Pair_JSON": json.dumps(verb_pair_data, ensure_ascii=False),
                "Audio": target_audio_filename,
                "Speaker": target_speaker,
                "Avatar": target_avatar,
                "Master_Note_ID": str(master_note_id),
                "Context_Note_ID": str(new_context_id),
                "Card_ID": cloze_card_uuid
            }
        
            cloze_tags = list(tags)
            if target_speaker in ("-", "", "none") and target_avatar == "none":
                cloze_tags.append("Narrator")

            new_cloze_id = await tx.create_note(
                deck_name=cloze_deck_name,
                model_name="JP_VerbPair_Cloze_Dark",
                fields=cloze_fields,
                tags=cloze_tags,
                allow_duplicate=True
            )

            # 7. 反向更新母卡片 JSON
            target_field = "Intransitive_Data_JSON" if llm_result.cloze.verb_type_used == "intransitive" else "Transitive_Data_JSON"
            new_example_item = {
                "audio": target_audio_filename,
                "avatar": target_avatar,
                "speaker": target_speaker,
                "text": full_sentence_html,
                "context_note_id": new_context_id,
                "cloze_note_id": new_cloze_id
            }
            await AnkiJsonFieldManager.append_to_list(
                card_service, master_note_id, target_field, new_example_item
            )

        # 8. 圖譜關聯
        # S003：卡片與母卡 JSON 此時已提交，圖譜關聯失敗屬「可事後補救」等級
        # （/sync 的孤兒清理會處理），不應為了圖譜一致性回頭刪除使用者已看得到
        # 的卡片。因此改為記錄警告並以旗標回報，不讓例外中斷整個生成流程。
        # S003: the notes and the master JSON are already committed here, so a
        # failed graph link is recoverable (the /sync orphan sweep handles it)
        # and must not delete notes the user can already see. It is logged and
        # reported via a flag instead of aborting the whole generation.
        relation_failed = False
        try:
            await relation_service.create_relation(
                CardRelationCreate(
                    source_note_id=master_note_id,
                    target_note_id=new_context_id,
                    relation_type="has_context",
                    source_label=target_verb,
                    target_label=f"{target_verb}_context"
                )
            )
            await relation_service.create_relation(
                CardRelationCreate(
                    source_note_id=master_note_id,
                    target_note_id=new_cloze_id,
                    relation_type="has_cloze",
                    source_label=target_verb,
                    target_label=f"{target_verb}_cloze"
                )
            )
        except Exception as e:  # noqa: BLE001 - 圖譜失敗不得中斷已成功的建卡
            relation_failed = True
            logger.warning(
                "圖譜關聯建立失敗（卡片已建立，可由 /sync 後續修正）: %s", e
            )

        return {
            "context_note_id": new_context_id,
            "cloze_note_id": new_cloze_id,
            "kept_dialog": dialog_turns,
            "llm_model": raw_result.model_name,
            "relation_failed": relation_failed
        }

    @override
    async def execute_update(self, card_service: "CardService", relation_service: "RelationService", note_id: int, parameters: dict) -> None:
        """更新卡片（目前無更新需求，保留擴充點）。

        Update a card (no current use case; extension point reserved).
        """
        pass

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
