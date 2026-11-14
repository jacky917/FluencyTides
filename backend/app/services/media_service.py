"""
網頁上傳媒體（media_upload 計畫書 D1、D2）：檢查、轉檔、放進暫存區。

Web media uploads: validate, convert and stage.

上傳和使用分開（D1），但上傳**不寫進 Anki**：檔案放在暫存區（``media_staging``），建卡或修改欄位成功時
才存進 Anki 媒體資料夾（``media_commit``）。所以上傳後放棄、換檔或建卡失敗，Anki 都和操作前一樣。

處理順序：檢查大小 → 依內容判斷格式 → 需要時轉成 mp3（轉完再檢查一次大小）→ 產生檔名 → 放進暫存區。

Uploading is separate from using, and does not touch Anki: files are staged
and reach Anki only when a create or update that uses them succeeds.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from app.core.exceptions import (
    MediaConverterUnavailableError,
    MediaFileNotFoundError,
    MediaConvertFailedError,
    MediaTooLargeError,
    MediaTypeUnsupportedError,
)
from app.infrastructure.ffmpeg.client import FfmpegClient, FfmpegError, FfmpegTimeoutError
from app.services.media_names import MediaKind, new_media_name
from app.services.media_staging import media_staging

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.services.media_staging import MediaStaging

logger = logging.getLogger(__name__)

#: 轉檔最長幾秒：10 MB 的錄音正常幾秒就轉完，超過多半是檔案有問題；也要短於 nginx 的 180 秒。
#: The conversion time limit; well under nginx's 180-second timeout.
CONVERT_TIMEOUT_SECONDS = 90

#: 大小上限（位元組），套在存進 Anki 的檔案（轉檔的是轉檔後的大小）。整個檔案要以 base64 經 AnkiConnect
#: 傳送，太大會拖慢甚至失敗。前端 ``lib/mediaUpload.ts`` 有同一份，改的時候兩邊一起改。
#: Size limits in bytes of the stored file; the whole file travels to AnkiConnect as base64.
MAX_MEDIA_BYTES: dict[str, int] = {
    "audio": 10 * 1024 * 1024,
    "image": 5 * 1024 * 1024,
}

#: 各種類接受的格式（依內容判斷出的名稱）。Accepted formats per kind.
ACCEPTED_FORMATS: dict[str, tuple[str, ...]] = {
    "audio": ("mp3", "ogg", "wav", "m4a", "webm"),
    "image": ("jpg", "png", "webp"),
}

#: 要先轉成 mp3 的格式：Anki（尤其手機版）不一定能播。其餘原樣保留（ogg 是 TG 語音的格式）。
#: Formats converted to mp3 first; the rest are stored as they are.
CONVERT_TO_MP3 = frozenset({"m4a", "webm"})

# ISO 媒體檔（ftyp）裡屬於圖片的 brand：HEIC／AVIF 和 m4a 是同一種容器，不能當音檔收
_IMAGE_BRANDS = frozenset({b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1", b"avif", b"avis"})


class _Converter(Protocol):
    """轉檔器（``FfmpegClient`` 的一部分；測試可換成假的）。失敗時拋 ``FfmpegError``（逾時是
    ``FfmpegTimeoutError``）。The converter used here; raises ``FfmpegError`` on failure."""

    async def convert_file_to_mp3(self, source: Path, timeout: float | None = None) -> bytes: ...


def sniff_format(data: bytes) -> str | None:
    """依檔案開頭的特徵判斷格式，不看副檔名（D2 第 1 步）。

    Detect the format from the file's leading bytes, ignoring its extension.

    Args:
        data: 檔案內容。The file content.

    Returns:
        ``mp3``、``ogg``、``wav``、``m4a``、``webm``、``jpg``、``png``、``webp`` 之一；認不出來時為 None。
        The format, or None when unrecognized.
    """
    head = data[:16]
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head.startswith(b"RIFF") and len(head) >= 12:
        return {b"WEBP": "webp", b"WAVE": "wav"}.get(head[8:12])
    if head.startswith(b"OggS"):
        return "ogg"
    if head.startswith(b"\x1a\x45\xdf\xa3"):  # EBML：webm（瀏覽器錄音）與 matroska
        return "webm"
    if head[4:8] == b"ftyp" and len(head) >= 12:
        return None if head[8:12] in _IMAGE_BRANDS else "m4a"
    if head.startswith(b"ID3") or _is_mp3_frame(head):
        return "mp3"
    return None


def _is_mp3_frame(head: bytes) -> bool:
    """開頭是不是一個 MPEG 音訊幀（沒有 ID3 標籤的 mp3）。Whether the data starts with an MPEG audio frame.

    只看同步字還不夠（AAC 的 ADTS 也是 0xFFF 開頭）：另外檢查版本、層、位元率、取樣率都不是保留值。
    """
    if len(head) < 3 or head[0] != 0xFF or (head[1] & 0xE0) != 0xE0:
        return False
    version = (head[1] >> 3) & 0b11
    layer = (head[1] >> 1) & 0b11
    bitrate = head[2] >> 4
    sample_rate = (head[2] >> 2) & 0b11
    return version != 0b01 and layer != 0b00 and bitrate not in (0b0000, 0b1111) and sample_rate != 0b11


def _megabytes(size: int) -> str:
    """位元組數寫成 MB（錯誤訊息用）。A size in MB for messages."""
    return f"{size / (1024 * 1024):.1f} MB"


class MediaService:
    """檢查、轉檔網頁上傳的檔案並放進暫存區。每個請求建立一次。

    Validates, converts and stages web uploads; built once per request.

    Attributes:
        staging: 暫存區。The staging area.
    """

    def __init__(
        self, staging: MediaStaging | None = None, converter_factory: Callable[[], _Converter] | None = None
    ) -> None:
        """初始化服務。Initialize the service.

        Args:
            staging: 暫存區；省略時用全站共用的 ``media_staging``。測試可換成自己的。The staging area.
            converter_factory: 取得轉檔器，需要轉檔時才呼叫；省略時用 ``FfmpegClient``（找不到 ffmpeg 時
                會拋 ``FileNotFoundError``）。測試可換成假的。Builds the converter when one is needed.
        """
        self.staging = staging or media_staging
        self._converter_factory = converter_factory

    async def upload(self, kind: MediaKind, data: bytes) -> dict[str, object]:
        """檢查、必要時轉檔，然後放進暫存區（不寫進 Anki）。Validate, convert if needed, then stage.

        Args:
            kind: 上傳的人宣告的種類（``audio`` 或 ``image``）。The declared kind.
            data: 檔案內容。呼叫端只需讀到上限加一個位元組（超過就會被擋下）。The file content.

        Returns:
            ``{"filename", "kind", "size"}``：檔名（之後存進 Anki 也用這個名字）、種類、實際的大小（轉檔後的）。
            The staged name, the kind and the size.

        Raises:
            MediaTooLargeError: 超過大小上限（轉檔的在轉檔前後各檢查一次）。Over the size limit.
            MediaTypeUnsupportedError: 檔案是空的、認不出格式，或不是這個種類接受的格式。Not an accepted format.
            MediaConvertFailedError: 轉 mp3 失敗、逾時或轉出空的。Conversion failed.
            MediaConverterUnavailableError: 需要轉檔，但後端找不到或無法啟動 ffmpeg。FFmpeg is unavailable.
            OSError: 暫存目錄寫不進去（伺服器問題，變成 500）。When staging fails.
        """
        limit = MAX_MEDIA_BYTES[kind]
        label = "音檔" if kind == "audio" else "圖片"
        if len(data) > limit:
            raise MediaTooLargeError(f"{label}最大 {limit // (1024 * 1024)} MB，這個檔案超過了")
        if not data:
            raise MediaTypeUnsupportedError("檔案是空的")

        detected = sniff_format(data)
        accepted = ACCEPTED_FORMATS[kind]
        if detected not in accepted:
            found = f"這個檔案是 {detected}" if detected else "認不出這個檔案的格式"
            raise MediaTypeUnsupportedError(f"{label}只接受 {'、'.join(accepted)}；{found}")

        extension = detected
        if detected in CONVERT_TO_MP3:
            data = await self._to_mp3(data, detected)
            extension = "mp3"
            # 低位元率的錄音（例如 opus 語音）轉成 mp3 會變大好幾倍：上限要套在實際送進 Anki 的大小
            if len(data) > limit:
                raise MediaTooLargeError(
                    f"{label}轉成 mp3 後是 {_megabytes(len(data))}，超過上限 {limit // (1024 * 1024)} MB；"
                    "請上傳較短的錄音，或先轉成 mp3 再上傳"
                )

        filename = new_media_name(kind, extension)
        self.staging.put(filename, data)
        logger.info("網頁上傳媒體，已暫存：%s（%s，%d bytes）", filename, detected, len(data))
        return {"filename": filename, "kind": kind, "size": len(data)}

    def read_staged(self, filename: str) -> bytes:
        """讀出一個暫存檔（網頁預覽還沒送出的上傳）。Read a staged file for previewing.

        Args:
            filename: 上傳時回傳的檔名。The name returned by the upload.

        Returns:
            內容。The content.

        Raises:
            MediaFileNotFoundError: 不在暫存區（過期、後端重新部署過、已經用掉，或不是上傳的檔名）。Not staged.
        """
        data = self.staging.read(filename)
        if data is None:
            raise MediaFileNotFoundError(f"暫存區裡沒有 {filename}：可能已經用掉、超過一天，或後端重新部署過")
        return data

    async def _to_mp3(self, data: bytes, detected: str) -> bytes:
        """把 webm、m4a 轉成 mp3（D2 第 3 步）。Convert webm or m4a to mp3.

        Raises:
            MediaConverterUnavailableError: 後端找不到或無法啟動 ffmpeg（伺服器的問題，換檔案沒有用）。
                FFmpeg is missing or cannot start.
            MediaConvertFailedError: 轉檔失敗、逾時，或轉出來是空的（多半是檔案本身的問題）。
                Conversion failed, timed out, or produced nothing.
        """
        try:
            converter = (self._converter_factory or FfmpegClient)()
        except FileNotFoundError as exc:
            logger.error("網頁上傳的 %s 需要轉檔，但後端找不到 ffmpeg：%s", detected, exc)
            raise MediaConverterUnavailableError(
                f"後端沒有 ffmpeg，暫時無法把 {detected} 轉成 mp3；請改上傳 mp3、ogg 或 wav"
            ) from exc
        try:
            with tempfile.TemporaryDirectory(prefix="ft_media_") as folder:
                source = Path(folder) / f"upload.{detected}"
                source.write_bytes(data)
                converted = await converter.convert_file_to_mp3(source, timeout=CONVERT_TIMEOUT_SECONDS)
        except FfmpegTimeoutError as exc:
            logger.warning("網頁上傳的 %s（%s）轉 mp3 逾時：%s", detected, _megabytes(len(data)), exc)
            raise MediaConvertFailedError(
                f"把這個 {detected} 轉成 mp3 超過 {CONVERT_TIMEOUT_SECONDS} 秒：檔案可能損壞或太長"
            ) from exc
        except FfmpegError as exc:
            if exc.return_code is None:
                # 子程序根本沒啟動（權限、資源、執行檔壞掉）：不是檔案的問題
                logger.error("網頁上傳的 %s 轉檔時 ffmpeg 無法啟動：%s", detected, exc)
                raise MediaConverterUnavailableError(
                    f"後端無法執行 ffmpeg，暫時無法把 {detected} 轉成 mp3；請改上傳 mp3、ogg 或 wav"
                ) from exc
            logger.warning(
                "網頁上傳的 %s（%s）轉 mp3 失敗（exit %s）：%s",
                detected, _megabytes(len(data)), exc.return_code, exc.stderr_output[-500:],
            )
            raise MediaConvertFailedError(f"無法把這個 {detected} 轉成 mp3：檔案可能損壞，或裡面沒有聲音") from exc
        if not converted:
            logger.warning("網頁上傳的 %s（%s）轉 mp3 後是空的", detected, _megabytes(len(data)))
            raise MediaConvertFailedError(f"把這個 {detected} 轉成 mp3 後是空的：裡面可能沒有聲音")
        logger.info("%s 轉 mp3：%s → %s", detected, _megabytes(len(data)), _megabytes(len(converted)))
        return converted
