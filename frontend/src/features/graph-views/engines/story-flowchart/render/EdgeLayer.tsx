/**
 * 連線層：一張 SVG 畫線、箭頭、S 曲線（D9）。顏色、粗細、虛線都由 CSS 變數決定；
 * 選取路徑的線另外疊一條深色描邊。
 * The edge layer: one SVG for lines, arrows and S-curves, styled by CSS
 * variables; edges on the selected path get a dark casing underneath.
 */

import { memo } from 'react'
import type { GraphThemeSize } from '../../../core/theme'
import type { LaidBack, LaidEdge } from '../core/layout'
import type { StoryEdge } from '../core/types'

/** {@link EdgeLayer} 的 props。Props of {@link EdgeLayer}. */
export interface EdgeLayerProps {
  width: number
  height: number
  edges: LaidEdge[]
  backs: LaidBack[]
  size: GraphThemeSize
  pathEdges: Set<StoryEdge>
  backLabel: (key: string) => string
  onBackClick?: (target: string) => void
}

function edgeClass(e: LaidEdge, onPath: boolean): string {
  const flags = e.edge.flags ?? []
  const kind = e.edge.kind ?? 'normal'
  const cls = ['sf-edge', `sf-edge--${kind}`, `sf-edge--${e.shape}`]
  for (const f of flags) cls.push(`sf-edge--${f}`)
  if (onPath) cls.push(kind === 'branch' ? 'sf-edge--branch-path' : 'sf-edge--path')
  return cls.join(' ')
}

function marker(e: LaidEdge, onPath: boolean): string {
  if (!onPath) return 'url(#sf-arrow)'
  return (e.edge.kind ?? 'normal') === 'branch' ? 'url(#sf-arrow-branch-path)' : 'url(#sf-arrow-path)'
}

/**
 * 連線層。The edge layer.
 * @param props - 見 {@link EdgeLayerProps}。See {@link EdgeLayerProps}.
 * @returns SVG。The SVG.
 */
export const EdgeLayer = memo(function EdgeLayer({
  width, height, edges, backs, size, pathEdges, backLabel, onBackClick,
}: EdgeLayerProps) {
  const a = size.arrowSize
  const arrow = (id: string, cls: string) => (
    <marker id={id} viewBox={`0 0 ${a} ${a}`} refX={a - 1} refY={a / 2} markerWidth={a} markerHeight={a}
      markerUnits="userSpaceOnUse" orient="auto">
      <path className={cls} d={`M 1 1 L ${a - 1} ${a / 2} L 1 ${a - 1}`} />
    </marker>
  )
  const onPath = edges.filter((e) => pathEdges.has(e.edge))
  return (
    <svg className="sf-edges" width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
      <defs>
        {arrow('sf-arrow', 'sf-arrow')}
        {arrow('sf-arrow-path', 'sf-arrow sf-arrow--path')}
        {arrow('sf-arrow-branch-path', 'sf-arrow sf-arrow--branch-path')}
      </defs>
      {edges.map((e) => {
        const on = pathEdges.has(e.edge)
        return (
          <path key={`${e.from}>${e.to}`} className={edgeClass(e, on)} d={e.d} markerEnd={marker(e, on)}
            data-from={e.from} data-to={e.to} />
        )
      })}
      {/* 選取路徑畫在最上層：先描邊再畫線 */}
      {onPath.map((e) => (
        <path key={`casing ${e.from}>${e.to}`} className="sf-edge-casing" d={e.d} />
      ))}
      {onPath.map((e) => (
        <path key={`top ${e.from}>${e.to}`} className={edgeClass(e, true)} d={e.d} markerEnd={marker(e, true)} />
      ))}
      {edges.filter((e) => e.label).map((e) => (
        <text key={`label ${e.from}>${e.to}`} className="sf-edge__label" x={e.label!.x} y={e.label!.y}
          textAnchor="middle" data-unlock={(e.edge.flags ?? []).includes('unlock') || undefined}>
          {(e.edge.flags ?? []).includes('unlock') ? `🔒 ${e.label!.text}` : e.label!.text}
        </text>
      ))}
      {backs.map((b) => (
        <g key={`back ${b.from}>${b.to}`} className="sf-back" role="button" tabIndex={-1}
          onClick={() => onBackClick?.(b.to)} style={{ pointerEvents: 'all', cursor: 'pointer' }}>
          <title>{`往回跳到「${backLabel(b.to)}」`}</title>
          <circle className="sf-back__dot" cx={b.x} cy={b.y} r={9} />
          <text className="sf-back__label" x={b.x} y={b.y + 4} textAnchor="middle">↩</text>
        </g>
      ))}
    </svg>
  )
})
