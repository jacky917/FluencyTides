"""註記提供者（與遊戲無關）。Annotation providers (game-agnostic)."""

from app.services.story.annotations.base import AnnotationProvider
from app.services.story.annotations.cards import CardsAnnotationProvider

#: 依 kind 註冊的提供者。Providers keyed by kind.
PROVIDERS: dict[str, AnnotationProvider] = {"cards": CardsAnnotationProvider()}

__all__ = ["AnnotationProvider", "CardsAnnotationProvider", "PROVIDERS"]
