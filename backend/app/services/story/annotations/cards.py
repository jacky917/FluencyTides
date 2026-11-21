"""Card annotations read from ``rel_links``: ``core.example_from`` (note → line) and ``core.teaches`` (note → term).

卡片註記：讀 ``rel_links`` 的 ``core.example_from``（筆記 → 台詞）與 ``core.teaches``（筆記 → 詞）。

Only card types listed in the game's ``annotations.cards.card_types`` are reported.

只回報遊戲設定 ``annotations.cards.card_types`` 列出的卡片類型。

A node count is the number of lines carrying that card type: a line's context card and cloze card both point to
it, so counting notes would count it twice.

節點統計的數字是「有這種卡的台詞句數」：一句台詞的情境卡與填空卡都指向它，算筆記數會重複。
"""

from __future__ import annotations

from collections import defaultdict

from app.schemas.story import CardAnnotation, StoryConfig
from app.services.story.repository import StoryRepository


class CardsAnnotationProvider:
    """The ``cards`` annotation kind.

    ``cards`` 註記。
    """

    kind = "cards"

    @staticmethod
    def _types(config: StoryConfig) -> dict[str, str]:
        cards = config.annotations.cards
        return {t.card_type: t.label for t in cards.card_types} if cards else {}

    def enabled(self, config: StoryConfig) -> bool:
        """Return whether the game config lists any card type.

        回傳遊戲設定有沒有列出任何卡片類型。

        Args:
            config: the game config
                遊戲設定

        Returns:
            ``True`` if ``annotations.cards.card_types`` is not empty
            ``annotations.cards.card_types`` 不為空時為 ``True``
        """
        return bool(self._types(config))

    async def node_stats(self, repo: StoryRepository, build_id: int, config: StoryConfig) -> dict[str, dict[str, int]]:
        """Return, per node, the number of lines carrying each listed card type.

        回傳每個節點中，有各種已列出卡片類型的台詞句數。

        Args:
            repo: the story repository
                劇情資料庫存取物件
            build_id: the build to count in
                要統計的版本
            config: the game config
                遊戲設定

        Returns:
            ``{node_key: {card_type: line count}}``; unlisted card types are left out
            ``{node_key: {card_type: 台詞句數}}``；未列出的卡片類型不計
        """
        types = self._types(config)
        stats: dict[str, dict[str, int]] = defaultdict(dict)
        for node_key, card_type, count in await repo.annotation_counts(build_id):
            if card_type in types:
                stats[node_key][card_type] = stats[node_key].get(card_type, 0) + count
        return dict(stats)

    async def search_index(self, repo: StoryRepository, build_id: int, config: StoryConfig) -> dict[str, dict[str, list[str]]]:
        """Return the verb search index of a build.

        回傳一個版本的動詞搜尋索引。

        Args:
            repo: the story repository
                劇情資料庫存取物件
            build_id: the build to index
                要建索引的版本
            config: the game config
                遊戲設定

        Returns:
            ``{"verbs": {verb: [node_key, ...]}}`` across all listed card types, plus one
            ``"verbs:<card_type>"`` index per card type; verbs and node keys are sorted
            跨所有已列出卡片類型的 ``{"verbs": {動詞: [node_key…]}}``，再加上每種卡片類型各一個
            ``"verbs:<card_type>"`` 索引；動詞與 node_key 皆已排序
        """
        types = self._types(config)
        verbs: dict[str, set[str]] = defaultdict(set)
        by_type: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        for node_key, card_type, verb in await repo.verb_index(build_id):
            if card_type in types and verb:
                verbs[verb].add(node_key)
                by_type[card_type][verb].add(node_key)
        index = {"verbs": {v: sorted(keys) for v, keys in sorted(verbs.items())}}
        for card_type, mapping in by_type.items():
            index[f"verbs:{card_type}"] = {v: sorted(keys) for v, keys in sorted(mapping.items())}
        return index

    async def line_annotations(self, repo: StoryRepository, line_ids: list[int],
                               config: StoryConfig) -> dict[int, list[CardAnnotation]]:
        """Return the card annotations of each line, one per card type and verb.

        回傳每句的卡片註記，每種卡片類型與動詞各一則。

        Args:
            repo: the story repository
                劇情資料庫存取物件
            line_ids: the lines to annotate
                要查註記的台詞
            config: the game config
                遊戲設定

        Returns:
            ``{line_id: [CardAnnotation, ...]}`` sorted by card type and verb, each with its sorted note ids;
            lines without listed cards are absent
            ``{line_id: [CardAnnotation…]}``，依卡片類型與動詞排序，各帶排序後的筆記 ID；沒有已列出卡片的台詞不出現
        """
        types = self._types(config)
        grouped: dict[tuple[int, str, str | None], set[int]] = defaultdict(set)
        for link in await repo.card_links(line_ids):
            if link.card_type in types:
                grouped[(link.line_id, link.card_type, link.verb)].add(link.note_id)
        out: dict[int, list[CardAnnotation]] = defaultdict(list)
        for (line_id, card_type, verb), notes in sorted(grouped.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] or "")):
            out[line_id].append(CardAnnotation(card_type=card_type, label=types[card_type], verb=verb,
                                               note_ids=sorted(notes)))
        return dict(out)
