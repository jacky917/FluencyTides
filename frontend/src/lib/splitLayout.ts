/**
 * 分欄頁面共用的版面計算：依內容區寬度決定並排或疊放，以及可拖曳面板的寬度。
 * Shared math for the split page layouts: the column mode follows the
 * content width (which changes when the sidebar collapses), plus the widths
 * of the resizable panels.
 */

/**
 * 版面模式：全部並排、前面幾欄並排而最後一欄在下方（只有給了 `partial` 斷點才有）、全部疊成一欄。
 * The layout mode: all side by side, all but the last side by side (only
 * with a `partial` breakpoint), or stacked.
 */
export type SplitMode = 'full' | 'partial' | 'stacked'

/** 切換版面模式的內容區寬度（px）。Content widths at which the mode switches. */
export interface SplitBreakpoints {
  /** 至少這麼寬才全部並排。Width needed for all columns side by side. */
  full: number
  /** 至少這麼寬才部分並排；沒給就沒有這個模式。Width for the partial mode, if any. */
  partial?: number
}

/** 一個可拖曳面板的寬度限制與預設值（px）。Limits and default of a resizable panel. */
export interface PanelSpec {
  min: number
  max: number
  /** 依視窗寬度決定的預設寬度。The default width for a window width. */
  defaultWidth: (windowWidth: number) => number
}

/** 頁面上各可拖曳面板的規格，鍵為面板 ID。The specs of a page's resizable panels. */
export type PanelSpecs<K extends string> = Readonly<Record<K, Readonly<PanelSpec>>>

/**
 * 一個分欄頁面的版面設定：頁面 ID、存寬度的鍵、可拖曳面板的規格、其他欄的最窄寬度與切換斷點。
 * 全部一起傳，`usePanelWidths` 與 `SplitLayout` 才不會拿到不一致的設定，斷點也能和各欄的最窄寬度
 * 一起檢查（見 `pageLayouts.test.ts`）。
 * A split page's layout: its ID, storage key, resizable panels, the minimum
 * widths of the other columns and the breakpoints, passed together so they
 * cannot disagree and the breakpoints can be checked against the minimums.
 */
export interface SplitPageConfig<K extends string> {
  readonly id: string
  /**
   * 存面板寬度的 localStorage 鍵；沒給時用 `${id}_panel_widths`。
   * The localStorage key of the widths; defaults to `${id}_panel_widths`.
   */
  readonly storageKey?: string
  readonly specs: PanelSpecs<K>
  /**
   * 吃剩下寬度的欄（沒有 `resizable` 的欄）最窄多少（px），鍵為欄 ID。
   * Minimum widths of the flexible columns, keyed by column ID.
   */
  readonly fluidMin: Readonly<Record<string, number>>
  readonly breakpoints: Readonly<SplitBreakpoints>
}

/**
 * 某頁存面板寬度的 localStorage 鍵。The localStorage key of a page's panel widths.
 * @param layout - 頁面的版面設定。The page's layout config.
 * @returns 儲存鍵。The key.
 */
export function panelWidthsKey(layout: Pick<SplitPageConfig<string>, 'id' | 'storageKey'>): string {
  return layout.storageKey ?? `${layout.id}_panel_widths`
}

/** 把寬度四捨五入並拉回面板的範圍內。Rounds a width and clamps it to the panel's range. */
function clampWidth(spec: Readonly<PanelSpec>, width: number): number {
  return Math.min(spec.max, Math.max(spec.min, Math.round(width)))
}

/**
 * 依內容區寬度決定版面模式。The layout mode for a content width.
 * @param width - 內容區寬度（px）。Content width in pixels.
 * @param breakpoints - 切換的寬度。The breakpoints.
 * @returns 版面模式。The mode.
 */
export function splitMode(width: number, breakpoints: SplitBreakpoints): SplitMode {
  if (width >= breakpoints.full) return 'full'
  if (breakpoints.partial !== undefined && width >= breakpoints.partial) return 'partial'
  return 'stacked'
}

/**
 * 各面板的預設寬度（拉回面板的範圍內）。The default widths, clamped to each panel's range.
 * @param windowWidth - 視窗寬度（px）。The window width.
 * @param specs - 面板規格。The panel specs.
 * @returns 預設寬度。The defaults.
 */
export function defaultWidths<K extends string>(windowWidth: number, specs: PanelSpecs<K>): Record<K, number> {
  const result = {} as Record<K, number>
  for (const key of Object.keys(specs) as K[]) result[key] = clampWidth(specs[key], specs[key].defaultWidth(windowWidth))
  return result
}

/**
 * 整理存下來的面板寬度：只留下使用者拖曳過的面板（沒存的用預設，預設才會跟著螢幕大小變），
 * 不是有限數字的丟掉、超出範圍的拉回範圍內。
 * Sanitizes stored widths, keeping only valid entries (clamped); missing
 * entries fall back to the screen-dependent defaults.
 * @param raw - localStorage 讀出的值。The stored value.
 * @param specs - 面板規格。The panel specs.
 * @returns 使用者設定過的寬度。The widths the user set.
 */
export function sanitizeWidths<K extends string>(raw: unknown, specs: PanelSpecs<K>): Partial<Record<K, number>> {
  const value = (typeof raw === 'object' && raw !== null ? raw : {}) as Partial<Record<K, unknown>>
  const result: Partial<Record<K, number>> = {}
  for (const key of Object.keys(specs) as K[]) {
    const size = value[key]
    if (typeof size === 'number' && Number.isFinite(size)) result[key] = clampWidth(specs[key], size)
  }
  return result
}
