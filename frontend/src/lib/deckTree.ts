/**
 * 把 Anki 的牌組名（`::` 分層）組成樹。
 * Builds a tree from Anki deck names (`::` separates levels).
 */

/** 牌組樹的一個節點。A node of the deck tree. */
export interface DeckNode {
  /** 這一層的名稱，例如 `外語糾錯`。This level's name. */
  name: string
  /** 完整名稱，例如 `日本語::外語糾錯`，查詢卡片用。Full deck name. */
  fullName: string
  children: DeckNode[]
}

/** Anki 牌組名的分層符號。Anki's deck level separator. */
export const DECK_SEPARATOR = '::'

/**
 * 組成牌組樹。Anki 的清單通常已含每一層的父牌組，但即使缺了也會補上中間節點；
 * 同一層依名稱排序（不分大小寫、以 UTF-16 碼元比較，與語系無關）。
 *
 * Builds the tree, adding any missing intermediate levels; siblings are
 * sorted by name.
 *
 * @param deckNames - 牌組完整名稱的清單。Full deck names.
 * @returns 最上層的節點。The top-level nodes.
 */
export function buildDeckTree(deckNames: string[]): DeckNode[] {
  const roots: DeckNode[] = []
  const byFullName = new Map<string, DeckNode>()

  for (const deckName of deckNames) {
    const parts = deckName.split(DECK_SEPARATOR)
    let siblings = roots
    for (let depth = 0; depth < parts.length; depth++) {
      const fullName = parts.slice(0, depth + 1).join(DECK_SEPARATOR)
      let node = byFullName.get(fullName)
      if (!node) {
        node = { name: parts[depth], fullName, children: [] }
        byFullName.set(fullName, node)
        siblings.push(node)
      }
      siblings = node.children
    }
  }

  const sortLevel = (nodes: DeckNode[]) => {
    // 不分大小寫、以 `<` 比較 UTF-16 碼元：結果不隨瀏覽器語系改變（localeCompare 在不同語系下中日文與英文的先後不同）
    nodes.sort((a, b) => {
      const left = a.name.toLowerCase()
      const right = b.name.toLowerCase()
      return left < right ? -1 : left > right ? 1 : 0
    })
    nodes.forEach((node) => sortLevel(node.children))
  }
  sortLevel(roots)
  return roots
}

/**
 * 一個牌組及其所有上層的完整名稱（由上而下），用於展開到目前選取的牌組。
 * The full names of a deck and all its ancestors, top-down.
 * @param fullName - 牌組完整名稱。Full deck name.
 * @returns 例如 `['日本語', '日本語::外語糾錯']`。The ancestor chain.
 */
export function deckAncestors(fullName: string): string[] {
  const parts = fullName.split(DECK_SEPARATOR)
  return parts.map((_, index) => parts.slice(0, index + 1).join(DECK_SEPARATOR))
}

/**
 * 相對於選取牌組的路徑，清單中顯示卡片所在的子牌組用；就在選取牌組本身時為空字串。
 * The deck path relative to the selected deck, or '' for the deck itself.
 * @param deckName - 卡片所在的牌組。The card's deck.
 * @param selected - 目前選取的牌組；未選時顯示完整名稱。The selected deck.
 * @returns 相對路徑。The relative path.
 */
export function relativeDeckPath(deckName: string, selected: string | null): string {
  if (!selected) return deckName
  if (deckName === selected) return ''
  const prefix = selected + DECK_SEPARATOR
  return deckName.startsWith(prefix) ? deckName.slice(prefix.length) : deckName
}
