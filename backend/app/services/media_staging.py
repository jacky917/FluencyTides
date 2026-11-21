"""Staging area for media uploaded from the web (media_upload plan D1; changed to stage first on 2026-11-14).

網頁上傳媒體的暫存區（media_upload 計畫書 D1，2026-11-14 改為先暫存）。

``POST /media`` only puts files here and does **not** write them into Anki; only when a card creation or field
update succeeds does ``media_commit`` store them in Anki's media folder and delete them from here. So abandoning
after an upload, swapping files, or a failed card creation never leaves unreferenced files in Anki.

``POST /media`` 只把檔案放在這裡，**不**寫進 Anki；建卡或修改欄位成功時才由 ``media_commit`` 存進 Anki
媒體資料夾並從這裡刪掉。所以上傳後放棄、換檔、建卡失敗，都不會在 Anki 留下沒被引用的檔案。

- Files live in the backend container's temp directory: they are gone after a redeploy or restart, the names in
  the form become "expired", and the user must upload again.
- Files kept longer than ``STAGING_TTL_SECONDS`` are removed on the next upload.
- Only names produced by ``new_media_name`` are accepted (letters, digits, underscores and dots only), so nothing
  is written outside the directory.
- There is only one backend process (the card-creation slot ``CreateSession`` assumes the same), so sharing
  between processes is not handled.

- 放在後端容器的暫存目錄：重新部署或重啟後就沒了，表單裡的檔名會變成「已過期」，要重新上傳。
- 放超過 ``STAGING_TTL_SECONDS`` 的檔案在下一次上傳時清掉。
- 只接受 ``new_media_name`` 產生的檔名（只有英數、底線與點），不會寫到目錄以外。
- 只有一個後端程序（建卡槽位 ``CreateSession`` 也假設如此），不必處理多程序共用。
"""

from __future__ import annotations

import logging
import tempfile
import time
from pathlib import Path

from app.services.media_names import uploaded_media_kind

logger = logging.getLogger(__name__)

#: How long staged files are kept, in seconds: anything not used within a day is removed.
#: 暫存檔保留多久（秒）：一天內沒有用掉的就清掉。
STAGING_TTL_SECONDS = 24 * 60 * 60


class MediaStaging:
    """Media files that have been uploaded but not used yet.

    上傳後、還沒用到的媒體檔。

    Attributes:
        root: the staging directory (created on the first write)
            暫存目錄（第一次寫入時建立）
    """

    def __init__(self, root: Path) -> None:
        """Initialize the staging area.

        初始化暫存區。

        Args:
            root: the staging directory; it need not exist yet
                暫存目錄，可以還不存在
        """
        self.root = root

    def _path(self, name: str) -> Path | None:
        """Return the path for a name, or ``None`` if it is not a name ``POST /media`` produces.

        回傳檔名對應的路徑；不是 ``POST /media`` 產生的檔名時為 ``None``（不碰檔案系統）。
        """
        return self.root / name if uploaded_media_kind(name) else None

    def put(self, name: str, data: bytes) -> None:
        """Stage a file.

        放進一個檔案。

        The data is written to a temporary file and then renamed, so readers never see a half-written file. Expired
        files are cleaned up first.

        先寫暫存檔再改名，讀的人不會看到寫到一半的內容。寫入前會先清掉過期的檔案。

        Args:
            name: a name produced by ``new_media_name``
                ``new_media_name`` 產生的檔名
            data: the content
                內容

        Raises:
            OSError: if writing fails
                若寫入失敗
            ValueError: if ``name`` is not produced by ``new_media_name`` (a programming error)
                若不是 ``new_media_name`` 產生的檔名（程式錯誤）
        """
        path = self._path(name)
        if path is None:
            raise ValueError(f"不是上傳產生的檔名：{name!r}")
        self.root.mkdir(parents=True, exist_ok=True)
        self.cleanup_expired()
        partial = path.with_name(f".{name}.partial")
        partial.write_bytes(data)
        partial.replace(path)

    def read(self, name: str) -> bytes | None:
        """Read a staged file.

        讀出一個暫存檔。

        Args:
            name: the file name
                檔名

        Returns:
            the content, or ``None`` if the file does not exist (already used, expired, or the backend restarted) or
            the name is not valid
            內容；不存在（已用掉、過期、後端重啟過）或檔名不合格時為 ``None``
        """
        path = self._path(name)
        if path is None or not path.is_file():
            return None
        return path.read_bytes()

    def discard(self, name: str) -> None:
        """Remove a staged file (already stored in Anki, or no longer wanted).

        刪掉一個暫存檔（已經存進 Anki，或不要了）。

        Does nothing if the file does not exist or the name is not valid. A deletion failure is only logged as a
        warning; it does not raise.

        不存在或檔名不合格時不做事。刪除失敗只記 warning，不拋錯。

        Args:
            name: the file name
                檔名
        """
        path = self._path(name)
        if path is None:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            # 只是暫存檔：刪不掉會在過期時再清一次，不影響已經完成的寫入
            logger.warning("刪不掉暫存的媒體檔 %s：%s", name, exc)

    def cleanup_expired(self, now: float | None = None) -> None:
        """Remove staged files kept longer than ``STAGING_TTL_SECONDS``.

        清掉放超過 ``STAGING_TTL_SECONDS`` 的暫存檔。

        Does nothing if the directory does not exist. A failure on one file is logged as a warning and the rest are
        still processed.

        目錄不存在時不做事。單一檔案失敗時記 warning，其餘照常處理。

        Args:
            now: the current time in epoch seconds, for tests; ``None`` means use the current time
                現在的時間（epoch 秒），測試用；``None`` 表示用現在的時間
        """
        if not self.root.is_dir():
            return
        cutoff = (time.time() if now is None else now) - STAGING_TTL_SECONDS
        for path in self.root.iterdir():
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
                    logger.info("清掉過期的暫存媒體檔：%s", path.name)
            except OSError as exc:
                logger.warning("清理暫存媒體檔 %s 失敗：%s", path.name, exc)


#: The staging area shared by the whole site.
#: 全站共用的暫存區。
media_staging = MediaStaging(Path(tempfile.gettempdir()) / "fluencytides_media_staging")
