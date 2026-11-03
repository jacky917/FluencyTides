/**
 * 捲動控制：⏫ 上一章、▲ 往上約 0.8 個畫面高（至少 200px）、▼ 往下同樣距離、⏬ 下一章（flowchart_spec §7）。
 * Scroll controls: previous chapter, up, down, next chapter.
 * 有主題素材（`assets.scrollButtons`）時按鈕顯示圖片，否則顯示文字符號。
 */

import type { GraphTheme } from '../../../core/theme'
import type { LaidBand } from '../core/layout'

/** {@link ScrollControls} 的 props。Props of {@link ScrollControls}. */
export interface ScrollControlsProps {
  bands: LaidBand[]
  scrollTop: number
  pageHeight: number
  theme: GraphTheme
  onScrollTo: (top: number) => void
}

const BUTTONS = [
  { id: 'top', label: '上一章', glyph: '⏫' },
  { id: 'up', label: '往上捲', glyph: '▲' },
  { id: 'down', label: '往下捲', glyph: '▼' },
  { id: 'bottom', label: '下一章', glyph: '⏬' },
] as const

/**
 * 捲動控制。The scroll controls.
 * @param props - 見 {@link ScrollControlsProps}。See {@link ScrollControlsProps}.
 * @returns 按鈕列。The buttons.
 */
export function ScrollControls({ bands, scrollTop, pageHeight, theme, onScrollTo }: ScrollControlsProps) {
  const step = Math.max(200, pageHeight * 0.8)
  const target = (id: (typeof BUTTONS)[number]['id']) => {
    if (id === 'up') return scrollTop - step
    if (id === 'down') return scrollTop + step
    const tops = bands.map((b) => b.y - 8)
    if (id === 'top') return [...tops].reverse().find((t) => t < scrollTop - 4) ?? 0
    return tops.find((t) => t > scrollTop + 4) ?? scrollTop + step
  }
  return (
    <div className="sf-scroll" role="group" aria-label="捲動">
      {BUTTONS.map((b) => {
        const img = theme.assets.scrollButtons?.[b.id]
        return (
          <button key={b.id} type="button" className={`sf-scroll__button sf-scroll__button--${b.id}`}
            title={b.label} aria-label={b.label} onClick={() => onScrollTo(Math.max(0, target(b.id)))}>
            {img ? <img src={img} alt="" draggable={false} /> : b.glyph}
          </button>
        )
      })}
    </div>
  )
}
