"""Card relation REST API router module.

卡片關聯 REST API 路由模組。

Provides knowledge-graph queries and manual CRUD endpoints for relation data.
Follows Clean Architecture: all business logic is delegated to RelationService.

提供知識圖譜查詢與關聯資料的手動增刪改查介面。
遵守 Clean Architecture，所有業務邏輯委託給 RelationService 執行。
"""

import logging

from fastapi import APIRouter, Depends

from app.infrastructure.anki.search import deck_term
from app.core.auth import verify_api_key
from app.core.dependencies import get_anki_client, get_relation_service
from app.infrastructure.anki.client import AnkiClient
from app.schemas.card import ErrorResponse
from app.schemas.relation import (
    CardRelationCreate,
    CardRelationDelete,
    CardRelationRead,
    RelationSyncResponse,
)
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
    """Return the knowledge graph data.

    取得知識圖譜資料。

    Args:
        deck_name: the deck to filter by; ``None`` means all decks
            篩選特定牌組的名稱；``None`` 為全部牌組
        anki_client: the injected AnkiClient instance
            注入的 AnkiClient 實例
        relation_service: the injected RelationService instance
            注入的 RelationService 實例

    Returns:
        the graph data ``{"nodes": [...], "links": [...]}``
        圖譜資料字典 ``{"nodes": [...], "links": [...]}``
    """
    query = deck_term(deck_name) if deck_name else "deck:*"
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
    """Create a card relation manually.

    手動建立卡片關聯。

    Args:
        request: the relation creation DTO
            關聯建立 DTO
        relation_service: the injected RelationService instance
            注入的 RelationService 實例

    Returns:
        the created relation record
        已建立的關聯資料
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
    """Return all registered relation type names.

    取得所有已註冊的關聯類型名稱。

    Args:
        relation_service: the injected RelationService instance
            注入的 RelationService 實例

    Returns:
        the relation type names
        關聯類型名稱串列
    """
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
    """Delete the specified relation between two nodes.

    精準刪除兩個節點之間的指定關聯。

    Args:
        request: the relation deletion DTO
            關聯刪除 DTO
        relation_service: the injected RelationService instance
            注入的 RelationService 實例

    Returns:
        the deletion summary, for example ``{"deleted_count": 2}``
        刪除結果摘要，例如 ``{"deleted_count": 2}``
    """
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
    """Delete every relation that involves a removed note.

    連動刪除與已刪除筆記相關的所有關聯。

    Args:
        note_id: the target Anki note ID
            目標 Anki Note ID
        relation_service: the injected RelationService instance
            注入的 RelationService 實例

    Returns:
        the deletion summary, for example ``{"deleted_count": 2}``
        刪除結果摘要，例如 ``{"deleted_count": 2}``
    """
    deleted_count = await relation_service.delete_relations_by_note_id(note_id)
    return {"deleted_count": deleted_count}


@router.post(
    "/sync",
    response_model=RelationSyncResponse,
    responses={
        409: {
            "model": ErrorResponse,
            "description": (
                "已停止清理、未刪除任何資料：`RELATION_SYNC_EMPTY_ANKI`（Anki 回傳 0 張筆記，force 也無效）、"
                "`RELATION_SYNC_NEEDS_FORCE`（要刪超過一半，確認後帶 force=true 重送）"
            ),
        },
        502: {"model": ErrorResponse, "description": "Anki 服務異常"},
    },
    summary="同步清理孤兒關聯",
    description=(
        "向 Anki 查詢目前所有存在的筆記 ID，刪除資料庫中引用已不存在筆記的關聯。"
        "Anki 回傳 0 張筆記時一律拒絕；要刪超過一半（且超過 20 筆）時需帶 force=true。"
    ),
)
async def sync_relations(
    force: bool = False,
    anki_client: AnkiClient = Depends(get_anki_client),
    relation_service: RelationService = Depends(get_relation_service),
) -> RelationSyncResponse:
    """Sync with Anki and prune orphan relations.

    與 Anki 同步並清理孤兒關聯。

    Args:
        force: delete even when more than half would go (an Anki answer of 0 notes is still refused)
            要刪超過一半時仍執行（Anki 回傳 0 張筆記時照樣拒絕）
        anki_client: the injected AnkiClient instance
            注入的 AnkiClient 實例
        relation_service: the injected RelationService instance
            注入的 RelationService 實例

    Returns:
        the result summary
        結果摘要

    Raises:
        AnkiConnectError: if Anki is unreachable or reports an error; nothing is deleted (502)
            若連不上 Anki 或 Anki 回報錯誤；不刪任何資料（502）
        RelationSyncEmptyAnkiError: if Anki returns 0 notes; ``force`` does not help (409)
            若 Anki 回傳 0 張筆記；``force`` 也無效（409）
        RelationSyncNeedsForceError: if more than half would be deleted and ``force`` is not set (409)
            若要刪超過一半且未帶 ``force``（409）
    """
    result = await relation_service.sync_with_anki(anki_client, force=force)
    return RelationSyncResponse(
        deleted_count=result.deleted,
        scanned_notes=result.scanned_notes,
        orphan_notes=result.orphan_notes,
        ankiweb_synced=result.ankiweb_synced,
    )
