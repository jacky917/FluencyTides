"""《魔女的夜宴》的遊戲適配器：只放和預設不同的部分。

Adapter for Sabbat of the Witch; only the differences from the default.

- 路線預設只能是設定裡有的分頁，而且旗標必須是整數（遊戲的 ``f.*_flag`` 都是整數）。
- Route presets must name a known tab and carry integer flags.
"""

from __future__ import annotations

from pydantic import field_validator

from app.schemas.story import StoryConfig
from app.services.story.base import StoryGame
from app.services.story.registry import register_story_game


class SabbatConfig(StoryConfig):
    """魔女的夜宴的設定：路線旗標一律是整數。Config with integer route flags."""

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
    """魔女的夜宴。Sabbat of the Witch."""

    config_model = SabbatConfig
