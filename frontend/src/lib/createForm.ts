/**
 * 建卡表單：把 `GET /handlers` 的 `create_schema`（或預覽流程的 `draft_schema`，JSON Schema）轉成
 * 表單欄位，並把後端 422 的錯誤對回欄位（handlers 計畫書 D3、§6）。
 *
 * The create form: turns a handler's `create_schema` into form fields and
 * maps the backend's 422 errors back onto them.
 *
 * 支援的 schema 寫法：`title`、`description`、`required`、`default`、`examples`（可輸入的下拉）、
 * `enum`（只能選的下拉）、`pattern`、`x-ui: textarea`（多行）；不認得的 `x-ui` 值當一般文字欄位。
 * 只畫字串欄位，以及 `x-ui: lines` 的字串陣列（多行文字框、一行一筆，送出時拆成陣列、去掉空行，
 * 例如對話卡的多筆參考回答）；其他型別的參數不會出現在網頁表單。
 */

import type { DeckStatus } from '../hooks/useDeckStatus'

/** 表單的一個欄位。One form field. */
export interface FormField {
  /** 參數名（送出時的鍵）。The parameter name. */
  name: string
  /** 顯示名稱；schema 沒寫時用參數名。The label. */
  title: string
  description: string | null
  required: boolean
  defaultValue: string
  /** 建議值（可輸入的下拉）。Suggested values. */
  examples: string[]
  /** 只能從中選一個的值（下拉選單）；空陣列表示自由輸入。Allowed values, or empty. */
  options: string[]
  multiline: boolean
  /** 一行一筆的陣列欄位（`x-ui: lines`）：送出時拆成字串陣列。A one-entry-per-line list. */
  lines: boolean
  /** 格式（正規表示式），用來提示；實際驗證由後端負責。A format hint. */
  pattern: string | null
}

/** schema 的一個屬性（只列用到的鍵）。A schema property. */
interface SchemaProperty {
  type?: string
  items?: { type?: string }
  anyOf?: { type?: string }[]
  title?: string
  description?: string
  default?: unknown
  examples?: unknown[]
  enum?: unknown[]
  pattern?: string
  'x-ui'?: string
}

/** 是否為字串欄位（含可為 null 的字串）。Whether a property holds a string. */
function isStringProperty(prop: SchemaProperty): boolean {
  if (prop.type === 'string') return true
  return Array.isArray(prop.anyOf) && prop.anyOf.some((option) => option.type === 'string')
}

/** 是否為一行一筆的字串陣列（`x-ui: lines`）。Whether a property is a one-per-line string list. */
function isLinesProperty(prop: SchemaProperty): boolean {
  return prop.type === 'array' && prop.items?.type === 'string' && prop['x-ui'] === 'lines'
}

/**
 * 把 `create_schema` 轉成表單欄位（依 schema 的屬性順序）。
 * Turns a create schema into form fields.
 * @param schema - `create_schema`；null 時沒有表單。The schema, or null.
 * @returns 欄位與略過的（非字串）參數名。The fields and the skipped parameters.
 */
export function schemaFields(schema: Record<string, unknown> | null): { fields: FormField[]; skipped: string[] } {
  const properties = (schema?.properties ?? {}) as Record<string, SchemaProperty>
  const required = new Set(Array.isArray(schema?.required) ? (schema.required as string[]) : [])
  const fields: FormField[] = []
  const skipped: string[] = []
  for (const [name, prop] of Object.entries(properties)) {
    const lines = isLinesProperty(prop)
    if (!lines && !isStringProperty(prop)) {
      skipped.push(name)
      continue
    }
    fields.push({
      name,
      title: prop.title || name,
      description: prop.description ?? null,
      required: required.has(name),
      defaultValue: typeof prop.default === 'string' ? prop.default : '',
      examples: lines ? [] : (prop.examples ?? []).filter((example): example is string => typeof example === 'string'),
      options: lines ? [] : (prop.enum ?? []).filter((option): option is string => typeof option === 'string'),
      multiline: lines || prop['x-ui'] === 'textarea',
      lines,
      pattern: prop.pattern ?? null,
    })
  }
  return { fields, skipped }
}

/**
 * 表單的值轉成送出的參數：一行一筆的欄位拆成陣列（去掉前後空白與空行），其餘原樣。
 * Turns form values into request parameters; line fields become arrays.
 * @param fields - 欄位。The fields.
 * @param values - 表單的值。The form values.
 * @returns 參數。The parameters.
 */
export function formParams(fields: FormField[], values: Record<string, string>): Record<string, unknown> {
  const params: Record<string, unknown> = { ...values }
  for (const field of fields) {
    if (!field.lines) continue
    params[field.name] = (values[field.name] ?? '')
      .split(/\r\n|\r|\n/)
      .map((line) => line.trim())
      .filter((line) => line !== '')
  }
  return params
}

/**
 * 表單的初始值（schema 的 `default`，沒有時空字串）。Initial values.
 * @param fields - 欄位。The fields.
 * @returns 參數名 → 值。Name to value.
 */
export function initialValues(fields: FormField[]): Record<string, string> {
  return Object.fromEntries(fields.map((field) => [field.name, field.defaultValue]))
}

/**
 * 還沒填的必填欄位（去掉前後空白後為空）。Required fields still empty.
 * @param fields - 欄位。The fields.
 * @param values - 目前的值。Current values.
 * @returns 參數名。The names.
 */
export function missingRequired(fields: FormField[], values: Record<string, string>): string[] {
  return fields.filter((field) => field.required && !(values[field.name] ?? '').trim()).map((field) => field.name)
}

/**
 * 為什麼還不能建立（牌組的部分）；可以建立時為 null。按鈕旁顯示這句，使用者不必捲回最上方看牌組說明。
 * Why the deck blocks creating, or null when it does not.
 * @param fixedDeck - 卡片固定建在的牌組；有值時牌組不會擋。The fixed deck.
 * @param selectedDeck - 牌組樹選的牌組。The selected deck.
 * @param deckStatus - 所選牌組的狀態（見 `useDeckStatus`）。The selected deck's status.
 * @returns 原因，或 null。The reason, or null.
 */
export function deckBlockedReason(fixedDeck: string | null, selectedDeck: string | null, deckStatus: DeckStatus): string | null {
  if (fixedDeck) return null
  if (deckStatus === 'all' || selectedDeck === null) return '還沒選牌組：請先在牌組樹選一個牌組（「全部牌組」不能建立）。'
  if (deckStatus === 'loading') return '正在確認牌組是否存在…'
  if (deckStatus === 'error') return `無法載入牌組清單，暫時無法確認「${selectedDeck}」是否存在；請在牌組樹重新整理。`
  if (deckStatus === 'missing') return `牌組「${selectedDeck}」已不存在，請在牌組樹重新選擇。`
  return null
}

/**
 * 牌組不能用（沒選、已不存在、清單載入失敗）而且卡片要建在所選牌組：表單整個不顯示，改顯示原因，
 * 免得填完、生成完才發現不能建立。清單載入中是一瞬間的事，不擋（按鈕旁會寫「正在確認」）。
 * Whether the deck rules out creating at all, so the form is replaced by a notice.
 * @param fixedDeck - 卡片固定建在的牌組。The fixed deck.
 * @param selectedDeck - 牌組樹選的牌組。The selected deck.
 * @param deckStatus - 所選牌組的狀態。The selected deck's status.
 * @returns 是否擋住。Whether it blocks the form.
 */
export function deckBlocksForm(fixedDeck: string | null, selectedDeck: string | null, deckStatus: DeckStatus): boolean {
  if (fixedDeck) return false
  return selectedDeck === null || deckStatus === 'all' || deckStatus === 'missing' || deckStatus === 'error'
}

/** 422 錯誤對回表單的結果。A 422 error mapped onto the form. */
export interface FormErrors {
  /** 參數名 → 錯誤訊息。Name to message. */
  fields: Record<string, string>
  /** 對不到欄位的錯誤。Errors that match no field. */
  other: string[]
}

/** FastAPI 驗證錯誤的一項。One FastAPI validation error. */
interface ValidationItem {
  loc?: unknown[]
  msg?: string
}

/**
 * 把 `POST /handlers/{name}/notes` 的標準 422（`detail[].loc` 為 `["body", "params", 欄位]`）對回欄位。
 * 不是這種錯誤時回傳 null，由呼叫端照一般錯誤顯示。
 * Maps the standard 422 of the create endpoint onto form fields; null for
 * any other error.
 * @param error - API client 丟出的錯誤。The error.
 * @param fieldNames - 表單上的參數名。The form's parameter names.
 * @returns 欄位錯誤，或 null。The field errors, or null.
 */
export function formErrorsFrom(error: unknown, fieldNames: string[]): FormErrors | null {
  const response = (error as { response?: { status?: number; data?: { detail?: unknown } } } | null)?.response
  const detail = response?.data?.detail
  if (response?.status !== 422 || !Array.isArray(detail)) return null
  const known = new Set(fieldNames)
  const result: FormErrors = { fields: {}, other: [] }
  for (const item of detail as ValidationItem[]) {
    const loc = Array.isArray(item.loc) ? item.loc : []
    const rawMsg = item.msg ?? '格式不符'
    const name = loc.length >= 3 && loc[0] === 'body' && loc[1] === 'params' ? String(loc[2]) : null
    // 陣列欄位的某一筆（例如 ["body", "params", "references", 1]）：標在欄位上並註明第幾筆
    const index = loc.length === 4 && typeof loc[3] === 'number' ? loc[3] : null
    const msg = index !== null ? `第 ${index + 1} 筆：${rawMsg}` : rawMsg
    if (name !== null && known.has(name) && (loc.length === 3 || index !== null)) {
      result.fields[name] = result.fields[name] ? `${result.fields[name]}；${msg}` : msg
    } else {
      result.other.push(name !== null ? `${name}: ${msg}` : msg)
    }
  }
  return result
}
