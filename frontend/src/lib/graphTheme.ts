/**
 * 知識圖譜的主題選擇規則（`docs/wip/graph_themes_FEAT_2026-11-22.md` D4）：存在哪裡、預設是哪個，以及單字卡
 * 舊的標籤樣式（`kg_fontStyleId`）怎麼換算成主題。
 *
 * Knowledge-graph theme rules: where the choice is stored, the default, and how the retired label styles map
 * to themes.
 */

/** 知識圖譜選的主題（`localStorage`）。Where the chosen theme is stored. */
export const GRAPH_THEME_KEY = 'kg_theme'

/** 單字卡舊的標籤樣式（已退役，換算後刪掉）。The retired label style key. */
export const LEGACY_STYLE_KEY = 'kg_fontStyleId'

/** 沒選過主題、系統是深色外觀時用的主題（平台預設只有亮色）。The fallback in dark mode. */
export const DARK_FALLBACK_THEME = 'classic-white'

/**
 * 沒選過主題時用哪個：亮色外觀用平台預設（`null`），深色外觀用經典白（預設的深色版，同樣繼承預設），
 * 圖區才不會在深色頁面上變成一塊亮色。選過的主題（包括明確選了預設）一律照選的。
 * The theme to use when none was chosen: the platform default in light mode, Classic White in dark mode.
 * @param prefersDark - 系統是否為深色外觀。Whether the system prefers dark.
 * @returns 主題 id；`null` 表示平台預設。The theme id, or null for the default.
 */
export function fallbackTheme(prefersDark: boolean): string | null {
  return prefersDark ? DARK_FALLBACK_THEME : null
}

/**
 * 舊的標籤樣式 → 主題。保留的兩種對到同名主題；被刪掉的四種（亮色字配深色描邊）最接近經典白。
 * Retired label styles mapped to themes.
 */
const LEGACY_STYLE_THEMES: Record<string, string> = {
  classic: 'classic-white',
  slate: 'slate-gray',
  neon: 'classic-white',
  sunset: 'classic-white',
  mint: 'classic-white',
  cyber: 'classic-white',
}

/**
 * 舊的標籤樣式要換成哪個主題；沒有存過或不認得時為 null（用平台預設）。
 * The theme a retired label style becomes, or null.
 * @param styleId - 存過的樣式 id。The stored style id.
 * @returns 主題 id，或 null。The theme id, or null.
 */
export function themeForLegacyStyle(styleId: unknown): string | null {
  return typeof styleId === 'string' ? (LEGACY_STYLE_THEMES[styleId] ?? null) : null
}
