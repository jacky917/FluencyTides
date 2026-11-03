"""存檔寫入器介面與依引擎的註冊（D13）。

Save-writer protocol and the per-engine registry. Games sharing an engine
(e.g. Yuzusoft titles on ``kirikiri_packinone``) share one writer.
"""

from __future__ import annotations

from typing import Any, Protocol

from app.infrastructure.krkr.packinone import PackinOneSaveWriter, SaveMeta


class SaveWriter(Protocol):
    """某種引擎的存檔寫入器。A save writer for one engine."""

    engine: str

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

__all__ = ["SaveMeta", "SaveWriter", "register_save_writer", "save_writer_for"]
