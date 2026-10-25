/**
 * 卡片頁的欄位搜尋：選筆記類型、點欄位按鈕、輸入關鍵字，條件之間是「且」，由後端組成 Anki 查詢。
 * Field search on the card page: pick a note type, add field conditions (ANDed);
 * the backend turns them into an Anki query.
 */

import type { HandlerInfo, SearchCondition } from '../types/api'

/** 「全部欄位」的鍵（後端保留）。The key for "any field". */
export const ANY_FIELD = '*'

/** 一個條件（`id` 給 React 的 key 與刪除用）。One condition. */
export interface FieldCondition {
  id: number
  field: string
  value: string
}

/** 欄位搜尋的狀態：用哪個 handler 的欄位、有哪些條件。The field-search state. */
export interface FieldSearchState {
  /** handler 名稱；null 表示不用欄位搜尋（只用 Anki 語法）。The handler, or null. */
  handler: string | null
  conditions: FieldCondition[]
}

export const EMPTY_FIELD_SEARCH: FieldSearchState = { handler: null, conditions: [] }

/**
 * 開放欄位搜尋、而且有欄位按鈕的 handler。
 * Handlers offering field search.
 * @param handlers - `GET /handlers` 的結果。The handlers.
 * @returns 可以欄位搜尋的 handler。The searchable handlers.
 */
export function searchableHandlers(handlers: HandlerInfo[] | undefined): HandlerInfo[] {
  return (handlers ?? []).filter((h) => h.capabilities.search?.supported && (h.search_fields?.length ?? 0) > 0)
}

/**
 * 換成另一個 handler：條件清空（欄位按鈕不同）。
 * Switches the handler and clears the conditions.
 * @param handler - handler 名稱或 null。The handler, or null.
 * @returns 新狀態。The new state.
 */
export function selectHandler(handler: string | null): FieldSearchState {
  return { handler, conditions: [] }
}

/**
 * 加一個條件（同一個欄位可以加很多次）。
 * Adds a condition; the same field may appear more than once.
 * @param state - 目前狀態。The state.
 * @param field - 欄位按鈕的鍵。The button key.
 * @returns 新狀態與新條件的 id。The new state and the new condition's id.
 */
export function addCondition(state: FieldSearchState, field: string): { state: FieldSearchState; id: number } {
  const id = state.conditions.reduce((max, c) => Math.max(max, c.id), 0) + 1
  return { state: { ...state, conditions: [...state.conditions, { id, field, value: '' }] }, id }
}

/**
 * 改一個條件的關鍵字。
 * Sets a condition's keyword.
 * @param state - 目前狀態。The state.
 * @param id - 條件 id。The condition.
 * @param value - 關鍵字。The keyword.
 * @returns 新狀態。The new state.
 */
export function setConditionValue(state: FieldSearchState, id: number, value: string): FieldSearchState {
  return { ...state, conditions: state.conditions.map((c) => (c.id === id ? { ...c, value } : c)) }
}

/**
 * 刪掉一個條件。
 * Removes a condition.
 * @param state - 目前狀態。The state.
 * @param id - 條件 id。The condition.
 * @returns 新狀態。The new state.
 */
export function removeCondition(state: FieldSearchState, id: number): FieldSearchState {
  return { ...state, conditions: state.conditions.filter((c) => c.id !== id) }
}

/**
 * 送給後端的條件：空白的關鍵字略過。
 * The conditions to send; blank keywords are skipped.
 * @param state - 目前狀態。The state.
 * @returns 條件。The conditions.
 */
export function requestConditions(state: FieldSearchState): SearchCondition[] {
  return state.conditions
    .map((c) => ({ field: c.field, value: c.value.trim() }))
    .filter((c) => c.value !== '')
}

/**
 * 欄位按鈕的名稱（`*` 是「全部欄位」；找不到時顯示鍵）。
 * The label of a field button.
 * @param handler - handler。The handler.
 * @param key - 欄位按鈕的鍵。The key.
 * @returns 名稱。The label.
 */
export function fieldTitle(handler: HandlerInfo | undefined, key: string): string {
  if (key === ANY_FIELD) return '全部欄位'
  return handler?.search_fields?.find((f) => f.key === key)?.title ?? key
}

/**
 * 讀回存起來的狀態；格式不對時回到空狀態（localStorage 可能被改過或來自舊版）。
 * Restores a stored state, falling back to empty.
 * @param value - 存起來的值。The stored value.
 * @returns 狀態。The state.
 */
export function restoreFieldSearch(value: unknown): FieldSearchState {
  if (!value || typeof value !== 'object') return EMPTY_FIELD_SEARCH
  const { handler, conditions } = value as Partial<FieldSearchState>
  if (handler !== null && typeof handler !== 'string') return EMPTY_FIELD_SEARCH
  if (!Array.isArray(conditions)) return { handler: handler ?? null, conditions: [] }
  const valid = conditions.filter(
    (c): c is FieldCondition =>
      !!c && typeof c.id === 'number' && typeof c.field === 'string' && typeof c.value === 'string',
  )
  return { handler: handler ?? null, conditions: valid }
}
