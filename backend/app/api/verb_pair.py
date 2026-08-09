"""JP 動詞對（自動詞/他動詞）卡片 API 路由模組。

JP verb pair (intransitive/transitive) card API router module.
"""

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.core.dependencies import get_card_service, get_relation_service
from app.core.exceptions import FluencyTidesError
from app.services.card_service import CardService
from app.services.relation_service import RelationService
from app.services.task_handlers.jp_verb_pair_handler import JPVerbPairHandler

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/verb-pair", tags=["Verb Pair"])

class CreateVerbPairRequest(BaseModel):
    """建立動詞對卡片的請求結構。

    Request schema for creating a verb pair card.

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

class UpdateVerbPairAudioRequest(BaseModel):
    """更新動詞對卡片音檔的請求結構。

    Request schema for updating audio on a verb pair card.

    Attributes:
        field_name: 目標 JSON 陣列欄位名稱。Target JSON-array field name.
        audio: 音檔檔名。Audio file name.
        text: 例句文字。Example sentence text.
        speaker: 說話者名稱。Speaker name.
        avatar: 頭像檔名。Avatar file name.
    """

    field_name: str
    audio: str
    text: str = ""
    speaker: str = ""
    avatar: str = "none"

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

@router.post("/create", status_code=status.HTTP_201_CREATED)
async def create_verb_pair(
    request: CreateVerbPairRequest,
    card_service: CardService = Depends(get_card_service),
    relation_service: RelationService = Depends(get_relation_service)
):
    """建立自動詞/他動詞對應的 Anki 母卡片（暫無 LLM 例句產生邏輯）。

    Create the Anki master card for an intransitive/transitive verb pair
    (LLM example-sentence generation not yet implemented).

    Args:
        request: 建立請求。The creation request.
        card_service: 卡片服務（依賴注入）。Injected CardService.
        relation_service: 關聯服務（依賴注入）。Injected RelationService.
    """
    # TODO: Implement creating empty Master card only
    pass

@router.put("/{note_id}/update", status_code=status.HTTP_200_OK)
async def update_verb_pair_audio(
    note_id: int,
    request: UpdateVerbPairAudioRequest,
    card_service: CardService = Depends(get_card_service),
    relation_service: RelationService = Depends(get_relation_service)
):
    """在特定的 JSON 陣列欄位中新增或更新音檔與例句。

    Add or update audio and example sentences inside a specific
    JSON-array field.

    Args:
        note_id: 目標 Anki Note ID。Target Anki note ID.
        request: 更新請求。The update request.
        card_service: 卡片服務（依賴注入）。Injected CardService.
        relation_service: 關聯服務（依賴注入）。Injected RelationService.

    Returns:
        成功狀態字典。A success status dict.

    Raises:
        HTTPException: 發生未預期錯誤時回傳 500。
            Returns 500 on unexpected errors.
    """
    handler = JPVerbPairHandler()
    parameters = request.model_dump()
    parameters["action"] = "add_audio"
    
    try:
        await handler.execute_update(
            card_service=card_service,
            relation_service=relation_service,
            note_id=note_id,
            parameters=parameters
        )
        return {"status": "success", "message": "已成功更新音檔資訊"}
    except FluencyTidesError:
        raise
    except Exception as e:
        logger.exception("更新動詞對卡片音檔失敗")
        raise HTTPException(status_code=500, detail=str(e))

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
