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

from typing import override, TYPE_CHECKING
import json
import logging
import uuid

from app.services.task_handlers.base import BaseHandler
from app.services.task_handlers.registry import register_handler
from app.services.task_handlers.shared.trilingual_lang import (
    LANG_CODES,
    recordings_field,
    references_field,
)

if TYPE_CHECKING:
    from app.services.card_service import CardService
    from app.services.relation_service import RelationService

logger = logging.getLogger(__name__)

from app.core.config import settings
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager


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

    @property
    @override
    def handler_name(self) -> str:
        """處理器名稱。

        Handler name.
        """
        return "speaking_trilingual"

    @property
    @override
    def supported_models(self) -> list[str]:
        """支援的 Anki 模型名稱清單。

        Supported Anki model names.
        """
        return ["Speaking_Trilingual_Dark"]

    @override
    def get_input_schema(self) -> dict:
        """回傳前端需要的參數 Schema。

        Return the parameter JSON schema required by the frontend.
        """
        return {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "共通口說情境提示（三語共用）"},
                "context": {"type": "string", "description": "情境描述 (選填)"},
                "tg_bot": {"type": "string", "description": "TG Bot ID (選填)"},
            },
            "required": ["prompt"],
        }

    @override
    async def execute_create(
        self,
        card_service: "CardService",
        relation_service: "RelationService",
        deck_name: str,
        model_name: str,
        parameters: dict
    ) -> int:
        """建立一張空的三語口說卡片（七個 JSON 欄位皆為空陣列）。

        Create an empty trilingual speaking card (all seven JSON fields
        start as empty arrays).

        Args:
            card_service: 卡片服務。Card service.
            relation_service: 關聯服務。Relation service.
            deck_name: 目標牌組。Target deck.
            model_name: 目標模型。Target model.
            parameters: 需含 prompt，可選 context / tg_bot。Requires
                'prompt'; optional 'context', 'tg_bot'.

        Returns:
            建立成功的 Note ID。ID of the created note.
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
        for lang in LANG_CODES:
            fields[recordings_field(lang)] = empty_json_array
            fields[references_field(lang)] = empty_json_array

        logger.info("建立三語口說卡片 (Model: %s) -> Prompt: %s", model_name, prompt_text)
        return await card_service.create_note(
            deck_name=deck_name,
            model_name=model_name,
            fields=fields,
            tags=["Speaking_Trilingual", "HandlerGenerated"],
        )

    async def execute_read_list(
        self,
        card_service: "CardService",
        relation_service: "RelationService",
        deck_name: str | None = None
    ) -> list[dict]:
        """讀取三語口說卡片列表（各語言錄音數統計）。

        Read the trilingual speaking card list with per-language
        recording counts.

        Args:
            card_service: 卡片服務。Card service.
            relation_service: 關聯服務。Relation service.
            deck_name: 可選的牌組篩選。Optional deck filter.

        Returns:
            卡片摘要字典列表。List of card summary dicts.
        """
        query = " OR ".join([f'"note:{m}"' for m in self.supported_models])
        if deck_name:
            query = f'"deck:{deck_name}" ({query})'

        note_ids = await card_service.find_notes(query)
        notes_info = await card_service.get_notes_info(note_ids)

        results = []
        for n in notes_info:
            fields = n.get("fields", {})
            recordings_count = {}
            for lang in LANG_CODES:
                rec_str = fields.get(recordings_field(lang), {}).get("value", "[]")
                recordings_count[lang] = len(
                    AnkiJsonFieldManager.parse_field_string(rec_str)
                )
            results.append({
                "note_id": n["noteId"],
                "model_name": n["modelName"],
                "prompt": fields.get("Prompt", {}).get("value", ""),
                "context": fields.get("Context", {}).get("value", ""),
                "recordings_count": recordings_count,
            })
        return results

    @override
    async def execute_read_graph(
        self,
        card_service: "CardService",
        relation_service: "RelationService",
        deck_name: str | None = None
    ) -> dict:
        """三語口說卡片不需要知識圖譜，回傳空結構。

        Trilingual speaking cards need no knowledge graph; returns an
        empty struct.
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

    @override
    async def execute_delete(
        self,
        card_service: "CardService",
        relation_service: "RelationService",
        note_id: int
    ) -> None:
        """刪除三語口說卡片並清除關聯。

        Delete a trilingual speaking card and clean up its relations.

        Args:
            card_service: 卡片服務。Card service.
            relation_service: 關聯服務。Relation service.
            note_id: 目標 Note ID。Target note ID.
        """
        await card_service.delete_note(note_id)
        await relation_service.delete_relations_for_note(note_id)
