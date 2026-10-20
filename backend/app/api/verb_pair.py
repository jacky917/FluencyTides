"""JP 動詞對（自動詞/他動詞）卡片 API 路由模組。

JP verb pair (intransitive/transitive) card API router module.
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.core.dependencies import get_card_service, get_relation_service
from app.core.exceptions import FluencyTidesError
from app.services.card_service import CardService
from app.services.relation_service import RelationService
from app.services.task_handlers.jp_verb_pair_handler import JPVerbPairHandler

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/verb-pair", tags=["Verb Pair"])

class GenerateChildCardsRequest(BaseModel):
    """生成動詞對子卡片的請求結構。

    Request schema for generating verb pair child cards.

    Attributes:
        master_note_id: 母卡片的 Anki note id。Anki note id of the master card.
        deck_name: 子卡片目標牌組名稱。Target deck name for child cards.
        target_verb: 目標動詞。The target verb.
        source_game: 遊戲來源前綴。Source-game prefix.
        context_dialogue: 上下文對話陣列。Context dialogue array.
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
    """根據傳入的文本生成 Context 和 Cloze 子卡片並追加至母卡片。

    Generate Context and Cloze child cards from the given text and attach
    them to the master card.

    Args:
        request: 生成請求。The generation request.
        card_service: 卡片服務（依賴注入）。Injected CardService.
        relation_service: 關聯服務（依賴注入）。Injected RelationService.

    Returns:
        含 status、message 與生成結果 data 的字典。
        Dict containing status, message, and generation result data.

    Raises:
        HTTPException: 發生未預期錯誤時回傳 500。
            Returns 500 on unexpected errors.
    """
    handler = JPVerbPairHandler()
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
        logger.exception("產生子卡片失敗")
        raise HTTPException(status_code=500, detail=str(e))
