/**
 * 牌組條件列（`components/shared/DeckScopeBar.tsx`）的選項：選項的型別與層數選項。放在元件檔以外，
 * 元件檔只匯出元件（React Fast Refresh 的要求）。
 * Options of the deck scope bar, kept out of the component file.
 */

/** 選單的一個選項。One option. */
export interface ScopeOption<T extends string | number = string> {
  value: T
  label: string
}

/**
 * 層數選項，知識圖譜的主題樹與卡片清單共用同一組（0＝全部）。1 層在主題樹是根底下一層、在卡片清單是牌組本身。
 * The depth options shared by the topic tree and the card list, 0 meaning all.
 */
export const DEPTH_OPTIONS: ScopeOption<number>[] = [
  { value: 1, label: '顯示 1 層' },
  { value: 2, label: '顯示 2 層' },
  { value: 3, label: '顯示 3 層' },
  { value: 0, label: '全部展開' },
]
