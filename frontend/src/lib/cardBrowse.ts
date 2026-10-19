/**
 * 卡片瀏覽頁的網址參數：頁面狀態都放在網址，這裡集中定義什麼是合法值。
 * URL parameters of the card browsing page, parsed in one place.
 */

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
}

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
  }
}
