"""遊戲適配器的基底：每款遊戲的預設行為（D14）。

Base game adapter with the default behaviour; game-specific adapters only
override what differs (e.g. route-flag validation or anchor parsing).
"""

from __future__ import annotations

from typing import Any

from app.schemas.story import StoryConfig
from app.services.story.graph_index import GraphIndex
from app.services.story.repository import LineRecord


class StoryGame:
    """預設的遊戲適配器。The default game adapter."""

    #: 遊戲代號；註冊時設定。Game id, set at registration.
    game_id: str = ""
    #: 這款遊戲的設定模型（可以換成更嚴格的子類別）。Config model.
    config_model: type[StoryConfig] = StoryConfig

    def parse_config(self, raw: dict[str, Any]) -> StoryConfig:
        """驗證並解析 ``story_builds.config``。Parse the stored config."""
        return self.config_model.model_validate(raw)

    def save_position(self, line: LineRecord) -> dict[str, Any] | None:
        """存檔的劇情位置；不能存時回 None。

        預設：``<檔名>*<標籤>`` 加上這句的 ``point``；背景語音（沒有 point）改用它的播放位置
        ``attrs.plays_at``（D12）。
        Default: ``<file>*<label>`` with the line's point; background voices
        use ``attrs.plays_at``.
        """
        if line.point is not None and line.kind != "bgv":
            label = line.label if line.label.startswith("*") else f"*{line.label}"
            return {"scene": f"{line.file_name}{label}", "point": int(line.point)}
        plays_at = (line.attrs or {}).get("plays_at")
        if isinstance(plays_at, dict) and plays_at.get("scene") and plays_at.get("point") is not None:
            return {"scene": str(plays_at["scene"]), "point": int(plays_at["point"])}
        return None

    def save_variables(self, index: GraphIndex, node_key: str, config: StoryConfig,
                       route_preset: str | None) -> dict[str, Any]:
        """存檔的遊戲變數：路徑上的 ``effects`` 累加，再疊上路線預設。Save variables."""
        variables = index.path_variables(node_key)
        if route_preset:
            variables.update(config.route_presets.get(route_preset, {}))
        return variables

    def last_text(self, line: LineRecord) -> str:
        """存檔清單顯示的台詞（去掉換行）。The line text shown in the save list."""
        return (line.text_ja or "").replace("\r", "").replace("\n", "")
