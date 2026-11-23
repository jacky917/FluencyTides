"""Build the topic tree of trilingual cards from their decks: a card owns the child deck that bears its title.

由牌組結構組出三語卡的主題樹：卡片擁有和自己標題同名的子牌組。

A card ``X`` in deck ``D`` with title ``T`` owns ``D::T``; the cards placed **directly** in ``D::T`` are its
children, and the rule repeats below them (``docs/wip/trilingual_topic_tree_FEAT_2026-11-22.md`` D2). Nothing else
is stored: the graph is whatever the Anki decks say. A child deck that no card in ``D`` owns (a plain category deck,
or the deck of a card that was deleted or retitled) is shown as a deck node one can still enter (D5).

在牌組 ``D``、標題 ``T`` 的卡 ``X`` 擁有 ``D::T``；**直接**放在 ``D::T`` 裡的卡就是它的子卡，更下層照同樣規則
（``docs/wip/trilingual_topic_tree_FEAT_2026-11-22.md`` D2）。不另外存任何東西：Anki 的牌組怎麼放，圖就怎麼畫。
``D`` 裡沒有卡擁有的子牌組（純分類用的牌組，或卡被刪掉、改了標題後留下的牌組）畫成可以進入的牌組節點（D5）。

This module is pure (no Anki calls), so every rule is tested without a fake Anki.

本模組是純函式（不呼叫 Anki），所以每條規則都不需要假的 Anki 就能測。
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Anki's deck separator.
#: Anki 的牌組分隔符號。
DECK_SEPARATOR = "::"

#: How many characters of the prompt label a card that has no usable title.
#: 沒有可用標題的卡，節點名稱取 Prompt 的前幾個字。
PROMPT_LABEL_LENGTH = 20

#: The label of the virtual root when no deck is chosen.
#: 沒有選牌組時虛擬根節點的名稱。
ALL_DECKS_LABEL = "全部牌組"


@dataclass(frozen=True)
class TreeCard:
    """One trilingual card as the tree sees it.

    主題樹看到的一張三語卡。

    Attributes:
        note_id: the Anki note ID
            Anki 的筆記 ID
        deck: the full name of the deck the card is in
            卡片所在牌組的完整名稱
        title: the usable title (``trilingual_title.usable_title``), or ``None``
            可用的標題（``trilingual_title.usable_title``），沒有時為 ``None``
        prompt: the prompt text, HTML removed
            情境提示（已去掉 HTML）
    """

    note_id: int
    deck: str
    title: str | None
    prompt: str

    @property
    def label(self) -> str:
        """Return the node label: the title, or the start of the prompt, or the note ID.

        回傳節點名稱：標題；沒有就用 Prompt 的開頭；都沒有就用 Note ID。
        """
        if self.title:
            return self.title
        prompt = " ".join(self.prompt.split())
        if not prompt:
            return str(self.note_id)
        return prompt if len(prompt) <= PROMPT_LABEL_LENGTH else prompt[:PROMPT_LABEL_LENGTH] + "…"


@dataclass
class _Folder:
    """What lives directly in one deck: its cards and its direct child decks (by casefolded last segment).

    一個牌組裡直接放著的東西：卡片，以及直接的子牌組（以最後一段的 casefold 為鍵）。
    """

    cards: list[TreeCard] = field(default_factory=list)
    subdecks: dict[str, str] = field(default_factory=dict)


def _key(deck: str) -> str:
    """Return the comparison key of a deck name (Anki compares deck names case-insensitively).

    回傳牌組名的比對鍵（Anki 比對牌組名不分大小寫）。
    """
    return deck.casefold()


def _parent(deck: str) -> str | None:
    """Return the parent deck name, or ``None`` for a top-level deck.

    回傳上一層牌組名；最上層的牌組為 ``None``。
    """
    head, sep, _ = deck.rpartition(DECK_SEPARATOR)
    return head if sep else None


def _last(deck: str) -> str:
    """Return the last segment of a deck name.

    回傳牌組名的最後一段。
    """
    return deck.rpartition(DECK_SEPARATOR)[2]


class TopicTree:
    """The decks and trilingual cards under a root, indexed for building the graph.

    根牌組以下的牌組與三語卡，整理成方便組圖的索引。

    Only decks that hold a trilingual card somewhere below them take part: other decks in the collection (word
    cards, verb cards) never show up as empty deck nodes.

    只有底下某處有三語卡的牌組才算進來：收藏裡的其他牌組（單字卡、動詞卡）不會變成空的牌組節點。

    Args:
        cards: the trilingual cards (any decks; those outside ``root`` are ignored)
            三語卡（任何牌組；不在 ``root`` 以下的會被忽略）
        root: the full name of the root deck; ``None`` means every deck
            根牌組的完整名稱；``None`` 表示全部牌組
    """

    def __init__(self, cards: list[TreeCard], root: str | None) -> None:
        self.root = root
        self._folders: dict[str, _Folder] = {}
        # 所有卡依牌組分類（不限根以下）：找「擁有根牌組的卡」時，那張卡在根的上一層
        self._cards_by_deck: dict[str, list[TreeCard]] = {}
        root_key = _key(root) if root else None
        for card in sorted(cards, key=lambda c: c.note_id):
            self._cards_by_deck.setdefault(_key(card.deck), []).append(card)
            deck_key = _key(card.deck)
            if root_key and deck_key != root_key and not deck_key.startswith(root_key + DECK_SEPARATOR):
                continue
            self._folder(card.deck).cards.append(card)
            # 一路往上登記子牌組，直到根；沒有根時登記到最上層，掛在虛擬根（""）底下
            deck = card.deck
            while (root_key is None or _key(deck) != root_key) and (parent := _parent(deck)) is not None:
                self._folder(parent).subdecks.setdefault(_key(_last(deck)), deck)
                deck = parent
            if root_key is None:
                self._folder("").subdecks.setdefault(_key(deck), deck)

    def _folder(self, deck: str) -> _Folder:
        """Return the folder of a deck, creating it on first use (``""`` is the virtual root).

        回傳牌組的資料夾，第一次用到時建立（``""`` 是虛擬根）。
        """
        return self._folders.setdefault(_key(deck), _Folder())

    def children(self, deck: str) -> list[tuple[str, TreeCard | None, str | None, bool]]:
        """List what to draw directly under a deck.

        列出一個牌組底下直接要畫的東西。

        Cards come first in note-ID order, each with the child deck it owns (if that deck exists); child decks no
        card owns follow as deck nodes, sorted by name. When two cards in the deck share a title, only the first
        (lowest note ID) owns the child deck and the other is flagged.

        先列卡片（依 Note ID），各帶它擁有的子牌組（該牌組存在時）；沒有卡擁有的子牌組接在後面，當成牌組節點，
        依名稱排序。同一個牌組裡兩張卡標題相同時，只有第一張（Note ID 最小）擁有子牌組，另一張會被標記。

        Args:
            deck: the full deck name; ``""`` means the virtual root (every top-level deck)
                牌組完整名稱；``""`` 表示虛擬根（所有最上層牌組）

        Returns:
            ``(kind, card, child_deck, duplicate_title)`` tuples, where ``kind`` is ``"card"`` or ``"deck"``; for a
            deck node ``card`` is ``None`` and ``child_deck`` is the deck itself
            ``(kind, card, child_deck, duplicate_title)``；``kind`` 是 ``"card"`` 或 ``"deck"``；牌組節點的
            ``card`` 為 ``None``，``child_deck`` 是牌組本身
        """
        folder = self._folders.get(_key(deck), _Folder())
        owned: set[str] = set()
        result: list[tuple[str, TreeCard | None, str | None, bool]] = []
        for card in folder.cards:
            title_key = _key(card.title) if card.title else None
            duplicate = title_key is not None and title_key in owned
            child = folder.subdecks.get(title_key) if title_key and not duplicate else None
            if title_key and not duplicate:
                owned.add(title_key)
            result.append(("card", card, child, duplicate))
        for name_key, subdeck in sorted(folder.subdecks.items(), key=lambda item: item[1].casefold()):
            if name_key not in owned:
                result.append(("deck", None, subdeck, False))
        return result

    def owner_of(self, deck: str) -> TreeCard | None:
        """Return the card in the parent deck whose title names this deck, if any.

        回傳上一層牌組裡、標題就是這個牌組名的卡（沒有就是 ``None``）。

        When several cards there share that title, the one with the lowest note ID owns it, as in
        :meth:`children`.

        上一層有好幾張卡標題相同時，和 :meth:`children` 一樣由 Note ID 最小的那張擁有。

        Args:
            deck: the full deck name
                牌組完整名稱

        Returns:
            the owning card, or ``None``
            擁有這個牌組的卡，沒有時為 ``None``
        """
        parent = _parent(deck)
        if parent is None:
            return None
        name_key = _key(_last(deck))
        for card in self._cards_by_deck.get(_key(parent), []):
            if card.title and _key(card.title) == name_key:
                return card
        return None


def build_graph(cards: list[TreeCard], root: str | None, depth: int) -> dict[str, object]:
    """Return the tree graph under ``root``, expanded ``depth`` levels deep.

    回傳 ``root`` 以下、展開 ``depth`` 層的樹狀圖譜。

    The root node is the card that owns ``root`` when there is one (so it opens the card's details), otherwise a
    deck node; without ``root`` it is a virtual "every deck" node. Every node carries ``child_count``, the number of
    nodes directly under it, also when it lies on the last expanded level, so the frontend can show "3" on a
    folded node.

    根節點：有卡擁有 ``root`` 時就是那張卡（點了開卡片詳情），否則是牌組節點；沒有 ``root`` 時是虛擬的「全部牌組」。
    每個節點都帶 ``child_count``（直接在它底下的節點數），在最後一層展開的節點也有，前端才能在收起的節點上顯示數字。

    Args:
        cards: every trilingual card that may be under ``root`` (cards in the parent deck of ``root`` are used to
            find the card that owns it)
            可能在 ``root`` 以下的所有三語卡（``root`` 上一層的卡用來找擁有它的卡）
        root: the full name of the root deck; ``None`` means every deck
            根牌組的完整名稱；``None`` 表示全部牌組
        depth: how many levels to expand below the root; ``0`` or less means all of them
            根以下展開幾層；``0`` 以下表示全部

    Returns:
        ``{"nodes", "links", "root"}``: nodes carry ``id``, ``kind`` (``root`` / ``card`` / ``deck``), ``label``,
        ``deck``, ``child_deck``, ``child_count``, ``depth``, and, for cards, ``note_id``, ``title`` (``None`` when
        unusable), ``prompt`` and ``duplicate_title``; links go from parent to child with ``label`` ``"child"``;
        ``root`` is the root node's ID
        ``{"nodes", "links", "root"}``：節點帶 ``id``、``kind``（``root`` / ``card`` / ``deck``）、``label``、
        ``deck``、``child_deck``、``child_count``、``depth``，卡片另帶 ``note_id``、``title``（不能用時為 ``None``）、
        ``prompt`` 與 ``duplicate_title``；連線由父指向子，``label`` 為 ``"child"``；``root`` 是根節點的 ID
    """
    tree = TopicTree(cards, root)
    limit = depth if depth > 0 else None

    nodes: list[dict[str, object]] = []
    links: list[dict[str, object]] = []
    root_deck = root or ""
    owner = tree.owner_of(root) if root else None
    root_id = "root"
    root_node: dict[str, object] = {
        "id": root_id,
        "kind": "root",
        "label": owner.label if owner else (_last(root) if root else ALL_DECKS_LABEL),
        "deck": root,
        "child_deck": root,
        "child_count": len(tree.children(root_deck)),
        "depth": 0,
    }
    if owner:
        root_node.update(note_id=owner.note_id, title=owner.title, prompt=owner.prompt)
    nodes.append(root_node)

    queue: list[tuple[str, str, int]] = [(root_id, root_deck, 0)]
    seen_decks = {_key(root_deck)}
    while queue:
        parent_id, deck, level = queue.pop(0)
        if limit is not None and level >= limit:
            continue
        for kind, card, child_deck, duplicate in tree.children(deck):
            node_id = f"n:{card.note_id}" if card else f"d:{child_deck}"
            count = len(tree.children(child_deck)) if child_deck else 0
            node: dict[str, object] = {
                "id": node_id,
                "kind": kind,
                "label": card.label if card else _last(child_deck or ""),
                "deck": card.deck if card else child_deck,
                "child_deck": child_deck if count else None,
                "child_count": count,
                "depth": level + 1,
            }
            if card:
                node.update(note_id=card.note_id, title=card.title, prompt=card.prompt, duplicate_title=duplicate)
            nodes.append(node)
            links.append({"source": parent_id, "target": node_id, "label": "child"})
            if child_deck and count and _key(child_deck) not in seen_decks:
                seen_decks.add(_key(child_deck))
                queue.append((node_id, child_deck, level + 1))
    return {"nodes": nodes, "links": links, "root": root_id}
