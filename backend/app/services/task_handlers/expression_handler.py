"""Expression correction handler module.

外語糾錯任務處理器（Expression Correction Handler）模組。

Generates and updates the master/micro card groups of foreign-language correction. The creator calls the LLM to
produce the correction result and creates Expression_Master_Dark master cards and Expression_Micro_Dark micro
cards; the handler supports JSON updates of the audio fields once the cards exist.

負責「外語糾錯」母子卡片群組的生成與更新：creator 呼叫 LLM 產生糾錯結果、
建立 Expression_Master_Dark 母卡片與 Expression_Micro_Dark 子卡片；handler 在卡片建好之後
支援音檔欄位的 JSON 更新。
"""

import html
import json
import logging
import re
from collections.abc import Mapping
from typing import override, TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from app.infrastructure.anki.search import deck_term, field_term, note_type_term
from app.infrastructure.anki.client import AnkiConnectError
from app.services.task_handlers.base import BaseHandler, DuplicateResolution
from app.services.task_handlers.capabilities import (
    CreateCapability,
    CreatorCapabilities,
    GraphCapability,
    HandlerCapabilities,
    Operation,
)
from app.services.task_handlers.creator import BaseCreator
from app.services.task_handlers.registry import register_creator, register_handler
from app.infrastructure.utils.id_generator import generate_unique_card_id
from app.core.dependencies import get_template_engine
from app.infrastructure.llm.routing import route
from app.schemas.llm.expression import LLMExpressionCorrectionResult
from app.core.config import settings
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager
from app.services.task_handlers.shared.anki_transaction import AnkiNoteTransaction

if TYPE_CHECKING:
    from app.services.card_service import CardService
    from app.services.relation_service import RelationService

logger = logging.getLogger(__name__)

# LLM 用途鍵（生效設定的解析見 app.infrastructure.llm.routing）
CORRECTION_LLM = route("expression_correction", "外語糾錯", capabilities={"llm"})

#: The fixed deck master cards are always created in (``execute_create`` ignores the given deck).
#: 母卡固定建在這個牌組（execute_create 忽略指定的牌組）。
MASTER_DECK = "日本語::外語糾錯::母卡片"
#: The deck of micro cards (split further into grammar correction and reorganization).
#: 子卡的牌組（底下再分文法修正、重新組織）。
MICRO_DECK = "日本語::外語糾錯::子卡片"
MASTER_MODEL = "Expression_Master_Dark"
MICRO_MODEL = "Expression_Micro_Dark"

_TEXTAREA = {"x-ui": "textarea"}
_BREAK = re.compile(r"<\s*br\s*/?\s*>|</\s*(div|p|li)\s*>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


def plain_text(value: str) -> str:
    """Convert a field value (HTML) into plain text.

    欄位值（HTML）轉成純文字。

    ``<br>`` and closing ``div``, ``p``, and ``li`` tags become line breaks, other tags are removed, escapes
    are decoded, and surrounding whitespace is stripped.

    ``<br>`` 等換成換行、去掉標籤、還原轉義、去掉前後空白。

    Args:
        value: the field value
            欄位值

    Returns:
        the plain text
        純文字
    """
    return html.unescape(_TAG.sub("", _BREAK.sub("\n", value))).strip()


def same_original(field_value: str, original_text: str) -> bool:
    """Check whether an old master's ``My_Original`` (HTML) is the same sentence as this original text.

    舊母卡的 ``My_Original``（HTML）與這次的原文（純文字）是不是同一句。

    Cards created by the web and TG write the original as is, so the field matches the original literally
    (the duplicate lookup also finds it literally); in cards typed in Anki, ``&`` and ``<`` are escaped and
    line breaks become ``<br>``, so they match only after conversion to plain text. Both count. The original
    must not be treated as HTML too: a ``<x>`` in it would be stripped as a tag.

    網頁與 TG 建的卡原樣寫入原文，欄位與原文字面相同（查重複也是用字面找到的）；在 Anki 裡手打的卡，
    ``&``、``<`` 會被轉義、換行變 ``<br>``，轉成純文字後才相同。兩種都算。不能把原文也當 HTML 處理：
    原文裡的 ``<x>`` 會被當成標籤去掉。

    Args:
        field_value: the old master's ``My_Original`` value
            舊母卡的 ``My_Original`` 值
        original_text: this original text (plain text)
            這次的原文（純文字）

    Returns:
        whether they are the same sentence, ignoring surrounding whitespace
        是否為同一句（忽略前後空白）
    """
    original = original_text.strip()
    return field_value.strip() == original or plain_text(field_value) == original


def _field_text(fields: Mapping[str, object], name: str) -> str:
    """Return a ``notesInfo`` field value as plain text; empty when the field is missing.

    notesInfo 的欄位值轉成純文字；欄位不存在時為空字串。
    """
    entry = fields.get(name)
    return plain_text(str(entry.get("value", "")) if isinstance(entry, dict) else "")


def duplicate_query(original_text: str) -> str:
    """Return the search for master cards with the same original text (literal match).

    找同一句原文的母卡（照字面比對）。

    The TG flow (``_check_duplicate_before_generate`` in ``expression_fsm.py``) has its own copy with the same
    conditions until it is removed in D10.3; change both when changing the conditions.

    TG 流程（``expression_fsm.py`` 的 ``_check_duplicate_before_generate``）在 D10.3 移除前有自己的一份，
    條件相同；改比對條件時兩邊要一起改。

    Args:
        original_text: the original text
            原文

    Returns:
        the Anki search query
        Anki 搜尋查詢字串
    """
    return f"{deck_term(MASTER_DECK)} {note_type_term(MASTER_MODEL)} {field_term('My_Original', original_text)}"


def _children_query(master_note_id: int) -> str:
    """Return the search for the micro cards of a master.

    一張母卡的子卡的搜尋。
    """
    return f"{note_type_term(MICRO_MODEL)} {field_term('Master_Note_ID', str(master_note_id))}"


class ExpressionDraftParams(BaseModel):
    """The draft input of the web preview flow: what the TG flow collects in six steps (server_drafts plan D10.2).

    網頁預覽流程的草稿輸入：TG 流程六步收的內容（server_drafts 計畫書 D10.2）。
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    native_language: str = Field(default="中文", min_length=1, title="母語", examples=["中文"])
    target_language: str = Field(default="日文", min_length=1, title="目標語言", examples=["日文", "英文"])
    original_text: str = Field(
        min_length=1, title="原文", description="自己寫的外語句子", json_schema_extra=_TEXTAREA
    )
    context: str = Field(
        default="",
        title="情境",
        description="選填；對話情境，多段以連續五個以上的「ー」分隔",
        json_schema_extra=_TEXTAREA,
    )
    source_tag: str = Field(
        default="",
        title="來源標籤",
        description="選填；加在母卡與子卡上",
        examples=settings.note_source_tags_list,
    )
    user_grammar_correction: str = Field(
        default="",
        title="自己的文法修正",
        description="選填；自己先改過的版本，LLM 會參考",
        json_schema_extra=_TEXTAREA,
    )
    user_reorganization: str = Field(
        default="",
        title="自己的重新組織",
        description="選填；自己想的另一種說法，LLM 會參考",
        json_schema_extra=_TEXTAREA,
    )


class ExpressionCreateParams(BaseModel):
    """The create parameters of an expression-correction group (D3).

    外語糾錯的建立參數（D3）。

    TG passes only ``original_text``, ``context``, ``source_tag``, ``llm_result``, ``selected_indices``, and
    ``allow_duplicate`` (``_execute_card_creation`` in ``expression_fsm.py``), so everything but the original
    text is optional; the languages and the user's own corrections are used only when no ``llm_result`` is
    given and the creator calls the LLM itself. The defaults match the creator's defaults when reading
    parameters. ``tg_bot`` is passed by the service layer.

    TG 只傳 ``original_text``、``context``、``source_tag``、``llm_result``、``selected_indices``、
    ``allow_duplicate``（``expression_fsm.py`` 的 ``_execute_card_creation``），所以除原文外都是選填；
    語言與使用者自訂修正只在沒給 ``llm_result``、要由 creator 自己呼叫 LLM 時才用。
    預設值與 creator 讀參數時的預設相同。``tg_bot`` 由服務層傳入。
    """

    original_text: str = Field(
        min_length=1, title="原文", description="自己寫的外語句子", json_schema_extra={"x-ui": "textarea"}
    )
    context: str = Field(
        default="",
        title="情境",
        description="對話情境；多段以連續五個以上的「ー」分隔",
        json_schema_extra={"x-ui": "textarea"},
    )
    source_tag: str = Field(default="", title="來源標籤", description="加在卡片上的標籤（選填）")
    native_language: str = Field(default="中文", title="母語")
    target_language: str = Field(default="日文", title="目標語言")
    user_grammar_correction: str = Field(default="", title="自己的文法修正（選填）")
    user_reorganization: str = Field(default="", title="自己的重新組織（選填）")
    llm_result: LLMExpressionCorrectionResult | None = Field(
        default=None, description="已預覽的 LLM 結果；沒給時由後端呼叫 LLM"
    )
    selected_indices: list[int] | None = Field(
        default=None, description="要建立的子卡編號（1 起算）；None 表示全部"
    )
    allow_duplicate: bool = Field(default=False, description="是否允許與既有卡片重複")
    replace_note_id: int | None = Field(
        default=None,
        description="覆蓋舊卡：新卡建好後刪掉這張舊母卡與其子卡（必須是同一句原文的母卡，否則不刪）",
    )

@register_handler
class ExpressionCorrectionHandler(BaseHandler):
    """Handler for foreign-language correction and atomic knowledge points.

    外語糾錯與原子化知識點處理器。

    Owns the master and micro cards once they exist (adding audio); creation is ``ExpressionCorrectionCreator``.

    負責建好之後的母卡與子卡（新增音檔）；建立見 ``ExpressionCorrectionCreator``。
    """

    CAPABILITIES = HandlerCapabilities(
        update_fields=Operation(reason="目前只支援新增音檔"),
        delete=Operation(reason="外語糾錯的母子卡刪除尚未開放"),
        actions=("add_audio",),
        graph=GraphCapability(reason="尚未實作"),
    )

    @property
    @override
    def handler_name(self) -> str:
        """The handler name used in URLs.

        處理器名稱，出現在網址裡。
        """
        return "expression_correction"

    @property
    @override
    def title(self) -> str:
        """The Chinese display title.

        中文名稱。
        """
        return "外語糾錯"

    @property
    @override
    def supported_models(self) -> list[str]:
        """The Anki note types this handler owns.

        這個處理器負責的 Anki 筆記類型。
        """
        return ["Expression_Master_Dark", "Expression_Micro_Dark"]

    @override
    async def execute_update(
        self,
        card_service: "CardService",
        relation_service: "RelationService",
        note_id: int,
        parameters: dict
    ) -> None:
        """Apply a task-specific update (currently only the ``add_audio`` action).

        客製化更新（目前僅支援 add_audio 動作）。

        ``add_audio`` replaces the audio of the first item when it is the AI's, and appends an AI item
        otherwise.

        ``add_audio`` 在第一筆是 AI 的音檔時替換它，否則新增一筆 AI 的音檔。

        Args:
            card_service: the card service
                卡片服務
            relation_service: the relation service (unused)
                關聯服務（不使用）
            note_id: the target note ID
                目標 Note ID
            parameters: must contain ``action``; ``add_audio`` also requires ``field_name`` and ``audio``
                需含 ``action``，add_audio 時另需 ``field_name`` 與 ``audio``

        Raises:
            ValueError: if a parameter is missing or the action is not supported
                若參數缺漏或 action 不支援
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


@register_creator
class ExpressionCorrectionCreator(BaseCreator):
    """Creates foreign-language correction card groups through the preview flow (web and TG).

    以預覽流程建立外語糾錯的母子卡片群組（網頁與 TG）。

    The LLM corrects the original text; the user reviews the result, then one master card and its micro cards are
    created together in the fixed decks.

    LLM 糾正原文；使用者看過結果後，一次在固定牌組建立一張母卡與它的子卡。
    """

    HANDLER = "expression_correction"
    CAPABILITIES = CreatorCapabilities(
        create=CreateCapability(
            supported=True,
            channels=("web", "tg"),
            flow="preview",
            deck="fixed",
            fixed_deck=MASTER_DECK,
            note_type=MASTER_MODEL,
        ),
    )
    PRODUCES = (MASTER_MODEL, MICRO_MODEL)
    CREATE_PARAMS = ExpressionCreateParams
    DRAFT_PARAMS = ExpressionDraftParams
    CREATE_TAGS = ("Expression_Correction", "HandlerGenerated", "MicroPoint")

    @property
    @override
    def creator_name(self) -> str:
        """The creator name used in create URLs.

        creator 名稱，出現在建卡網址裡。
        """
        return "expression_correction"

    async def execute_generate(
        self,
        parameters: Mapping[str, object]
    ) -> LLMExpressionCorrectionResult:
        """Call the LLM to generate the correction result only, without writing to Anki.

        僅呼叫 LLM 產生糾錯結果，不寫入 Anki。

        Used to produce a preview first (in the TG flow and the web draft), so the user can confirm and then
        decide which micro cards to write. This decouples "LLM generation" from "Anki writes", following the
        single responsibility principle.

        此方法用於先行產生預覽結果（TG 流程與網頁草稿），讓使用者確認後再決定要寫入哪些子卡片。將
        「LLM 生成」與「Anki 寫入」解耦，遵循單一職責原則。

        Args:
            parameters: a dict with the language settings, original text, context, and the user's own
                corrections
                包含語言設定、原文、情境、使用者自訂修正等參數的字典

        Returns:
            the structured ``LLMExpressionCorrectionResult``
            ``LLMExpressionCorrectionResult`` 結構化物件

        Raises:
            ValueError: if the LLM output is malformed or fails validation
                若 LLM 回傳格式異常或解析失敗
        """
        native_lang = parameters.get("native_language", "中文")
        target_lang = parameters.get("target_language", "日文")
        original_text = parameters.get("original_text", "")
        context_raw = str(parameters.get("context", ""))

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
        llm_client = await CORRECTION_LLM.client()
        schema_dict = LLMExpressionCorrectionResult.model_json_schema()

        result = await llm_client.generate_structured_data(
            system_prompt=system_prompt,
            user_prompt=prompt_text,
            response_schema=schema_dict
        )

        return LLMExpressionCorrectionResult.model_validate(result.parsed_data)

    @override
    async def execute_draft(
        self,
        parameters: dict[str, object],
        existing_tags: list[str],
        context: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Ask the LLM for the correction, shaped as draft create parameters (all micro cards selected, unwritten).

        網頁預覽流程的草稿：請 LLM 糾錯，整理成建立參數（子卡預設全選；不寫入）。

        The duplicate decision made before generating (the ``DuplicateResolution`` that
        ``NoteService.resolve_draft_duplicate`` put in the input) is turned into create parameters here: allow
        means ``allow_duplicate``; overwrite also needs ``allow_duplicate`` (the old card is deleted only after
        the new one is created, so both have the same original at creation time), plus ``replace_note_id``.
        Other system keys in the input (for example a skipped duplicate check) do not go into the create
        parameters.

        生成前的重複決定（``NoteService.resolve_draft_duplicate`` 放進輸入的 ``DuplicateResolution``）在這裡轉成
        建立參數：允許重複＝``allow_duplicate``；覆蓋＝也要 ``allow_duplicate``（舊卡在新卡建好後才刪，建立當下
        兩張原文相同），再加 ``replace_note_id``。輸入裡的其他系統鍵（例如略過了查重複）不進建立參數。

        Args:
            parameters: the input validated by ``ExpressionDraftParams``, possibly with a duplicate decision
                ``ExpressionDraftParams`` 驗證後的輸入，可能另帶重複決定
            existing_tags: unused (expression correction suggests no tags)
                用不到（外語糾錯不建議標籤）

        Returns:
            a draft shaped like ``ExpressionCreateParams``
            ``ExpressionCreateParams`` 形狀的草稿
        """
        result = await self.execute_generate(parameters)
        resolution = DuplicateResolution.from_input(parameters)
        return ExpressionCreateParams.model_validate({
            **{name: parameters[name] for name in ExpressionDraftParams.model_fields},
            "llm_result": result.model_dump(),
            "selected_indices": None,
            "allow_duplicate": resolution is not None,
            "replace_note_id": resolution.note_id if resolution and resolution.decision == "overwrite" else None,
        }).model_dump()

    @override
    async def find_draft_duplicate(
        self, card_service: "CardService", parameters: Mapping[str, object]
    ) -> dict[str, object] | None:
        """Look for a master card already created for the same original text.

        同一句原文建過母卡沒有。

        The match is the same as the TG flow's: the master deck, the master note type, and an identical
        original text.

        與 TG 流程相同的比對：母卡牌組、母卡類型、原文完全相同。

        Args:
            card_service: the card service
                卡片服務
            parameters: the input validated by ``ExpressionDraftParams``
                ``ExpressionDraftParams`` 驗證後的輸入

        Returns:
            ``{"note_id", "original_text", "correct_answer", "reorganized_expression"}`` (plain text), or
            ``None`` when there is none; with several, the first one (the same as TG)
            ``{"note_id", "original_text", "correct_answer", "reorganized_expression"}``（純文字），沒有時為
            ``None``；有好幾張時取第一張（與 TG 相同）

        Raises:
            AnkiConnectError: if the lookup fails
                若查詢失敗
        """
        note_ids = await card_service.find_notes(duplicate_query(str(parameters.get("original_text", ""))))
        if not note_ids:
            return None
        notes = await card_service.get_notes_info([note_ids[0]])
        if not notes:
            return None
        fields = notes[0].get("fields", {})
        return {
            "note_id": note_ids[0],
            "original_text": _field_text(fields, "My_Original"),
            "correct_answer": _field_text(fields, "Correct_Answer"),
            "reorganized_expression": _field_text(fields, "Reorganized_Expression"),
        }

    @override
    async def after_create(
        self, card_service: "CardService", parameters: Mapping[str, object], note_ids: list[int]
    ) -> str | None:
        """On overwrite, delete the old master and its micro cards now that the new cards exist.

        覆蓋舊卡：新卡已建好，刪掉舊的母卡與其子卡。

        Create first, delete afterwards: if the create fails, the old card is untouched. Only "a master with the
        same original text" is deleted: ``replace_note_id`` comes from the create parameters sent back by the
        web, so it must not be able to delete another card. No warning is needed when the old card is already
        gone (for example deleted in Anki first). An Anki error returns a warning (with the old card's ID and
        the reason); other exceptions propagate as usual and ``NoteService.create`` turns them into a generic
        warning (the new card exists, so this is not reported as a failed create either).

        先建後刪：建立失敗時舊卡不動。只刪「同一句原文的母卡」：``replace_note_id`` 來自網頁送回的建立參數，
        不能讓它刪到別的卡。舊卡已經不在時（例如在 Anki 裡先刪了）不必提醒。Anki 的錯誤回提醒（附舊卡 ID
        與原因）；其他例外照常往上拋，由 ``NoteService.create`` 轉成通用的提醒（新卡已建立，同樣不會報成
        建立失敗）。

        The TG flow (``expression_fsm.py``) does not pass ``replace_note_id`` until it is removed in D10.3; it
        deletes the old card itself, syncs separately, and does not go through here.

        TG 流程（``expression_fsm.py``）在 D10.3 移除前不帶 ``replace_note_id``，自己刪舊卡、另外同步一次，
        不經過這裡。

        Args:
            card_service: the card service
                卡片服務
            parameters: the create parameters (validated by ``ExpressionCreateParams``)
                建立參數（``ExpressionCreateParams`` 驗證後）
            note_ids: the notes just created
                剛建立的筆記

        Returns:
            a warning when the deletion fails or the old card does not match, or ``None`` when all is well
            刪除失敗或不符時的提醒；沒事時為 ``None``
        """
        old_id = parameters.get("replace_note_id")
        if not isinstance(old_id, int) or old_id in note_ids:
            return None
        new_text = str(parameters.get("original_text", ""))
        try:
            notes = await card_service.get_notes_info([old_id])
            if not notes:
                logger.info("覆蓋：舊母卡 %s 已不在，不必刪除", old_id)
                return None
            note = notes[0]
            field = note.get("fields", {}).get("My_Original")
            old_text = str(field.get("value", "")) if isinstance(field, dict) else ""
            if note.get("modelName") != MASTER_MODEL or not same_original(old_text, new_text):
                logger.warning(
                    "覆蓋：筆記 %s 不是同一句原文的母卡（modelName=%s, 舊原文=%r, 新原文=%r），不刪除",
                    old_id, note.get("modelName"), old_text, new_text,
                )
                return f"⚠️ 要覆蓋的舊卡（Note ID {old_id}）與這次的原文不符，沒有刪除；請到 Cards 頁確認。"
            children = await card_service.find_notes(_children_query(old_id))
            await card_service.anki_client.delete_notes([old_id, *children])
        except AnkiConnectError as exc:
            # 新卡已寫入：刪不掉舊卡只能請使用者手動處理，不能報成建立失敗
            logger.warning("覆蓋：新卡 %s 已建立，但刪除舊卡 %s 失敗：%s", note_ids, old_id, exc)
            return f"⚠️ 新卡已建立，但舊卡（Note ID {old_id}）刪除失敗（{exc}），請到 Cards 頁手動刪除舊的母卡與子卡。"
        logger.info("覆蓋：已刪除舊母卡 %s 及其 %d 張子卡", old_id, len(children))
        return None

    @override
    async def execute_create(
        self,
        card_service: "CardService",
        relation_service: "RelationService",
        deck_name: str,
        model_name: str,
        parameters: dict
    ) -> list[int]:
        """Create the master/micro card group.

        建立母子卡片群組。

        Two modes are supported:

        1. Full mode: no ``llm_result`` is given, and the LLM is called internally.
        2. Preview mode: an ``llm_result`` (an already generated result) plus ``selected_indices`` controls
           which micro cards the user chose to write.

        支援兩種模式：

        1. 完整模式：不提供 llm_result，內部自動呼叫 LLM 生成。
        2. 預覽模式：提供 llm_result（已生成的結果）+ selected_indices
           來控制只寫入使用者選擇的子卡片。

        The master and its micro cards succeed or fail as a group: if any card fails, the cards already created
        are deleted.

        母卡與子卡整組成功或整組不留：任一張失敗時刪除已建立的卡片。

        Args:
            card_service: the Anki card service
                Anki 卡片操作服務
            relation_service: the relation service (currently unused)
                關聯服務（目前未使用）
            deck_name: ignored; the master always goes to ``MASTER_DECK`` and the micro cards under
                ``MICRO_DECK``
                不使用；母卡固定建在 ``MASTER_DECK``，子卡建在 ``MICRO_DECK`` 底下
            model_name: ignored; the note types are always ``Expression_Master_Dark`` and
                ``Expression_Micro_Dark``
                不使用；筆記類型固定為 ``Expression_Master_Dark`` 與 ``Expression_Micro_Dark``
            parameters: the task parameters (``ExpressionCreateParams`` plus ``tg_bot``), which may also contain:

                - llm_result: a pre-generated ``LLMExpressionCorrectionResult`` (or its serialized dict)
                - selected_indices: the numbers of the micro cards to write (unified 1-based numbering, grammar
                  points first; ``None`` writes all)

                任務參數字典（``ExpressionCreateParams`` 加上 ``tg_bot``），可額外包含：

                - llm_result：預先生成的 ``LLMExpressionCorrectionResult`` 物件（或其序列化字典）
                - selected_indices：要寫入的子卡片編號（統一編號，1 起算、文法修正在前；``None`` 表示全部寫入）

        Returns:
            the created note IDs, the master first
            寫入成功的 Note ID 列表，母卡在前

        Raises:
            DuplicateCardError: if a card is a duplicate and ``allow_duplicate`` is not set
                若卡片重複且未設定 ``allow_duplicate``
            ValueError: if the LLM output is malformed or fails validation
                若 LLM 回傳格式異常或解析失敗
        """
        original_text = parameters.get("original_text", "")
        context_raw = parameters.get("context", "")
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

        master_deck = MASTER_DECK
        base_tags = ["Expression_Correction"]
        if source_tag:
            base_tags.append(source_tag)
            
        allow_duplicate = parameters.get("allow_duplicate", False)

        # S002 修復：母卡與子卡屬同一個知識群組，必須整組成功或整組不留痕跡。
        # 以補償式交易包裹，任一子卡建立失敗（最常見為 DuplicateCardError）時
        # 反序刪除已建立的卡片，避免殘留孤兒母卡與半套群組。
        # S002 fix: the master and its children form one knowledge group and
        # must either all succeed or leave nothing behind. The compensating
        # transaction deletes created notes in reverse when any child fails
        # (most often DuplicateCardError), preventing orphan masters and
        # half-created groups.
        async with AnkiNoteTransaction(card_service) as tx:
            master_note_id = await tx.create_note(
                deck_name=master_deck,
                model_name="Expression_Master_Dark",
                fields=master_fields,
                tags=base_tags + ["HandlerGenerated"],
                allow_duplicate=allow_duplicate
            )

            # 3. 建立統一編號的子卡片列表
            # 統一編號規則：grammar_micro_points 在前，reorganized_micro_points 在後，
            # 按照 LLM 回傳順序從 1 開始連續編號。
            all_micro_entries: list[tuple[str, str, object]] = []
            for mp in correction_result.grammar_micro_points:
                all_micro_entries.append(("Grammar", "文法修正", mp))
            for mp in correction_result.reorganized_micro_points:
                all_micro_entries.append(("Reorganized", "重新組織", mp))

            base_micro_deck = MICRO_DECK
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

                # 子卡與母卡用同一個 allow_duplicate：允許重複或覆蓋時舊卡的子卡還在（覆蓋是新卡建好後
                # 才刪舊卡），內容相同的子卡不能讓整組交易回滾
                await tx.create_note(
                    deck_name=deck_name_full,
                    model_name="Expression_Micro_Dark",
                    fields=micro_fields,
                    tags=micro_tags,
                    allow_duplicate=allow_duplicate,
                )

            return list(tx.created_ids)
