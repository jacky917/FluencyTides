"""卡片註記：讀 ``rel_links`` 的 ``core.example_from``（筆記 → 台詞）與 ``core.teaches``（筆記 → 詞）。

Card annotations from ``rel_links``: ``core.example_from`` (note → line)
and ``core.teaches`` (note → term). Only card types listed in the game's
``annotations.cards.card_types`` are reported.

節點統計的數字是「有這種卡的台詞句數」：一句台詞的情境卡與填空卡都指向它，算筆記數會重複。
Node counts are the number of lines carrying that card type (each card
generation links both its context and cloze notes to the same line).
"""

from __future__ import annotations

from collections import defaultdict

from app.schemas.story import CardAnnotation, StoryConfig
from app.services.story.repository import StoryRepository


class CardsAnnotationProvider:
    """``cards`` 註記。The ``cards`` annotation kind."""

    kind = "cards"

    @staticmethod
    def _types(config: StoryConfig) -> dict[str, str]:
        cards = config.annotations.cards
        return {t.card_type: t.label for t in cards.card_types} if cards else {}

    def enabled(self, config: StoryConfig) -> bool:
        return bool(self._types(config))

    async def node_stats(self, repo: StoryRepository, build_id: int, config: StoryConfig) -> dict[str, dict[str, int]]:
        types = self._types(config)
        stats: dict[str, dict[str, int]] = defaultdict(dict)
        for node_key, card_type, count in await repo.annotation_counts(build_id):
            if card_type in types:
                stats[node_key][card_type] = stats[node_key].get(card_type, 0) + count
        return dict(stats)

    async def search_index(self, repo: StoryRepository, build_id: int, config: StoryConfig) -> dict[str, dict[str, list[str]]]:
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
