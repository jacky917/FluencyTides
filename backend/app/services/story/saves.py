"""存檔寫入器介面與依引擎的註冊（D13）。

Save-writer protocol and the per-engine registry. Games sharing an engine
(e.g. Yuzusoft titles on ``kirikiri_packinone``) share one writer;
《千恋＊万花》uses ``kirikiri_savestruct``.
"""

from __future__ import annotations

from typing import Any, Protocol

from app.infrastructure.krkr.packinone import PackError, PackinOneSaveWriter, SaveMeta
from app.infrastructure.krkr.savestruct import SaveStructError, SaveStructSaveWriter

#: 寫入器拋出的存檔格式錯誤（樣板壞了）。Save-format errors raised by writers.
SaveFormatError = (PackError, SaveStructError)


class SaveWriter(Protocol):
    """某種引擎的存檔寫入器。A save writer for one engine."""

    engine: str
    #: 能嵌入存檔的縮圖格式（``jpeg``／``bmp``）。The thumbnail format the writer embeds.
    thumb_format: str

    def write(self, template: bytes, position: dict[str, Any], variables: dict[str, Any], meta: SaveMeta) -> bytes:
        """以樣板產生存檔。Write a save from the template."""


_WRITERS: dict[str, SaveWriter] = {}


def register_save_writer(writer: SaveWriter) -> SaveWriter:
    """註冊一個寫入器（同一引擎後註冊的覆寫）。Register a writer."""
    _WRITERS[writer.engine] = writer
    return writer


def save_writer_for(engine: str) -> SaveWriter | None:
    """依 ``game_profiles.engine`` 取寫入器；沒有時回 None。Writer for an engine, or None."""
    return _WRITERS.get(engine)


register_save_writer(PackinOneSaveWriter())
register_save_writer(SaveStructSaveWriter())

__all__ = ["SaveFormatError", "SaveMeta", "SaveWriter", "register_save_writer", "save_writer_for"]
