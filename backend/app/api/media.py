"""
網頁上傳媒體（media_upload 計畫書 D1、D2）。

Uploading media from the web.

- ``POST /api/v1/media``             上傳單一檔案（``multipart/form-data``：``file``、``kind``），回傳後端產生的檔名
- ``GET  /api/v1/media/{filename}``  讀回還沒用掉的上傳（網頁預覽用）

上傳不寫進 Anki：檔案放在後端的暫存區，拿到的檔名填進建卡（``POST /handlers/{name}/notes``）或修改欄位
（``PATCH /handlers/{name}/notes/{id}``）的媒體欄位，**那個請求成功時**才存進 Anki 媒體資料夾；失敗時
Anki 和送出前一樣（``app/services/media_commit.py``）。已經在 Anki 裡的檔案用 ``GET /anki-media/{filename}``
（``app/api/cards.py``）讀。認證與其他 API 相同（``verify_api_key``）。

Uploads are staged, not written to Anki; the create or update that uses
them stores them in Anki only if it succeeds.
"""

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from fastapi.responses import Response

from app.core.auth import verify_api_key
from app.schemas.card import ErrorResponse
from app.schemas.notes import MediaUploadResponse
from app.services.card_browse_service import media_type_for
from app.services.media_names import MediaKind
from app.services.media_service import MAX_MEDIA_BYTES, MediaService

router = APIRouter(tags=["Media"], dependencies=[Depends(verify_api_key)])


@router.post(
    "/media",
    response_model=MediaUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="上傳媒體（暫存，建卡或修改成功時才存進 Anki）",
    description=(
        "音檔接受 mp3、ogg、wav、m4a、webm（上限 10 MB；m4a、webm 會轉成 mp3），圖片接受 jpg、png、webp"
        "（上限 5 MB；轉檔的以轉成 mp3 後的大小為準）。格式依檔案內容判斷，不看副檔名；檔名由後端產生，"
        "不採用上傳的檔名。檔案先放在後端的暫存區（保留一天、重新部署後就沒了），不寫進 Anki。"
    ),
    responses={
        413: {"model": ErrorResponse, "description": "超過大小上限，或轉成 mp3 後超過（MEDIA_TOO_LARGE）"},
        415: {"model": ErrorResponse, "description": "不是這個種類接受的格式，或認不出格式（MEDIA_TYPE_UNSUPPORTED）"},
        422: {"description": "轉檔失敗（ErrorResponse，MEDIA_CONVERT_FAILED），或缺少欄位、kind 不對（detail）"},
        503: {"model": ErrorResponse, "description": "需要轉檔，但後端沒有 ffmpeg 或無法啟動（MEDIA_CONVERTER_UNAVAILABLE）"},
    },
)
async def upload_media(
    file: UploadFile = File(..., description="要上傳的檔案"),
    kind: MediaKind = Form(..., description="audio 或 image"),
) -> dict[str, object]:
    """檢查、轉檔、放進暫存區，回傳檔名。Validate, convert and stage one file.

    只讀到上限加一個位元組：超過上限的檔案不必整個讀進記憶體，服務層看到多出的那個位元組就會擋下。
    （整個請求仍會先被 Starlette 收完並暫存；網路層的上限是 nginx 的 50 MB。）

    Reads at most one byte past the limit, which is enough for the service to
    reject an oversized file.

    Raises:
        MediaTooLargeError: 413——超過大小上限。Over the size limit.
        MediaTypeUnsupportedError: 415——格式不符或認不出來。Not an accepted format.
        MediaConvertFailedError: 422——轉 mp3 失敗或逾時。Conversion failed.
        MediaConverterUnavailableError: 503——後端沒有 ffmpeg 或無法啟動。FFmpeg is unavailable.
    """
    data = await file.read(MAX_MEDIA_BYTES[kind] + 1)
    return await MediaService().upload(kind, data)


@router.get(
    "/media/{filename}",
    summary="讀回還沒用掉的上傳",
    response_class=Response,
    responses={
        200: {"content": {"application/octet-stream": {}}, "description": "檔案內容"},
        404: {"model": ErrorResponse, "description": "不在暫存區：已經用掉、超過一天，或後端重新部署過"},
    },
)
async def read_staged_media(filename: str) -> Response:
    """暫存區裡的一個檔案。與網頁同源，回應帶 ``nosniff`` 與 ``CSP: sandbox``（同 ``/anki-media``）。

    One staged file, served with ``nosniff`` and a sandbox CSP.
    """
    content = MediaService().read_staged(filename)
    headers = {
        # 暫存檔隨時會被用掉或過期：不快取，預覽才看得到「已經不在了」
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox; default-src 'none'",
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
    }
    return Response(content=content, media_type=media_type_for(filename), headers=headers)
