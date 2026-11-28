"""JP verb pair (intransitive/transitive) card API router module.

JP 動詞對（自動詞/他動詞）卡片 API 路由模組。
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.core.dependencies import get_card_service, get_relation_service
from app.core.exceptions import FluencyTidesError
from app.services.card_service import CardService
from app.services.relation_service import RelationService
from app.services.task_handlers.registry import handler_registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/verb-pair", tags=["Verb Pair"])

class GenerateChildCardsRequest(BaseModel):
    """The request body for generating verb pair child cards.

    生成動詞對子卡片的請求結構。

    Attributes:
        master_note_id: the Anki note ID of the master card
            母卡片的 Anki note id
        deck_name: the target deck name for child cards
            子卡片目標牌組名稱
        target_verb: the target verb
            目標動詞
        source_game: the source game prefix
            遊戲來源前綴
        context_dialogue: the context dialogue lines
            上下文對話陣列
    """

    master_note_id: int
    deck_name: str
    target_verb: str
    source_game: str
    context_dialogue: list[dict[str, Any]]

@router.post("/generate-child-cards", status_code=status.HTTP_200_OK)
async def generate_child_cards(
    request: GenerateChildCardsRequest,
    card_service: CardService = Depends(get_card_service),
    relation_service: RelationService = Depends(get_relation_service)
):
    """Generate Context and Cloze child cards from the given text and attach them to the master card.

    根據傳入的文本生成 Context 和 Cloze 子卡片並追加至母卡片。

    Args:
        request: the generation request
            生成請求
        card_service: the injected card service
            卡片服務（依賴注入）
        relation_service: the injected relation service
            關聯服務（依賴注入）

    Returns:
        a dict with ``status``, ``message``, and the generation result ``data``
        含 ``status``、``message`` 與生成結果 ``data`` 的字典

    Raises:
        FluencyTidesError: if the handler reports a business error; passed through to the global
            handler
            若 handler 回報業務錯誤；原樣交給全域 handler
        HTTPException: if an unexpected error occurs (500)
            若發生未預期錯誤（500）
    """
    creator = handler_registry.get_creator("jp_verb_pair")
    parameters = request.model_dump()
    parameters["action"] = "generate_child_cards"
    
    try:
        result = await creator.execute_generate(
            card_service=card_service,
            relation_service=relation_service,
            parameters=parameters
        )
        return {"status": "success", "message": "已成功產生子卡片並關聯至母卡片", "data": result}
    except FluencyTidesError:
        raise
    except Exception as e:
        logger.exception("產生子卡片失敗")
        raise HTTPException(status_code=500, detail=str(e))
