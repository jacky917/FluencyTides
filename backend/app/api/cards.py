"""Card browsing and Anki media API.

卡片瀏覽與 Anki 媒體 API。

Web UI plan web_ui_parity_FEAT_2026-10-18.md, phase A.

- ``GET /api/v1/cards``: the card list of any deck (including subdecks), optionally filtered by an
  Anki search query
- ``GET /api/v1/cards/{card_id}``: one card and its front and back as rendered by Anki
- ``GET /api/v1/anki-media/{filename}``: a file in Anki's media folder (served only with audio,
  video, or image types)

Does not go through task handlers, so any note type can be browsed. Authentication is the same as
the other APIs (``verify_api_key``). Business errors are turned into ``{"error_code", "message"}``
by the global handler: AnkiConnect unreachable or reporting an error 502 (``ANKI_SERVICE_ERROR``),
search query rejected by Anki 400 (``INVALID_ANKI_SEARCH``), card or media file not found 404,
media file name containing a path 400. Query parameters in the wrong format (for example
``page_size`` > 200) get FastAPI's own 422 (``detail``).

網頁版計畫 web_ui_parity_FEAT_2026-10-18.md 階段 A。

- ``GET /api/v1/cards``                    任一牌組（含子牌組）的卡片清單，可加 Anki 搜尋語句
- ``GET /api/v1/cards/{card_id}``          單一卡片與 Anki 渲染好的正反面
- ``GET /api/v1/anki-media/{filename}``    Anki 媒體資料夾中的檔案（只以音訊、影片、圖片類型回應）

不經過 task handler，任何筆記類型都能瀏覽。認證與其他 API 相同（``verify_api_key``）。
業務錯誤由全域 handler 轉成 ``{"error_code", "message"}``：AnkiConnect 連不上或回報錯誤
502（``ANKI_SERVICE_ERROR``）、搜尋語句被 Anki 拒絕 400（``INVALID_ANKI_SEARCH``）、卡片或
媒體檔不存在 404、媒體檔名含路徑 400；查詢參數格式不符（例如 ``page_size`` > 200）則是
FastAPI 自己的 422（``detail``）。
"""

from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from app.core.auth import verify_api_key
from app.core.dependencies import get_anki_client
from app.infrastructure.anki.client import AnkiClient
from app.schemas.card import ErrorResponse
from app.schemas.card_browse import CardPage, CardView
from app.services import card_browse_service

router = APIRouter(tags=["Cards"], dependencies=[Depends(verify_api_key)])

_ANKI_ERROR = {502: {"model": ErrorResponse, "description": "AnkiConnect 連不上或回傳錯誤"}}


@router.get(
    "/cards",
    response_model=CardPage,
    summary="列出牌組中的卡片",
    responses={400: {"model": ErrorResponse, "description": "搜尋語句被 Anki 拒絕"}, **_ANKI_ERROR},
)
async def list_cards(
    deck: str | None = Query(None, description="牌組完整名稱（含子牌組）；省略為全部牌組"),
    q: str | None = Query(None, description="額外的 Anki 搜尋語句，例如 note:Speaking_Coach_Dark"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    note_type: list[str] | None = Query(None, description="只列這些筆記類型（可重複）；省略為不限"),
    depth: int = Query(0, ge=0, le=20, description="包含幾層牌組：1 為牌組本身、2 再加直接的子牌組；0 為全部"),
    anki: AnkiClient = Depends(get_anki_client),
) -> CardPage:
    """Return a page of cards, newest first, without the card face HTML.

    回傳一頁卡片（新到舊），不含卡面 HTML。

    Args:
        deck: the full deck name (subdecks included); ``None`` means all decks
            牌組完整名稱（含子牌組）；``None`` 為全部牌組
        q: an extra Anki search query; ``None`` means no extra filter
            額外的 Anki 搜尋語句；``None`` 為不額外篩選
        page: the 1-based page number
            頁碼，從 1 起算
        page_size: the number of cards per page, 1 to 200
            每頁卡片數，1 到 200
        note_type: only these note types (repeatable); ``None`` means any note type
            只列這些筆記類型（可重複）；``None`` 為不限
        depth: how many deck levels to include, 0 to 20: ``1`` is the deck itself, ``2`` adds its direct sub-decks;
            ``0`` means all of them
            包含幾層牌組，0 到 20：``1`` 為牌組本身，``2`` 再加直接的子牌組；``0`` 為全部
        anki: the AnkiConnect client
            AnkiConnect 客戶端

    Returns:
        the requested page of cards
        所要求的那一頁卡片
    """
    return await card_browse_service.browse_cards(anki, deck, q, page, page_size, note_type, depth)


@router.get(
    "/cards/{card_id}",
    response_model=CardView,
    summary="讀取單一卡片與卡面",
    responses={404: {"model": ErrorResponse, "description": "卡片不存在"}, **_ANKI_ERROR},
)
async def read_card(card_id: int, anki: AnkiClient = Depends(get_anki_client)) -> CardView:
    """Return one card with its front and back HTML as rendered by Anki.

    回傳單一卡片與 Anki 渲染好的正反面 HTML。

    Play tags in the faces have been replaced with play buttons.

    播放標記已換成播放鈕。

    Args:
        card_id: the Anki card ID
            Anki 卡片 ID
        anki: the AnkiConnect client
            AnkiConnect 客戶端

    Returns:
        the card and its rendered faces
        卡片與渲染好的卡面
    """
    return await card_browse_service.get_card(anki, card_id)


@router.get(
    "/anki-media/{filename:path}",
    summary="讀取 Anki 媒體檔",
    response_class=Response,
    responses={
        200: {"content": {"application/octet-stream": {}}, "description": "檔案內容"},
        400: {"model": ErrorResponse, "description": "檔名含路徑"},
        404: {"model": ErrorResponse, "description": "檔案不存在"},
        **_ANKI_ERROR,
    },
)
async def read_anki_media(filename: str, anki: AnkiClient = Depends(get_anki_client)) -> Response:
    """Return one file from Anki's media folder.

    回傳 Anki 媒體資料夾中的單一檔案。

    The ``{filename:path}`` converter captures requests whose names contain slashes, so the service
    layer can reject them with a 400.

    以 ``{filename:path}`` 接住含斜線的請求，由服務層回 400。

    This endpoint is same-origin with the web page, so every response carries ``nosniff`` and
    ``CSP: sandbox``: even if someone opens a media file directly, the browser will not treat it as
    an executable page.

    這個端點與網頁同源，回應一律帶 ``nosniff`` 與 ``CSP: sandbox``：即使有人直接開啟某個
    媒體檔，瀏覽器也不會把它當成可執行的頁面。

    Args:
        filename: the media file name; must not contain a path
            媒體檔名，不可含路徑
        anki: the AnkiConnect client
            AnkiConnect 客戶端

    Returns:
        the file content with its media type and the security and cache headers
        檔案內容，帶媒體類型以及安全與快取標頭
    """
    content, media_type = await card_browse_service.read_media(anki, filename)
    headers = {
        # 媒體檔名多半帶時間戳、內容不變；仍只快取一小時，以免覆寫同名檔後一直看到舊的
        "Cache-Control": "private, max-age=3600",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox; default-src 'none'",
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
    }
    return Response(content=content, media_type=media_type, headers=headers)
