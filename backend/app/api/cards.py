"""卡片瀏覽與 Anki 媒體 API（網頁版計畫 web_ui_parity_FEAT_2026-10-18.md 階段 A）。

Card browsing and Anki media API.

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
    anki: AnkiClient = Depends(get_anki_client),
) -> CardPage:
    """一頁卡片（新到舊），不含卡面 HTML。A page of cards, newest first."""
    return await card_browse_service.browse_cards(anki, deck, q, page, page_size)


@router.get(
    "/cards/{card_id}",
    response_model=CardView,
    summary="讀取單一卡片與卡面",
    responses={404: {"model": ErrorResponse, "description": "卡片不存在"}, **_ANKI_ERROR},
)
async def read_card(card_id: int, anki: AnkiClient = Depends(get_anki_client)) -> CardView:
    """單一卡片與 Anki 渲染好的正反面 HTML（播放標記已換成播放鈕）。One card with its faces."""
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
    """Anki 媒體資料夾中的單一檔案；以 ``{filename:path}`` 接住含斜線的請求，由服務層回 400。

    One file from Anki's media folder. The ``:path`` converter captures
    names with slashes so the service can reject them with a 400.

    這個端點與網頁同源，回應一律帶 ``nosniff`` 與 ``CSP: sandbox``：即使有人直接開啟某個
    媒體檔，瀏覽器也不會把它當成可執行的頁面。
    Served same-origin, so responses carry ``nosniff`` and a sandbox CSP.
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
