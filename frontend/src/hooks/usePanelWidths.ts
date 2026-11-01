import { createRef, useRef, type RefObject } from 'react'
import type { Layout, LayoutChangedMeta, PanelImperativeHandle, PanelSize } from 'react-resizable-panels'
import { useLocalStorage } from './useLocalStorage'
import { defaultWidths, panelWidthsKey, sanitizeWidths, type SplitPageConfig } from '../lib/splitLayout'

/**
 * 某頁可拖曳面板的寬度：預設值、使用者拖曳過的寬度（依頁面 ID 分開存在 localStorage）、以及接到
 * react-resizable-panels 的各種回呼。只存使用者正在拖曳的那一個面板；程式為了放得下而被動縮小的
 * 寬度不會蓋掉偏好。回傳值帶著同一份版面設定，交給 `SplitLayout` 用，兩邊的設定一定一致。
 *
 * Widths of a page's resizable panels: defaults, the user's stored widths
 * (per page) and the callbacks for react-resizable-panels. Only the panel
 * being dragged is saved. The result carries the layout config for
 * `SplitLayout`, so both use the same one.
 *
 * @param layout - 頁面的版面設定。The page's layout config.
 * @returns 版面設定、目前的寬度、面板的 ref 與回呼。The layout, widths, panel refs and callbacks.
 */
export function usePanelWidths<K extends string>(layout: SplitPageConfig<K>) {
  const { specs } = layout
  const [storedRaw, setStored] = useLocalStorage<Partial<Record<K, number>>>(panelWidthsKey(layout), {})
  const stored = sanitizeWidths(storedRaw, specs)
  const defaults = defaultWidths(window.innerWidth, specs)
  const widths: Readonly<Record<K, number>> = { ...defaults, ...stored }
  const refs = useRef<Partial<Record<K, RefObject<PanelImperativeHandle | null>>>>({})
  const live = useRef<Record<K, number>>({ ...widths })
  const activeHandle = useRef<K | null>(null)

  /** 某個面板的 ref（第一次用到時建立）。A panel's ref, created on first use. */
  const refFor = (key: K) => (refs.current[key] ??= createRef<PanelImperativeHandle | null>())

  return {
    layout,
    widths,
    refFor,
    /** 面板大小改變時記下目前寬度。Tracks a panel's current width. */
    trackWidth: (key: K) => (size: PanelSize) => {
      live.current = { ...live.current, [key]: size.inPixels }
    },
    /** 使用者開始拖曳（或用鍵盤調整）某條分隔線。The user grabbed a separator. */
    grab: (key: K) => {
      activeHandle.current = key
    },
    /** 版面改變後：使用者操作造成的才存。Saves after a user-driven layout change. */
    save: (_layout: Layout, meta: LayoutChangedMeta) => {
      const key = activeHandle.current
      if (!meta.isUserInteraction || key === null) return
      setStored(sanitizeWidths({ ...stored, [key]: live.current[key] }, specs))
    },
    /** 恢復預設寬度並忘記使用者的設定。Restores a panel's default width. */
    reset: (key: K) => {
      refFor(key).current?.resize(defaults[key])
      const rest = { ...stored }
      delete rest[key]
      setStored(rest)
    },
  }
}

/** {@link usePanelWidths} 的回傳值。The value returned by {@link usePanelWidths}. */
export type PanelWidthsState<K extends string> = ReturnType<typeof usePanelWidths<K>>
