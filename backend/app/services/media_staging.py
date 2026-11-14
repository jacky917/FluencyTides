"""
網頁上傳媒體的暫存區（media_upload 計畫書 D1，2026-11-14 改為先暫存）。

Staging area for media uploaded from the web.

``POST /media`` 只把檔案放在這裡，**不**寫進 Anki；建卡或修改欄位成功時才由 ``media_commit`` 存進 Anki
媒體資料夾並從這裡刪掉。所以上傳後放棄、換檔、建卡失敗，都不會在 Anki 留下沒被引用的檔案。

- 放在後端容器的暫存目錄：重新部署或重啟後就沒了，表單裡的檔名會變成「已過期」，要重新上傳。
- 放超過 ``STAGING_TTL_SECONDS`` 的檔案在下一次上傳時清掉。
- 只接受 ``new_media_name`` 產生的檔名（只有英數、底線與點），不會寫到目錄以外。
- 只有一個後端程序（建卡槽位 ``CreateSession`` 也假設如此），不必處理多程序共用。

``POST /media`` only stages files here; they reach Anki's media folder only
when a create or update that uses them succeeds.
"""

from __future__ import annotations

import logging
import tempfile
import time
from pathlib import Path

from app.services.media_names import uploaded_media_kind

logger = logging.getLogger(__name__)

#: 暫存檔保留多久（秒）：一天內沒有用掉的就清掉。How long staged files are kept.
STAGING_TTL_SECONDS = 24 * 60 * 60


class MediaStaging:
    """上傳後、還沒用到的媒體檔。Media files uploaded but not used yet.

    Attributes:
        root: 暫存目錄（第一次寫入時建立）。The staging directory.
    """

    def __init__(self, root: Path) -> None:
        """初始化暫存區。Initialize the staging area.

        Args:
            root: 暫存目錄。The staging directory.
        """
        self.root = root

    def _path(self, name: str) -> Path | None:
        """檔名對應的路徑；不是 ``POST /media`` 產生的檔名時為 None（不碰檔案系統）。The file's path, or None."""
        return self.root / name if uploaded_media_kind(name) else None

    def put(self, name: str, data: bytes) -> None:
        """放進一個檔案（先寫暫存檔再改名，讀的人不會看到寫到一半的內容）。Stage a file.

        Args:
            name: ``new_media_name`` 產生的檔名。The generated name.
            data: 內容。The content.

        Raises:
            ValueError: 不是 ``new_media_name`` 產生的檔名（程式錯誤）。Not a generated name.
            OSError: 寫入失敗。When writing fails.
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
        """讀出一個暫存檔；不存在（已用掉、過期、後端重啟過）或檔名不合格時為 None。Read a staged file, or None.

        Args:
            name: 檔名。The name.

        Returns:
            內容，或 None。The content, or None.
        """
        path = self._path(name)
        if path is None or not path.is_file():
            return None
        return path.read_bytes()

    def discard(self, name: str) -> None:
        """刪掉一個暫存檔（已經存進 Anki，或不要了）；不存在時不做事。Remove a staged file if present."""
        path = self._path(name)
        if path is None:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            # 只是暫存檔：刪不掉會在過期時再清一次，不影響已經完成的寫入
            logger.warning("刪不掉暫存的媒體檔 %s：%s", name, exc)

    def cleanup_expired(self, now: float | None = None) -> None:
        """清掉放超過 ``STAGING_TTL_SECONDS`` 的暫存檔。Remove staged files older than the TTL.

        Args:
            now: 現在的時間（epoch 秒）；測試用。The current time, for tests.
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


#: 全站共用的暫存區。The shared staging area.
media_staging = MediaStaging(Path(tempfile.gettempdir()) / "fluencytides_media_staging")
