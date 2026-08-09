"""
Task Handlers REST API 路由模組。

Task Handlers REST API router module.

提供查詢系統可用處理器 (Handlers) 與動態呼叫生卡、更新、讀取等功能。
前端不再需要知道具體的 Anki 模型名稱或欄位結構，
只需指定 handler_name 與對應參數即可完成所有 CRUD 操作。

Provides discovery of available task handlers and dynamic dispatch for card
creation, update, and read operations. The frontend no longer needs to know
concrete Anki model names or field structures; specifying a handler_name and
its parameters is enough to perform all CRUD operations.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import verify_api_key
from app.core.dependencies import get_anki_client, get_card_service, get_relation_service
from app.core.exceptions import FluencyTidesError
from app.infrastructure.anki.client import AnkiClient
from app.services.card_service import CardService
from app.services.relation_service import RelationService
from app.services.task_handlers.registry import handler_registry
from app.schemas.card import ErrorResponse

# 導入 Handler 模組以觸發 @register_handler 裝飾器
import app.services.task_handlers.speaking_coach_handler
import app.services.task_handlers.vocabulary_mining_handler

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/handlers",
    tags=["Handlers"],
    dependencies=[Depends(verify_api_key)],
)





@router.get(
    "/",
    summary="取得所有可用的任務處理器",
    description="回傳每個 Handler 的名稱、支援模型與輸入 Schema，供前端動態建構 UI。",
)
async def list_handlers() -> list[dict[str, object]]:
    """Controller: 列出所有可用的 Handler。

    Controller: list all available handlers.

    Returns:
        每個 Handler 的名稱、支援模型與輸入 Schema 列表。
        A list of each handler's name, supported models, and input schema.
    """
    return handler_registry.list_all_handlers()


@router.post(
    "/{handler_name}/create",
    summary="透過指定處理器建立卡片",
    description="前端指定 handler_name、deck_name、model_name 與參數，由 Handler 負責 LLM 拼裝與 Anki 寫入。",
    responses={
        400: {"model": ErrorResponse, "description": "業務邏輯錯誤 (例如欄位不符、卡片重複等)"},
        500: {"model": ErrorResponse, "description": "系統未預期崩潰"},
    },
)
async def create_card(
    handler_name: str,
    payload: dict[str, object],
    card_service: CardService = Depends(get_card_service),
    relation_service: RelationService = Depends(get_relation_service),
) -> dict[str, object]:
    """Controller: 建立卡片。

    Controller: create a card via the specified handler.

    Args:
        handler_name: 處理器名稱。Name of the task handler.
        payload: 含 deck_name、model_name 與 parameters 的請求本文。
            Request body containing deck_name, model_name, and parameters.
        card_service: 注入的 CardService。Injected CardService instance.
        relation_service: 注入的 RelationService。Injected RelationService.

    Returns:
        含 note_id 與訊息的字典。Dict with the new note_id and a message.

    Raises:
        HTTPException: 缺少 deck_name/model_name 或 parameters 格式錯誤時回傳 400。
            Returns 400 when deck_name/model_name is missing or parameters
            is not a JSON object.
    """
    handler = handler_registry.get_handler(handler_name)
    deck_name = payload.get("deck_name")
    model_name = payload.get("model_name")
    parameters = payload.get("parameters", {})

    if not deck_name or not model_name:
        raise HTTPException(
            status_code=400,
            detail="必須提供 deck_name 與 model_name",
        )

    if not isinstance(parameters, dict):
        raise HTTPException(
            status_code=400,
            detail="parameters 必須是一個 JSON 物件",
        )

    note_id = await handler.execute_create(
        card_service, relation_service, str(deck_name), str(model_name), parameters
    )
    return {"note_id": note_id, "message": "建立成功"}


@router.get(
    "/{handler_name}/cards",
    summary="讀取該處理器管轄的卡片列表",
    responses={
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def read_cards(
    handler_name: str,
    deck_name: str | None = None,
    card_service: CardService = Depends(get_card_service),
    relation_service: RelationService = Depends(get_relation_service),
) -> list[dict[str, object]]:
    """Controller: 讀取清洗過後的卡片列表。

    Controller: read the cleaned card list managed by this handler.

    Args:
        handler_name: 處理器名稱。Name of the task handler.
        deck_name: (可選) 篩選的牌組名稱。Optional deck name filter.
        card_service: 注入的 CardService。Injected CardService instance.
        relation_service: 注入的 RelationService。Injected RelationService.

    Returns:
        卡片資料字典列表。A list of card data dicts.
    """
    handler = handler_registry.get_handler(handler_name)
    return await handler.execute_read_list(card_service, relation_service, deck_name)


@router.get(
    "/{handler_name}/graph",
    summary="讀取該處理器專屬的知識圖譜",
    responses={
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def read_graph(
    handler_name: str,
    deck_name: str | None = None,
    card_service: CardService = Depends(get_card_service),
    relation_service: RelationService = Depends(get_relation_service),
) -> dict[str, list[dict[str, object]]]:
    """Controller: 讀取圖譜。

    Controller: read the handler-specific knowledge graph.

    Args:
        handler_name: 處理器名稱。Name of the task handler.
        deck_name: (可選) 篩選的牌組名稱。Optional deck name filter.
        card_service: 注入的 CardService。Injected CardService instance.
        relation_service: 注入的 RelationService。Injected RelationService.

    Returns:
        圖譜資料字典 {"nodes": [...], "links": [...]}。
        Graph data dict {"nodes": [...], "links": [...]}.

    Raises:
        HTTPException: 該 Handler 不支援圖譜讀取時回傳 400。
            Returns 400 when the handler does not support graph reading.
    """
    handler = handler_registry.get_handler(handler_name)
    try:
        return await handler.execute_read_graph(card_service, relation_service, deck_name)
    except NotImplementedError:
        raise HTTPException(
            status_code=400,
            detail="此 Handler 不支援圖譜讀取",
        )


@router.put(
    "/{handler_name}/cards/{note_id}",
    summary="執行客製化的卡片更新",
    responses={
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def update_card(
    handler_name: str,
    note_id: int,
    parameters: dict[str, object],
    card_service: CardService = Depends(get_card_service),
    relation_service: RelationService = Depends(get_relation_service),
) -> dict[str, str]:
    """Controller: 更新卡片。

    Controller: perform a handler-specific card update.

    Args:
        handler_name: 處理器名稱。Name of the task handler.
        note_id: 目標 Anki Note ID。Target Anki note ID.
        parameters: 更新參數。Update parameters for the handler.
        card_service: 注入的 CardService。Injected CardService instance.
        relation_service: 注入的 RelationService。Injected RelationService.

    Returns:
        成功訊息字典。A success message dict.
    """
    handler = handler_registry.get_handler(handler_name)
    await handler.execute_update(card_service, relation_service, note_id, parameters)
    return {"message": "更新成功"}


@router.delete(
    "/{handler_name}/cards/{note_id}",
    summary="刪除卡片與其關聯",
    responses={
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def delete_card(
    handler_name: str,
    note_id: int,
    card_service: CardService = Depends(get_card_service),
    relation_service: RelationService = Depends(get_relation_service),
) -> dict[str, str]:
    """Controller: 刪除卡片。

    Controller: delete a card and its relations.

    Args:
        handler_name: 處理器名稱。Name of the task handler.
        note_id: 目標 Anki Note ID。Target Anki note ID.
        card_service: 注入的 CardService。Injected CardService instance.
        relation_service: 注入的 RelationService。Injected RelationService.

    Returns:
        成功訊息字典。A success message dict.
    """
    handler = handler_registry.get_handler(handler_name)
    await handler.execute_delete(card_service, relation_service, note_id)
    return {"message": "刪除成功"}


# =========================================================================
# Anki 基礎查詢 (不隸屬任何 Handler，但前端必需)
# =========================================================================


@router.get(
    "/decks",
    summary="取得所有 Anki 牌組",
    description="列出 Anki 中現有的所有牌組名稱與 ID。",
)
async def list_decks(
    anki_client: AnkiClient = Depends(get_anki_client),
) -> list[dict[str, object]]:
    """Controller: 列出所有牌組。

    Controller: list all Anki decks.

    Args:
        anki_client: 注入的 AnkiClient。Injected AnkiClient instance.

    Returns:
        含 deck_name 與 deck_id 的字典列表。
        A list of dicts containing deck_name and deck_id.
    """
    decks = await anki_client.get_deck_names_and_ids()
    return [
        {"deck_name": name, "deck_id": deck_id}
        for name, deck_id in decks.items()
    ]


@router.get(
    "/cards/{note_id}",
    summary="取得單一卡片詳情",
    description="讀取指定 Note ID 的完整卡片資訊。",
)
async def get_card_detail(
    note_id: int,
    card_service: CardService = Depends(get_card_service),
) -> dict[str, object]:
    """Controller: 讀取單一卡片詳情。

    Controller: read the full details of a single card.

    Args:
        note_id: 目標 Anki Note ID。Target Anki note ID.
        card_service: 注入的 CardService。Injected CardService instance.

    Returns:
        完整卡片資訊字典。The complete note info dict.
    """
    note = await card_service.get_note(note_id)
    return note
