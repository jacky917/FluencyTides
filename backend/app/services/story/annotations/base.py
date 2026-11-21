"""Annotation-provider interface: per-node stats, search index, and per-line annotations (D10, D11, D12).

註記提供者介面：節點統計、搜尋索引、每句註記（D10、D11、D12）。

Providers are game-agnostic; the frontend renders badges by ``kind``.

提供者與遊戲無關；前端依 ``kind`` 顯示徽章。
"""

from __future__ import annotations

from typing import Any, Protocol

from app.schemas.story import CardAnnotation, StoryConfig
from app.services.story.repository import StoryRepository


class AnnotationProvider(Protocol):
    """One annotation kind (for example cards).

    一種註記（例如卡片）。
    """

    kind: str

    def enabled(self, config: StoryConfig) -> bool:
        """Return whether this game's config enables this annotation kind.

        回傳這款遊戲的設定有沒有啟用這種註記。

        Args:
            config: the game config
                遊戲設定

        Returns:
            ``True`` if enabled
            啟用時為 ``True``
        """

    async def node_stats(self, repo: StoryRepository, build_id: int, config: StoryConfig) -> dict[str, dict[str, int]]:
        """Return the per-node counts.

        回傳每個節點的統計。

        Args:
            repo: the story repository
                劇情資料庫存取物件
            build_id: the build to count in
                要統計的版本
            config: the game config
                遊戲設定

        Returns:
            ``{node_key: {subcategory: count}}``
            ``{node_key: {子分類: 數量}}``
        """

    async def search_index(self, repo: StoryRepository, build_id: int, config: StoryConfig) -> dict[str, dict[str, list[str]]]:
        """Return the search index.

        回傳搜尋索引。

        Args:
            repo: the story repository
                劇情資料庫存取物件
            build_id: the build to index
                要建索引的版本
            config: the game config
                遊戲設定

        Returns:
            ``{index name: {term: [node_key, ...]}}``
            ``{索引名: {詞: [node_key…]}}``
        """

    async def line_annotations(self, repo: StoryRepository, line_ids: list[int],
                               config: StoryConfig) -> dict[int, list[CardAnnotation | Any]]:
        """Return the annotations of each line.

        回傳每句的註記。

        Args:
            repo: the story repository
                劇情資料庫存取物件
            line_ids: the lines to annotate
                要查註記的台詞
            config: the game config
                遊戲設定

        Returns:
            ``{line_id: [annotation, ...]}``; lines without annotations may be absent
            ``{line_id: [註記…]}``；沒有註記的台詞可以不出現
        """
