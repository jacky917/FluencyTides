"""Game-adapter registry: ``@register_story_game("<game_id>")`` (modeled on ``task_handlers/registry.py``).

遊戲適配器註冊表：``@register_story_game("<game_id>")``（比照 ``task_handlers/registry.py``）。

Games without an adapter get the default :class:`~app.services.story.base.StoryGame`.

沒有適配器的遊戲使用預設的 ``StoryGame``。
"""

from __future__ import annotations

from typing import Callable, TypeVar

from app.services.story.base import StoryGame

_GAMES: dict[str, StoryGame] = {}
T = TypeVar("T", bound=type[StoryGame])


def register_story_game(game_id: str) -> Callable[[T], T]:
    """Return a class decorator that registers an adapter for ``game_id``.

    回傳把適配器註冊到 ``game_id`` 的類別裝飾器。

    The decorated class is instantiated once, at decoration time, with ``game_id`` set; registering the same
    ``game_id`` again replaces the earlier adapter.

    被裝飾的類別在裝飾時建立一個實例並設定 ``game_id``；同一個 ``game_id`` 再註冊會取代先前的適配器。

    Args:
        game_id: the game id to register under
            要註冊的遊戲代號

    Returns:
        the decorator, which returns the class unchanged
        裝飾器，原樣回傳類別
    """

    def decorator(cls: T) -> T:
        instance = cls()
        instance.game_id = game_id
        _GAMES[game_id] = instance
        return cls

    return decorator


def get_story_game(game_id: str) -> StoryGame:
    """Get the adapter for a game.

    取適配器。

    Args:
        game_id: the game id
            遊戲代號

    Returns:
        the registered adapter, or a new default ``StoryGame`` with ``game_id`` set if none is registered
        註冊的適配器；沒註冊時回傳設定好 ``game_id`` 的新預設 ``StoryGame``
    """
    if game_id in _GAMES:
        return _GAMES[game_id]
    default = StoryGame()
    default.game_id = game_id
    return default


def _load_builtin_games() -> None:
    from app.services.story.games import sabbat_of_the_witch  # noqa: F401 - 註冊副作用
    from app.services.story.games import senren_banka  # noqa: F401 - 註冊副作用


_load_builtin_games()
