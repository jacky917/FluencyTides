"""動詞對卡片範例 API 路由模組（測試範例用）。

Example verb pair card API router module (for testing/demo purposes).
"""

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.core.dependencies import get_card_service, get_relation_service
from app.services.card_service import CardService
from app.services.relation_service import RelationService
from app.services.task_handlers.example.verb_pair_example_handler import VerbPairExampleHandler

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/verb-pair", tags=["Example Verb Pair"])

class CreateExampleVerbPairRequest(BaseModel):
    """建立範例動詞對卡片的請求結構。

    Request schema for creating an example verb pair card.

    Attributes:
        intransitive: 自動詞。The intransitive verb.
        transitive: 他動詞。The transitive verb.
        context_text: 上下文文字。Context text.
        llm_result: 可選的 LLM 預生成結果。Optional pre-generated LLM result.
        deck_name: 目標牌組名稱。Target deck name.
    """

    intransitive: str
    transitive: str
    context_text: str = ""
    llm_result: Optional[dict[str, Any]] = None
    deck_name: str = ""

@router.post("/create", status_code=status.HTTP_201_CREATED)
async def create_verb_pair_example(
    request: CreateExampleVerbPairRequest,
    card_service: CardService = Depends(get_card_service),
    relation_service: RelationService = Depends(get_relation_service)
):
    """建立自動詞/他動詞對應的 Anki 母子卡片群組 (測試範例用)。

    Create the Anki master/child card group for an intransitive/transitive
    verb pair (for testing/demo purposes).

    Args:
        request: 建立請求。The creation request.
        card_service: 卡片服務（依賴注入）。Injected CardService.
        relation_service: 關聯服務（依賴注入）。Injected RelationService.

    Returns:
        含 status 與 created_note_ids 的字典。
        Dict containing status and created_note_ids.

    Raises:
        HTTPException: 發生未預期錯誤時回傳 500。
            Returns 500 on unexpected errors.
    """
    handler = VerbPairExampleHandler()
    try:
        created_ids = await handler.execute_create(
            card_service=card_service,
            relation_service=relation_service,
            deck_name=request.deck_name,
            model_name="",
            parameters=request.model_dump()
        )
        return {"status": "success", "created_note_ids": created_ids}
    except Exception as e:
        logger.exception("建立範例動詞對卡片失敗")
        raise HTTPException(status_code=500, detail=str(e))
