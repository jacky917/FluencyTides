"""JP_CoreVerb (core verb) card API router.

JP_CoreVerb（核心動詞）卡片 API 路由。

Follows the same convention as ``verb_pair.py``: the handler is instantiated
directly to execute the task. Generation constraints (quotas, per-chapter
limits, diversity-based sentence selection) are all handled on the script
side; this API only generates child cards for a single sentence.

比照 ``verb_pair.py`` 的慣例：直接實例化 Handler 執行任務。
生成約束（配額、章節上限、多樣性選句）全部在腳本側消化，
本 API 僅負責單句的子卡片生成。
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.core.dependencies import get_card_service, get_relation_service
from app.core.exceptions import FluencyTidesError
from app.services.card_service import CardService
from app.services.relation_service import RelationService
from app.services.task_handlers.jp_core_verb_handler import JPCoreVerbHandler

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/core-verb", tags=["Core Verb"])

class GenerateChildCardsRequest(BaseModel):
    """The request body for generating core verb child cards.

    生成核心動詞子卡片的請求結構。

    Attributes:
        master_note_id: the Anki note ID of the master card
            母卡片的 Anki note id
        deck_name: the target deck name for child cards (subdivided into ``::Context`` / ``::Cloze``)
            子卡片目標牌組名稱（會再細分 ``::Context`` / ``::Cloze``）
        target_verb: the target core verb in furigana notation, for example 見[み]る
            目標核心動詞（furigana 標音格式，如 見[み]る）
        source_game: the source game prefix, used for tags and world-setting prompts
            遊戲來源前綴（用於標籤與世界觀提示詞）
        context_dialogue: the context dialogue lines (with ``is_target`` markers)
            上下文對話陣列（含 ``is_target`` 標記）
        target_verb_span: optional. The character span ``[start, end]`` of the target verb in the
            target sentence, morphologically verified on the script side, used to cross-check the
            cloze
            可選。腳本側形態素驗證過的目標動詞在目標句中的字元 span ``[start, end]``，供挖空交叉驗證
    """
    master_note_id: int
    deck_name: str
    target_verb: str
    source_game: str
    context_dialogue: list[dict[str, Any]]
    target_verb_span: list[int] | None = None

@router.post("/generate-child-cards", status_code=status.HTTP_200_OK)
async def generate_child_cards(
    request: GenerateChildCardsRequest,
    card_service: CardService = Depends(get_card_service),
    relation_service: RelationService = Depends(get_relation_service)
):
    """Generate Context and Cloze child cards from the given text and attach them to the master card.

    根據傳入的文本生成 Context 和 Cloze 子卡片並追加至母卡片。

    Args:
        request: the generation request (with optional ``target_verb_span`` cross-check info)
            生成請求（含可選的 ``target_verb_span`` 交叉驗證資訊）
        card_service: the injected card service
            卡片服務（依賴注入）
        relation_service: the injected relation service
            關聯服務（依賴注入）

    Returns:
        a dict with ``status``, ``message``, and the generation result ``data``
        包含 ``status``、``message`` 與生成結果 ``data`` 的字典

    Raises:
        FluencyTidesError: if the handler reports a business error; passed through to the global
            handler
            若 handler 回報業務錯誤；原樣交給全域 handler
        HTTPException: if an unexpected error occurs (500)
            若發生未預期錯誤（500）
    """
    handler = JPCoreVerbHandler()
    parameters = request.model_dump()
    parameters["action"] = "generate_child_cards"

    try:
        result = await handler.execute_generate(
            card_service=card_service,
            relation_service=relation_service,
            parameters=parameters
        )
        return {"status": "success", "message": "已成功產生子卡片並關聯至母卡片", "data": result}
    except FluencyTidesError:
        raise
    except Exception as e:
        logger.exception("產生核心動詞子卡片失敗")
        raise HTTPException(status_code=500, detail=str(e))
