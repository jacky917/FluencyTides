"""Annotation providers (game-agnostic).

註記提供者（與遊戲無關）。
"""

from app.services.story.annotations.base import AnnotationProvider
from app.services.story.annotations.cards import CardsAnnotationProvider

#: The providers, keyed by kind.
#: 依 kind 註冊的提供者。
PROVIDERS: dict[str, AnnotationProvider] = {"cards": CardsAnnotationProvider()}

__all__ = ["AnnotationProvider", "CardsAnnotationProvider", "PROVIDERS"]
