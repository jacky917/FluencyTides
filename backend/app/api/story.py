"""Story flowchart API.

劇情流程圖 API。

Plan docs/wip/story_flowchart_FEAT_2026-10-31.md D11. All endpoints live under ``/api/v1/story`` and
require ``verify_api_key``; business errors become ``{"error_code", "message"}``.

- ``GET  /story/games``: the game list and capabilities
- ``GET  /story/games/{game}``: the manifest
- ``GET  /story/games/{game}/graph?build=``: the whole story graph (``ETag`` from ``build_id``)
- ``GET  /story/games/{game}/annotations?kinds=cards``: node annotation counts and the search index
- ``GET  /story/games/{game}/nodes/{node_key}/lines``: a node's lines (paged)
- ``GET  /story/games/{game}/locate?note_id=|line_id=``: the position of a line or a note
- ``POST /story/games/{game}/saves``: generate a slot save file
- ``GET  /story/games/{game}/thumbs/{hash}``: thumbnails (read by the backend, see below)
- ``GET  /story/games/{game}/voices/{voice}``: line voice MP3 (same as above; ``voice`` is
  ``game_lines.voice_file``)
- ``GET  /story/games/{game}/bgm/{code}``: BGM audio (reserved; ``code`` is ``game_bgm_tracks.code``)

``{game}`` must match ``[A-Za-z0-9_-]{1,64}``; anything else is a 404 (it goes into asset object
keys).

Thumbnails do not use the design document's "302 redirect to a MinIO presigned URL": the MinIO host
is on the internal network or 127.0.0.1, which browsers cannot reach. Instead the backend reads the
file and returns it with a one-year ``immutable`` cache (the file name is the content hash).

Voices are also read by the backend. The file name is ``game_lines.voice_file`` (not a content
hash), but a voice with the same name never changes content, so it also gets a one-year
``immutable`` cache; names must match ``[A-Za-z0-9_]{1,64}`` (optionally with ``.mp3``), anything
else is a 404.

The BGM endpoint is reserved: there are no audio files yet (they are in the game's encrypted
archives, which are not unpacked), so files are served only when the active build's settings enable
``bgm_audio`` (the same switch as ``capabilities.bgm``); otherwise it is always a 404. Codes must
match ``[A-Za-z0-9_]{1,32}`` without an extension; the backend tries ``story/{game}/bgm/{code}.ogg``
then ``.mp3``, and ``Content-Type`` follows the file found. Likewise, files are not kept in the
memory cache and get a one-year ``immutable`` cache.

計畫 docs/wip/story_flowchart_FEAT_2026-10-31.md D11。所有端點都在 ``/api/v1/story`` 之下、需要
``verify_api_key``；業務錯誤轉成 ``{"error_code", "message"}``。

- ``GET  /story/games``                                 遊戲清單與能力
- ``GET  /story/games/{game}``                           manifest
- ``GET  /story/games/{game}/graph?build=``              整份劇情圖（``ETag = build_id``）
- ``GET  /story/games/{game}/annotations?kinds=cards``   節點註記統計與搜尋索引
- ``GET  /story/games/{game}/nodes/{node_key}/lines``    節點的台詞（分頁）
- ``GET  /story/games/{game}/locate?note_id=|line_id=``  台詞或筆記的位置
- ``POST /story/games/{game}/saves``                     產生欄位存檔
- ``GET  /story/games/{game}/thumbs/{hash}``             縮圖（後端代讀，見下）
- ``GET  /story/games/{game}/voices/{voice}``            台詞語音 MP3（同上，``voice`` 是 ``game_lines.voice_file``）
- ``GET  /story/games/{game}/bgm/{code}``                BGM 音檔（預留；``code`` 是 ``game_bgm_tracks.code``）

``{game}`` 只接受 ``[A-Za-z0-9_-]{1,64}``，其他一律 404（它會進素材的物件鍵）。

縮圖不用設計書的「302 導向 MinIO 預簽名網址」：MinIO 的位址是內網或 127.0.0.1，瀏覽器連不到；
改由後端讀出後回傳，並帶一年的 ``immutable`` 快取（檔名就是內容雜湊）。

語音也一樣由後端代讀。檔名是 ``game_lines.voice_file``（不是內容雜湊），但同一個名稱的語音不會換內容，
所以同樣帶一年的 ``immutable`` 快取；名稱只接受 ``[A-Za-z0-9_]{1,64}``（可帶 ``.mp3``），其他一律 404。

BGM 是預留的端點：音檔還沒有（在遊戲的加密封包裡，不解包），所以只有上線版本的設定打開
``bgm_audio`` 時才提供（``capabilities.bgm`` 同一個開關），否則一律 404。代號只接受
``[A-Za-z0-9_]{1,32}``、不帶副檔名；後端依序找 ``story/{game}/bgm/{code}.ogg``、``.mp3``，
``Content-Type`` 跟著找到的檔案。同樣不進記憶體快取、帶一年的 ``immutable`` 快取。
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response

from app.core.auth import verify_api_key
from app.schemas.card import ErrorResponse
from app.schemas.story import SaveRequest, StoryAnnotations, StoryGameSummary, StoryLinesPage, StoryLocation, StoryManifest
from app.services.story.assets import StoryAssetStore
from app.services.story.repository import MySQLStoryRepository
from app.services.story.service import MAX_LINES_PAGE, StoryService

router = APIRouter(prefix="/story", tags=["Story"], dependencies=[Depends(verify_api_key)])

_NOT_FOUND = {404: {"model": ErrorResponse, "description": "遊戲、版本、節點或台詞不存在"}}
_UNAVAILABLE = {503: {"model": ErrorResponse, "description": "素材來源（本機資料夾或 MinIO）暫時讀不到"}}
_IMMUTABLE = {"Cache-Control": "private, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"}


def get_story_service(request: Request) -> StoryService:
    """Return the story service shared by the whole app, creating it on first use.

    回傳整個應用程式共用的劇情服務，第一次使用時建立。

    One service per app, because the cache lives in it.

    應用程式共用一個服務（快取跟著它）。

    Args:
        request: the current request, used to reach ``app.state``
            目前的請求，用來取得 ``app.state``

    Returns:
        the shared story service
        共用的劇情服務
    """
    service = getattr(request.app.state, "story_service", None)
    if service is None:
        from app.core.config import settings

        assets = StoryAssetStore(minio_client=getattr(request.app.state, "minio_client", None),
                                 bucket=getattr(settings, "MINIO_DEFAULT_BUCKET", None))
        service = StoryService(MySQLStoryRepository(), assets)
        request.app.state.story_service = service
    return service


@router.get("/games", response_model=list[StoryGameSummary], summary="有流程圖的遊戲")
async def list_games(service: StoryService = Depends(get_story_service)) -> list[StoryGameSummary]:
    """Return the games with an active build, their capabilities, and card types.

    回傳上線中的遊戲與能力、卡片類型。

    Card types are used by the card page to look games up.

    卡片類型供卡片頁反查用。

    Args:
        service: the shared story service
            共用的劇情服務

    Returns:
        one summary per game
        每款遊戲一筆摘要
    """
    return await service.list_games()


@router.get("/games/{game_id}", response_model=StoryManifest, summary="遊戲的 manifest", responses=_NOT_FOUND)
async def read_manifest(game_id: str, service: StoryService = Depends(get_story_service)) -> StoryManifest:
    """Return the game manifest.

    回傳遊戲的 manifest。

    Args:
        game_id: the game ID
            遊戲 ID
        service: the shared story service
            共用的劇情服務

    Returns:
        the title, build, capabilities, languages, save slots, and default theme
        標題、版本、能力、語言、存檔欄位、預設主題
    """
    return await service.manifest(game_id)


@router.get("/games/{game_id}/graph", summary="整份劇情圖", responses=_NOT_FOUND)
async def read_graph(
    request: Request,
    game_id: str,
    build: int | None = Query(None, description="版本；省略為上線版本"),
    service: StoryService = Depends(get_story_service),
) -> Response:
    """Return the schema v1 story graph, with an ``ETag`` per build to support 304.

    回傳 schema v1 的劇情圖；同一個版本內容不變，以 ``ETag`` 支援 304。

    A request for an explicit ``build`` is cached for a year as ``immutable``; without ``build`` the
    active build may change, so the response is ``no-cache``.

    指定 ``build`` 時帶一年的 ``immutable`` 快取；省略 ``build`` 時上線版本可能會換，回應為 ``no-cache``。

    Args:
        request: the current request, used to read ``If-None-Match``
            目前的請求，用來讀 ``If-None-Match``
        game_id: the game ID
            遊戲 ID
        build: the build ID; ``None`` means the active build
            版本；``None`` 為上線版本
        service: the shared story service
            共用的劇情服務

    Returns:
        the graph JSON with ``build_id``, or an empty 304 if the ``ETag`` matches
        含 ``build_id`` 的劇情圖 JSON；``ETag`` 相符時為空的 304
    """
    build_id, graph = await service.graph(game_id, build)
    etag = f'"story-{game_id}-{build_id}"'
    headers = {"ETag": etag, "X-Story-Build": str(build_id),
               "Cache-Control": "private, max-age=31536000, immutable" if build is not None else "private, no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return JSONResponse({"build_id": build_id, **graph}, headers=headers)


@router.get("/games/{game_id}/annotations", response_model=StoryAnnotations, summary="節點註記統計",
            responses=_NOT_FOUND)
async def read_annotations(
    game_id: str,
    kinds: str | None = Query(None, description="逗號分隔，例 cards；省略為全部"),
    service: StoryService = Depends(get_story_service),
) -> StoryAnnotations:
    """Return the node annotation counts and the search index.

    回傳節點註記統計與搜尋索引。

    Args:
        game_id: the game ID
            遊戲 ID
        kinds: comma-separated annotation kinds, for example ``cards``; ``None`` means all kinds
            逗號分隔的註記種類，例 ``cards``；``None`` 為全部
        service: the shared story service
            共用的劇情服務

    Returns:
        ``nodes``: {node_key: {kind: {subcategory: count}}}; ``index``: {kind: {index: {term:
        [node_key]}}}
        ``nodes``：{node_key: {kind: {子分類: 數量}}}；``index``：{kind: {索引: {詞: [node_key]}}}
    """
    wanted = [k.strip() for k in kinds.split(",") if k.strip()] if kinds else None
    return await service.annotations(game_id, wanted)


@router.get("/games/{game_id}/nodes/{node_key}/lines", response_model=StoryLinesPage, summary="節點的台詞",
            responses=_NOT_FOUND)
async def read_lines(
    game_id: str,
    node_key: str,
    offset: int = Query(0, ge=0, description="從第幾句開始（seq，0 起算）"),
    limit: int = Query(200, ge=1, le=MAX_LINES_PAGE, description=f"這頁最多幾句（上限 {MAX_LINES_PAGE}）"),
    annotated_after: int | None = Query(
        None, ge=-1,
        description="給了才計算 next_annotated_seq：seq 大於此值的台詞中，第一句有註記的 seq（-1 表示從頭找）；"
                    "與 offset/limit 這頁的內容無關"),
    service: StoryService = Depends(get_story_service),
) -> StoryLinesPage:
    """Return a page of lines, by ``seq``, with each line's annotations.

    回傳依 ``seq`` 分頁的台詞與每句的註記。

    ``offset``/``limit`` select ``lines``; ``annotated_after`` only affects ``next_annotated_seq``
    ("next line with a card" can ask for just the position with ``limit=1``).

    ``offset``／``limit`` 決定 ``lines``；``annotated_after`` 只影響 ``next_annotated_seq``
    （「下一句有卡」可以用 ``limit=1`` 只問位置）。

    Args:
        game_id: the game ID
            遊戲 ID
        node_key: the node whose lines to read
            要讀取台詞的節點
        offset: the first ``seq`` to return, 0-based
            從第幾句開始（seq，0 起算）
        limit: the maximum number of lines in this page, up to ``MAX_LINES_PAGE``
            這頁最多幾句，上限為 ``MAX_LINES_PAGE``
        annotated_after: when given, ``next_annotated_seq`` is the first annotated ``seq`` greater
            than this value (-1 searches from the start); ``None`` skips the calculation
            給了才計算 ``next_annotated_seq``：seq 大於此值的台詞中第一句有註記的 seq（-1 表示從頭找）；
            ``None`` 不計算

    Returns:
        the page of lines and ``next_annotated_seq``
        這頁台詞與 ``next_annotated_seq``
    """
    return await service.lines(game_id, node_key, offset, limit, annotated_after)


@router.get("/games/{game_id}/locate", response_model=StoryLocation, summary="台詞或筆記的位置",
            responses={400: {"model": ErrorResponse, "description": "沒有或同時給了 note_id、line_id"}, **_NOT_FOUND})
async def locate(
    game_id: str,
    note_id: int | None = Query(None),
    line_id: int | None = Query(None),
    service: StoryService = Depends(get_story_service),
) -> StoryLocation:
    """Locate a line or a note; a master card is located through its child cards' example sentences.

    找出台詞或筆記的位置；母卡經由子卡找例句。

    Args:
        game_id: the game ID
            遊戲 ID
        note_id: the Anki note ID to locate; give exactly one of ``note_id`` and ``line_id``
            要定位的 Anki 筆記 ID；``note_id`` 與 ``line_id`` 只能給一個
        line_id: the line ID to locate
            要定位的台詞 ID
        service: the shared story service
            共用的劇情服務

    Returns:
        ``{tab, node_key, line_id, seq}``
        ``{tab, node_key, line_id, seq}``
    """
    return await service.locate(game_id, note_id, line_id)


@router.post(
    "/games/{game_id}/saves",
    summary="產生欄位存檔",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}}, "description": "存檔檔案"},
               400: {"model": ErrorResponse, "description": "欄位或路線預設不認得"},
               409: {"model": ErrorResponse, "description": "這款遊戲或這句台詞不能存檔"}, **_NOT_FOUND,
               **_UNAVAILABLE},
)
async def create_save(game_id: str, body: SaveRequest, service: StoryService = Depends(get_story_service)) -> Response:
    """Generate a slot save file.

    產生欄位存檔。

    The variables are accumulated by the backend along the path (D13); the file name is in
    ``Content-Disposition``.

    變數由後端沿路徑累加（D13）；檔名放在 ``Content-Disposition``。

    Args:
        game_id: the game ID
            遊戲 ID
        body: the line, save slot, and route preset
            台詞、存檔欄位與路線預設
        service: the shared story service
            共用的劇情服務

    Returns:
        the save file as an attachment, not cached
        以附件形式回傳的存檔檔案，不快取
    """
    save = await service.make_save(game_id, body.line_id, body.slot, body.route_preset)
    disposition = f"attachment; filename=\"{save.filename}\"; filename*=UTF-8''{quote(save.filename)}"
    return Response(content=save.data, media_type=save.media_type,
                    headers={"Content-Disposition": disposition, "Cache-Control": "no-store"})


@router.get("/games/{game_id}/thumbs/{thumb}", summary="台詞縮圖", response_class=Response,
            responses={200: {"content": {"image/jpeg": {}}}, **_NOT_FOUND, **_UNAVAILABLE})
async def read_thumb(game_id: str, thumb: str, service: StoryService = Depends(get_story_service)) -> Response:
    """Return a thumbnail JPEG; the file name is the content hash, so it can be cached forever.

    回傳縮圖 JPEG（檔名即內容雜湊，可永久快取）。

    Args:
        game_id: the game ID
            遊戲 ID
        thumb: the content hash, optionally with ``.jpg``
            內容雜湊，可帶 ``.jpg``
        service: the shared story service
            共用的劇情服務

    Returns:
        the JPEG with an ``immutable`` cache header
        帶 ``immutable`` 快取標頭的 JPEG
    """
    data = await service.thumb(game_id, thumb.removesuffix(".jpg"))
    return Response(content=data, media_type="image/jpeg", headers=_IMMUTABLE)


@router.get("/games/{game_id}/voices/{voice}", summary="台詞語音", response_class=Response,
            responses={200: {"content": {"audio/mpeg": {}}}, **_NOT_FOUND, **_UNAVAILABLE})
async def read_voice(game_id: str, voice: str, service: StoryService = Depends(get_story_service)) -> Response:
    """Return a line's voice MP3.

    回傳台詞語音 MP3。

    Args:
        game_id: the game ID
            遊戲 ID
        voice: ``StoryLine.voice``, optionally with ``.mp3``
            ``StoryLine.voice``，可帶 ``.mp3``
        service: the shared story service
            共用的劇情服務

    Returns:
        the MP3 with an ``immutable`` cache header
        帶 ``immutable`` 快取標頭的 MP3
    """
    data = await service.voice(game_id, voice.removesuffix(".mp3"))
    return Response(content=data, media_type="audio/mpeg", headers=_IMMUTABLE)


@router.get("/games/{game_id}/bgm/{code}", summary="BGM 音檔（預留）", response_class=Response,
            responses={200: {"content": {"audio/ogg": {}, "audio/mpeg": {}}}, **_NOT_FOUND, **_UNAVAILABLE})
async def read_bgm(game_id: str, code: str, service: StoryService = Depends(get_story_service)) -> Response:
    """Return a BGM file; always a 404 when ``capabilities.bgm`` is false.

    回傳 BGM 音檔；``capabilities.bgm`` 為 false 時一律 404。

    Args:
        game_id: the game ID
            遊戲 ID
        code: ``StoryLine.bgm[].code``, without an extension
            ``StoryLine.bgm[].code``，不帶副檔名
        service: the shared story service
            共用的劇情服務

    Returns:
        the audio file, with the media type of the file found and an ``immutable`` cache header
        音檔，媒體類型跟著找到的檔案，帶 ``immutable`` 快取標頭
    """
    data, media_type = await service.bgm(game_id, code)
    return Response(content=data, media_type=media_type, headers=_IMMUTABLE)
