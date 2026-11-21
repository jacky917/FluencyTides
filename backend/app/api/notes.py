"""Generic note read and the deck list.

通用的筆記讀取與牌組清單。

Handlers plan D5, §4.3, §4.7.

- ``GET /notes/{note_id}``: any note type: fields, the responsible handler, and whether it can be
  edited or deleted (``permissions``)
- ``GET /decks``: all decks (formerly ``/handlers/decks``)
- ``POST /decks``: create a deck (deck_create plan §5.3)
- ``POST /anki/sync``: sync Anki with AnkiWeb (the deck tree's refresh button calls it first, then
  refetches the decks)

Not routed through a handler; after the front end gets ``handler`` from ``GET /notes/{note_id}``, it
builds the ``/handlers/{name}/notes/...`` URLs. Authentication is the same as the other APIs
(``verify_api_key``).

handlers 計畫書 D5、§4.3、§4.7。

- ``GET /notes/{note_id}``  任何筆記類型：欄位、負責的 handler、能不能改或刪（``permissions``）
- ``GET /decks``             所有牌組（原本的 ``/handlers/decks``）
- ``POST /decks``            新增牌組（deck_create 計畫書 §5.3）
- ``POST /anki/sync``        Anki 與 AnkiWeb 同步（牌組樹的重新整理按鈕先呼叫它再重抓牌組）

不經 handler；前端從 ``GET /notes/{note_id}`` 拿到 ``handler`` 後，再組 ``/handlers/{name}/notes/...``
的網址。認證與其他 API 相同（``verify_api_key``）。
"""

from fastapi import APIRouter, Depends, Response, status

from app.core.auth import verify_api_key
from app.core.dependencies import get_anki_client, get_note_service
from app.infrastructure.anki.client import AnkiClient
from app.schemas.card import ErrorResponse
from app.schemas.notes import CreateDeckRequest, CreateDeckResponse, DeckInfo, GenericNoteDetail
from app.services.deck_service import DeckService
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
    """Read any note, with its fields in the note type's order, its handler, and its permissions.

    通用讀取：欄位依筆記類型的順序，附上負責的 handler 與權限。

    Args:
        note_id: the Anki note ID
            Anki 筆記 ID
        note_service: the note service
            筆記服務

    Returns:
        the note's fields, responsible handler, and permissions
        筆記的欄位、負責的 handler 與權限
    """
    return await note_service.get(note_id)


@router.get(
    "/decks",
    response_model=list[DeckInfo],
    tags=["Decks"],
    summary="列出所有 Anki 牌組",
    responses={502: {"model": ErrorResponse, "description": "AnkiConnect 連不上或回傳錯誤"}},
)
async def list_decks(anki_client: AnkiClient = Depends(get_anki_client)) -> list[dict[str, object]]:
    """Return every deck's name and ID.

    回傳所有牌組的名稱與 ID。

    Args:
        anki_client: the AnkiConnect client
            AnkiConnect 客戶端

    Returns:
        one ``{"deck_name", "deck_id"}`` dict per deck
        每個牌組一個 ``{"deck_name", "deck_id"}`` 字典
    """
    decks = await anki_client.get_deck_names_and_ids()
    return [{"deck_name": name, "deck_id": deck_id} for name, deck_id in decks.items()]


@router.post(
    "/decks",
    response_model=CreateDeckResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Decks"],
    summary="新增 Anki 牌組",
    responses={
        400: {"model": ErrorResponse, "description": "牌組名稱不合格（空的、某一層是空的、太長）"},
        409: {"model": ErrorResponse, "description": "同名的牌組已存在"},
        502: {"model": ErrorResponse, "description": "AnkiConnect 連不上，或 Anki 拒絕這個名稱"},
    },
)
async def create_deck(
    body: CreateDeckRequest, anki_client: AnkiClient = Depends(get_anki_client)
) -> dict[str, object]:
    """Create a deck and sync with AnkiWeb; ``A::B::C`` creates its parents too.

    新增牌組並同步 AnkiWeb；``A::B::C`` 會連上層一起建立。

    If the sync fails, the deck has still been created: the response is 201 with a ``sync_warning``.

    同步失敗時牌組仍已建立：回 201，並以 ``sync_warning`` 提醒。

    Args:
        body: the new deck's full name
            新牌組的完整名稱
        anki_client: the AnkiConnect client
            AnkiConnect 客戶端

    Returns:
        the created deck and the sync warning, if any
        建立的牌組，以及同步提醒（若有）

    Raises:
        AnkiConnectError: if Anki is unreachable or refuses the name (502)
            若連不上 Anki 或 Anki 拒絕（502）
        DeckExistsError: if a deck with the same name already exists (409)
            若同名的牌組已存在（409）
        DeckNameInvalidError: if the name is invalid (400)
            若名稱不合格（400）
    """
    return await DeckService(anki_client).create(body.name)


@router.post(
    "/anki/sync",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    tags=["Decks"],
    summary="Anki 與 AnkiWeb 同步",
    responses={502: {"model": ErrorResponse, "description": "AnkiConnect 連不上，或與 AnkiWeb 同步失敗（含同步衝突）"}},
)
async def sync_anki(anki_client: AnkiClient = Depends(get_anki_client)) -> Response:
    """Sync Anki with AnkiWeb now and wait for it to finish.

    立即同步並等它完成。

    Bypasses the 10-second debounce and reports failures as 502 instead of swallowing them.

    略過 10 秒防抖，失敗時回 502（不吞錯）。

    Args:
        anki_client: the AnkiConnect client
            AnkiConnect 客戶端

    Returns:
        an empty 204 response
        空的 204 回應

    Raises:
        AnkiConnectError: if Anki is unreachable or the sync fails (502)
            若連不上 Anki 或同步失敗（502）
    """
    await anki_client.sync(raise_errors=True, force=True)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
