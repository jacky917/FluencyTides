/**
 * 劇情流程圖的排版（D8）：先算邏輯格位（層、欄），再投影成幾何座標。兩步都是純函式。
 * Story-flowchart layout in two pure steps: logical grid (rank, lane), then
 * geometric projection. A horizontal layout would only need another
 * `project` function.
 *
 * 格位規則（千戀＊萬花 `flowchart_spec.md` 第一部分 §5）：
 * - 最長路徑分層；合流節點的層 = 所有入邊中最長的那條 + 1；`back`、`cross_tab` 邊不參與分層。
 * - 有 k 個出邊的分歧點，第 i 個子節點的欄位偏移是 `(i − (k−1)/2) × spread`（spread 預設 1 欄），
 *   k 不設上限。
 * - 巢狀分歧以所在欄為中心展開；同一層的節點重疊時，先試著改用窄版，不行就把兩者最近的共同
 *   分歧點的 spread 加大（整組往外推）。
 * - 合流節點回到分歧點所在的欄（入邊的「分歧堆疊」最長共同前綴的最後一個分歧點）。
 * - 章節帶插在章節改變的那一層之前（以最靠近主欄、有章節的節點為準）。
 */

import type { GraphThemeSize } from '../../../core/theme'
import { DEFAULT_SIZE } from '../../../core/theme'
import type { StoryEdge, StoryGraph, StoryNode } from './types'

/** 一個節點的邏輯格位。A node's logical cell. */
export interface GridCell {
  key: string
  rank: number
  /** 欄（以 1 欄為單位，可以是小數）；主欄是 0。Lane in column units; main lane is 0. */
  lane: number
  /** 改用窄版節點。Use the narrow node width. */
  narrow: boolean
}

/** 章節帶：插在 `rank` 那一層之前。A chapter band inserted before `rank`. */
export interface GridBand {
  rank: number
  label: string
}

/** 邏輯格位的結果。The logical grid. */
export interface StoryGrid {
  tab: string
  cells: Record<string, GridCell>
  /** 依層、欄排序的節點。Keys ordered by rank then lane. */
  order: string[]
  rankCount: number
  bands: GridBand[]
  minLane: number
  maxLane: number
  /** 參與排版的邊（分頁內、非 back）。Laid-out edges. */
  edges: StoryEdge[]
  /** 分頁內的 back 邊。Back edges inside the tab. */
  backEdges: StoryEdge[]
}

/** 排版選項。Layout options. */
export interface LayoutOptions {
  /** 重疊時最多推開幾次。Max collision-resolution passes. */
  maxPasses?: number
  /** spread 上限（欄）。Upper bound of a branch spread. */
  maxSpread?: number
  /** 同層節點的最小水平間隔（px）。Min horizontal gap between nodes. */
  minGap?: number
}

const SKIP = new Set(['back', 'cross_tab'])

/**
 * 節點在畫面上的寬度。The rendered width of a node.
 * @param node - 節點。The node.
 * @param narrow - 是否窄版。Narrow or not.
 * @param size - 尺寸。Sizes.
 * @returns 寬度（px）。Width in px.
 */
export function nodeWidth(node: Pick<StoryNode, 'type'>, narrow: boolean, size: GraphThemeSize): number {
  if (node.type === 'condition') return size.diamondW
  if (node.type === 'route_exit') return size.exitW
  return narrow ? size.nodeWNarrow : size.nodeW
}

/**
 * 第一步：邏輯格位。Step 1: the logical grid.
 * @param graph - 劇情圖。The graph.
 * @param tab - 分頁 key。The tab key.
 * @param size - 尺寸（判斷重疊用）。Sizes, for collision checks.
 * @param options - 排版選項。Options.
 * @returns 格位。The grid.
 */
export function assign(graph: StoryGraph, tab: string, size: GraphThemeSize = DEFAULT_SIZE,
  options: LayoutOptions = {}): StoryGrid {
  const { maxPasses = 60, maxSpread = 6, minGap = 16 } = options
  const nodes = graph.nodes.filter((n) => n.tab === tab)
  const byKey = new Map(nodes.map((n) => [n.key, n]))
  const nodeIndex = new Map(nodes.map((n, i) => [n.key, i]))
  const start = graph.tabs.find((t) => t.key === tab)?.start
  const inTab = graph.edges.filter((e) => byKey.has(e.from) && byKey.has(e.to))
  const edges = inTab.filter((e) => !SKIP.has(e.kind ?? 'normal'))
  const backEdges = inTab.filter((e) => (e.kind ?? 'normal') === 'back')

  const outs = new Map<string, StoryEdge[]>()
  const ins = new Map<string, StoryEdge[]>()
  for (const n of nodes) { outs.set(n.key, []); ins.set(n.key, []) }
  edges.forEach((e) => { outs.get(e.from)!.push(e); ins.get(e.to)!.push(e) })
  const edgeIndex = new Map(graph.edges.map((e, i) => [e, i]))
  for (const list of outs.values()) list.sort((a, b) => (a.order ?? 0) - (b.order ?? 0) || edgeIndex.get(a)! - edgeIndex.get(b)!)

  // ---- 拓撲排序（Kahn，穩定）；有迴圈時忽略回到已處理節點的邊 ----
  const indeg = new Map(nodes.map((n) => [n.key, ins.get(n.key)!.length]))
  const rootSort = (a: string, b: string) => (a === start ? -1 : b === start ? 1 : nodeIndex.get(a)! - nodeIndex.get(b)!)
  const ready = nodes.filter((n) => indeg.get(n.key) === 0).map((n) => n.key).sort(rootSort)
  const topo: string[] = []
  const done = new Set<string>()
  while (topo.length < nodes.length) {
    if (!ready.length) {
      const rest = nodes.map((n) => n.key).filter((k) => !done.has(k))
      ready.push(rest[0])
    }
    const cur = ready.shift()!
    if (done.has(cur)) continue
    done.add(cur)
    topo.push(cur)
    for (const e of outs.get(cur)!) {
      if (done.has(e.to)) continue
      indeg.set(e.to, indeg.get(e.to)! - 1)
      if (indeg.get(e.to)! <= 0) ready.push(e.to)
    }
  }
  const topoIndex = new Map(topo.map((k, i) => [k, i]))
  const forwardIns = (key: string) => ins.get(key)!.filter((e) => topoIndex.get(e.from)! < topoIndex.get(key)!)

  // ---- 層：最長路徑 ----
  const rank = new Map<string, number>()
  for (const key of topo) {
    const parents = forwardIns(key)
    rank.set(key, parents.length ? Math.max(...parents.map((e) => rank.get(e.from)! + 1)) : 0)
  }

  // ---- 欄 ----
  const spread = new Map<string, number>()
  const narrow = new Set<string>()
  const lane = new Map<string, number>()
  const stack = new Map<string, string[]>()
  const rootOf = new Map<string, string>()

  const computeLanes = () => {
    for (const key of topo) {
      const parents = forwardIns(key)
      if (!parents.length) {
        lane.set(key, 0)
        stack.set(key, [])
        rootOf.set(key, key)
        continue
      }
      const contrib = parents.map((e) => {
        const siblings = outs.get(e.from)!
        const k = siblings.length
        const i = siblings.indexOf(e)
        const branch = k >= 2
        return {
          lane: branch ? lane.get(e.from)! + (i - (k - 1) / 2) * (spread.get(e.from) ?? 1) : lane.get(e.from)!,
          stack: branch ? [...stack.get(e.from)!, e.from] : stack.get(e.from)!,
          root: rootOf.get(e.from)!,
        }
      })
      rootOf.set(key, contrib[0].root)
      if (contrib.length === 1) {
        lane.set(key, contrib[0].lane)
        stack.set(key, contrib[0].stack)
        continue
      }
      const prefix = commonPrefix(contrib.map((c) => c.stack))
      if (prefix.length) {
        const b = prefix[prefix.length - 1]
        lane.set(key, lane.get(b)!)
        stack.set(key, stack.get(b)!)
      } else {
        lane.set(key, contrib[0].lane)
        stack.set(key, [])
      }
    }
    // 其他根（起點以外沒有入邊的節點）整組放到右邊
    const groups = new Map<string, string[]>()
    for (const key of topo) {
      const r = rootOf.get(key)!
      if (!groups.has(r)) groups.set(r, [])
      groups.get(r)!.push(key)
    }
    const roots = [...groups.keys()].sort(rootSort)
    let right = -Infinity
    roots.forEach((r, gi) => {
      const members = groups.get(r)!
      const lanes = members.map((k) => lane.get(k)!)
      if (gi > 0) {
        const shift = right + 1 - Math.min(...lanes)
        members.forEach((k) => lane.set(k, lane.get(k)! + shift))
      }
      right = Math.max(right, ...members.map((k) => lane.get(k)!))
    })
  }

  const widthOf = (key: string) => nodeWidth(byKey.get(key)!, narrow.has(key), size)
  const findCollision = (): [string, string] | null => {
    const byRank = new Map<number, string[]>()
    for (const key of topo) {
      const r = rank.get(key)!
      if (!byRank.has(r)) byRank.set(r, [])
      byRank.get(r)!.push(key)
    }
    for (const keys of byRank.values()) {
      keys.sort((a, b) => lane.get(a)! - lane.get(b)!)
      for (let i = 1; i < keys.length; i++) {
        const a = keys[i - 1]
        const b = keys[i]
        const dist = (lane.get(b)! - lane.get(a)!) * size.colGap
        if (dist + 1e-6 < (widthOf(a) + widthOf(b)) / 2 + minGap) return [a, b]
      }
    }
    return null
  }

  computeLanes()
  for (let pass = 0; pass < maxPasses; pass++) {
    const hit = findCollision()
    if (!hit) break
    const [a, b] = hit
    const dist = Math.abs(lane.get(b)! - lane.get(a)!) * size.colGap
    const narrowFits = dist + 1e-6 >= (nodeWidth(byKey.get(a)!, true, size) + nodeWidth(byKey.get(b)!, true, size)) / 2 + minGap
    if (narrowFits && !(narrow.has(a) && narrow.has(b))) {
      narrow.add(a)
      narrow.add(b)
      continue
    }
    const common = commonPrefix([[...stack.get(a)!, a], [...stack.get(b)!, b]])
    const pivot = common[common.length - 1]
    if (!pivot || (spread.get(pivot) ?? 1) >= maxSpread) break
    spread.set(pivot, (spread.get(pivot) ?? 1) + 0.5)
    narrow.delete(a)
    narrow.delete(b)
    computeLanes()
  }

  // ---- 章節帶 ----
  const rankCount = topo.length ? Math.max(...topo.map((k) => rank.get(k)!)) + 1 : 0
  const byRank: string[][] = Array.from({ length: rankCount }, () => [])
  for (const key of topo) byRank[rank.get(key)!].push(key)
  const bands: GridBand[] = []
  let current: string | null = null
  for (let r = 0; r < rankCount; r++) {
    const labelled = byRank[r]
      .filter((k) => byKey.get(k)!.chapter_label)
      .sort((a, b) => Math.abs(lane.get(a)!) - Math.abs(lane.get(b)!) || nodeIndex.get(a)! - nodeIndex.get(b)!)
    if (!labelled.length) continue
    const label = byKey.get(labelled[0])!.chapter_label!
    if (label !== current) {
      bands.push({ rank: r, label })
      current = label
    }
  }

  const cells: Record<string, GridCell> = {}
  for (const key of topo) cells[key] = { key, rank: rank.get(key)!, lane: lane.get(key)!, narrow: narrow.has(key) }
  const order = [...topo].sort((a, b) => cells[a].rank - cells[b].rank || cells[a].lane - cells[b].lane)
  const lanes = topo.map((k) => lane.get(k)!)
  return {
    tab, cells, order, rankCount, bands, edges, backEdges,
    minLane: lanes.length ? Math.min(...lanes) : 0,
    maxLane: lanes.length ? Math.max(...lanes) : 0,
  }
}

function commonPrefix(lists: string[][]): string[] {
  if (!lists.length) return []
  const out: string[] = []
  for (let i = 0; ; i++) {
    const v = lists[0][i]
    if (v === undefined || lists.some((l) => l[i] !== v)) return out
    out.push(v)
  }
}

// ============================================================================
// 第二步：幾何投影
// ============================================================================

/** 投影後的節點。A projected node. */
export interface LaidNode {
  key: string
  x: number
  y: number
  w: number
  h: number
  /** 中心 x。Center x. */
  cx: number
  rank: number
  lane: number
  narrow: boolean
}

/** 投影後的邊。A projected edge. */
export interface LaidEdge {
  edge: StoryEdge
  from: string
  to: string
  /** SVG 路徑。SVG path data. */
  d: string
  /** `straight`、`side`（分歧側邊出發）、`curve`（S 形）。Path shape. */
  shape: 'straight' | 'side' | 'curve'
  start: { x: number; y: number }
  end: { x: number; y: number }
  label?: { x: number; y: number; text: string }
  minY: number
  maxY: number
}

/** 往回跳的短箭頭。A back-edge stub. */
export interface LaidBack {
  edge: StoryEdge
  from: string
  to: string
  x: number
  y: number
}

/** 投影後的章節帶。A projected band. */
export interface LaidBand {
  y: number
  height: number
  label: string
  rank: number
}

/** 投影結果。The projected layout. */
export interface StoryLayout {
  tab: string
  nodes: LaidNode[]
  nodeMap: Record<string, LaidNode>
  edges: LaidEdge[]
  backs: LaidBack[]
  bands: LaidBand[]
  width: number
  height: number
}

/**
 * 第二步：直向投影。Step 2: vertical projection.
 * @param grid - 格位。The grid.
 * @param graph - 劇情圖（取節點類型與標籤）。The graph.
 * @param size - 尺寸。Sizes.
 * @returns 座標。The layout.
 */
export function project(grid: StoryGrid, graph: StoryGraph, size: GraphThemeSize = DEFAULT_SIZE): StoryLayout {
  const byKey = new Map(graph.nodes.map((n) => [n.key, n]))
  const { nodeW, nodeH, rowGap, colGap, bandHeight, padding, cornerRadius } = size
  const gap = rowGap - nodeH
  const bandsUpTo = (rank: number) => grid.bands.filter((b) => b.rank <= rank).length
  const top = (rank: number) => padding + rank * rowGap + bandsUpTo(rank) * bandHeight
  const half = Math.max(nodeW, size.exitW, size.diamondW) / 2
  const originX = padding + half - grid.minLane * colGap

  const nodes: LaidNode[] = grid.order.map((key) => {
    const cell = grid.cells[key]
    const w = nodeWidth(byKey.get(key)!, cell.narrow, size)
    const cx = originX + cell.lane * colGap
    return { key, x: cx - w / 2, y: top(cell.rank), w, h: nodeH, cx, rank: cell.rank, lane: cell.lane, narrow: cell.narrow }
  })
  const nodeMap: Record<string, LaidNode> = Object.fromEntries(nodes.map((n) => [n.key, n]))
  const outCount = new Map<string, number>()
  for (const e of grid.edges) outCount.set(e.from, (outCount.get(e.from) ?? 0) + 1)

  const edges: LaidEdge[] = grid.edges.map((edge) => {
    const s = nodeMap[edge.from]
    const t = nodeMap[edge.to]
    const tx = t.cx
    const ty = t.y
    const dx = tx - s.cx
    const sign = dx > 0 ? 1 : -1
    let d: string
    let shape: LaidEdge['shape']
    let start: { x: number; y: number }
    const branch = (outCount.get(edge.from) ?? 0) >= 2
    if (Math.abs(dx) < 0.5) {
      start = { x: s.cx, y: s.y + s.h }
      d = `M ${start.x} ${start.y} L ${tx} ${ty}`
      shape = 'straight'
    } else if (branch && Math.abs(dx) > s.w / 2 + 4) {
      // 從側邊中點（菱形是左右頂點）水平出發，圓角轉向下
      start = { x: s.cx + (sign * s.w) / 2, y: s.y + s.h / 2 }
      const r = Math.max(0, Math.min(cornerRadius, Math.abs(dx) - s.w / 2, ty - start.y))
      d = `M ${start.x} ${start.y} H ${tx - sign * r} Q ${tx} ${start.y} ${tx} ${start.y + r} L ${tx} ${ty}`
      shape = 'side'
    } else {
      // 合流：先直下，在目標上方的間隔裡用 S 形三次貝茲曲線橫移
      start = { x: s.cx, y: s.y + s.h }
      const curveTop = Math.max(start.y, ty - gap)
      const mid = (curveTop + ty) / 2
      d = `M ${start.x} ${start.y}${curveTop > start.y ? ` V ${curveTop}` : ''} C ${start.x} ${mid} ${tx} ${mid} ${tx} ${ty}`
      shape = 'curve'
    }
    const fromNode = byKey.get(edge.from)
    const showLabel = !!edge.label && (fromNode?.type === 'condition' || (edge.flags ?? []).includes('unlock'))
    return {
      edge, from: edge.from, to: edge.to, d, shape, start, end: { x: tx, y: ty },
      label: showLabel ? { x: tx, y: ty - Math.min(gap / 2, 22), text: edge.label! } : undefined,
      minY: Math.min(start.y, ty), maxY: Math.max(start.y, ty),
    }
  })

  const backs: LaidBack[] = grid.backEdges
    .filter((e) => nodeMap[e.from] && nodeMap[e.to])
    .map((edge) => {
      const s = nodeMap[edge.from]
      // 節點右下角的小圖示（不會蓋到相鄰的分支節點）
      return { edge, from: edge.from, to: edge.to, x: s.x + s.w - 6, y: s.y + s.h + 4 }
    })

  const bands: LaidBand[] = grid.bands.map((b) => ({
    rank: b.rank, label: b.label, height: bandHeight, y: top(b.rank) - bandHeight - gap / 2 + 6,
  }))
  const lastRank = Math.max(0, grid.rankCount - 1)
  const width = (grid.maxLane - grid.minLane) * colGap + 2 * half + 2 * padding + 120
  const height = grid.rankCount ? top(lastRank) + nodeH + padding : 2 * padding
  return { tab: grid.tab, nodes, nodeMap, edges, backs, bands, width, height }
}

/**
 * 兩步合在一起。Both steps at once.
 * @param graph - 劇情圖。The graph.
 * @param tab - 分頁。The tab.
 * @param size - 尺寸。Sizes.
 * @param options - 排版選項。Options.
 * @returns 座標。The layout.
 */
export function layoutTab(graph: StoryGraph, tab: string, size: GraphThemeSize = DEFAULT_SIZE,
  options?: LayoutOptions): StoryLayout {
  return project(assign(graph, tab, size, options), graph, size)
}
