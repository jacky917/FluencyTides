/**
 * 卡片頁的網址參數：頁面狀態都放在網址，這裡集中定義什麼是合法值。
 * URL parameters of the card page, parsed in one place.
 */

/** 記住瀏覽位置的 localStorage 鍵。localStorage key of the remembered position. */
export const REMEMBERED_SEARCH_KEY = 'cards_last_search'

/**
 * 讀出記住的瀏覽位置（`searchToRemember` 的字串）；讀不到時為空字串。
 * Reads the remembered position, or '' when unavailable.
 * @returns 參數字串。The query string.
 */
export function readRememberedSearch(): string {
  try {
    return window.localStorage.getItem(REMEMBERED_SEARCH_KEY) ?? ''
  } catch {
    return ''
  }
}

/**
 * 記下瀏覽位置（存不了時略過，只影響下次是否回到同一個位置）。
 * Stores the position, ignoring failures.
 * @param search - 參數字串。The query string.
 */
export function writeRememberedSearch(search: string): void {
  try {
    window.localStorage.setItem(REMEMBERED_SEARCH_KEY, search)
  } catch {
    // 只影響下次是否回到同一個位置
  }
}

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
  /**
   * 右欄顯示建卡表單（`new=1`）；`card`、`trail` 保留，退出時回到原本看的卡片。
   * Whether the right column shows the create form; the card is kept for when it closes.
   */
  creating: boolean
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
    creating: params.get('new') === '1',
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

/**
 * 要記下來的瀏覽位置（下次從側邊欄回到這頁時恢復）：牌組、搜尋、頁碼、選取的卡片與正反面；
 * 跳轉紀錄與建卡畫面（`new`）只對這一輪瀏覽有意義，不記。參數依固定順序排列，同樣的位置得到同樣的字串。
 * The browse position to remember, without the jump trail, in a stable order.
 * @param params - 目前的網址參數。The URL parameters.
 * @returns 參數字串（沒有位置時為空字串）。The query string, or ''.
 */
export function searchToRemember(params: URLSearchParams): string {
  const kept = new URLSearchParams()
  for (const key of ['deck', 'q', 'page', 'card', 'side']) {
    const value = params.get(key)
    if (value) kept.set(key, value)
  }
  return kept.toString()
}

/**
 * 從別處（草稿通知的「前往」等）打開卡片頁建卡畫面的網址（`docs/wip/retire_generate_page_FEAT_2026-11-23.md` D3）：
 * 已經在卡片頁就留在目前的位置，否則回到上次在卡片頁看的位置（牌組在網址裡，草稿沒有記牌組），再加上 `new=1`。
 * The URL that opens the card page's create form: the current position when on
 * the card page, otherwise the remembered one, plus `new=1`.
 * @param pathname - 目前的路徑。The current path.
 * @param currentSearch - 目前的網址參數字串（可帶 `?`）。The current query string.
 * @param remembered - 記住的瀏覽位置。The remembered position.
 * @returns 路徑與參數。The path with its query.
 */
export function createFormPath(pathname: string, currentSearch: string, remembered: string): string {
  const params = new URLSearchParams(pathname === '/cards' ? currentSearch : remembered)
  params.set('new', '1')
  return `/cards?${params.toString()}`
}

/**
 * 卡片頁的建卡畫面是否已經開著（看參數內容，不比網址字串，編碼不同也認得出來）。
 * Whether the card page's create form is already open.
 * @param pathname - 目前的路徑。The current path.
 * @param currentSearch - 目前的網址參數字串（可帶 `?`）。The current query string.
 * @returns 已經開著時為 `true`。Whether it is open.
 */
export function isCreateFormOpen(pathname: string, currentSearch: string): boolean {
  return pathname === '/cards' && new URLSearchParams(currentSearch).get('new') === '1'
}

/** 卡片清單的層數選項的值（0＝全部子牌組）。The allowed list depths. */
export const CARD_DEPTH_VALUES = [0, 1, 2, 3] as const

/**
 * 讀回存起來的清單層數（`docs/wip/cards_page_revamp_FEAT_2026-11-23.md` D3）；不是選項之一時回到全部（0）。
 * Restores the stored list depth, falling back to all.
 * @param value - 存起來的值。The stored value.
 * @returns 層數。The depth.
 */
export function restoreCardDepth(value: unknown): number {
  return (CARD_DEPTH_VALUES as readonly unknown[]).includes(value) ? (value as number) : 0
}

/**
 * 清單要篩的筆記類型：選了筆記類型（而且屬於這個卡片類型）就是那一種，只選卡片類型就是它的全部筆記類型，
 * 「全部卡片類型」不篩。
 * The note types the list is limited to.
 * @param noteTypes - 卡片類型的筆記類型；undefined 為「全部卡片類型」。The card type's note types, or undefined.
 * @param noteType - 選的筆記類型；null 為全部。The chosen note type.
 * @returns 筆記類型；undefined 表示不篩。The note types, or undefined for no filter.
 */
export function browseNoteTypes(noteTypes: string[] | undefined, noteType: string | null): string[] | undefined {
  if (!noteTypes || noteTypes.length === 0) return undefined
  return noteType && noteTypes.includes(noteType) ? [noteType] : noteTypes
}
