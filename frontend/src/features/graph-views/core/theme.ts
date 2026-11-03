/**
 * 圖的主題系統（所有排版引擎共用，D15）：一份 JSON 設定檔驅動顏色、尺寸、字型、素材與自訂 CSS。
 * Graph theme system shared by every layout engine: a JSON file drives
 * colors, sizes, fonts, assets and extra CSS. Switching themes never touches
 * component code.
 *
 * - 顏色 → `--sf-color-<key>`；尺寸 → `--sf-size-<key>`（px）；字型 → `--sf-font-<key>`；
 *   素材 → `--sf-asset-<key>`（`url(...)`）。Values become CSS variables.
 * - `extends` 繼承另一個主題，只寫差異。Themes can extend another theme.
 * - 素材網址相對於主題資料夾（`<base>/<id>/`），也可以是絕對網址。Asset URLs are
 *   relative to the theme folder unless absolute.
 */

/** 主題的尺寸（px）。Theme sizes in px. */
export interface GraphThemeSize {
  nodeW: number
  nodeWNarrow: number
  nodeH: number
  rowGap: number
  colGap: number
  bandHeight: number
  edgeWidth: number
  arrowSize: number
  diamondW: number
  exitW: number
  cornerRadius: number
  padding: number
  [key: string]: number
}

/** 主題素材（全部選填）。Theme assets (all optional). */
export interface GraphThemeAssets {
  background?: string
  backgroundOverlay?: string
  nodeTexture?: string
  nodeMasks?: Record<string, string>
  chapterIcon?: string
  tabButton?: string
  tabAvatars?: Record<string, string>
  scrollThumb?: string
  scrollButtons?: Partial<Record<'top' | 'up' | 'down' | 'bottom', string>>
  arrow?: string
  titleBadge?: string
}

/** 一個主題（已解析繼承）。A resolved theme. */
export interface GraphTheme {
  id: string
  label?: string
  extends?: string
  colors: Record<string, string>
  size: GraphThemeSize
  fonts: Record<string, string>
  assets: GraphThemeAssets
  /** 主題附加的 CSS 檔網址。Extra CSS file URL. */
  css?: string
}

/** 主題檔的原始內容（任何欄位都可以省略，由繼承或預設補上）。A raw theme file. */
export interface GraphThemeFile {
  id: string
  label?: string
  extends?: string
  colors?: Record<string, string>
  size?: Partial<GraphThemeSize>
  fonts?: Record<string, string>
  assets?: GraphThemeAssets
  css?: string
}

/** 劇情流程圖的主題就是圖主題。The story theme is a graph theme. */
export type StoryTheme = GraphTheme

/** 平台預設尺寸（千戀＊萬花規格的一半比例，網頁上比較合適）。Default sizes. */
export const DEFAULT_SIZE: GraphThemeSize = {
  nodeW: 240,
  nodeWNarrow: 128,
  nodeH: 48,
  rowGap: 112,
  colGap: 280,
  bandHeight: 72,
  edgeWidth: 4,
  arrowSize: 12,
  diamondW: 96,
  exitW: 160,
  cornerRadius: 14,
  padding: 48,
}

/** 平台預設主題：中性灰階、不需要任何素材。The neutral default theme. */
export const DEFAULT_THEME: GraphTheme = {
  id: 'default',
  label: '預設（灰階）',
  colors: {
    canvas: '#f4f4f5',
    'canvas-text': '#27272a',
    'node-bg': '#ffffff',
    'node-text': '#3f3f46',
    'node-border': '#a1a1aa',
    'node-border-strong': '#52525b',
    'node-hover': '#f4f4f5',
    'node-selected': '#18181b',
    'node-dim-bg': '#e4e4e7',
    'node-dim-text': '#a1a1aa',
    'node-highlight': '#f59e0b',
    'ending-bg': '#fee2e2',
    'ending-border': '#b91c1c',
    'condition-bg': '#ffffff',
    'condition-line': '#a1a1aa',
    edge: '#a1a1aa',
    'edge-path': '#f59e0b',
    'edge-branch-path': '#f472b6',
    'edge-casing': '#18181b',
    'edge-label': '#71717a',
    'band-text': '#71717a',
    'band-line': '#d4d4d8',
    'band-accent': '#71717a',
    'tab-bg': '#ffffff',
    'tab-text': '#3f3f46',
    'tab-active-bg': '#27272a',
    'tab-active-text': '#fafafa',
    'badge-bg': '#2563eb',
    'badge-text': '#ffffff',
    'minimap-bg': 'rgba(255,255,255,0.7)',
    'minimap-node': '#52525b',
    'minimap-edge': '#a1a1aa',
    'minimap-view': 'rgba(37,99,235,0.18)',
    'scroll-button-bg': '#ffffff',
    'scroll-button-text': '#3f3f46',
  },
  size: DEFAULT_SIZE,
  fonts: {
    node: '"Zen Maru Gothic", "M PLUS Rounded 1c", "Hiragino Maru Gothic ProN", "Noto Sans JP", system-ui, sans-serif',
    band: '"Times New Roman", "Noto Serif JP", serif',
    title: '"Noto Serif JP", serif',
  },
  assets: {},
}

const ABSOLUTE = /^(?:[a-z]+:|\/|data:)/i

/**
 * 素材網址：絕對網址原樣，相對網址接在主題資料夾後面。
 * Resolves an asset URL against the theme folder.
 * @param url - 主題檔裡的網址。The URL in the theme file.
 * @param folder - 主題資料夾網址（以 `/` 結尾）。The theme folder URL.
 * @returns 可直接使用的網址。The usable URL.
 */
export function resolveAssetUrl(url: string, folder: string): string {
  return ABSOLUTE.test(url) ? url : folder + url.replace(/^\.\//, '')
}

function resolveAssets(assets: GraphThemeAssets | undefined, folder: string): GraphThemeAssets {
  const out: GraphThemeAssets = {}
  for (const [key, value] of Object.entries(assets ?? {})) {
    if (typeof value === 'string') (out as Record<string, unknown>)[key] = resolveAssetUrl(value, folder)
    else if (value && typeof value === 'object') {
      (out as Record<string, unknown>)[key] = Object.fromEntries(
        Object.entries(value as Record<string, string>).map(([k, v]) => [k, resolveAssetUrl(v, folder)]),
      )
    }
  }
  return out
}

/**
 * 把主題檔疊在父主題上（顏色、尺寸、字型、素材逐鍵覆寫）。
 * Merges a theme file onto its parent, key by key.
 * @param parent - 父主題（已解析）。The resolved parent.
 * @param file - 主題檔。The theme file.
 * @param folder - 這個主題檔的資料夾網址；素材相對於它。The file's folder URL.
 * @returns 解析後的主題。The resolved theme.
 */
export function mergeTheme(parent: GraphTheme, file: GraphThemeFile, folder = ''): GraphTheme {
  const assets = resolveAssets(file.assets, folder)
  return {
    id: file.id,
    label: file.label ?? file.id,
    extends: file.extends,
    colors: { ...parent.colors, ...(file.colors ?? {}) },
    size: { ...parent.size, ...(file.size ?? {}) } as GraphThemeSize,
    fonts: { ...parent.fonts, ...(file.fonts ?? {}) },
    assets: {
      ...parent.assets,
      ...assets,
      nodeMasks: { ...(parent.assets.nodeMasks ?? {}), ...(assets.nodeMasks ?? {}) },
      tabAvatars: { ...(parent.assets.tabAvatars ?? {}), ...(assets.tabAvatars ?? {}) },
      scrollButtons: { ...(parent.assets.scrollButtons ?? {}), ...(assets.scrollButtons ?? {}) },
    },
    css: file.css ? resolveAssetUrl(file.css, folder) : undefined,
  }
}

/** 讀主題檔的函式（預設用 `fetch`）。Fetches a theme file. */
export type ThemeFetcher = (url: string) => Promise<GraphThemeFile>

const defaultFetcher: ThemeFetcher = async (url) => {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`主題檔讀取失敗：${url}（${res.status}）`)
  return (await res.json()) as GraphThemeFile
}

/**
 * 讀取並解析主題（含 `extends` 鏈）；`default` 不需要讀檔。
 * Loads a theme by id, following `extends`; `default` is built in.
 * @param id - 主題 id。Theme id.
 * @param base - 主題根網址，預設 `/story-themes`。Themes base URL.
 * @param fetcher - 讀檔函式。The fetcher.
 * @returns 解析後的主題。The resolved theme.
 */
export async function loadTheme(id: string, base = '/story-themes', fetcher: ThemeFetcher = defaultFetcher): Promise<GraphTheme> {
  const chain: Array<{ file: GraphThemeFile; folder: string }> = []
  let cur: string | undefined = id
  const seen = new Set<string>()
  while (cur && cur !== DEFAULT_THEME.id) {
    if (seen.has(cur)) throw new Error(`主題繼承形成迴圈：${[...seen, cur].join(' → ')}`)
    seen.add(cur)
    const folder: string = ABSOLUTE.test(cur) && cur.endsWith('.json')
      ? cur.slice(0, cur.lastIndexOf('/') + 1)
      : `${base.replace(/\/$/, '')}/${cur}/`
    const url: string = ABSOLUTE.test(cur) && cur.endsWith('.json') ? cur : `${folder}theme.json`
    const file: GraphThemeFile = await fetcher(url)
    chain.push({ file, folder })
    cur = file.extends
  }
  let theme = DEFAULT_THEME
  for (const { file, folder } of chain.reverse()) theme = mergeTheme(theme, file, folder)
  return theme
}

const cssUrl = (url: string) => `url("${url.replace(/"/g, '%22')}")`

/**
 * 主題 → CSS 變數（給 `style` 用）。缺的素材不輸出，CSS 以 `var(--x, 預設)` 退回。
 * Converts a theme into CSS custom properties; missing assets are omitted so
 * stylesheets fall back via `var(--x, fallback)`.
 * @param theme - 主題。The theme.
 * @returns CSS 變數物件。CSS variables.
 */
export function themeToCssVars(theme: GraphTheme): Record<string, string> {
  const vars: Record<string, string> = {}
  for (const [k, v] of Object.entries(theme.colors)) vars[`--sf-color-${k}`] = v
  for (const [k, v] of Object.entries(theme.size)) vars[`--sf-size-${k}`] = `${v}px`
  for (const [k, v] of Object.entries(theme.fonts)) vars[`--sf-font-${k}`] = v
  const a = theme.assets
  const single: Array<[string, string | undefined]> = [
    ['background', a.background], ['background-overlay', a.backgroundOverlay], ['node-texture', a.nodeTexture],
    ['chapter-icon', a.chapterIcon], ['tab-button', a.tabButton], ['scroll-thumb', a.scrollThumb],
    ['arrow', a.arrow], ['title-badge', a.titleBadge],
  ]
  for (const [k, v] of single) if (v) vars[`--sf-asset-${k}`] = cssUrl(v)
  for (const [k, v] of Object.entries(a.nodeMasks ?? {})) vars[`--sf-asset-mask-${k}`] = cssUrl(v)
  for (const [k, v] of Object.entries(a.scrollButtons ?? {})) if (v) vars[`--sf-asset-scroll-${k}`] = cssUrl(v)
  return vars
}
