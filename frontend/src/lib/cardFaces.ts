/**
 * 卡片瀏覽頁的卡面切換規則：要顯示哪一面、這次是翻面還是換卡、往前還是往回。與 React 無關，方便測試。
 * Which face the card viewer shows and how it got there: flip or switch,
 * forward or back. React-free.
 */

import type { CardSide } from './cardBrowse'
import type { FaceOccasion } from './faceTransitions'

/**
 * 切換動畫裡的一面：哪張卡片的哪一面，格式為 `卡片ID:正反面`。卡片與正反面一起編進同一個值，
 * 換卡和翻面才都是「值變了」，都會播動畫。
 * A face in the transition, `cardId:side`; card and side share one value so both changes animate.
 */
export type FaceValue = `${number}:${CardSide}`

/**
 * 組出一面的值。Builds a face value.
 * @param cardId - 卡片 ID。The card.
 * @param side - 正面或背面。The side.
 * @returns 一面的值。The face value.
 */
export function toFaceValue(cardId: number, side: CardSide): FaceValue {
  return `${cardId}:${side}`
}

/**
 * 拆開一面的值。Splits a face value.
 * @param value - 一面的值。The face value.
 * @returns 卡片 ID 與正反面。The card and the side.
 */
export function parseFaceValue(value: FaceValue): { cardId: number; side: CardSide } {
  const separator = value.indexOf(':')
  return { cardId: Number(value.slice(0, separator)), side: value.slice(separator + 1) as CardSide }
}

/** 正在顯示的那一面，以及換到它時播哪種動畫、往哪個方向。The face on screen and how it was reached. */
export interface ShownFace {
  value: FaceValue
  occasion: FaceOccasion
  /** 1 為往前（翻到背面、換到新的卡片），-1 為往回（翻回正面、回到上一張）。+1 forward, -1 back. */
  direction: 1 | -1
}

/**
 * 選取的卡片（已載入）與正反面改變後要顯示的那一面。卡片變了是換卡：按「返回」或瀏覽器上一頁
 * 回來的倒著播，其他（選卡、跳轉、瀏覽器下一頁）往前；卡片沒變是翻面：翻到背面往前。
 * The face to show for the selected card and side.
 * @param previous - 目前顯示的那一面；還沒顯示過時為 null。The face on screen, or null.
 * @param cardId - 選取的卡片（資料已載入）。The selected, loaded card.
 * @param side - 選取的正反面。The selected side.
 * @param back - 這次是不是往回的導覽（見 {@link isBackNavigation}）。Whether the navigation went back.
 * @returns 要顯示的那一面；沒有變化時回傳 `previous` 本身。The face to show; `previous` itself when unchanged.
 */
export function nextShownFace(
  previous: ShownFace | null,
  cardId: number,
  side: CardSide,
  back: boolean,
): ShownFace {
  const value = toFaceValue(cardId, side)
  if (previous?.value === value) return previous
  if (previous !== null && parseFaceValue(previous.value).cardId !== cardId) {
    return { value, occasion: 'switch', direction: back ? -1 : 1 }
  }
  return { value, occasion: 'flip', direction: side === 'answer' ? 1 : -1 }
}

/** 導覽的種類（與 react-router 的 `NavigationType` 相同）。Navigation types, as in react-router. */
export type NavigationKind = 'POP' | 'PUSH' | 'REPLACE'

/** 「返回」改網址（沒有上一筆歷史可退）時放進歷史的狀態。History state of a “back” done by replacing. */
export interface BackState {
  back?: boolean
}

/**
 * 這次導覽是不是往回：瀏覽器上一頁（`history.state.idx` 變小）或「返回」改網址（狀態帶 `back`）。
 * 瀏覽器上一頁與下一頁的種類都是 POP，只能比歷史的位置；位置不明時當成上一頁（較常見）。
 * Whether a navigation went back: a POP to an earlier history index, or a
 * “back” done by replacing. An unknown index counts as back.
 * @param kind - 導覽種類。The navigation type.
 * @param state - 新位置的歷史狀態。The new location's state.
 * @param index - 新位置在歷史中的位置。The new history index.
 * @param previousIndex - 上一個位置在歷史中的位置。The previous history index.
 * @returns 是否往回。Whether it went back.
 */
export function isBackNavigation(kind: NavigationKind, state: unknown, index: unknown, previousIndex: unknown): boolean {
  switch (kind) {
    case 'POP':
      return typeof index === 'number' && typeof previousIndex === 'number' ? index < previousIndex : true
    case 'REPLACE':
      return (state as BackState | null)?.back === true
    case 'PUSH':
      return false
  }
}

/**
 * 記下一筆，只留最近的幾筆（重新記下的那筆算最新）。Remembers an entry, keeping only the latest ones.
 * @param map - 目前的紀錄（依記下的先後）。The entries, oldest first.
 * @param key - 鍵。The key.
 * @param value - 值。The value.
 * @param limit - 最多留幾筆。How many to keep.
 * @returns 新的紀錄。The new entries.
 */
export function rememberLatest<K, V>(map: ReadonlyMap<K, V>, key: K, value: V, limit: number): Map<K, V> {
  const next = new Map(map)
  next.delete(key)
  next.set(key, value)
  for (const oldest of next.keys()) {
    if (next.size <= limit) break
    next.delete(oldest)
  }
  return next
}
