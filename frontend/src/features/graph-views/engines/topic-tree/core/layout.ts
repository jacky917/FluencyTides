/**
 * 主題樹的版面（`docs/wip/trilingual_topic_tree_FEAT_2026-11-22.md` D3）：由左往右的樹，每一層一欄、每個葉節點一列，
 * 父節點對齊在它第一個與最後一個子節點的中間。座標固定，不跑力導向排版，同一層節點多時也不會重疊。
 *
 * The topic tree layout: one column per level, one row per leaf, parents centred on their children; fixed
 * coordinates instead of a force simulation.
 */

import type { TopicTreeLink, TopicTreeNode } from './types'

/** 一欄的寬度（圖的座標單位）。The column width, in graph units. */
export const LEVEL_GAP = 240
/** 一列的高度。The row height. */
export const ROW_GAP = 52
/** 節點框的寬度與高度。The node box size. */
export const NODE_WIDTH = 170
export const NODE_HEIGHT = 34

/** 根節點的 id（後端 `topic_tree.build_graph`）。The root node id. */
export const ROOT_ID = 'root'

/** 一個節點的座標（框的中心）。A node position, at the box centre. */
export interface TreePosition {
  x: number
  y: number
}

/**
 * 連線的端點：後端給的是 id；交給 react-force-graph 之後會被換成節點物件。
 * A link end, either the id from the API or the node object force-graph swaps in.
 */
export function linkEnd(end: TopicTreeLink['source'] | { id: string }): string {
  return typeof end === 'string' ? end : end.id
}

/**
 * 算出每個節點的座標；從根走不到的節點不給座標（不畫）。
 * Positions every node reachable from the root.
 * @param nodes - 節點。The nodes.
 * @param links - 父指向子的連線，順序就是子節點由上到下的順序。Parent-to-child links, in display order.
 * @param rootId - 根節點 id。The root id.
 * @returns id → 座標。Positions by node id.
 */
export function layoutTree(nodes: TopicTreeNode[], links: TopicTreeLink[], rootId: string = ROOT_ID): Map<string, TreePosition> {
  const known = new Set(nodes.map((node) => node.id))
  const children = new Map<string, string[]>()
  for (const link of links) {
    const source = linkEnd(link.source)
    const target = linkEnd(link.target)
    if (!known.has(source) || !known.has(target)) continue
    children.set(source, [...(children.get(source) ?? []), target])
  }
  const positions = new Map<string, TreePosition>()
  if (!known.has(rootId)) return positions
  let row = 0
  // 深度優先：葉節點依序佔一列，父節點放在子節點的中間；已放過的節點不再走（防止資料有環）
  const place = (id: string, level: number): number => {
    const kids = (children.get(id) ?? []).filter((kid) => !positions.has(kid) && kid !== id)
    positions.set(id, { x: level * LEVEL_GAP, y: 0 })
    if (kids.length === 0) {
      const y = row * ROW_GAP
      row += 1
      positions.set(id, { x: level * LEVEL_GAP, y })
      return y
    }
    const ys = kids.map((kid) => place(kid, level + 1))
    const y = (ys[0] + ys[ys.length - 1]) / 2
    positions.set(id, { x: level * LEVEL_GAP, y })
    return y
  }
  place(rootId, 0)
  return positions
}

/**
 * 這個節點的子節點有沒有畫在圖上；沒有就在框上顯示子卡數（收起的節點）。
 * Whether the node has children that are not drawn, so its box shows the count.
 * @param node - 節點。The node.
 * @param links - 連線。The links.
 * @returns 有子節點但都沒畫出來時為 true。True when folded.
 */
export function isFolded(node: TopicTreeNode, links: TopicTreeLink[]): boolean {
  if (!node.child_count) return false
  return !links.some((link) => linkEnd(link.source) === node.id)
}

/**
 * 點節點要做什麼：有子節點就進入子牌組；沒有子節點的卡片（含根節點的主題卡）開卡片詳情；其他不做事。
 * 根節點本身就是目前所在的牌組，所以點根節點不會「進入」。
 * What a click on a node does.
 * @param node - 節點。The node.
 * @returns 進入的牌組、要開的筆記，或 null。The deck to enter, the note to open, or null.
 */
export function nodeAction(node: TopicTreeNode): { enter: string } | { open: number } | null {
  if (node.kind !== 'root' && node.child_deck && node.child_count) return { enter: node.child_deck }
  if (node.note_id) return { open: node.note_id }
  return null
}

/**
 * 麵包屑：牌組路徑的每一段與點了要回到的牌組。
 * The breadcrumb of a deck path.
 * @param deck - 牌組完整名稱；null 表示全部牌組。The deck, or null for all decks.
 * @returns 每一段的名稱與對應的牌組（第一段是「全部牌組」，對應 null）。The crumbs.
 */
export function deckCrumbs(deck: string | null): { label: string; deck: string | null }[] {
  const crumbs: { label: string; deck: string | null }[] = [{ label: '全部牌組', deck: null }]
  if (!deck) return crumbs
  const parts = deck.split('::')
  parts.forEach((part, index) => crumbs.push({ label: part, deck: parts.slice(0, index + 1).join('::') }))
  return crumbs
}
