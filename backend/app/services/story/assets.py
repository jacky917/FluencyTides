"""劇情流程圖的素材（縮圖、存檔樣板）讀取：本機資料夾或 MinIO。

Reads story assets (thumbnails, save templates) from a local folder or MinIO.

物件鍵即 MinIO 的物件名稱，例 ``story/SabbatOfTheWitch/thumbs/<雜湊>.jpg``。設定 ``STORY_ASSETS_DIR``
時先讀 ``<資料夾>/<物件鍵>``（本機開發用，匯入腳本 ``--assets local:<資料夾>`` 寫的就是這個結構），
找不到再問 MinIO（``MINIO_DEFAULT_BUCKET``）。讀到的物件放在記憶體 LRU 快取（最近 512 個，
不看檔案大小；實際上是縮圖與存檔樣板，都只有幾十 KB）。

Object keys are MinIO object names. ``STORY_ASSETS_DIR`` (if set) is
checked first, then MinIO. The last 512 objects read are kept in an
in-memory LRU regardless of size (in practice thumbnails and save
templates of a few dozen KB each).

錯誤分兩種：物件不存在回 ``None``（呼叫端轉成 404）；來源本身出問題（MinIO 連不上、檔案讀不了）
記 WARNING 並拋 :class:`StoryAssetUnavailableError`（503），不偽裝成「不存在」。
A missing object yields ``None`` (404 upstream); any other source failure is
logged and raised as :class:`StoryAssetUnavailableError` (503).

物件鍵一律先檢查：不能是絕對路徑、不能有磁碟代號或 ``:``、以 ``/`` 與 ``\\`` 切開後不能有空段、
``.`` 或 ``..``；讀本機檔案前再 ``resolve`` 確認仍在資料夾內（防 Windows 的 ``..\\`` 跳出）。
Keys are checked before use (no absolute paths, drives, ``:``, or empty /
``.`` / ``..`` segments on either separator), and local reads are resolved
and confined to the root.
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
#: MinIO／S3 表示「物件不存在」的錯誤碼。S3 error codes meaning "no such object".
_MISSING_CODES = frozenset({"NoSuchKey", "NoSuchObject"})
_SEPARATORS = re.compile(r"[\\/]")


def safe_key_parts(key: str) -> list[str] | None:
    """把物件鍵切成路徑段；不安全（絕對路徑、磁碟代號、``..`` 等）時回 None。

    Split an object key into segments, or None when the key is unsafe.
    """
    if not key or key[0] in "/\\" or ":" in key:
        return None
    parts = _SEPARATORS.split(key)
    if any(p in ("", ".", "..") for p in parts):
        return None
    return parts


def _s3_code(exc: BaseException) -> str | None:
    """取 S3Error 的錯誤碼（MinioClient 會把它包成 MinioStorageError，放在 ``__cause__``）。"""
    for err in (exc, exc.__cause__):
        code = getattr(err, "code", None)
        if isinstance(code, str):
            return code
    return None


class StoryAssetStore:
    """素材讀取（本機資料夾 → MinIO）。Asset reader: local folder, then MinIO."""

    def __init__(self, minio_client: Any = None, bucket: str | None = None, local_dir: str | None = None) -> None:
        self.minio = minio_client
        self.bucket = bucket
        root = local_dir if local_dir is not None else story_setting("STORY_ASSETS_DIR")
        self.local_root = Path(root).resolve() if root else None
        self._cache: OrderedDict[str, bytes] = OrderedDict()

    @property
    def available(self) -> bool:
        """有沒有任何素材來源（MinIO 要有用戶端也要有 bucket）。Whether any source is configured."""
        return self.local_root is not None or (self.minio is not None and bool(self.bucket))

    async def get(self, key: str) -> bytes | None:
        """讀取一個物件；不存在或物件鍵不安全時回 None。Read an object, or None.

        Raises:
            StoryAssetUnavailableError: 來源出錯（不是「不存在」）時。When the
                source fails for another reason than a missing object.
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
        if data is not None:
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
