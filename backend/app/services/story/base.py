"""Base game adapter: the default behavior of every game (D14).

遊戲適配器的基底：每款遊戲的預設行為（D14）。

Game-specific adapters override only what differs (for example route-flag validation or anchor parsing).

各遊戲的適配器只覆寫不同的部分（例如路線旗標的驗證或錨點解析）。
"""

from __future__ import annotations

from typing import Any

from app.schemas.story import StoryConfig
from app.services.story.graph_index import GraphIndex
from app.services.story.repository import LineRecord


class StoryGame:
    """The default game adapter.

    預設的遊戲適配器。
    """

    #: The game id; set at registration.
    #: 遊戲代號；註冊時設定。
    game_id: str = ""
    #: The config model of this game (may be replaced by a stricter subclass).
    #: 這款遊戲的設定模型（可以換成更嚴格的子類別）。
    config_model: type[StoryConfig] = StoryConfig

    def parse_config(self, raw: dict[str, Any]) -> StoryConfig:
        """Validate and parse ``story_builds.config``.

        驗證並解析 ``story_builds.config``。

        Args:
            raw: the stored config
                存下來的設定

        Returns:
            the config parsed with ``config_model``
            以 ``config_model`` 解析後的設定

        Raises:
            ValidationError: if ``raw`` does not match ``config_model``
                若 ``raw`` 不符合 ``config_model``
        """
        return self.config_model.model_validate(raw)

    def save_position(self, line: LineRecord) -> dict[str, Any] | None:
        """Return the story position of a save for ``line``.

        回傳存檔的劇情位置。

        By default this is ``<file name>*<label>`` plus the line's ``point``; a background voice (which has no point)
        uses its playback position ``attrs.plays_at`` instead (D12).

        預設：``<檔名>*<標籤>`` 加上這句的 ``point``；背景語音（沒有 point）改用它的播放位置
        ``attrs.plays_at``（D12）。

        Args:
            line: the line to save at
                要存檔的台詞

        Returns:
            ``{"scene": ..., "point": ...}``, or ``None`` if the line cannot be saved
            ``{"scene": ..., "point": ...}``；不能存時為 ``None``
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
        """Return the game variables of a save.

        回傳存檔的遊戲變數。

        Args:
            index: the graph index of the build
                版本的圖索引
            node_key: the node the save is at
                存檔所在的節點
            config: the game config
                遊戲設定
            route_preset: the route preset to apply on top; ``None`` or empty applies none, and an unknown name
                adds nothing
                要疊上的路線預設；``None`` 或空字串表示不疊，不認得的名稱不會加任何值

        Returns:
            the ``effects`` accumulated along the path to ``node_key``, overlaid with the route preset
            路徑上累加的 ``effects``，再疊上路線預設
        """
        variables = index.path_variables(node_key)
        if route_preset:
            variables.update(config.route_presets.get(route_preset, {}))
        return variables

    def last_text(self, line: LineRecord) -> str:
        """Return the line text shown in the save list, without line breaks.

        回傳存檔清單顯示的台詞（去掉換行）。

        Args:
            line: the saved line
                存檔的台詞

        Returns:
            the Japanese text without line breaks; empty if the line has no Japanese text
            去掉換行的日文台詞；沒有日文時為空字串
        """
        return (line.text_ja or "").replace("\r", "").replace("\n", "")
