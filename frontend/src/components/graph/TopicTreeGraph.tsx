/**
 * 三語對話卡的主題樹圖（`docs/wip/trilingual_topic_tree_FEAT_2026-11-22.md` D3）：由左往右的樹，卡片是實線圓角框、
 * 沒有對應卡的子牌組是虛線框、根節點加粗。收起的節點右側顯示子卡數；點有子節點的節點進入它的子牌組，
 * 點沒有子節點的卡片（含根節點的主題卡）開卡片詳情。
 *
 * 座標由 `layoutTree` 固定算好（不跑力導向排版、不能拖曳），資料一換就縮放到整張圖都看得到。
 *
 * The topic tree graph: a left-to-right tree with fixed coordinates; clicking a node with children enters its
 * deck, clicking a card without children opens it.
 */

import { useEffect, useMemo, useRef } from 'react'
import ForceGraph2D from 'react-force-graph-2d'
import {
  NODE_HEIGHT,
  NODE_WIDTH,
  isFolded,
  layoutTree,
  linkEnd,
  nodeAction,
} from '../../lib/topicTreeLayout'
import type { GraphData, GraphNode } from '../../types/api'

/** 畫布上的節點：API 的節點加上固定座標。A drawn node, with fixed coordinates. */
type DrawnNode = GraphNode & { x: number; y: number; fx: number; fy: number; folded: boolean }

/**
 * 節點的配色：卡片紫色實線、牌組灰色虛線，深色主題時改用深底淺字（與單字卡的圓點節點明顯不同）。
 * Node colours by kind and theme.
 */
function palette(dark: boolean) {
  return dark
    ? {
        card: { fill: '#3C3489', stroke: '#AFA9EC', text: '#EEEDFE' },
        deck: { fill: '#2C2C2A', stroke: '#B4B2A9', text: '#F1EFE8' },
        badge: { fill: '#AFA9EC', text: '#26215C' },
        warn: '#EF9F27',
        link: '#5F5E5A',
      }
    : {
        card: { fill: '#EEEDFE', stroke: '#534AB7', text: '#3C3489' },
        deck: { fill: '#F1EFE8', stroke: '#888780', text: '#444441' },
        badge: { fill: '#534AB7', text: '#FFFFFF' },
        warn: '#BA7517',
        link: '#B4B2A9',
      }
}

/**
 * 截斷文字到指定寬度（加上省略號）。Truncates text to a width.
 */
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
export function topicTooltip(node: GraphNode): string {
  const lines = [node.label ?? node.id]
  if (node.prompt && node.prompt !== node.label) lines.push(node.prompt)
  if (node.duplicate_title) lines.push('⚠️ 同一層有標題相同的卡，子牌組只算給前一張')
  const action = nodeAction(node)
  if (action && 'enter' in action) lines.push(`點一下進入（${node.child_count} 個子項目）`)
  else if (action) lines.push('點一下看卡片')
  return lines.join('\n')
}

/**
 * 主題樹圖。The topic tree graph.
 * @param props.graph - `GET /handlers/{name}/graph` 的結果（樹狀）。The tree graph.
 * @param props.width - 畫布寬度。Canvas width.
 * @param props.height - 畫布高度。Canvas height.
 * @param props.onEnter - 進入子牌組。Called with the deck to enter.
 * @param props.onOpenNote - 開卡片詳情。Called with the note to open.
 * @param props.fontScale - 文字大小倍率（頁面的字級設定）。The font size multiplier.
 * @returns 圖的 JSX。The graph JSX.
 */
export function TopicTreeGraph({
  graph,
  width,
  height,
  onEnter,
  onOpenNote,
  fontScale = 1,
}: {
  graph: GraphData
  width?: number
  height?: number
  onEnter: (deck: string) => void
  onOpenNote: (noteId: number) => void
  fontScale?: number
}) {
  const fgRef = useRef<any>()
  const dark = typeof document !== 'undefined' && document.documentElement.classList.contains('dark')
  const colors = palette(dark)

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
      cooldownTicks={0}
      enableNodeDrag={false}
      nodeLabel={(node: any) => topicTooltip(node as GraphNode)}
      nodeCanvasObjectMode={() => 'replace'}
      nodeCanvasObject={(raw: any, ctx: CanvasRenderingContext2D) => {
        const node = raw as DrawnNode
        const style = node.kind === 'deck' ? colors.deck : colors.card
        const left = node.x - NODE_WIDTH / 2
        const top = node.y - NODE_HEIGHT / 2
        roundRect(ctx, left, top, NODE_WIDTH, NODE_HEIGHT, 7)
        ctx.fillStyle = style.fill
        ctx.fill()
        ctx.setLineDash(node.kind === 'deck' ? [4, 3] : [])
        ctx.lineWidth = node.kind === 'root' ? 2 : 1
        ctx.strokeStyle = node.duplicate_title ? colors.warn : style.stroke
        ctx.stroke()
        ctx.setLineDash([])

        const badge = node.folded ? String(node.child_count) : ''
        const badgeWidth = badge ? Math.max(20, 8 + badge.length * 7) : 0
        const fontSize = 12 * fontScale
        ctx.font = `${node.kind === 'deck' ? '' : '600 '}${fontSize}px sans-serif`
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
          ctx.font = `600 ${11 * fontScale}px sans-serif`
          ctx.textAlign = 'center'
          ctx.fillStyle = colors.badge.text
          ctx.fillText(badge, bx + badgeWidth / 2, node.y + 0.5)
        }
      }}
      nodePointerAreaPaint={(raw: any, color: string, ctx: CanvasRenderingContext2D) => {
        const node = raw as DrawnNode
        ctx.fillStyle = color
        ctx.fillRect(node.x - NODE_WIDTH / 2, node.y - NODE_HEIGHT / 2, NODE_WIDTH, NODE_HEIGHT)
      }}
      linkCanvasObjectMode={() => 'replace'}
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
      onNodeClick={(raw: any) => {
        const action = nodeAction(raw as GraphNode)
        if (!action) return
        if ('enter' in action) onEnter(action.enter)
        else onOpenNote(action.open)
      }}
    />
  )
}
