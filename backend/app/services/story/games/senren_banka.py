"""Game adapter for 《千恋＊万花》 (Senren*Banka): only the differences from the default.

《千恋＊万花》的遊戲適配器：只放和預設不同的部分。

- Route presets may only set the in-game flowchart's branch flags (``s<file number>*<label>_sel``), and the values
  are integers (the option number, starting at 1); the route counters (Yoshino, Mako, and so on) are recomputed by
  the game in file 015 from these flags, so they are not put in the presets.

- 路線預設只能用遊戲內流程圖的分歧旗標（``s<檔號>*<標籤>_sel``），值是整數（選項序號，1 起）；
  路線計數（芳乃、茉子等）由遊戲在 015 依這些旗標重算，不放進預設。
"""

from __future__ import annotations

import re

from pydantic import field_validator

from app.schemas.story import StoryConfig
from app.services.story.base import StoryGame
from app.services.story.registry import register_story_game

_BRANCH_FLAG = re.compile(r"^s\d+[ab]?\*\S+_sel$")


class SenrenBankaConfig(StoryConfig):
    """The config of 《千恋＊万花》: route flags must be branch flags with integer values.

    千戀＊萬花的設定：路線旗標只能是分歧旗標、值為整數。
    """

    @field_validator("route_presets")
    @classmethod
    def _branch_flags(cls, value: dict[str, dict]) -> dict[str, dict]:
        for route, flags in value.items():
            for key, flag in flags.items():
                if not _BRANCH_FLAG.match(key):
                    raise ValueError(f"路線 {route} 的旗標 {key} 不是分歧旗標（s<檔號>*<標籤>_sel）")
                if not isinstance(flag, int) or isinstance(flag, bool):
                    raise ValueError(f"路線 {route} 的旗標 {key} 必須是整數")
        return value


@register_story_game("SenrenBanka")
class SenrenBanka(StoryGame):
    """The adapter for 《千恋＊万花》 (Senren*Banka).

    千戀＊萬花。
    """

    config_model = SenrenBankaConfig
