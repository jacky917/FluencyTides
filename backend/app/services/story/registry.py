"""遊戲適配器註冊表：``@register_story_game("<game_id>")``（比照 ``task_handlers/registry.py``）。

Game-adapter registry. Games without an adapter get the default
:class:`~app.services.story.base.StoryGame`.
"""

from __future__ import annotations

from typing import Callable, TypeVar

from app.services.story.base import StoryGame

_GAMES: dict[str, StoryGame] = {}
T = TypeVar("T", bound=type[StoryGame])


def register_story_game(game_id: str) -> Callable[[T], T]:
    """類別裝飾器：把適配器註冊到 ``game_id``。Register an adapter class for a game."""

    def decorator(cls: T) -> T:
        instance = cls()
        instance.game_id = game_id
        _GAMES[game_id] = instance
        return cls

    return decorator


def get_story_game(game_id: str) -> StoryGame:
    """取適配器；沒註冊時回預設行為。The adapter for a game, or the default."""
    if game_id in _GAMES:
        return _GAMES[game_id]
    default = StoryGame()
    default.game_id = game_id
    return default


def _load_builtin_games() -> None:
    from app.services.story.games import sabbat_of_the_witch  # noqa: F401 - 註冊副作用


_load_builtin_games()
