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

/** 卡面預設寬度（約平板直放）。Default viewer width, about a portrait tablet. */
export const VIEWER_DEFAULT = 768
export const VIEWER_MIN = 400
export const VIEWER_MAX = 1200
export const DECK_DEFAULT = 288
export const DECK_MIN = 180
export const DECK_MAX = 560
/** 清單最窄的寬度。Narrowest card list. */
export const LIST_MIN = 240

/** 內容區至少這麼寬才三欄並排。Content width needed for three columns. */
export const THREE_COLUMN_MIN = 1200
/** 內容區至少這麼寬才讓牌組與清單並排。Content width needed for two columns. */
export const TWO_COLUMN_MIN = 640

export const DEFAULT_PANEL_WIDTHS: PanelWidths = { deck: DECK_DEFAULT, viewer: VIEWER_DEFAULT }

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

/** 數值限制在範圍內；不是有限數字時用預設值。Clamps a number, defaulting non-finite input. */
function clamp(value: unknown, min: number, max: number, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? Math.min(max, Math.max(min, Math.round(value))) : fallback
}

/**
 * 整理存下來的面板寬度：缺少或不合理的值退回預設、超出範圍的拉回範圍內。
 * Sanitizes stored panel widths.
 * @param raw - localStorage 讀出的值。The stored value.
 * @returns 可用的寬度。Usable widths.
 */
export function sanitizePanelWidths(raw: unknown): PanelWidths {
  const value = (typeof raw === 'object' && raw !== null ? raw : {}) as Partial<Record<keyof PanelWidths, unknown>>
  return {
    deck: clamp(value.deck, DECK_MIN, DECK_MAX, DECK_DEFAULT),
    viewer: clamp(value.viewer, VIEWER_MIN, VIEWER_MAX, VIEWER_DEFAULT),
  }
}
