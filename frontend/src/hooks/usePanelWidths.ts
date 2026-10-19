import { useRef } from 'react'
import { usePanelRef, type Layout, type LayoutChangedMeta, type PanelSize } from 'react-resizable-panels'
import { useLocalStorage } from './useLocalStorage'
import { defaultPanelWidths, sanitizePanelWidths, type PanelWidths } from '../lib/cardLayout'

/** 存面板寬度的 localStorage 鍵。localStorage key of the panel widths. */
const PANEL_WIDTHS_KEY = 'cards_panel_widths_v2'

/** 可調寬度的面板。A resizable panel. */
export type PanelKey = keyof PanelWidths

/**
 * 卡片瀏覽頁可拖曳面板（牌組、卡面）的寬度：預設值、使用者拖曳過的寬度（存在 localStorage）、
 * 以及接到 react-resizable-panels 的各種回呼。只存使用者正在拖曳的那一個面板；程式為了放得下
 * 而被動縮小的寬度不會蓋掉偏好。
 *
 * Widths of the resizable panels: defaults, the user's stored widths and the
 * callbacks for react-resizable-panels. Only the panel being dragged is saved.
 *
 * @returns 目前的寬度、面板的 ref 與回呼。Widths, panel refs and callbacks.
 */
export function usePanelWidths() {
  const [storedRaw, setStored] = useLocalStorage<Partial<PanelWidths>>(PANEL_WIDTHS_KEY, {})
  const stored = sanitizePanelWidths(storedRaw)
  const defaults = defaultPanelWidths(window.innerWidth)
  const widths: PanelWidths = { ...defaults, ...stored }
  const deckRef = usePanelRef()
  const viewerRef = usePanelRef()
  const live = useRef<PanelWidths>(widths)
  const activeHandle = useRef<PanelKey | null>(null)

  return {
    widths,
    deckRef,
    viewerRef,
    /** 面板大小改變時記下目前寬度。Tracks a panel's current width. */
    trackWidth: (key: PanelKey) => (size: PanelSize) => {
      live.current = { ...live.current, [key]: size.inPixels }
    },
    /** 使用者開始拖曳（或用鍵盤調整）某條分隔線。The user grabbed a separator. */
    grab: (key: PanelKey) => {
      activeHandle.current = key
    },
    /** 版面改變後：使用者操作造成的才存。Saves after a user-driven layout change. */
    save: (_layout: Layout, meta: LayoutChangedMeta) => {
      const key = activeHandle.current
      if (!meta.isUserInteraction || key === null) return
      setStored(sanitizePanelWidths({ ...stored, [key]: live.current[key] }))
    },
    /** 恢復預設寬度並忘記使用者的設定。Restores a panel's default width. */
    reset: (key: PanelKey) => {
      ;(key === 'deck' ? deckRef : viewerRef).current?.resize(defaults[key])
      const rest = { ...stored }
      delete rest[key]
      setStored(rest)
    },
  }
}
