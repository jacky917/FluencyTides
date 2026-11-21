"""Save-writer protocol and the per-engine registry (D13).

存檔寫入器介面與依引擎的註冊（D13）。

Games sharing an engine (for example Yuzusoft titles on ``kirikiri_packinone``) share one writer;
《千恋＊万花》 uses ``kirikiri_savestruct``.

同一引擎的遊戲（例如 ``kirikiri_packinone`` 上的柚子社作品）共用一個寫入器；《千恋＊万花》用 ``kirikiri_savestruct``。
"""

from __future__ import annotations

from typing import Any, Protocol

from app.infrastructure.krkr.packinone import PackError, PackinOneSaveWriter, SaveMeta
from app.infrastructure.krkr.savestruct import SaveStructError, SaveStructSaveWriter

#: Save-format errors raised by writers (the template is broken).
#: 寫入器拋出的存檔格式錯誤（樣板壞了）。
SaveFormatError = (PackError, SaveStructError)


class SaveWriter(Protocol):
    """A save writer for one engine.

    某種引擎的存檔寫入器。
    """

    engine: str
    #: The thumbnail format the writer can embed in a save (``jpeg`` or ``bmp``).
    #: 能嵌入存檔的縮圖格式（``jpeg``／``bmp``）。
    thumb_format: str

    def write(self, template: bytes, position: dict[str, Any], variables: dict[str, Any], meta: SaveMeta) -> bytes:
        """Produce a save from the template.

        以樣板產生存檔。

        Args:
            template: the save template of the game
                遊戲的存檔樣板
            position: the story position, as returned by ``StoryGame.save_position``
                劇情位置，即 ``StoryGame.save_position`` 的回傳值
            variables: the game variables to write
                要寫入的遊戲變數
            meta: the extra data written into the save (shown line, thumbnail, save time)
                寫進存檔的附帶資訊（顯示的台詞、縮圖、存檔時間）

        Returns:
            the save file content
            存檔內容

        Raises:
            PackError: if the template is malformed (``kirikiri_packinone``)
                若樣板格式不對（``kirikiri_packinone``）
            SaveStructError: if the template is malformed (``kirikiri_savestruct``)
                若樣板格式不對（``kirikiri_savestruct``）
        """


_WRITERS: dict[str, SaveWriter] = {}


def register_save_writer(writer: SaveWriter) -> SaveWriter:
    """Register a writer under its engine.

    註冊一個寫入器。

    A writer registered later for the same engine replaces the earlier one.

    同一引擎後註冊的覆寫先前的。

    Args:
        writer: the writer to register
            要註冊的寫入器

    Returns:
        ``writer`` itself
        ``writer`` 本身
    """
    _WRITERS[writer.engine] = writer
    return writer


def save_writer_for(engine: str) -> SaveWriter | None:
    """Get the writer for ``game_profiles.engine``.

    依 ``game_profiles.engine`` 取寫入器。

    Args:
        engine: the engine name
            引擎名稱

    Returns:
        the registered writer, or ``None`` if there is none
        註冊的寫入器；沒有時為 ``None``
    """
    return _WRITERS.get(engine)


register_save_writer(PackinOneSaveWriter())
register_save_writer(SaveStructSaveWriter())

__all__ = ["SaveFormatError", "SaveMeta", "SaveWriter", "register_save_writer", "save_writer_for"]
