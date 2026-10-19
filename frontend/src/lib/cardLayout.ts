/**
 * 卡片瀏覽頁的版面：依內容區的實際寬度（會隨左側選單收合而變）決定欄數，以及可拖曳面板的寬度。
 * Layout of the card browsing page: the column mode follows the content
 * width (which changes when the sidebar collapses), plus the panel widths.
 */

/** 版面模式：三欄並排、牌組與清單並排而卡面在下方、全部疊成一欄。The layout mode. */
export type LayoutMode = 'three' | 'two' | 'stacked'

/** 可拖曳面板的寬度（px）。Widths of the resizable panels, in pixels. */
export interface PanelWidths {
  deck: number
  viewer: number
}

/** 視窗寬度不超過這個值時卡面預設 {@link VIEWER_DEFAULT}，更寬時 {@link VIEWER_DEFAULT_WIDE}。 */
export const WIDE_SCREEN = 1920
/** 卡面預設寬度（約平板直放）。Default viewer width, about a portrait tablet. */
export const VIEWER_DEFAULT = 768
/** 視窗比 {@link WIDE_SCREEN} 寬時的卡面預設寬度。Default viewer width on wide screens. */
export const VIEWER_DEFAULT_WIDE = 1080
export const VIEWER_MIN = 400
export const VIEWER_MAX = 1400
export const DECK_DEFAULT = 288
export const DECK_MIN = 180
export const DECK_MAX = 560
/** 清單最窄的寬度。Narrowest card list. */
export const LIST_MIN = 240

/** 內容區至少這麼寬才三欄並排。Content width needed for three columns. */
export const THREE_COLUMN_MIN = 1200
/** 內容區至少這麼寬才讓牌組與清單並排。Content width needed for two columns. */
export const TWO_COLUMN_MIN = 640

/**
 * 依內容區寬度決定版面模式。The layout mode for a content width.
 * @param width - 內容區寬度（px）。Content width in pixels.
 * @returns 版面模式。The mode.
 */
export function layoutMode(width: number): LayoutMode {
  if (width >= THREE_COLUMN_MIN) return 'three'
  if (width >= TWO_COLUMN_MIN) return 'two'
  return 'stacked'
}

/**
 * 預設寬度：卡面依視窗寬度是 768 或 1080px。
 * The default widths; the viewer depends on the window width.
 * @param windowWidth - 視窗寬度（px）。The window width.
 * @returns 預設寬度。The defaults.
 */
export function defaultPanelWidths(windowWidth: number): PanelWidths {
  return { deck: DECK_DEFAULT, viewer: windowWidth > WIDE_SCREEN ? VIEWER_DEFAULT_WIDE : VIEWER_DEFAULT }
}

const LIMITS: Record<keyof PanelWidths, [number, number]> = {
  deck: [DECK_MIN, DECK_MAX],
  viewer: [VIEWER_MIN, VIEWER_MAX],
}

/**
 * 整理存下來的面板寬度：只留下使用者拖曳過的面板（沒存的用預設，預設才會跟著螢幕大小變），
 * 不是有限數字的丟掉、超出範圍的拉回範圍內。
 * Sanitizes stored widths, keeping only valid entries (clamped); missing
 * entries fall back to the screen-dependent defaults.
 * @param raw - localStorage 讀出的值。The stored value.
 * @returns 使用者設定過的寬度。The widths the user set.
 */
export function sanitizePanelWidths(raw: unknown): Partial<PanelWidths> {
  const value = (typeof raw === 'object' && raw !== null ? raw : {}) as Partial<Record<keyof PanelWidths, unknown>>
  const result: Partial<PanelWidths> = {}
  for (const key of Object.keys(LIMITS) as (keyof PanelWidths)[]) {
    const size = value[key]
    if (typeof size === 'number' && Number.isFinite(size)) {
      const [min, max] = LIMITS[key]
      result[key] = Math.min(max, Math.max(min, Math.round(size)))
    }
  }
  return result
}
