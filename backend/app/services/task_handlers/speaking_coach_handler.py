"""口說教練任務處理器 (Speaking Coach Handler) 模組。

負責 Speaking_Coach_Dark 等口說卡片的建立、列表讀取，
以及 Recordings / References / Prompt_Audios 等 JSON 欄位的更新。

Speaking coach handler module.

Handles creation and listing of speaking cards such as
Speaking_Coach_Dark, and updates of JSON fields like Recordings,
References, and Prompt_Audios.
"""

import json
import logging
import uuid
from datetime import date
from typing import TYPE_CHECKING, Annotated, override

from pydantic import BaseModel, Field, StringConstraints, field_validator

from app.core.config import settings
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager
from app.schemas.language import LANG_CODE_PATTERN, TargetLanguage, normalize_language_code
from app.services.task_handlers.base import BaseHandler
from app.services.task_handlers.capabilities import (
    CreateCapability,
    GraphCapability,
    HandlerCapabilities,
    Operation,
)
from app.services.task_handlers.registry import register_handler

if TYPE_CHECKING:
    from app.services.card_service import CardService
    from app.services.relation_service import RelationService

logger = logging.getLogger(__name__)

#: 口說卡（對話卡、三語對話卡）共用的具名動作，TG 的錄音與上傳音檔在用。
SPEAKING_ACTIONS = ("add_audio", "add_recording", "delete_recording")

# 參考回答的上限：只防前端 bug 或手動呼叫 API 寫進超大內容，與三語對話卡相同
MAX_REFERENCES = 100
MAX_REFERENCE_LENGTH = 2000

#: 一筆參考回答（去掉前後空白後不能是空的）。One reference answer.
ReferenceText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_REFERENCE_LENGTH)]


class SpeakingCoachCreateParams(BaseModel):
    """對話卡的建立參數（D3）。``tg_bot`` 不在這裡，由服務層的關鍵字參數傳入。

    Create parameters of a speaking card; ``tg_bot`` is passed by the service
    layer, not here.
    """

    prompt: str = Field(
        min_length=1,
        title="目標台詞",
        description="口說情境對白的目標台詞",
        json_schema_extra={"x-ui": "textarea"},
    )
    context: str = Field(
        default="", title="情境", description="情境描述（選填）", json_schema_extra={"x-ui": "textarea"}
    )
    target_language: str = Field(
        default=TargetLanguage.EN_US.value,
        title="目標語言",
        description="BCP-47 語言代碼，例如 en-US、ja-JP、zh-TW",
        pattern=LANG_CODE_PATTERN,
        examples=[TargetLanguage.EN_US.value, TargetLanguage.JA_JP.value, TargetLanguage.ZH_TW.value],
    )
    references: list[ReferenceText] = Field(
        default_factory=list,
        max_length=MAX_REFERENCES,
        title="參考回答",
        description="一行一筆（選填）；之後在 TG 錄音評分時可以對照",
        json_schema_extra={"x-ui": "lines"},
    )

    @field_validator("references", mode="before")
    @classmethod
    def _drop_blank(cls, value: object) -> object:
        """去掉空白的一筆（網頁一行一筆，空行不算）。Drops blank entries."""
        if isinstance(value, list):
            return [item for item in value if not (isinstance(item, str) and not item.strip())]
        return value

    @field_validator("target_language")
    @classmethod
    def _normalize(cls, value: str) -> str:
        """與 TG 流程相同的正規化（``ja-jp`` → ``ja-JP``）。Same normalization as the TG flow."""
        normalized = normalize_language_code(value)
        if normalized is None:
            raise ValueError(f"無法辨識的語言代碼：{value}")
        return normalized


@register_handler
class SpeakingCoachHandler(BaseHandler):
    """口說教練任務處理器。

    Speaking coach task handler.

    支援 Speaking_Coach_Dark 等口說卡片。
    管理口說情境的建立、以及複雜 JSON 欄位（Recordings）的更新。

    Supports speaking cards such as Speaking_Coach_Dark, managing
    scenario creation and updates of complex JSON fields (Recordings).
    """

    CAPABILITIES = HandlerCapabilities(
        create=CreateCapability(
            supported=True,
            channels=("web", "tg"),
            flow="form",
            deck="selected",
            note_type="Speaking_Coach_Dark",
        ),
        update_fields=Operation(reason="對話卡的欄位修改尚未開放"),
        delete=Operation(reason="對話卡的刪除尚未開放"),
        actions=SPEAKING_ACTIONS,
        graph=GraphCapability(reason="口說卡沒有知識圖譜"),
    )
    CREATE_PARAMS = SpeakingCoachCreateParams
    CREATE_TAGS = ("Speaking_Coach", "HandlerGenerated")

    @property
    @override
    def handler_name(self) -> str:
        """處理器名稱。

        Handler name.
        """
        return "speaking_coach"

    @property
    @override
    def title(self) -> str:
        """中文名稱。Display title."""
        return "對話卡"

    @property
    @override
    def supported_models(self) -> list[str]:
        """支援的 Anki 模型名稱清單。

        Supported Anki model names.
        """
        return ["Speaking_Coach_Dark"]

    @override
    async def execute_create(
        self,
        card_service: "CardService",
        relation_service: "RelationService",
        deck_name: str,
        model_name: str,
        parameters: dict
    ) -> list[int]:
        """建立一張口說卡片：參考回答依參數寫入（補上今天、啟用、沒有音檔），錄音與題目音檔為空陣列。

        Create a speaking card with the given reference answers.

        Args:
            card_service: 卡片服務。Card service.
            relation_service: 關聯服務。Relation service.
            deck_name: 目標牌組。Target deck.
            model_name: 目標模型。Target model.
            parameters: ``SpeakingCoachCreateParams`` 驗證後的參數與 tg_bot。The
                validated parameters plus 'tg_bot'.

        Returns:
            建立成功的 Note ID（單元素陣列）。The created note ID, as a list.
        """
        prompt_text = parameters.get("prompt", "")
        context_text = parameters.get("context", "")
        target_language = parameters.get("target_language", "en-US")
        tg_bot = parameters.get("tg_bot", settings.TG_BOT_USERNAME or "")
        
        # 產生唯一 UUID 作為卡片 ID
        card_id = str(uuid.uuid4())

        # 預設的空 JSON 陣列字串
        empty_json_array = json.dumps([], ensure_ascii=False)
        # 每筆的形狀與 TG 建卡相同（app/schemas/speaking.py 的 ReferenceItem）；寫入方式與三語對話卡相同，
        # 經 HTML 轉義（內容可能含 <、&；TG 舊流程沒轉義，卡片與 TG 的讀取兩種都吃）
        today = date.today().isoformat()
        references = [
            {"date": today, "content": content, "status": 1, "audios": []}
            for content in parameters.get("references") or []
        ]

        fields = {
            "Card_ID": card_id,
            "Prompt": prompt_text,
            "Context": context_text,
            "Prompt_Audios": empty_json_array,
            "Recordings": empty_json_array,
            "References": AnkiJsonFieldManager.serialize(references),
            "Target_Language": target_language,
            "TG_Bot": tg_bot
        }

        # 將透過 CardService 的嚴格欄位檢查後寫入 Anki
        logger.info("建立口說任務卡片 (Model: %s) -> Prompt: %s", model_name, prompt_text)
        note_id = await card_service.create_note(
            deck_name=deck_name,
            model_name=model_name,
            fields=fields,
            tags=list(self.CREATE_TAGS),
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
        """客製化更新，例如新增或刪除錄音紀錄。

        Task-specific update, e.g. adding or deleting recordings.

        parameters 支援:
        - {"action": "add_recording", "recording": {"date": "...", "score": 90, ...}}
        - {"action": "delete_recording", "index": 1}
        - {"action": "update_fields", "fields": {"Context": "new context"}}

        Supported actions: add_recording, delete_recording, add_audio,
        update_fields (see the examples above).

        Args:
            card_service: 卡片服務。Card service.
            relation_service: 關聯服務。Relation service.
            note_id: 目標 Note ID。Target note ID.
            parameters: 更新參數（需含 action）。Update params (must
                contain 'action').

        Raises:
            ValueError: 參數缺漏或不支援的 action。On missing params or
                unsupported action.
        """
        action = parameters.get("action")
        
        if action == "update_fields":
            # 直接更新欄位
            update_fields = parameters.get("fields", {})
            await card_service.update_note_fields(note_id, update_fields)
            return
            
        # 讀取現有筆記
        note = await card_service.get_note(note_id)
        fields = note["fields"]
        
        if action in ("add_recording", "delete_recording"):
            recordings = await AnkiJsonFieldManager.safe_read_list(card_service, note_id, "Recordings")
                
            if action == "add_recording":
                new_rec = parameters.get("recording")
                if new_rec:
                    recordings.append(new_rec)
            elif action == "delete_recording":
                index = parameters.get("index")
                if index is not None and 0 <= index < len(recordings):
                    recordings.pop(index)
                    
            await AnkiJsonFieldManager.update_field(card_service, note_id, "Recordings", recordings)
            logger.info("已更新口說卡片 %d 的 Recordings 欄位 (目前 %d 筆)", note_id, len(recordings))
            return
            
        if action == "add_audio":
            field_name = parameters.get("field_name")
            index_str = parameters.get("index")
            audio_filename = parameters.get("audio")

            if not field_name or not index_str or not audio_filename:
                raise ValueError("add_audio 需要 field_name, index, audio 參數")

            items = await AnkiJsonFieldManager.safe_read_list(card_service, note_id, field_name)

            if field_name == "Prompt_Audios":
                avatar_filename = parameters.get("avatar", "")
                speaker_name = parameters.get("speaker", "User")
                new_audio_obj = {"audio": audio_filename, "speaker": speaker_name, "avatar": avatar_filename}
                if index_str == "last" or index_str == "none":
                    items.append(new_audio_obj)
                else:
                    try:
                        idx = int(index_str)
                        items.insert(idx, new_audio_obj)
                    except ValueError:
                        items.append(new_audio_obj)
            elif field_name == "References":
                try:
                    idx = -1 if (index_str == "last" or index_str == "none") else int(index_str)
                    if items:
                        ref_item = items[idx]
                        if isinstance(ref_item, dict):
                            if "audios" not in ref_item or not isinstance(ref_item["audios"], list):
                                ref_item["audios"] = []
                            avatar_filename = parameters.get("avatar", "")
                            speaker_name = parameters.get("speaker", "User")
                            ref_item["audios"].append({"audio": audio_filename, "speaker": speaker_name, "avatar": avatar_filename})
                    else:
                        logger.warning(f"References 是空的，無法插入音檔: {audio_filename}")
                except (ValueError, IndexError):
                    logger.warning(f"References 找不到對應的 index: {index_str}")
            else:
                logger.warning(f"未知的音檔新增目標欄位: {field_name}")

            await AnkiJsonFieldManager.update_field(card_service, note_id, field_name, items)
            logger.info("已新增音檔 %s 到 %s (索引 %s)", audio_filename, field_name, index_str)
            return

        raise ValueError(f"不支援的更新操作: {action}")
