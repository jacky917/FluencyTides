/**
 * 小地圖：目前分頁整張圖的縮略與可視範圍框；點或拖曳可以捲動（flowchart_spec §6）。
 * The minimap: a scaled view of the tab with the visible window; click or
 * drag to scroll.
 */

import { memo, useRef } from 'react'
import type { StoryLayout } from '../core/layout'

/** {@link Minimap} 的 props。Props of {@link Minimap}. */
export interface MinimapProps {
  layout: StoryLayout
  viewTop: number
  viewLeft: number
  viewHeight: number
  viewWidth: number
  width?: number
  maxHeight?: number
  highlighted?: Set<string>
  onScrollTo: (top: number, left: number) => void
}

/**
 * 小地圖。The minimap.
 * @param props - 見 {@link MinimapProps}。See {@link MinimapProps}.
 * @returns 小地圖 JSX。Minimap JSX.
 */
export const Minimap = memo(function Minimap({
  layout, viewTop, viewLeft, viewHeight, viewWidth, width = 168, maxHeight = 360, highlighted, onScrollTo,
}: MinimapProps) {
  const scale = Math.min(width / Math.max(layout.width, 1), maxHeight / Math.max(layout.height, 1))
  const w = layout.width * scale
  const h = layout.height * scale
  const dragging = useRef(false)

  const scrollFromEvent = (e: React.PointerEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const y = (e.clientY - rect.top) / scale
    const x = (e.clientX - rect.left) / scale
    onScrollTo(Math.max(0, y - viewHeight / 2), Math.max(0, x - viewWidth / 2))
  }

  return (
    <div className="sf-minimap" style={{ width: w, height: h }}>
      <svg
        width={w}
        height={h}
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        onPointerDown={(e) => {
          dragging.current = true
          e.currentTarget.setPointerCapture(e.pointerId)
          scrollFromEvent(e)
        }}
        onPointerMove={(e) => dragging.current && scrollFromEvent(e)}
        onPointerUp={() => { dragging.current = false }}
        role="img"
        aria-label="小地圖"
      >
        {layout.bands.map((b) => (
          <line key={`b${b.rank}`} className="sf-minimap__band" x1={0} x2={layout.width} y1={b.y} y2={b.y} />
        ))}
        {layout.edges.map((e) => (
          <line key={`${e.from}>${e.to}`} className="sf-minimap__edge" x1={e.start.x} y1={e.start.y} x2={e.end.x} y2={e.end.y} />
        ))}
        {layout.nodes.map((n) => (
          <rect key={n.key} className="sf-minimap__node" data-highlight={highlighted?.has(n.key) || undefined}
            x={n.x} y={n.y} width={n.w} height={n.h} rx={n.h / 4} />
        ))}
        <rect className="sf-minimap__view" x={viewLeft} y={viewTop} width={Math.min(viewWidth, layout.width)}
          height={Math.min(viewHeight, layout.height)} />
      </svg>
    </div>
  )
})
