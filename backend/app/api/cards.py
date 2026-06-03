"""
卡片生成與管理 REST API 路由模組。

本模組定義了 /api/v1/cards/ 前綴下的所有端點，
提供卡片生成、模型查詢、牌組列表等功能。

設計原則（嚴格遵守 Clean Architecture）：
    - Controller 層零業務邏輯：僅負責接收請求、委託 Service、回傳結果。
    - 所有業務邏輯封裝在 CardService 中。
    - 錯誤處理由全域 Exception Handler 統一處理，
      Controller 不需要 try/except 業務異常。
    - 所有端點均受 API Key 認證保護（透過 router 級 dependencies）。
"""

import logging

from fastapi import APIRouter, Depends

from app.core.auth import verify_api_key
from app.core.dependencies import get_card_service
from app.schemas.anki import AnkiDeckInfo, AnkiModelInfo
from app.schemas.card import (
    CardGenerateRequest,
    CardGenerateResponse,
    CardUpdateRequest,
    ErrorResponse,
)
from app.services.card_service import CardService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/cards",
    tags=["Cards"],
    dependencies=[Depends(verify_api_key)],
)


@router.post(
    "/generate",
    response_model=CardGenerateResponse,
    responses={
        409: {"model": ErrorResponse, "description": "卡片已存在（重複）"},
        404: {"model": ErrorResponse, "description": "牌組/模型/模板不存在"},
        502: {"model": ErrorResponse, "description": "LLM 或 Anki 服務異常"},
        401: {"model": ErrorResponse, "description": "API Key 認證失敗"},
    },
    summary="生成 Anki 學習卡片",
    description=(
        "輸入單字或句子，透過 LLM 生成結構化內容並寫入 Anki。\n\n"
        "完整流程：牌組檢查 → 防重複 → LLM 結構化生成 → 組裝 Note → 提交至 Anki。"
    ),
)
async def generate_card(
    request: CardGenerateRequest,
    card_service: CardService = Depends(get_card_service),
) -> CardGenerateResponse:
    """生成 Anki 學習卡片的 Controller 端點。

    此端點僅負責：
    1. 接收 CardGenerateRequest。
    2. 委託 CardService.generate_card() 執行業務邏輯。
    3. 回傳 CardGenerateResponse。

    所有業務異常由全域 Exception Handler 統一處理。

    Args:
        request: CardGenerateRequest Pydantic 模型實例。
        card_service: 注入的 CardService 實例。

    Returns:
        CardGenerateResponse 包含 note_id 與成功訊息。
    """
    return await card_service.generate_card(request)


@router.get(
    "/models",
    response_model=list[AnkiModelInfo],
    summary="列出可用的 Anki 模型",
    description="掃描本地 anki_models/ 目錄，回傳所有可用的模型定義摘要。",
)
async def list_models(
    card_service: CardService = Depends(get_card_service),
) -> list[AnkiModelInfo]:
    """列出所有可用的 Anki 模型定義的 Controller 端點。

    Args:
        card_service: 注入的 CardService 實例。

    Returns:
        AnkiModelInfo 模型實例列表。
    """
    return card_service.list_available_models()


@router.get(
    "/models/{model_file_name}",
    response_model=dict[str, object],
    responses={
        404: {"model": ErrorResponse, "description": "模型定義檔不存在"},
    },
    summary="取得模型詳細 Schema",
    description="回傳指定模型定義檔的完整 JSON 內容，包含欄位定義與 LLM Schema。",
)
async def get_model_detail(
    model_file_name: str,
    card_service: CardService = Depends(get_card_service),
) -> dict[str, object]:
    """取得單一模型的完整定義資訊的 Controller 端點。

    Args:
        model_file_name: JSON 檔案名稱（如 'TOEIC_Coach_Dark.json'）。
        card_service: 注入的 CardService 實例。

    Returns:
        模型定義檔的完整 JSON 字典。
    """
    return card_service.get_model_detail(model_file_name)


@router.get(
    "/decks",
    response_model=list[AnkiDeckInfo],
    responses={
        502: {"model": ErrorResponse, "description": "Anki 服務異常"},
    },
    summary="列出 Anki 牌組",
    description="從 AnkiConnect 取得所有牌組的名稱與 ID。需要 Anki Desktop 運行中。",
)
async def list_decks(
    card_service: CardService = Depends(get_card_service),
) -> list[AnkiDeckInfo]:
    """列出 Anki 牌組的 Controller 端點。

    Args:
        card_service: 注入的 CardService 實例。

    Returns:
        AnkiDeckInfo 模型實例列表。
    """
    return await card_service.list_decks()


# ============================================================================
# Phase 6: RUD 端點 (Read, Update, Delete)
# ============================================================================

@router.get(
    "/{note_id}",
    response_model=dict[str, object],
    responses={
        404: {"model": ErrorResponse, "description": "找不到該卡片"},
        502: {"model": ErrorResponse, "description": "Anki 服務異常"},
    },
    summary="取得單一卡片詳細資訊",
    description="透過筆記 ID 向 Anki 查詢卡片欄位內容與標籤。",
)
async def get_card(
    note_id: int,
    card_service: CardService = Depends(get_card_service),
) -> dict[str, object]:
    """取得卡片詳細資訊的 Controller 端點。"""
    return await card_service.get_card(note_id)


@router.put(
    "/{note_id}",
    responses={
        200: {"description": "卡片更新成功"},
        404: {"model": ErrorResponse, "description": "找不到該卡片"},
        502: {"model": ErrorResponse, "description": "Anki 服務異常"},
    },
    summary="更新卡片欄位",
    description="更新卡片內容。若修改了 Expression 等主要欄位，後端會自動同步更新 SQLite 中的關聯紀錄。",
)
async def update_card(
    note_id: int,
    request: CardUpdateRequest,
    card_service: CardService = Depends(get_card_service),
) -> dict[str, str]:
    """更新卡片欄位的 Controller 端點。"""
    await card_service.update_card(note_id, request.fields)
    return {"message": "卡片更新成功"}


@router.delete(
    "/{note_id}",
    responses={
        200: {"description": "卡片刪除成功"},
        404: {"model": ErrorResponse, "description": "找不到該卡片"},
        502: {"model": ErrorResponse, "description": "Anki 服務異常"},
    },
    summary="刪除卡片",
    description="從 Anki 刪除該卡片，並連動刪除關聯資料庫中所有跟這張卡片有關的知識圖譜連線 (Orphans cleanup)。",
)
async def delete_card(
    note_id: int,
    card_service: CardService = Depends(get_card_service),
) -> dict[str, str]:
    """刪除卡片的 Controller 端點。"""
    await card_service.delete_card(note_id)
    return {"message": "卡片刪除成功"}




