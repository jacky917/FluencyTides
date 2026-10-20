/**
 * 各頁的分欄版面設定：卡片瀏覽頁三欄（牌組、清單、卡面），Generate、Graph 兩欄（牌組、主區塊）。
 * 計算本身在 `lib/splitLayout.ts`。
 * The split layouts of the pages: three columns on the card browsing page,
 * two (deck tree and main area) on Generate and Graph.
 */

import type { PanelSpec, SplitPageConfig } from './splitLayout'

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
/** 內容區至少這麼寬才讓牌組與旁邊的區塊並排。Content width needed for two columns. */
export const TWO_COLUMN_MIN = 640

/** 牌組樹面板（三頁共用）。The deck tree panel, shared by the three pages. */
export const DECK_PANEL: Readonly<PanelSpec> = Object.freeze({
  min: DECK_MIN,
  max: DECK_MAX,
  defaultWidth: () => DECK_DEFAULT,
})

/** 卡片瀏覽頁：牌組、清單、卡面三欄；卡面寬度依視窗而定。The card browsing page. */
export const CARD_LAYOUT: SplitPageConfig<'deck' | 'viewer'> = Object.freeze({
  id: 'cards',
  specs: Object.freeze({
    deck: DECK_PANEL,
    viewer: Object.freeze({
      min: VIEWER_MIN,
      max: VIEWER_MAX,
      defaultWidth: (windowWidth: number) => (windowWidth > WIDE_SCREEN ? VIEWER_DEFAULT_WIDE : VIEWER_DEFAULT),
    }),
  }),
  breakpoints: Object.freeze({ full: THREE_COLUMN_MIN, partial: TWO_COLUMN_MIN }),
})

/** Generate、Graph 頁共用的面板與斷點：牌組樹＋主區塊。Panels and breakpoints of the two-column pages. */
const SIDE_LAYOUT = {
  specs: Object.freeze({ deck: DECK_PANEL }),
  breakpoints: Object.freeze({ full: TWO_COLUMN_MIN }),
} as const

/** Card Generator 頁。The card generator page. */
export const GENERATE_LAYOUT: SplitPageConfig<'deck'> = Object.freeze({ id: 'generate', ...SIDE_LAYOUT })

/** Knowledge Graph 頁。The knowledge graph page. */
export const GRAPH_LAYOUT: SplitPageConfig<'deck'> = Object.freeze({ id: 'graph', ...SIDE_LAYOUT })
