"""註記提供者介面：節點統計、搜尋索引、每句註記（D10、D11、D12）。

Annotation-provider interface: per-node stats, search index, per-line
annotations. Providers are game-agnostic; the frontend renders badges by
``kind``.
"""

from __future__ import annotations

from typing import Any, Protocol

from app.schemas.story import CardAnnotation, StoryConfig
from app.services.story.repository import StoryRepository


class AnnotationProvider(Protocol):
    """一種註記（例如卡片）。One annotation kind (e.g. cards)."""

    kind: str

    def enabled(self, config: StoryConfig) -> bool:
        """這款遊戲的設定有沒有啟用這種註記。Whether the game config enables it."""

    async def node_stats(self, repo: StoryRepository, build_id: int, config: StoryConfig) -> dict[str, dict[str, int]]:
        """``{node_key: {子分類: 數量}}``。Per-node counts."""

    async def search_index(self, repo: StoryRepository, build_id: int, config: StoryConfig) -> dict[str, dict[str, list[str]]]:
        """``{索引名: {詞: [node_key…]}}``。Search index."""

    async def line_annotations(self, repo: StoryRepository, line_ids: list[int],
                               config: StoryConfig) -> dict[int, list[CardAnnotation | Any]]:
        """``{line_id: [註記…]}``。Per-line annotations."""
