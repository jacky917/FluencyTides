/**
 * 分頁起點到節點的路徑（D10 的 `pathTo`）。規則與後端 `services/story/graph_index.py` 相同，
 * 所以畫出的選取路徑就是存檔變數所走的路徑：
 * 在節點所屬分頁內、不含 `back` 與 `cross_tab` 邊，先找出從起點走得到的節點；再從目標往回走，
 * 每一步在「來源走得到」的入邊中選 `order` 最小（沒有 `order` 當 0）、其次在邊清單中最前面的那一條。
 *
 * The path from the tab start to a node. Same rule as the backend, so the
 * highlighted path is the one whose effects become save variables.
 */

import type { StoryEdge, StoryGraph } from './types'

/** 路徑結果。A path. */
export interface StoryPath {
  /** 路徑上的節點（起點在前）。Node keys, start first. */
  nodes: string[]
  /** 路徑上的邊（起點在前）。Edges, start first. */
  edges: StoryEdge[]
}

const SKIP = new Set(['back', 'cross_tab'])

/**
 * 某個分頁內從起點走得到的節點。Nodes reachable from a tab's start.
 * @param graph - 劇情圖。The graph.
 * @param tab - 分頁 key。The tab key.
 * @returns 節點 key 集合。The reachable keys.
 */
export function reachableInTab(graph: StoryGraph, tab: string): Set<string> {
  const start = graph.tabs.find((t) => t.key === tab)?.start
  const tabOf = new Map(graph.nodes.map((n) => [n.key, n.tab]))
  const out = new Map<string, string[]>()
  for (const e of graph.edges) {
    if (SKIP.has(e.kind ?? 'normal')) continue
    if (!out.has(e.from)) out.set(e.from, [])
    out.get(e.from)!.push(e.to)
  }
  const seen = new Set<string>()
  if (!start) return seen
  const queue = [start]
  seen.add(start)
  while (queue.length) {
    const cur = queue.shift()!
    for (const next of out.get(cur) ?? []) {
      if (!seen.has(next) && tabOf.get(next) === tab) {
        seen.add(next)
        queue.push(next)
      }
    }
  }
  return seen
}

/**
 * 起點到節點的路徑；走不到時回 null。The path to a node, or null.
 * @param graph - 劇情圖。The graph.
 * @param target - 目標節點。The target node key.
 * @returns 路徑或 null。The path, or null.
 */
export function pathTo(graph: StoryGraph, target: string): StoryPath | null {
  const node = graph.nodes.find((n) => n.key === target)
  if (!node) return null
  const tab = graph.tabs.find((t) => t.key === node.tab)
  if (!tab) return null
  const reach = reachableInTab(graph, node.tab)
  if (!reach.has(target)) return null
  const incoming = new Map<string, Array<{ i: number; e: StoryEdge }>>()
  graph.edges.forEach((e, i) => {
    if (SKIP.has(e.kind ?? 'normal')) return
    if (!incoming.has(e.to)) incoming.set(e.to, [])
    incoming.get(e.to)!.push({ i, e })
  })
  const edges: StoryEdge[] = []
  let cur = target
  let guard = 0
  while (cur !== tab.start && guard++ <= graph.nodes.length) {
    const candidates = (incoming.get(cur) ?? []).filter((c) => reach.has(c.e.from))
    if (!candidates.length) break
    candidates.sort((a, b) => (a.e.order ?? 0) - (b.e.order ?? 0) || a.i - b.i)
    edges.push(candidates[0].e)
    cur = candidates[0].e.from
  }
  edges.reverse()
  const nodes = edges.length ? [edges[0].from, ...edges.map((e) => e.to)] : [target]
  return { nodes, edges }
}

const isNumber = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

/**
 * 沿路徑累加 `effects`，給除錯或預覽用。規則與後端 `GraphIndex.path_variables` 相同：
 * `set` 覆寫；`add` 只在「要加的值」與「目前的值（沒有當 0）」都是數字時累加，否則略過並在
 * console 警告（後端的 schema 已擋下非數字的 `add`，這裡是防禦）。
 * Accumulates effects along a path with the backend's rule: `set`
 * overwrites, `add` applies only when both sides are numbers, otherwise it
 * is skipped with a console warning.
 * @param path - 路徑。The path.
 * @returns 變數。The variables.
 */
export function pathVariables(path: StoryPath | null): Record<string, number | string> {
  const vars: Record<string, number | string> = {}
  for (const e of path?.edges ?? []) {
    for (const eff of e.effects ?? []) {
      if (eff.op === 'set') {
        vars[eff.var] = eff.value
        continue
      }
      const current = vars[eff.var] ?? 0
      if (!isNumber(eff.value) || !isNumber(current)) {
        console.warn(`略過非數字的 add：${eff.var} ${JSON.stringify(current)} + ${JSON.stringify(eff.value)}（${e.from} → ${e.to}）`)
        continue
      }
      vars[eff.var] = current + eff.value
    }
  }
  return vars
}
