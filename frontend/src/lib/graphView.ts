/**
 * 知識圖譜的視角記憶：每個牌組記下縮放、中心點與各節點位置（localStorage），下次打開時從上次
 * 的佈局與視角開始，不必重新跑一次力導向排版、也不用再找一次節點。與 React 無關，方便測試。
 *
 * Remembers each deck's zoom, center and node positions so the graph reopens
 * where it was left. React-free.
 */

/** 一個牌組的視角。The remembered view of one deck. */
export interface GraphView {
  zoom: number
  center: { x: number; y: number }
  /** 節點 id → [x, y]。Node positions. */
  nodes: Record<string, [number, number]>
}

/** 最多記幾個節點的位置（避免超大的圖塞爆 localStorage）。Max remembered node positions. */
export const MAX_REMEMBERED_NODES = 5000

/** 最多記幾個牌組的視角（最近使用的優先），避免塞滿 localStorage。Max remembered decks. */
export const MAX_REMEMBERED_DECKS = 8

const KEY_PREFIX = 'kg_view:'
/** 記錄哪些牌組有存視角、最近使用的在後。Index of remembered decks, most recent last. */
const INDEX_KEY = 'kg_view_index'

/**
 * 某個牌組的儲存鍵。The storage key of a deck.
 * @param deck - 牌組名稱（空字串為全部）。The deck.
 * @returns 鍵。The key.
 */
export function graphViewKey(deck: string): string {
  return `${KEY_PREFIX}${deck}`
}

const isFiniteNumber = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)

/**
 * 檢查存下來的視角：形狀不對時為 null；位置不是有限數字的節點略過。
 * Validates a stored view.
 * @param raw - 讀出的值。The stored value.
 * @returns 視角，或 null。The view, or null.
 */
export function sanitizeGraphView(raw: unknown): GraphView | null {
  if (typeof raw !== 'object' || raw === null) return null
  const value = raw as { zoom?: unknown; center?: { x?: unknown; y?: unknown }; nodes?: unknown }
  if (!isFiniteNumber(value.zoom) || value.zoom <= 0) return null
  if (!value.center || !isFiniteNumber(value.center.x) || !isFiniteNumber(value.center.y)) return null
  const nodes: Record<string, [number, number]> = {}
  if (typeof value.nodes === 'object' && value.nodes !== null) {
    for (const [id, position] of Object.entries(value.nodes as Record<string, unknown>)) {
      if (Array.isArray(position) && isFiniteNumber(position[0]) && isFiniteNumber(position[1])) {
        nodes[id] = [position[0], position[1]]
      }
    }
  }
  return { zoom: value.zoom, center: { x: value.center.x, y: value.center.y }, nodes }
}

/**
 * 讀出某個牌組的視角；讀不到或格式不對時為 null。
 * Reads a deck's view from storage.
 * @param deck - 牌組。The deck.
 * @returns 視角，或 null。The view, or null.
 */
export function readGraphView(deck: string): GraphView | null {
  try {
    const raw = window.localStorage.getItem(graphViewKey(deck))
    return raw ? sanitizeGraphView(JSON.parse(raw)) : null
  } catch {
    return null
  }
}

/**
 * 存下某個牌組的視角（存不了時略過，例如隱私模式或空間不足）。
 * Stores a deck's view, ignoring storage failures.
 * @param deck - 牌組。The deck.
 * @param view - 視角。The view.
 */
export function writeGraphView(deck: string, view: GraphView): void {
  try {
    const storage = window.localStorage
    const { kept, evicted } = touchDeckIndex(readIndex(storage), deck)
    for (const old of evicted) storage.removeItem(graphViewKey(old))
    storage.setItem(INDEX_KEY, JSON.stringify(kept))
    storage.setItem(graphViewKey(deck), JSON.stringify(view))
  } catch {
    // 只影響下次是否從同一個視角開始
  }
}

/**
 * 只更新某個牌組的縮放與中心點，保留已存的節點位置。
 * Updates only a deck's zoom and center, keeping stored node positions.
 * @param deck - 牌組。The deck.
 * @param camera - 縮放與中心點。Zoom and center.
 */
export function writeGraphCamera(deck: string, camera: Pick<GraphView, 'zoom' | 'center'>): void {
  writeGraphView(deck, { ...camera, nodes: readGraphView(deck)?.nodes ?? {} })
}

/** 讀出牌組索引（格式不對時為空）。Reads the deck index. */
function readIndex(storage: Storage): string[] {
  try {
    const raw: unknown = JSON.parse(storage.getItem(INDEX_KEY) ?? '[]')
    return Array.isArray(raw) ? raw.filter((item): item is string => typeof item === 'string') : []
  } catch {
    return []
  }
}

/**
 * 把牌組移到索引最後（最近使用），超過上限時移除最舊的。
 * Moves a deck to the end of the index, evicting the oldest beyond the limit.
 * @param index - 目前的索引。The current index.
 * @param deck - 剛使用的牌組。The deck just used.
 * @returns 保留與要移除的牌組。Kept and evicted decks.
 */
export function touchDeckIndex(index: string[], deck: string): { kept: string[]; evicted: string[] } {
  const next = [...index.filter((item) => item !== deck), deck]
  const overflow = Math.max(0, next.length - MAX_REMEMBERED_DECKS)
  return { kept: next.slice(overflow), evicted: next.slice(0, overflow) }
}

/**
 * 從目前的節點收集位置（只收有座標的，最多 {@link MAX_REMEMBERED_NODES} 個，四捨五入到 0.1）。
 * Collects node positions for storage.
 * @param nodes - 圖上的節點（力導向排版會在上面寫 x、y）。The graph nodes.
 * @returns 節點 id → [x, y]。Node positions.
 */
export function collectNodePositions(nodes: { id?: unknown; x?: unknown; y?: unknown }[]): Record<string, [number, number]> {
  const positions: Record<string, [number, number]> = {}
  let count = 0
  for (const node of nodes) {
    if (count >= MAX_REMEMBERED_NODES) break
    if (node.id === undefined || !isFiniteNumber(node.x) || !isFiniteNumber(node.y)) continue
    positions[String(node.id)] = [Math.round(node.x * 10) / 10, Math.round(node.y * 10) / 10]
    count += 1
  }
  return positions
}

/**
 * 把記下的位置放回節點（排版從這裡開始）；回傳是否每個節點都有記下的位置——都有的話不必再跑
 * 排版，畫面會和上次一模一樣。
 * Seeds nodes with remembered positions; reports whether every node had one.
 * @param nodes - 要放上位置的節點（會被修改）。Nodes to seed, mutated.
 * @param view - 記下的視角。The remembered view.
 * @returns 是否全部都有位置。Whether every node was seeded.
 */
export function seedNodePositions(nodes: { id?: unknown; x?: number; y?: number }[], view: GraphView | null): boolean {
  if (!view || nodes.length === 0) return false
  let all = true
  for (const node of nodes) {
    const position = node.id === undefined ? undefined : view.nodes[String(node.id)]
    if (position) {
      node.x = position[0]
      node.y = position[1]
    } else {
      all = false
    }
  }
  return all
}
