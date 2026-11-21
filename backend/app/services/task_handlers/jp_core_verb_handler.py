"""Dedicated handler for JP_CoreVerb (core verb) cards.

JP_CoreVerb（核心動詞）卡片專屬 Handler。

Adapted from ``jp_verb_pair_handler.py``: the LLM task changes from transitivity judgment to deep verb
analysis (five Verb_Analysis_JSON fields), the master write-back changes from the two transitivity fields to
the single ``Word_Data_JSON`` field, and a script-provided ``target_verb_span`` is supported for cloze
cross-validation. Cloze positioning and dialog assembly are shared with VerbPair through
``shared/cloze_positioning.py``.

以 ``jp_verb_pair_handler.py`` 為藍本改造：
LLM 任務由「自他判定」改為「深度動詞解析」（Verb_Analysis_JSON 五欄），
Master 回寫由自他雙欄改為單欄 ``Word_Data_JSON``，
並支援腳本側傳入的 ``target_verb_span`` 挖空交叉驗證。
挖空定位與對話組裝邏輯與 VerbPair 共用 ``shared/cloze_positioning.py``。
"""

import json
import logging
import re
from typing import override, TYPE_CHECKING

from app.services.task_handlers.base import BaseHandler
from app.services.task_handlers.capabilities import (
    CreateCapability,
    GraphCapability,
    HandlerCapabilities,
    Operation,
)
from app.services.task_handlers.registry import register_handler
from app.services.task_handlers.shared.cloze_positioning import (
    assemble_dialog_turns,
    position_cloze,
)
from app.infrastructure.utils.id_generator import generate_unique_card_id
from app.core.dependencies import get_template_engine
from app.infrastructure.llm.routing import route
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager
from app.services.task_handlers.shared.anki_transaction import AnkiNoteTransaction
from app.schemas.relation import CardRelationCreate

if TYPE_CHECKING:
    from app.services.card_service import CardService
    from app.services.relation_service import RelationService

logger = logging.getLogger(__name__)

# 圖譜關聯寫進語料庫 rel_links 時的建立者與 attrs.card_type（與 card_gen_log.card_type 一致）。
# created_by / attrs.card_type of the relations this handler writes to rel_links.
CARD_TYPE = "jp_core_verb"

# LLM 用途鍵（生效設定的解析見 app.infrastructure.llm.routing）
CHILD_CARD_LLM = route("jp_core_verb", "核心動詞子卡生成", capabilities={"llm"})

@register_handler
class JPCoreVerbHandler(BaseHandler):
    """Dedicated handler for Japanese core verbs (high-frequency polysemous verbs).

    日文核心動詞（高頻多義動詞）專屬處理器。
    """

    # 子卡由本機批次腳本產生（D7）；母卡 JSON 與查重紀錄互相參照，網頁只能檢視（D4）
    CAPABILITIES = HandlerCapabilities(
        create=CreateCapability(channels=("script",), reason="子卡由本機批次腳本 generate_child_cards.py 產生"),
        update_fields=Operation(reason="由本機批次腳本管理，網頁只能檢視"),
        delete=Operation(
            reason="由本機批次腳本管理；刪除請用 scripts/local_anki/JP_CoreVerb/delete_child_cards.py"
            "（會一併更新母卡 JSON 與查重紀錄）"
        ),
        graph=GraphCapability(reason="尚未實作"),
    )

    @property
    @override
    def handler_name(self) -> str:
        """The handler name used in URLs: ``"jp_core_verb"``.

        處理器唯一識別名稱 ``"jp_core_verb"``，出現在網址裡。
        """
        return "jp_core_verb"

    @property
    @override
    def title(self) -> str:
        """The Chinese display title.

        中文名稱。
        """
        return "核心動詞"

    @property
    @override
    def supported_models(self) -> list[str]:
        """The Anki note types this handler owns.

        本處理器負責的 Anki 筆記類型。

        Context cards use the ``JP_Context_Dark`` model shared with JP_VerbPair (the model definition is
        shared; card instances stay private, 1 Context : 1 Cloze).

        Context 卡使用與 JP_VerbPair 共用的 ``JP_Context_Dark`` 模型
        （共用的是模型定義，卡片實體仍為 1 Context : 1 Cloze 私有）。
        """
        return ["JP_CoreVerb_Master_Dark", "JP_Context_Dark", "JP_CoreVerb_Cloze_Dark"]

    @override
    async def execute_generate(self, card_service: "CardService", relation_service: "RelationService", parameters: dict) -> dict:
        """Generate Context and Cloze child cards from text and append the result to the master card's JSON.

        根據文本生成 Context 和 Cloze 子卡片，並將結果追加至母卡片 JSON 中。

        Differences from JP_VerbPair:

        1. The LLM output becomes ``CoreVerbCardGenerationResult`` (with five verb_analysis fields).
        2. The Cloze card uses ``Verb_Analysis_JSON`` instead of the transitivity-pair fields.
        3. The master write-back always targets the single ``Word_Data_JSON`` field.
        4. The optional parameter ``target_verb_span`` is supported: the target verb's character span verified
           morphologically on the script side, cross-validated against the LLM cloze positions (fail-fast when
           they do not overlap).

        與 JP_VerbPair 的差異：

        1. LLM 輸出改為 ``CoreVerbCardGenerationResult``（含 verb_analysis 五欄）。
        2. Cloze 卡欄位以 ``Verb_Analysis_JSON`` 取代自他對照欄位。
        3. Master 回寫固定為單欄 ``Word_Data_JSON``。
        4. 支援可選參數 ``target_verb_span``：腳本側形態素驗證過的
           目標動詞字元 span，與 LLM 挖空位置交叉驗證（不重疊即 fail-fast）。

        The two child cards and the master JSON write-back are rolled back together if any of them fails. A
        failure to create the graph relations afterwards does not raise; it is reported as
        ``relation_failed``.

        兩張子卡與母卡 JSON 回寫任一步失敗時一起回滾。之後建立圖譜關聯失敗不會拋錯，改以
        ``relation_failed`` 回報。

        Args:
            card_service: the card service
                卡片服務
            relation_service: the relation service
                關聯服務
            parameters: the generation parameters; master_note_id, deck_name, target_verb, and
                context_dialogue are required, source_game (added as a ``Game::`` tag when given) and
                target_verb_span (``[start, end]``) are optional
                生成參數，需包含 master_note_id / deck_name / target_verb / context_dialogue；
                source_game（有值時加上 ``Game::`` 標籤）與 target_verb_span（``[start, end]``）選填

        Returns:
            a dict with context_note_id, cloze_note_id, kept_dialog, llm_model, and relation_failed
            包含 context_note_id、cloze_note_id、kept_dialog、llm_model、relation_failed 的字典

        Raises:
            ClozePositioningError: if cloze positioning or the span cross-validation fails
                若挖空定位失敗或 span 交叉驗證失敗
            ValueError: if a required parameter is missing, ``target_verb_span`` is not a two-element array, no
                sentence is marked as the target, the deck or the master card does not exist, or the LLM output
                fails
                若參數缺漏、``target_verb_span`` 不是兩元素陣列、沒有標記目標句、牌組不存在、母卡不存在或
                LLM 產出失敗
        """
        master_note_id = parameters.get("master_note_id")
        deck_name = parameters.get("deck_name")
        target_verb = parameters.get("target_verb")
        source_game = parameters.get("source_game")
        context_dialogue = parameters.get("context_dialogue", [])
        target_verb_span = parameters.get("target_verb_span")

        if not all([master_note_id, deck_name, target_verb, context_dialogue]):
            raise ValueError("缺少必要的參數：master_note_id, deck_name, target_verb, context_dialogue")

        # 將腳本側傳入的 span 正規化為 tuple（JSON 傳輸為 list）
        verified_span: tuple[int, int] | None = None
        if target_verb_span is not None:
            if not isinstance(target_verb_span, (list, tuple)) or len(target_verb_span) != 2:
                raise ValueError("target_verb_span 格式錯誤，必須為 [start, end] 兩元素陣列")
            verified_span = (int(target_verb_span[0]), int(target_verb_span[1]))

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
            "prompts/anki/JP_CoreVerb_Child.j2",
            target_verb=target_verb,
            source_game=source_game,
            context_text=clean_context_text,
            target_sentence=target_sentence
        )

        from app.schemas.llm.jp_core_verb import CoreVerbCardGenerationResult
        llm_client = await CHILD_CARD_LLM.client()
        try:
            schema = CoreVerbCardGenerationResult.model_json_schema()
            raw_result = await llm_client.generate_structured_data(
                system_prompt="你是一位專業的日語教師，專長是將遊戲/小說台詞拆解為克漏字填空與上下文閱讀卡。請嚴格根據指定的 JSON 格式輸出。",
                user_prompt=prompt_text,
                response_schema=schema
            )
            llm_result = CoreVerbCardGenerationResult.model_validate(raw_result.parsed_data)
        except Exception as e:
            raise ValueError(f"LLM 產出失敗: {e}") from e

        # 4. 在 Python 中組裝確定性的對話結構 (Deterministic Dialog)
        # 將 LLM 翻譯映射為 id -> translation，再交由共用模組組裝
        translation_map = {item.id: item.translation for item in llm_result.context.dialog_translations}
        dialog_turns = assemble_dialog_turns(context_dialogue, translation_map)

        # 5. 處理 Cloze 挖空定位（在建立任何卡片之前先驗證）
        # 採用 fail-fast 策略：先確認挖空定位成功，再建立 Context 與 Cloze 卡片。
        # 這樣可以避免當 LLM 產出的 cloze_blanks 無法匹配原文時，
        # 產生孤兒 Context 卡片或內容損壞的 Cloze 卡片。
        # 額外傳入 verified_span：腳本側以形態素分析器驗證過的目標動詞 span，
        # 與挖空位置交叉驗證，防止 LLM 挖到同形污染詞（見送る/てみる 等）。
        cloze_sentence, full_sentence_html = position_cloze(
            target_sentence,
            llm_result.cloze.cloze_blanks,
            llm_result.cloze.target_particle_verb,
            task_name="JP_CoreVerb_Cloze",
            model_name=raw_result.model_name,
            prompt_text=prompt_text,
            raw_response=raw_result.raw_content,
            verified_span=verified_span,
        )

        # 6. 建立 Context 子卡片（只有在 Cloze 挖空驗證通過後才會執行到這裡）
        context_deck_name = f"{deck_name}::Context"
        context_card_uuid = generate_unique_card_id(prefix="cv")
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

            # 7. 建立 Cloze 子卡片
            cloze_deck_name = f"{deck_name}::Cloze"
            cloze_card_uuid = generate_unique_card_id(prefix="cv")

            word = str(master_note.get("fields", {}).get("Word", {}).get("value", ""))
            target_particle_verb = llm_result.cloze.target_particle_verb

            # 檢查句子實際使用的漢字是否與本次生成的目標動詞 (target_verb) 漢字一致
            # 因為母卡片可能包含多個同義寫法，直接檢查母卡片欄位會導致誤判
            kanji_in_word = set(re.findall(r'[一-龥々]', target_verb))

            mismatch = False
            if kanji_in_word:
                for k in kanji_in_word:
                    if k not in target_particle_verb:
                        mismatch = True
                        break

            # 若漢字不一致 (例如目標動詞是 見る，但句子用平假名 みた)
            # 則將面板文字退化為純平假名，避免造成學習者尋找漢字的困惑
            if mismatch:
                def to_pure_kana(text: str) -> str:
                    """Degrade furigana ``Base[Ruby]`` notation into the pure kana ``Ruby``.

                    把 furigana 標音格式的 Base[Ruby] 退化為純假名 Ruby。

                    Args:
                        text: the furigana string, for example ``"見[み]る"``
                            furigana 標音字串，例如 ``"見[み]る"``

                    Returns:
                        the pure kana string, for example ``"みる"``
                        純假名字串，例如 ``"みる"``
                    """
                    # 把 Base[Ruby] 替換為 Ruby
                    # 例如 "見[み]る" -> "みる"
                    return re.sub(r'[^\s\[\]]+\[([^\]]+)\]', r'\1', text)

                word = to_pure_kana(word)

            # Verb_Analysis_JSON 為 Cloze 卡解析區的單一事實來源；
            # 額外附上（可能已退化為純假名的）word 供前端渲染動詞標題使用
            verb_analysis_data = llm_result.cloze.verb_analysis.model_dump()
            verb_analysis_data["word"] = word

            cloze_fields = {
                "Cloze_Sentence": cloze_sentence,
                "Full_Sentence_HTML": full_sentence_html,
                "Translation": llm_result.cloze.translation.replace("\n", " "),
                "Verb_Analysis_JSON": json.dumps(verb_analysis_data, ensure_ascii=False),
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
                model_name="JP_CoreVerb_Cloze_Dark",
                fields=cloze_fields,
                tags=cloze_tags,
                allow_duplicate=True
            )

            # 8. 反向更新母卡片 JSON（CoreVerb 為單欄 Word_Data_JSON）
            new_example_item = {
                "audio": target_audio_filename,
                "avatar": target_avatar,
                "speaker": target_speaker,
                "text": full_sentence_html,
                "context_note_id": new_context_id,
                "cloze_note_id": new_cloze_id
            }
            await AnkiJsonFieldManager.append_to_list(
                card_service, master_note_id, "Word_Data_JSON", new_example_item
            )

        # 9. 圖譜關聯
        # S003：卡片與母卡 JSON 此時已提交，圖譜關聯失敗不應為了圖譜一致性回頭刪除使用者
        # 已看得到的卡片，所以只記錄並以旗標回報，不讓例外中斷整個生成流程。兩條關聯在同一個
        # transaction 寫入（batch_create_relations），不會只留下一半。
        # 注意 /relations/sync 只會撤銷孤兒關聯、不會補上缺的；缺的關聯由建卡管線寫
        # card_gen_log 時補上（create_or_restore_record 的 _ensure_link）。
        # S003: the notes and the master JSON are already committed, so a
        # failed graph link is logged and flagged instead of deleting cards
        # the user can see. Both links go in one transaction. /relations/sync
        # only retires orphans; missing links are restored when the pipeline
        # records the card (create_or_restore_record -> _ensure_link).
        relation_failed = False
        try:
            await relation_service.batch_create_relations(
                [
                    CardRelationCreate(
                        source_note_id=master_note_id,
                        target_note_id=new_context_id,
                        relation_type="has_context",
                        source_label=target_verb,
                        target_label=f"{target_verb}_context"
                    ),
                    CardRelationCreate(
                        source_note_id=master_note_id,
                        target_note_id=new_cloze_id,
                        relation_type="has_cloze",
                        source_label=target_verb,
                        target_label=f"{target_verb}_cloze"
                    ),
                ],
                created_by=CARD_TYPE,
                attrs={"card_type": CARD_TYPE},
            )
        except Exception:  # noqa: BLE001 - 圖譜失敗不得中斷已成功的建卡
            relation_failed = True
            logger.warning(
                "圖譜關聯建立失敗（卡片已建立；建卡管線寫入 card_gen_log 時會補上）"
                "：master=%s, context=%s, cloze=%s",
                master_note_id, new_context_id, new_cloze_id,
                exc_info=True,
            )

        return {
            "context_note_id": new_context_id,
            "cloze_note_id": new_cloze_id,
            "kept_dialog": dialog_turns,
            "llm_model": raw_result.model_name,
            "relation_failed": relation_failed
        }
