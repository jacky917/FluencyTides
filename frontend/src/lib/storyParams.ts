/**
 * 劇情流程圖頁的網址參數（D16，比照 `cardBrowse.ts`）：`/story/:game?tab=&node=&line=&kind=&verb=`。
 * URL parameters of the story page, parsed in one place.
 *
 * - 選節點新增一筆歷史（上一頁可以關閉抽屜）；篩選、搜尋、選台詞用取代。
 * - Selecting a node pushes history; filters, search and line selection replace.
 */

import { positiveInt } from './cardBrowse'

/** 流程圖頁的狀態。The page state kept in the URL. */
export interface StoryParams {
  /** 分頁；null 為總覽。Tab key, or null for the overview. */
  tab: string | null
  /** 抽屜打開的節點。The open node. */
  node: string | null
  /** 抽屜中選取的台詞。The selected line. */
  line: number | null
  /** 註記篩選（卡片類型）。Card-type filter. */
  kind: string | null
  /** 動詞搜尋。Verb search. */
  verb: string
}

/**
 * 讀出網址參數；不合法的值退回預設。Parses the URL parameters.
 * @param params - 網址參數。The URL parameters.
 * @returns 頁面狀態。The page state.
 */
export function parseStoryParams(params: URLSearchParams): StoryParams {
  return {
    tab: params.get('tab') || null,
    node: params.get('node') || null,
    line: positiveInt(params.get('line')),
    kind: params.get('kind') || null,
    verb: (params.get('verb') ?? '').trim(),
  }
}

/**
 * 轉回網址參數（只寫有值的鍵，順序固定）。Formats the state as URL parameters.
 * @param state - 頁面狀態。The state.
 * @returns 網址參數。The URL parameters.
 */
export function formatStoryParams(state: Partial<StoryParams>): URLSearchParams {
  const out = new URLSearchParams()
  if (state.tab) out.set('tab', state.tab)
  if (state.node) out.set('node', state.node)
  if (state.line) out.set('line', String(state.line))
  if (state.kind) out.set('kind', state.kind)
  if (state.verb) out.set('verb', state.verb)
  return out
}

/** 會新增歷史的欄位。Fields whose change pushes a history entry. */
const PUSH_FIELDS: (keyof StoryParams)[] = ['tab', 'node']

/**
 * 從目前狀態改到下一個狀態時，要不要新增一筆歷史。
 * Whether moving between two states pushes history.
 * @param prev - 目前狀態。The current state.
 * @param next - 下一個狀態。The next state.
 * @returns 新增歷史時為 true。True to push.
 */
export function shouldPushHistory(prev: StoryParams, next: StoryParams): boolean {
  return PUSH_FIELDS.some((f) => prev[f] !== next[f])
}

/**
 * 某個分頁捲動位置在 sessionStorage 的鍵。The sessionStorage key for a tab's scroll.
 * @param gameId - 遊戲。The game.
 * @param tab - 分頁。The tab.
 * @returns 鍵。The key.
 */
export function scrollStorageKey(gameId: string, tab: string): string {
  return `story-scroll:${gameId}:${tab}`
}

/**
 * 跳到卡片頁的網址：`/cards?q=nid:<ids>&card=<card_id>`（D12）。
 * The card-page URL for a set of notes.
 * @param noteIds - 筆記 ID。Note IDs.
 * @param cardId - 要選取的卡片 ID。The card to select.
 * @returns 網址。The URL.
 */
export function cardPageUrl(noteIds: number[], cardId?: number | null): string {
  const params = new URLSearchParams({ q: `nid:${noteIds.join(',')}` })
  if (cardId) params.set('card', String(cardId))
  return `/cards?${params.toString()}`
}

/**
 * 卡片類型命中的節點與各分頁命中數。Search hits and per-tab counts.
 * @param index - `annotations.index.cards`。The cards index.
 * @param verb - 搜尋的動詞。The verb.
 * @param kind - 卡片類型篩選。The card-type filter.
 * @param tabOf - 節點 → 分頁。Node to tab.
 * @returns 命中的節點與每個分頁的數量。Hits and per-tab counts.
 */
export function verbHits(
  index: Record<string, Record<string, string[]>> | undefined,
  verb: string,
  kind: string | null,
  tabOf: (key: string) => string | undefined,
): { nodes: Set<string>; perTab: Record<string, number> } {
  const nodes = new Set<string>()
  const perTab: Record<string, number> = {}
  if (!index || !verb) return { nodes, perTab }
  const table = (kind ? index[`verbs:${kind}`] : index.verbs) ?? {}
  for (const key of table[verb] ?? []) {
    nodes.add(key)
    const tab = tabOf(key)
    if (tab) perTab[tab] = (perTab[tab] ?? 0) + 1
  }
  return { nodes, perTab }
}
