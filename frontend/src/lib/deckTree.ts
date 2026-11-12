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
  /**
   * Anki 裡有沒有這個牌組。清單裡沒有、只是因為底下有牌組才補上的中間節點為 false（「分類節點」，
   * 不能建卡，可以在牌組樹補建）。
   * Whether the deck exists in Anki; false for an intermediate level added only because a deck below it exists.
   */
  exists: boolean
  children: DeckNode[]
}

/** Anki 牌組名的分層符號。Anki's deck level separator. */
export const DECK_SEPARATOR = '::'

/** 牌組全名的長度上限（與後端 `MAX_DECK_NAME_LENGTH` 相同）。Maximum length of a full deck name. */
export const MAX_DECK_NAME_LENGTH = 200

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
  const listed = new Set(deckNames)

  for (const deckName of deckNames) {
    const parts = deckName.split(DECK_SEPARATOR)
    let siblings = roots
    for (let depth = 0; depth < parts.length; depth++) {
      const fullName = parts.slice(0, depth + 1).join(DECK_SEPARATOR)
      let node = byFullName.get(fullName)
      if (!node) {
        node = { name: parts[depth], fullName, exists: listed.has(fullName), children: [] }
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
 * 牌組名是不是「分類節點」：Anki 裡沒有這個牌組，但有牌組在它底下（牌組樹為了分層補上的中間節點）。
 * Whether a name is only a category: not a deck in Anki, but a deck exists below it.
 * @param deckNames - Anki 的牌組完整名稱清單。Full deck names in Anki.
 * @param fullName - 要判斷的名稱。The name to check.
 * @returns 是否為分類節點。Whether it is a category node.
 */
export function isCategoryDeck(deckNames: string[], fullName: string): boolean {
  if (deckNames.includes(fullName)) return false
  const prefix = fullName + DECK_SEPARATOR
  return deckNames.some((name) => name.startsWith(prefix))
}

/**
 * 接出子牌組的完整名稱；`parent` 為 null 時就是最上層。
 * Joins a parent and what was typed into a full deck name; a null parent means the top level.
 * @param parent - 上層牌組的完整名稱。The parent's full name.
 * @param typed - 輸入的名稱（可含 `::` 一次建多層）。The typed name.
 * @returns 完整名稱（未整理空白）。The full name, not yet trimmed.
 */
export function joinDeckName(parent: string | null, typed: string): string {
  return parent === null ? typed : parent + DECK_SEPARATOR + typed
}

/**
 * 檢查新牌組的完整名稱，規則與訊息和後端的 `normalize_deck_name` 相同（deck_create 計畫書 §4）：
 * 以 `::` 分層，每一層去掉前後空白後不能是空的；整理後的全名不超過長度上限。其他字元交給 Anki 判斷。
 * Validates a new deck's full name with the same rules and messages as the backend.
 * @param fullName - 完整名稱。The full name.
 * @returns 錯誤訊息；合格時為 null。The error message, or null when valid.
 */
export function validateDeckName(fullName: string): string | null {
  if (!fullName.trim()) return '牌組名稱不能是空的'
  const levels = fullName.split(DECK_SEPARATOR).map((level) => level.trim())
  const empty = levels.findIndex((level) => level === '')
  if (empty >= 0) return `牌組名稱的第 ${empty + 1} 層是空的`
  // 與後端同樣以 Unicode 字元（code point）計數，表情符號等不會被算成兩個
  const length = Array.from(levels.join(DECK_SEPARATOR)).length
  if (length > MAX_DECK_NAME_LENGTH) return `牌組名稱最長 ${MAX_DECK_NAME_LENGTH} 字（目前 ${length} 字）`
  return null
}

/**
 * 新增牌組失敗時訊息放哪裡：後端回 400（名稱不合格）、409（已存在）是使用者改名稱就能解決的，顯示在
 * 輸入列下方（`inline`）；其他（Anki 連不上、閘道逾時、網路中斷）用 toast，而且牌組可能其實已經建好。
 * Where a failed create is reported: a backend 400 or 409 goes under the input, anything else to a toast.
 * @param status - HTTP 狀態碼；沒有回應時為 undefined。The HTTP status, if any.
 * @param backendError - 是不是後端的業務錯誤（`isBackendError`）。Whether it is a backend business error.
 * @returns `inline` 或 `toast`。Where to show it.
 */
export function deckCreateErrorPlacement(status: number | undefined, backendError: boolean): 'inline' | 'toast' {
  return backendError && (status === 400 || status === 409) ? 'inline' : 'toast'
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
