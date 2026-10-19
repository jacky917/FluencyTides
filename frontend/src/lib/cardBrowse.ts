/**
 * 卡片瀏覽頁的網址參數：頁面狀態都放在網址，這裡集中定義什麼是合法值。
 * URL parameters of the card browsing page, parsed in one place.
 */

/** 清單每頁幾張（跳轉查詢筆記的卡片也用這個上限）。Cards per list page. */
export const BROWSE_PAGE_SIZE = 50

/** 卡片瀏覽頁的狀態。The page state kept in the URL. */
export interface BrowseParams {
  /** 牌組完整名稱；null 為全部牌組。Full deck name, or null for all. */
  deck: string | null
  /** Anki 搜尋語句；沒有時為空字串。Anki search, or ''. */
  search: string
  /** 頁碼（1 起算）。Page number. */
  page: number
  /** 選取的卡片；null 為未選。Selected card, or null. */
  cardId: number | null
  /** 顯示正面或背面。The face shown. */
  side: CardSide
  /**
   * 從卡面按鈕（「查看母卡片」等）跳過來之前看的卡片，最舊的在前；從清單選卡時清空。
   * Cards visited before jumping here via a card-face button, oldest first.
   */
  trail: number[]
}

/** 卡面的正面或背面。A card face. */
export type CardSide = 'question' | 'answer'

/** 跳轉紀錄最多保留幾張，避免網址無限變長。Max trail length, keeping the URL bounded. */
export const MAX_TRAIL = 20

/**
 * 正整數參數；缺少、不是數字、不是整數或小於 1 時為 null。
 * A positive-integer parameter, or null.
 * @param raw - 參數值。The raw value.
 * @returns 正整數或 null。The integer, or null.
 */
export function positiveInt(raw: string | null): number | null {
  if (raw === null || !/^\d+$/.test(raw)) return null
  const value = Number(raw)
  return Number.isSafeInteger(value) && value >= 1 ? value : null
}

/**
 * 讀出網址參數；不合法的值退回預設，不會送出後端會拒絕的請求（例如 `page=1.5`）。
 * Parses the URL parameters, falling back to defaults on invalid values.
 * @param params - 網址參數。The URL parameters.
 * @returns 頁面狀態。The page state.
 */
export function parseBrowseParams(params: URLSearchParams): BrowseParams {
  return {
    deck: params.get('deck') || null,
    search: params.get('q') ?? '',
    page: positiveInt(params.get('page')) ?? 1,
    cardId: positiveInt(params.get('card')),
    side: params.get('side') === 'answer' ? 'answer' : 'question',
    trail: parseTrail(params.get('trail')),
  }
}

/**
 * 讀出跳轉紀錄（逗號分隔的卡片 ID）；有任何一項不合法就整串不用，超過上限時只留最近的。
 * Parses the trail; any invalid entry drops the whole trail.
 * @param raw - 參數值。The raw value.
 * @returns 卡片 ID，最舊的在前。Card IDs, oldest first.
 */
export function parseTrail(raw: string | null): number[] {
  if (!raw) return []
  const ids = raw.split(',').map(positiveInt)
  if (ids.some((id) => id === null)) return []
  return (ids as number[]).slice(-MAX_TRAIL)
}

/**
 * 把卡片加到跳轉紀錄尾端（超過上限時丟掉最舊的），轉成網址參數值。
 * Appends a card to the trail and formats it for the URL.
 * @param trail - 目前的紀錄。The current trail.
 * @param cardId - 要加入的卡片。The card to append.
 * @returns 參數值。The parameter value.
 */
export function pushTrail(trail: number[], cardId: number): string {
  return [...trail, cardId].slice(-MAX_TRAIL).join(',')
}

/**
 * 從某筆記的卡片中挑出要顯示的那張：依 `noteIds` 的順序找第一個有卡片的筆記，取其中模板序號
 * 最小的卡片（與 Anki 瀏覽器打開筆記時選到的相同）。
 * Picks the card to show for a list of notes: the first note that has cards,
 * and its lowest template ordinal.
 * @param cards - 這些筆記的卡片。The notes' cards.
 * @param noteIds - 筆記 ID，依優先順序。Note IDs in priority order.
 * @returns 卡片 ID；都找不到時為 null。The card ID, or null.
 */
export function pickNoteCard(cards: { card_id: number; note_id: number; ord: number }[], noteIds: number[]): number | null {
  for (const noteId of noteIds) {
    const candidates = cards.filter((card) => card.note_id === noteId)
    if (candidates.length > 0) {
      return candidates.reduce((best, card) => (card.ord < best.ord ? card : best)).card_id
    }
  }
  return null
}
