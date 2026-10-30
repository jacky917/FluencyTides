"""三語口說練習卡片處理器（Speaking_Trilingual_Dark）。

架構對齊 ``speaking_coach_handler.py``；差異：

- 一張卡片包含 ZH/JA/EN 三語練習；Prompt / Context 共通，
  Recordings / References 每語言獨立欄位。
- 不設 Target_Language 欄位——語言由欄位後綴或語言碼決定
  （``shared/trilingual_lang.py``）。
- ``Prompt_Audios`` 為單一 JSON 欄位，項目帶 ``lang`` 鍵，
  **每語言最多一條**（同 lang 覆蓋）。

欄位讀寫一律走 ``CardService`` / ``AnkiJsonFieldManager``。

Trilingual speaking practice card handler (Speaking_Trilingual_Dark).

Architecture mirrors ``speaking_coach_handler.py``; differences: one card
covers ZH/JA/EN practice with shared Prompt/Context and per-language
Recordings/References fields; no Target_Language field (language comes
from the field suffix or a language code, see
``shared/trilingual_lang.py``); ``Prompt_Audios`` is a single JSON field
whose items carry a ``lang`` key, at most one per language (same lang
overwrites). All field I/O goes through ``CardService`` /
``AnkiJsonFieldManager``.
"""

import json
import logging
import uuid
from datetime import date
from typing import TYPE_CHECKING, Literal, override

from pydantic import BaseModel, Field, field_validator

from app.core.config import settings
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager
from app.services import speaking_trilingual_generate as generate_service
from app.services.task_handlers.base import BaseHandler
from app.services.task_handlers.capabilities import (
    CreateCapability,
    GraphCapability,
    HandlerCapabilities,
    Operation,
)
from app.services.task_handlers.registry import register_handler
from app.services.task_handlers.shared.trilingual_familiarity import parse_familiarity
from app.services.task_handlers.shared.trilingual_lang import (
    LANG_CODES,
    recordings_field,
    references_field,
)
from app.services.task_handlers.speaking_coach_handler import SPEAKING_ACTIONS

if TYPE_CHECKING:
    from app.services.card_service import CardService
    from app.services.relation_service import RelationService

logger = logging.getLogger(__name__)

# 建卡參數的上限：只防前端 bug 或手動呼叫 API 寫進超大內容（AnkiDroid 打開會很慢），
# 正常使用遠遠碰不到——LLM 一次約生成二十幾句，每句幾十字
MAX_REFERENCES_PER_LANG = 100
MAX_TEXT_LENGTH = 2000


class ReferenceDraft(BaseModel):
    """建卡時的一句參考答案；``date``、``status``、``audios`` 由建卡補上。

    One reference answer given at creation.
    """

    content: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH, description="句子")
    note: str = Field(
        default="",
        max_length=MAX_TEXT_LENGTH,
        description="說明：對象與場合、語氣的關鍵或場景補充（卡片背面以小字顯示）",
    )
    familiarity: int | None = Field(default=None, description="熟識度 0–9；省略表示未標")

    @field_validator("content", "note", mode="before")
    @classmethod
    def _strip(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("familiarity", mode="before")
    @classmethod
    def _familiarity(cls, value: object) -> int | None:
        # 規則與匯入、卡片相同（trilingual_familiarity.py）
        return parse_familiarity(value)


class SpeakingTrilingualCreateParams(BaseModel):
    """三語對話卡的建立參數（D3）。語言由欄位決定，所以沒有語言參數；``tg_bot`` 由服務層傳入。

    Create parameters of a trilingual speaking card. 網頁的預覽流程由草稿（:meth:`execute_draft`）
    預先填好，使用者勾選後送回。
    """

    prompt: str = Field(
        min_length=1,
        max_length=MAX_TEXT_LENGTH,
        title="情境提示",
        description="共通口說情境提示（三語共用）",
        json_schema_extra={"x-ui": "textarea"},
    )
    context: str = Field(
        default="",
        max_length=MAX_TEXT_LENGTH,
        title="情境",
        description="情境描述（選填）",
        json_schema_extra={"x-ui": "textarea"},
    )
    references_zh: list[ReferenceDraft] = Field(
        default_factory=list, max_length=MAX_REFERENCES_PER_LANG, title="中文說法"
    )
    references_ja: list[ReferenceDraft] = Field(
        default_factory=list, max_length=MAX_REFERENCES_PER_LANG, title="日文說法"
    )
    references_en: list[ReferenceDraft] = Field(
        default_factory=list, max_length=MAX_REFERENCES_PER_LANG, title="英文說法"
    )


class SpeakingTrilingualDraftParams(BaseModel):
    """網頁預覽流程的草稿輸入：場景、想怎麼答、對象。Draft input of the web preview flow."""

    background: str = Field(
        min_length=1,
        title="背景",
        description="發生什麼事、自己的處境",
        json_schema_extra={"x-ui": "textarea"},
    )
    intent: str = Field(
        default="",
        title="想怎麼答",
        description="選填；沒填時由 LLM 想出各種正反回應的自然說法",
        json_schema_extra={"x-ui": "textarea"},
    )
    audience: Literal[*generate_service.AUDIENCES] = Field(  # type: ignore[valid-type]
        default=generate_service.ANY_AUDIENCE, title="對象", description="不指定時生成對各種對象的說法"
    )
    audience_note: str = Field(default="", title="對象補充", description="選填，例：房東，年紀比我大")


@register_handler
class SpeakingTrilingualHandler(BaseHandler):
    """三語口說練習任務處理器。

    Trilingual speaking practice task handler.

    支援 Speaking_Trilingual_Dark：管理共通情境的建立，以及
    per-language JSON 欄位（Recordings_×3 / References_×3）與
    Prompt_Audios（帶 lang 鍵）的更新。

    Supports Speaking_Trilingual_Dark: manages shared-scenario creation
    and updates of per-language JSON fields (Recordings x3 /
    References x3) plus Prompt_Audios (with lang keys).
    """

    CAPABILITIES = HandlerCapabilities(
        create=CreateCapability(
            supported=True,
            channels=("web",),
            flow="preview",
            deck="selected",
            note_type="Speaking_Trilingual_Dark",
        ),
        update_fields=Operation(reason="三語對話卡的欄位修改尚未開放"),
        delete=Operation(reason="三語對話卡的刪除尚未開放"),
        actions=SPEAKING_ACTIONS,
        graph=GraphCapability(reason="口說卡沒有知識圖譜"),
    )
    CREATE_PARAMS = SpeakingTrilingualCreateParams
    DRAFT_PARAMS = SpeakingTrilingualDraftParams

    @property
    @override
    def handler_name(self) -> str:
        """處理器名稱。

        Handler name.
        """
        return "speaking_trilingual"

    @property
    @override
    def title(self) -> str:
        """中文名稱。Display title."""
        return "三語對話卡"

    @property
    @override
    def supported_models(self) -> list[str]:
        """支援的 Anki 模型名稱清單。

        Supported Anki model names.
        """
        return ["Speaking_Trilingual_Dark"]

    @override
    async def execute_draft(self, parameters: dict[str, object]) -> dict[str, object]:
        """請 LLM 為場景生成三語說法，整理成建立參數的草稿（不寫入）。

        Ask the LLM for sayings and shape them as draft create parameters.

        Args:
            parameters: ``SpeakingTrilingualDraftParams`` 驗證後的輸入。The validated input.

        Returns:
            ``SpeakingTrilingualCreateParams`` 形狀的草稿。The draft create parameters.
        """
        audience = generate_service.target_audience(str(parameters["audience"]), str(parameters["audience_note"]))
        draft = await generate_service.generate(str(parameters["background"]), str(parameters["intent"]), audience)
        references = {
            f"references_{lang.lower()}": [
                saying.model_dump() for saying in getattr(draft, lang.lower()) if saying.content.strip()
            ]
            for lang in LANG_CODES
        }
        return SpeakingTrilingualCreateParams.model_validate(
            {"prompt": draft.prompt, "context": draft.context, **references}
        ).model_dump()

    @override
    async def execute_create(
        self,
        card_service: "CardService",
        relation_service: "RelationService",
        deck_name: str,
        model_name: str,
        parameters: dict
    ) -> list[int]:
        """建立一張三語口說卡片：參考答案依參數寫入（補上今天、啟用、沒有音檔），其餘 JSON 欄位為空陣列。

        Create a trilingual speaking card with the given reference answers;
        the other JSON fields start as empty arrays.

        Args:
            card_service: 卡片服務。Card service.
            relation_service: 關聯服務。Relation service.
            deck_name: 目標牌組。Target deck.
            model_name: 目標模型。Target model.
            parameters: ``SpeakingTrilingualCreateParams`` 驗證後的參數與 tg_bot。The
                validated parameters plus 'tg_bot'.

        Returns:
            建立成功的 Note ID（單元素陣列）。The created note ID, as a list.
        """
        prompt_text = parameters.get("prompt", "")
        context_text = parameters.get("context", "")
        tg_bot = parameters.get("tg_bot", settings.TG_BOT_USERNAME or "")

        card_id = str(uuid.uuid4())
        empty_json_array = json.dumps([], ensure_ascii=False)

        fields = {
            "Prompt": prompt_text,
            "Prompt_Audios": empty_json_array,
            "Context": context_text,
            "Card_ID": card_id,
            "TG_Bot": tg_bot,
        }
        today = date.today().isoformat()
        for lang in LANG_CODES:
            fields[recordings_field(lang)] = empty_json_array
            references = [
                {**_without_blank_note(ref), "status": 1, "date": today, "audios": []}
                for ref in parameters.get(f"references_{lang.lower()}") or []
            ]
            # 參考答案可能含 <、& 等字元：與語音流程、匯入相同，經 HTML 轉義後寫入
            fields[references_field(lang)] = AnkiJsonFieldManager.serialize(references)

        logger.info("建立三語口說卡片 (Model: %s) -> Prompt: %s", model_name, prompt_text)
        note_id = await card_service.create_note(
            deck_name=deck_name,
            model_name=model_name,
            fields=fields,
            tags=["Speaking_Trilingual", "HandlerGenerated"],
        )
        return [note_id]

    @override
    async def execute_update(
        self,
        card_service: "CardService",
        relation_service: "RelationService",
        note_id: int,
        parameters: dict
    ) -> None:
        """客製化更新（皆以 AnkiJsonFieldManager 讀寫）。

        Task-specific update (all I/O via AnkiJsonFieldManager).
        Supported actions: add_recording, delete_recording, add_audio,
        update_fields (see the examples below). Raises ValueError on
        missing params or an unsupported action.

        parameters 支援:
        - {"action": "add_recording", "lang": "JA", "recording": {...}}
        - {"action": "delete_recording", "lang": "JA", "index": 1}
        - {"action": "add_audio", "field_name": "References_JA"|"Prompt_Audios",
           "index": "0"|"last"|"ZH"|"JA"|"EN", "audio": "...",
           "speaker": "...", "avatar": "..."}
        - {"action": "update_fields", "fields": {"Context": "..."}}
        """
        action = parameters.get("action")

        if action == "update_fields":
            update_fields = parameters.get("fields", {})
            await card_service.update_note_fields(note_id, update_fields)
            return

        if action in ("add_recording", "delete_recording"):
            lang = parameters.get("lang")
            if lang not in LANG_CODES:
                raise ValueError(f"{action} 需要合法的 lang 參數 (ZH/JA/EN)，收到: {lang}")
            field_name = recordings_field(lang)
            recordings = await AnkiJsonFieldManager.safe_read_list(
                card_service, note_id, field_name
            )

            if action == "add_recording":
                new_rec = parameters.get("recording")
                if new_rec:
                    recordings.append(new_rec)
            else:
                index = parameters.get("index")
                if index is not None and 0 <= index < len(recordings):
                    recordings.pop(index)

            await AnkiJsonFieldManager.update_field(
                card_service, note_id, field_name, recordings
            )
            logger.info(
                "已更新三語口說卡片 %d 的 %s 欄位 (目前 %d 筆)",
                note_id, field_name, len(recordings),
            )
            return

        if action == "add_audio":
            field_name = parameters.get("field_name")
            index_str = parameters.get("index")
            audio_filename = parameters.get("audio")

            if not field_name or not index_str or not audio_filename:
                raise ValueError("add_audio 需要 field_name, index, audio 參數")

            items = await AnkiJsonFieldManager.safe_read_list(
                card_service, note_id, field_name
            )
            avatar_filename = parameters.get("avatar", "")
            speaker_name = parameters.get("speaker", "User")

            if field_name == "Prompt_Audios":
                # index 槽位為語言碼：同 lang 覆蓋（每語言最多一條，合計 ≤3）
                if index_str not in LANG_CODES:
                    raise ValueError(
                        f"Prompt_Audios 的 index 槽位須為語言碼 (ZH/JA/EN)，收到: {index_str}"
                    )
                new_audio_obj = {
                    "lang": index_str,
                    "audio": audio_filename,
                    "speaker": speaker_name,
                    "avatar": avatar_filename,
                }
                replaced = False
                for i, item in enumerate(items):
                    if isinstance(item, dict) and item.get("lang") == index_str:
                        items[i] = new_audio_obj
                        replaced = True
                        break
                if not replaced:
                    items.append(new_audio_obj)
            elif field_name.startswith("References_"):
                try:
                    idx = -1 if index_str in ("last", "none") else int(index_str)
                    if items:
                        ref_item = items[idx]
                        if isinstance(ref_item, dict):
                            if "audios" not in ref_item or not isinstance(ref_item["audios"], list):
                                ref_item["audios"] = []
                            ref_item["audios"].append({
                                "audio": audio_filename,
                                "speaker": speaker_name,
                                "avatar": avatar_filename,
                            })
                    else:
                        logger.warning(f"{field_name} 是空的，無法插入音檔: {audio_filename}")
                except (ValueError, IndexError):
                    logger.warning(f"{field_name} 找不到對應的 index: {index_str}")
            else:
                logger.warning(f"未知的音檔新增目標欄位: {field_name}")

            await AnkiJsonFieldManager.update_field(
                card_service, note_id, field_name, items
            )
            logger.info("已新增音檔 %s 到 %s (索引 %s)", audio_filename, field_name, index_str)
            return

        raise ValueError(f"不支援的更新操作: {action}")


def _without_blank_note(reference: dict[str, object]) -> dict[str, object]:
    """參考答案省略空的說明與未標的熟識度（與匯入的資料一致）。Drop an empty note and unset familiarity."""
    return {key: value for key, value in reference.items() if value not in ("", None)}
