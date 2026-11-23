/**
 * 主題樹圖（`docs/wip/trilingual_topic_tree_FEAT_2026-11-22.md` D3）：由左往右的樹，卡片是實線圓角框、
 * 沒有對應卡的子牌組是虛線框、根節點加粗。收起的節點右側顯示子卡數；點有子節點的節點進入它的子牌組，
 * 點沒有子節點的卡片（含根節點的主題卡）開卡片詳情。
 *
 * 顏色、字型與背景圖都取自圖主題（`graph-views/core/theme.ts`，`docs/wip/graph_themes_FEAT_2026-11-22.md` D2），
 * 和劇情流程圖共用同一套主題；主題的 `theme.css` 只認劇情流程圖的元素，canvas 用不到。
 * 座標由 `layoutTree` 固定算好（不跑力導向排版、不能拖曳），資料或大小一換就縮放到整張圖都看得到。
 *
 * The topic tree graph: a left-to-right tree with fixed coordinates, coloured by the shared graph theme;
 * clicking a node with children enters its deck, clicking a card without children opens it.
 */

import { useEffect, useMemo, useRef } from 'react'
import ForceGraph2D from 'react-force-graph-2d'
import type { GraphTheme } from '../../../core/theme'
import { NODE_HEIGHT, NODE_WIDTH, isFolded, layoutTree, linkEnd, nodeAction } from '../core/layout'
import type { TopicTreeData, TopicTreeNode } from '../core/types'

/** 畫布上的節點：資料的節點加上固定座標。A drawn node, with fixed coordinates. */
type DrawnNode = TopicTreeNode & { x: number; y: number; fx: number; fy: number; folded: boolean }

/**
 * 從主題取出主題樹要用的顏色（graph_themes 計畫書 D2 的對照表）。
 * The theme colours the topic tree uses.
 * @param theme - 圖主題。The graph theme.
 * @returns 各部位的顏色。Colours by part.
 */
export function treeColors(theme: GraphTheme) {
  const c = theme.colors
  return {
    canvas: c.canvas,
    card: { fill: c['node-bg'], stroke: c['node-border'], text: c['node-text'] },
    deck: { fill: c['node-dim-bg'], stroke: c['node-border'], text: c['node-dim-text'] },
    root: c['node-border-strong'],
    badge: { fill: c['badge-bg'], text: c['badge-text'] },
    warn: c['node-highlight'],
    link: c.edge,
  }
}

/** 截斷文字到指定寬度（加上省略號）。Truncates text to a width. */
function fitText(ctx: CanvasRenderingContext2D, text: string, width: number): string {
  if (ctx.measureText(text).width <= width) return text
  let end = text.length
  while (end > 1 && ctx.measureText(`${text.slice(0, end)}…`).width > width) end -= 1
  return `${text.slice(0, end)}…`
}

/** 畫一個圓角矩形的路徑。Traces a rounded rectangle. */
function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath()
  ctx.moveTo(x + r, y)
  ctx.arcTo(x + w, y, x + w, y + h, r)
  ctx.arcTo(x + w, y + h, x, y + h, r)
  ctx.arcTo(x, y + h, x, y, r)
  ctx.arcTo(x, y, x + w, y, r)
  ctx.closePath()
}

/**
 * 節點的提示文字：名稱、情境、同名提示與點了會做什麼。The node tooltip.
 * @param node - 節點。The node.
 * @returns 提示文字。The tooltip.
 */
export function topicTooltip(node: TopicTreeNode): string {
  const lines = [node.label ?? node.id]
  if (node.prompt && node.prompt !== node.label) lines.push(node.prompt)
  if (node.duplicate_title) lines.push('⚠️ 同一層有標題相同的卡，子牌組只算給前一張')
  const action = nodeAction(node)
  if (action && 'enter' in action) lines.push(`點一下進入（${node.child_count} 個子項目）`)
  else if (action) lines.push('點一下看卡片')
  return lines.join('\n')
}

/** {@link TopicTreeGraph} 的 props。Props of {@link TopicTreeGraph}. */
export interface TopicTreeGraphProps {
  /** 樹狀圖譜（後端 `topic_tree.build_graph`）。The tree graph. */
  graph: TopicTreeData
  /** 圖主題。The graph theme. */
  theme: GraphTheme
  width?: number
  height?: number
  /** 進入子牌組。Called with the deck to enter. */
  onEnter: (deck: string) => void
  /** 開卡片詳情。Called with the note to open. */
  onOpenNote: (noteId: number) => void
  /** 文字大小倍率（頁面的字級設定）。The font size multiplier. */
  fontScale?: number
}

/**
 * 文字在螢幕上小於這個大小（px）時只畫框、不畫字。主題樹的字是框的一部分、會跟著縮放，所以不沿用單字卡
 * 「多大的縮放才顯示文字」的設定（那是螢幕上固定大小的標籤用的；套上去的話，放進整張圖的縮放通常小於
 * 100%，字一開始就不見）。
 * Below this on-screen size (px) only the boxes are drawn.
 */
export const MIN_TEXT_PX = 5

/**
 * 主題樹圖。The topic tree graph.
 * @param props - 見 {@link TopicTreeGraphProps}。See {@link TopicTreeGraphProps}.
 * @returns 圖的 JSX。The graph JSX.
 */
export function TopicTreeGraph({
  graph,
  theme,
  width,
  height,
  onEnter,
  onOpenNote,
  fontScale = 1,
}: TopicTreeGraphProps) {
  // react-force-graph 沒有提供 ref 的型別
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const fgRef = useRef<any>()
  const colors = treeColors(theme)
  const font = theme.fonts.node ?? 'sans-serif'

  const data = useMemo(() => {
    const positions = layoutTree(graph.nodes, graph.links)
    const nodes: DrawnNode[] = graph.nodes
      .filter((node) => positions.has(node.id))
      .map((node) => {
        const { x, y } = positions.get(node.id)!
        return { ...node, x, y, fx: x, fy: y, folded: isFolded(node, graph.links) }
      })
    const drawn = new Set(nodes.map((node) => node.id))
    const links = graph.links
      .filter((link) => drawn.has(linkEnd(link.source)) && drawn.has(linkEnd(link.target)))
      .map((link) => ({ ...link }))
    return { nodes, links }
  }, [graph])

  // 換資料（換牌組、改層數）或改變大小後，縮放到整張圖都看得到。zoomToFit 只看節點中心點、不知道框有多寬，
  // 所以自己用框的範圍算：縮放取寬高都放得下的倍率（最大 1.5 倍，節點少時不會放得過大），中心對準整張圖的中心
  useEffect(() => {
    if (!width || !height || data.nodes.length === 0) return
    const xs = data.nodes.map((node) => node.x)
    const ys = data.nodes.map((node) => node.y)
    const left = Math.min(...xs) - NODE_WIDTH / 2
    const right = Math.max(...xs) + NODE_WIDTH / 2
    const top = Math.min(...ys) - NODE_HEIGHT / 2
    const bottom = Math.max(...ys) + NODE_HEIGHT / 2
    const padding = 32
    const zoom = Math.min(1.5, (width - 2 * padding) / (right - left), (height - 2 * padding) / (bottom - top))
    const timer = window.setTimeout(() => {
      fgRef.current?.zoom(zoom, 300)
      fgRef.current?.centerAt((left + right) / 2, (top + bottom) / 2, 300)
    }, 50)
    return () => window.clearTimeout(timer)
  }, [data, width, height])

  return (
    <ForceGraph2D
      ref={fgRef}
      width={width}
      height={height}
      graphData={data}
      backgroundColor="rgba(0,0,0,0)"
      cooldownTicks={0}
      enableNodeDrag={false}
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      nodeLabel={(node: any) => topicTooltip(node as TopicTreeNode)}
      nodeCanvasObjectMode={() => 'replace'}
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      nodeCanvasObject={(raw: any, ctx: CanvasRenderingContext2D, globalScale: number) => {
        const node = raw as DrawnNode
        const style = node.kind === 'deck' ? colors.deck : colors.card
        const left = node.x - NODE_WIDTH / 2
        const top = node.y - NODE_HEIGHT / 2
        roundRect(ctx, left, top, NODE_WIDTH, NODE_HEIGHT, 7)
        ctx.fillStyle = style.fill
        ctx.fill()
        ctx.setLineDash(node.kind === 'deck' ? [4, 3] : [])
        ctx.lineWidth = node.kind === 'root' ? 2 : 1
        ctx.strokeStyle = node.duplicate_title ? colors.warn : node.kind === 'root' ? colors.root : style.stroke
        ctx.stroke()
        ctx.setLineDash([])
        if (12 * fontScale * globalScale < MIN_TEXT_PX) return

        const badge = node.folded ? String(node.child_count) : ''
        const badgeWidth = badge ? Math.max(20, 8 + badge.length * 7) : 0
        ctx.font = `${node.kind === 'deck' ? '' : '600 '}${12 * fontScale}px ${font}`
        ctx.textAlign = 'left'
        ctx.textBaseline = 'middle'
        ctx.fillStyle = style.text
        const room = NODE_WIDTH - 20 - (badge ? badgeWidth + 6 : 0)
        ctx.fillText(fitText(ctx, node.label ?? node.id, room), left + 10, node.y)

        if (badge) {
          const bx = left + NODE_WIDTH - 8 - badgeWidth
          roundRect(ctx, bx, node.y - 9, badgeWidth, 18, 9)
          ctx.fillStyle = colors.badge.fill
          ctx.fill()
          ctx.font = `600 ${11 * fontScale}px ${font}`
          ctx.textAlign = 'center'
          ctx.fillStyle = colors.badge.text
          ctx.fillText(badge, bx + badgeWidth / 2, node.y + 0.5)
        }
      }}
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      nodePointerAreaPaint={(raw: any, color: string, ctx: CanvasRenderingContext2D) => {
        const node = raw as DrawnNode
        ctx.fillStyle = color
        ctx.fillRect(node.x - NODE_WIDTH / 2, node.y - NODE_HEIGHT / 2, NODE_WIDTH, NODE_HEIGHT)
      }}
      linkCanvasObjectMode={() => 'replace'}
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      linkCanvasObject={(link: any, ctx: CanvasRenderingContext2D) => {
        const source = link.source as DrawnNode
        const target = link.target as DrawnNode
        if (typeof source !== 'object' || typeof target !== 'object') return
        // 從父節點框的右緣畫到子節點框的左緣，中間是 S 形曲線
        const x1 = source.x + NODE_WIDTH / 2
        const x2 = target.x - NODE_WIDTH / 2
        const mid = (x1 + x2) / 2
        ctx.beginPath()
        ctx.moveTo(x1, source.y)
        ctx.bezierCurveTo(mid, source.y, mid, target.y, x2, target.y)
        ctx.lineWidth = 1
        ctx.strokeStyle = colors.link
        ctx.stroke()
      }}
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      onNodeClick={(raw: any) => {
        const action = nodeAction(raw as TopicTreeNode)
        if (!action) return
        if ('enter' in action) onEnter(action.enter)
        else onOpenNote(action.open)
      }}
    />
  )
}
