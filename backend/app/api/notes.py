"""
通用的筆記讀取與牌組清單（handlers 計畫書 D5、§4.3、§4.7）。

Generic note read and the deck list.

- ``GET /notes/{note_id}``   任何筆記類型：欄位、負責的 handler、能不能改或刪（``permissions``）
- ``GET /decks``             所有牌組（原本的 ``/handlers/decks``）
- ``POST /anki/sync``        Anki 與 AnkiWeb 同步（牌組樹的重新整理按鈕先呼叫它再重抓牌組）

不經 handler；前端從 ``GET /notes/{note_id}`` 拿到 ``handler`` 後，再組 ``/handlers/{name}/notes/...``
的網址。認證與其他 API 相同（``verify_api_key``）。

Not routed through a handler; the frontend reads ``handler`` here and then
builds the ``/handlers/{name}/notes/...`` URLs.
"""

from fastapi import APIRouter, Depends, Response, status

from app.core.auth import verify_api_key
from app.core.dependencies import get_anki_client, get_note_service
from app.infrastructure.anki.client import AnkiClient
from app.schemas.card import ErrorResponse
from app.schemas.notes import DeckInfo, GenericNoteDetail
from app.services.note_service import NoteService

router = APIRouter(dependencies=[Depends(verify_api_key)])


@router.get(
    "/notes/{note_id}",
    response_model=GenericNoteDetail,
    tags=["Notes"],
    summary="讀取任一筆記",
    responses={
        404: {"model": ErrorResponse, "description": "筆記不存在"},
        502: {"model": ErrorResponse, "description": "AnkiConnect 連不上或回傳錯誤"},
    },
)
async def read_note(note_id: int, note_service: NoteService = Depends(get_note_service)) -> dict[str, object]:
    """通用讀取：欄位依筆記類型的順序，附上負責的 handler 與權限。The generic read."""
    return await note_service.get(note_id)


@router.get(
    "/decks",
    response_model=list[DeckInfo],
    tags=["Decks"],
    summary="列出所有 Anki 牌組",
    responses={502: {"model": ErrorResponse, "description": "AnkiConnect 連不上或回傳錯誤"}},
)
async def list_decks(anki_client: AnkiClient = Depends(get_anki_client)) -> list[dict[str, object]]:
    """所有牌組的名稱與 ID。Every deck's name and ID."""
    decks = await anki_client.get_deck_names_and_ids()
    return [{"deck_name": name, "deck_id": deck_id} for name, deck_id in decks.items()]


@router.post(
    "/anki/sync",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    tags=["Decks"],
    summary="Anki 與 AnkiWeb 同步",
    responses={502: {"model": ErrorResponse, "description": "AnkiConnect 連不上，或與 AnkiWeb 同步失敗（含同步衝突）"}},
)
async def sync_anki(anki_client: AnkiClient = Depends(get_anki_client)) -> Response:
    """立即同步並等它完成；略過 10 秒防抖，失敗時回 502（不吞錯）。

    Sync Anki with AnkiWeb now and wait for it; bypasses the debounce and
    reports failures as 502 instead of swallowing them.

    Raises:
        AnkiConnectError: 502——連不上 Anki 或同步失敗。Anki unreachable or the sync failed.
    """
    await anki_client.sync(raise_errors=True, force=True)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
