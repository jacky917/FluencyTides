"""
卡片關聯 REST API 路由模組。

提供知識圖譜查詢與關聯資料的手動增刪改查介面。
遵守 Clean Architecture，所有業務邏輯委託給 RelationService 執行。
"""

import logging

from fastapi import APIRouter, Depends

from app.core.auth import verify_api_key
from app.core.dependencies import get_anki_client, get_relation_service
from app.infrastructure.anki.client import AnkiClient
from app.schemas.card import ErrorResponse
from app.schemas.relation import CardRelationCreate, CardRelationRead, CardRelationDelete
from app.services.relation_service import RelationService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/relations",
    tags=["Relations"],
    dependencies=[Depends(verify_api_key)],
)


@router.get(
    "/graph",
    response_model=dict[str, list[dict]],
    summary="取得全域知識圖譜資料",
    description="從關聯資料庫中高效讀取節點與連線。若指定 deck_name，將透過 Anki 篩選特定牌組的卡片關聯。",
)
async def get_graph_data(
    deck_name: str | None = None,
    anki_client: AnkiClient = Depends(get_anki_client),
    relation_service: RelationService = Depends(get_relation_service),
) -> dict[str, list[dict]]:
    """取得知識圖譜資料的 Controller 端點。

    Args:
        deck_name: (可選) 篩選特定牌組的名稱。
        anki_client: 注入的 AnkiClient 實例。
        relation_service: 注入的 RelationService 實例。

    Returns:
        圖譜資料字典: {"nodes": [...], "links": [...]}
    """
    query = f'deck:"{deck_name}"' if deck_name else "deck:*"
    note_ids = await anki_client.find_notes(query)
    
    notes_info = []
    cards_info = []
    if note_ids:
        # 取得這些卡片的詳細資訊 (用來建立包含翻譯的孤立節點)
        notes_info = await anki_client.get_notes_info(notes=note_ids)
        
        # 取得對應卡片的狀態 (用來上色)
        card_ids = [n.cards[0] for n in notes_info if n.cards]
        if card_ids:
            cards_info = await anki_client.get_cards_info(cards=card_ids)
        
    return await relation_service.get_graph_data(notes_info, cards_info)


@router.post(
    "/",
    response_model=CardRelationRead,
    summary="手動建立卡片關聯",
    description="手動新增一筆有向關聯，允許 target_note_id 為空（建立懸空節點）。",
)
async def create_relation(
    request: CardRelationCreate,
    relation_service: RelationService = Depends(get_relation_service),
) -> CardRelationRead:
    """手動建立關聯的 Controller 端點。

    Args:
        request: 關聯建立 DTO。
        relation_service: 注入的 RelationService 實例。

    Returns:
        已建立的關聯資料。
    """
    return await relation_service.create_relation(request)


@router.get(
    "/types",
    response_model=list[str],
    summary="取得所有自訂關聯類型",
    description="取得系統中已註冊的所有關聯類型名稱，供前端下拉選單使用。",
)
async def get_relation_types(
    relation_service: RelationService = Depends(get_relation_service),
) -> list[str]:
    """取得關聯類型的 Controller 端點。"""
    return await relation_service.get_all_relation_types()


@router.post(
    "/delete",
    response_model=dict[str, int],
    summary="刪除卡片關聯",
    description="精準刪除兩個節點之間的特定關係（若為雙向，將一併刪除 A->B 與 B->A）。",
)
async def delete_relation_by_nodes(
    request: CardRelationDelete,
    relation_service: RelationService = Depends(get_relation_service),
) -> dict[str, int]:
    """刪除指定關聯的 Controller 端點。"""
    return await relation_service.delete_relation_by_nodes(request)


@router.delete(
    "/by-note/{note_id}",
    response_model=dict[str, int],
    summary="連動刪除卡片關聯",
    description="當 Anki 中的卡片被刪除時，呼叫此端點清除資料庫中所有與該卡片相關的死連結（無論是起點還是終點）。",
)
async def delete_relations_by_note_id(
    note_id: int,
    relation_service: RelationService = Depends(get_relation_service),
) -> dict[str, int]:
    """連動刪除卡片關聯的 Controller 端點。

    Args:
        note_id: 目標 Anki Note ID。
        relation_service: 注入的 RelationService 實例。

    Returns:
        刪除結果摘要，例如 {"deleted_count": 2}。
    """
    deleted_count = await relation_service.delete_relations_by_note_id(note_id)
    return {"deleted_count": deleted_count}


@router.post(
    "/sync",
    response_model=dict[str, int],
    responses={
        502: {"model": ErrorResponse, "description": "Anki 服務異常"},
    },
    summary="同步清理孤兒關聯",
    description="向 Anki 查詢目前所有存在的筆記 ID，並刪除資料庫中已經不存在於 Anki 的孤兒關聯記錄。",
)
async def sync_relations(
    anki_client: AnkiClient = Depends(get_anki_client),
    relation_service: RelationService = Depends(get_relation_service),
) -> dict[str, int]:
    """同步清理孤兒關聯的 Controller 端點。

    Args:
        anki_client: 注入的 AnkiClient 實例。
        relation_service: 注入的 RelationService 實例。

    Returns:
        刪除結果摘要，例如 {"deleted_count": 2}。
    """
    # 查詢所有 Anki 筆記，取得所有有效的 Note ID
    # 註解: 'deck:*' 可涵蓋所有牌組內的卡片
    valid_note_ids = await anki_client.find_notes("deck:*")
    
    # 交由 RelationService 進行比對與刪除
    deleted_count = await relation_service.sync_with_anki(valid_note_ids)
    
    return {"deleted_count": deleted_count}
