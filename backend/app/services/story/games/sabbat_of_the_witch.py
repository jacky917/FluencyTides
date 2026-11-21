"""Game adapter for 《魔女的夜宴》 (Sabbat of the Witch): only the differences from the default.

《魔女的夜宴》的遊戲適配器：只放和預設不同的部分。

- Route-preset flags must be integers (the game's ``f.*_flag`` are all integers). That a preset names an existing
  tab is checked when packaging (``scripts/story/common/package.py``), not here.

- 路線預設的旗標必須是整數（遊戲的 ``f.*_flag`` 都是整數）。路線預設必須是存在的分頁，這點在打包時
  （``scripts/story/common/package.py``）檢查，不在這裡。
"""

from __future__ import annotations

from pydantic import field_validator

from app.schemas.story import StoryConfig
from app.services.story.base import StoryGame
from app.services.story.registry import register_story_game


class SabbatConfig(StoryConfig):
    """The config of 《魔女的夜宴》: route flags are always integers.

    魔女的夜宴的設定：路線旗標一律是整數。
    """

    @field_validator("route_presets")
    @classmethod
    def _int_flags(cls, value: dict[str, dict]) -> dict[str, dict]:
        for route, flags in value.items():
            for key, flag in flags.items():
                if not isinstance(flag, int) or isinstance(flag, bool):
                    raise ValueError(f"路線 {route} 的旗標 {key} 必須是整數")
        return value


@register_story_game("SabbatOfTheWitch")
class SabbatOfTheWitch(StoryGame):
    """The adapter for 《魔女的夜宴》 (Sabbat of the Witch).

    魔女的夜宴。
    """

    config_model = SabbatConfig
