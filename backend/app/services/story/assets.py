"""Reading of story-flowchart assets (thumbnails, save templates) from a local folder or MinIO.

劇情流程圖的素材（縮圖、存檔樣板）讀取：本機資料夾或 MinIO。

An object key is the MinIO object name, for example ``story/SabbatOfTheWitch/thumbs/<hash>.jpg``. When
``STORY_ASSETS_DIR`` is set, ``<folder>/<object key>`` is read first (for local development; this is the layout the
import script writes with ``--assets local:<folder>``), and MinIO (``MINIO_DEFAULT_BUCKET``) is asked only if it is
not found. Objects read are kept in an in-memory LRU cache (the last 512, regardless of file size; in practice they
are thumbnails and save templates of a few dozen KB each). Voices (about 20,000 per game, about 2 GB in total) are
read with ``cache=False`` and stay out of the cache, so they neither evict thumbnails nor grow memory with playback.

物件鍵即 MinIO 的物件名稱，例 ``story/SabbatOfTheWitch/thumbs/<雜湊>.jpg``。設定 ``STORY_ASSETS_DIR``
時先讀 ``<資料夾>/<物件鍵>``（本機開發用，匯入腳本 ``--assets local:<資料夾>`` 寫的就是這個結構），
找不到再問 MinIO（``MINIO_DEFAULT_BUCKET``）。讀到的物件放在記憶體 LRU 快取（最近 512 個，
不看檔案大小；實際上是縮圖與存檔樣板，都只有幾十 KB）。語音（一款遊戲約兩萬個、合計約 2 GB）
以 ``cache=False`` 讀取，不進快取，免得把縮圖擠出去、也不讓記憶體隨播放量成長。

There are two kinds of error: a missing object yields ``None`` (the caller turns it into 404); a failure of the
source itself (MinIO unreachable, a file unreadable) is logged as WARNING and raised as
:class:`StoryAssetUnavailableError` (503), never disguised as "not found".

錯誤分兩種：物件不存在回 ``None``（呼叫端轉成 404）；來源本身出問題（MinIO 連不上、檔案讀不了）
記 WARNING 並拋 ``StoryAssetUnavailableError``（503），不偽裝成「不存在」。

Object keys are always checked first: no absolute path, no drive letter or ``:``, and after splitting on ``/`` and
``\\`` no empty, ``.``, or ``..`` segment; before reading a local file the path is also ``resolve``-d to confirm it
is still inside the folder (guarding against escapes such as Windows ``..\\``).

物件鍵一律先檢查：不能是絕對路徑、不能有磁碟代號或 ``:``、以 ``/`` 與 ``\\`` 切開後不能有空段、
``.`` 或 ``..``；讀本機檔案前再 ``resolve`` 確認仍在資料夾內（防 Windows 的 ``..\\`` 跳出）。
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
from collections import OrderedDict
from pathlib import Path
from typing import Any

from app.infrastructure.database.story_corpus_database import story_setting
from app.services.story.errors import StoryAssetUnavailableError

logger = logging.getLogger(__name__)

_CACHE_ITEMS = 512
#: MinIO / S3 error codes meaning "no such object".
#: MinIO／S3 表示「物件不存在」的錯誤碼。
_MISSING_CODES = frozenset({"NoSuchKey", "NoSuchObject"})
_SEPARATORS = re.compile(r"[\\/]")


def safe_key_parts(key: str) -> list[str] | None:
    """Split an object key into path segments.

    把物件鍵切成路徑段。

    Args:
        key: the object key; both ``/`` and ``\\`` count as separators
            物件鍵；``/`` 與 ``\\`` 都算分隔符號

    Returns:
        the segments, or ``None`` if the key is unsafe (empty, absolute, containing ``:`` such as a drive letter, or
        having an empty, ``.``, or ``..`` segment)
        路徑段；不安全（空字串、絕對路徑、含 ``:`` 如磁碟代號、有空段、``.`` 或 ``..``）時為 ``None``
    """
    if not key or key[0] in "/\\" or ":" in key:
        return None
    parts = _SEPARATORS.split(key)
    if any(p in ("", ".", "..") for p in parts):
        return None
    return parts


def _s3_code(exc: BaseException) -> str | None:
    """Return the error code of an S3Error.

    取 S3Error 的錯誤碼。

    MinioClient wraps it in a MinioStorageError and puts it in ``__cause__``, so both are checked.

    MinioClient 會把它包成 MinioStorageError，放在 ``__cause__``，所以兩者都檢查。
    """
    for err in (exc, exc.__cause__):
        code = getattr(err, "code", None)
        if isinstance(code, str):
            return code
    return None


class StoryAssetStore:
    """An asset reader: the local folder first, then MinIO.

    素材讀取（本機資料夾 → MinIO）。
    """

    def __init__(self, minio_client: Any = None, bucket: str | None = None, local_dir: str | None = None) -> None:
        """Create an asset store.

        建立素材讀取器。

        Args:
            minio_client: the MinIO client; ``None`` disables MinIO
                MinIO 用戶端；``None`` 表示不用 MinIO
            bucket: the MinIO bucket; ``None`` or empty disables MinIO
                MinIO 的 bucket；``None`` 或空字串表示不用 MinIO
            local_dir: the local asset folder; ``None`` reads ``STORY_ASSETS_DIR``, and an empty value disables the
                local folder
                本機素材資料夾；``None`` 時讀 ``STORY_ASSETS_DIR``，空值表示不用本機資料夾
        """
        self.minio = minio_client
        self.bucket = bucket
        root = local_dir if local_dir is not None else story_setting("STORY_ASSETS_DIR")
        self.local_root = Path(root).resolve() if root else None
        self._cache: OrderedDict[str, bytes] = OrderedDict()

    @property
    def available(self) -> bool:
        """Whether any asset source is configured (MinIO needs both a client and a bucket).

        有沒有任何素材來源（MinIO 要有用戶端也要有 bucket）。
        """
        return self.local_root is not None or (self.minio is not None and bool(self.bucket))

    async def get(self, key: str, *, cache: bool = True) -> bytes | None:
        """Read an object.

        讀取一個物件。

        Args:
            key: the object key
                物件鍵
            cache: whether to put the object into the LRU cache after reading (objects already in the cache are
                still served from it); pass ``False`` for voices
                讀到後放進 LRU 快取（已在快取裡的照樣從快取回傳）；語音傳 ``False``

        Returns:
            the object content, or ``None`` if it does not exist or the key is unsafe
            物件內容；不存在或物件鍵不安全時為 ``None``

        Raises:
            StoryAssetUnavailableError: if the source fails for a reason other than a missing object
                若來源出錯（不是「不存在」）
        """
        parts = safe_key_parts(key)
        if parts is None:
            return None
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        data = self._read_local(key, parts)
        if data is None and self.minio is not None and self.bucket:
            data = await self._read_minio(key)
        if data is not None and cache:
            self._cache[key] = data
            while len(self._cache) > _CACHE_ITEMS:
                self._cache.popitem(last=False)
        return data

    def _read_local(self, key: str, parts: list[str]) -> bytes | None:
        if self.local_root is None:
            return None
        path = self.local_root.joinpath(*parts).resolve()
        if not path.is_relative_to(self.local_root):
            logger.warning("素材物件鍵 %s 跳出本機資料夾，拒絕讀取", key)
            return None
        try:
            return path.read_bytes() if path.is_file() else None
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.warning("讀取本機素材 %s 失敗", key, exc_info=True)
            raise StoryAssetUnavailableError(f"本機素材 {key} 讀取失敗：{exc}") from exc

    async def _read_minio(self, key: str) -> bytes | None:
        fd, tmp = tempfile.mkstemp(prefix="story-asset-")
        os.close(fd)
        try:
            await self.minio.download_file(self.bucket, key, tmp)
            return await asyncio.to_thread(Path(tmp).read_bytes)
        except Exception as exc:  # noqa: BLE001 - 依錯誤碼分成「不存在」與「來源出錯」
            if _s3_code(exc) in _MISSING_CODES:
                return None
            logger.warning("讀取 MinIO 素材 %s/%s 失敗", self.bucket, key, exc_info=True)
            raise StoryAssetUnavailableError(f"MinIO 素材 {key} 讀取失敗") from exc
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
