/**
 * 建卡表單：把 `GET /handlers` 的 `create_schema`（或預覽流程的 `draft_schema`，JSON Schema）轉成
 * 表單欄位，並把後端 422 的錯誤對回欄位（handlers 計畫書 D3、§6）。
 *
 * The create form: turns a handler's `create_schema` into form fields and
 * maps the backend's 422 errors back onto them.
 *
 * 支援的 schema 寫法：`title`、`description`、`required`、`default`、`examples`（可輸入的下拉）、
 * `enum`（只能選的下拉）、`pattern`、`x-ui: textarea`（多行）；不認得的 `x-ui` 值當一般文字欄位。
 * `x-ui: list` 是一列一筆的欄位（人脈卡會變的欄位）：值是字串清單，或物件清單（`items` 指到 `$defs`
 * 裡的物件時，每列一格一個屬性，例如職位的公司＋職稱）；`x-item: textarea` 每列多行，`x-item: tag` 畫成
 * 一個一個小標籤（語言、興趣這種短值），`x-quick` 是快速選項。
 * 只畫字串與一列一筆的欄位：其他型別的參數（例如外語糾錯預覽流程的 LLM 結果）不會出現在網頁表單。
 */

import type { DeckStatus } from '../hooks/useDeckStatus'

/** 一列一筆的欄位的值：每列一個字串，或每列一個物件（每格一個屬性）。The rows of a list field. */
export type RowsValue = string[] | Record<string, string>[]

/** 表單上一個欄位的值。A form value. */
export type FormValue = string | RowsValue

/** 一列一筆的欄位的設定。The settings of a list field. */
export interface RowsSpec {
  /** 每列是物件時的格子（屬性鍵與顯示名稱）；每列是字串時為 null。The cells of object rows. */
  cells: { key: string; title: string }[] | null
  /** 每列多行。Multi-line rows. */
  multiline: boolean
  /** 畫成一個一個小標籤（短值的欄位）。Shown as removable tags. */
  tags: boolean
  /** 快速選項：點一下加一列（已有的不重複）。Quick picks. */
  quick: string[]
}

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
  /** 格式（正規表示式），用來提示；實際驗證由後端負責。A format hint. */
  pattern: string | null
  /** 一列一筆的欄位（`x-ui: list`）；一般文字欄位為 null。The list settings, or null. */
  rows: RowsSpec | null
}

/** schema 的一個屬性（只列用到的鍵）。A schema property. */
interface SchemaProperty {
  type?: string
  anyOf?: { type?: string; items?: { type?: string; $ref?: string } }[]
  title?: string
  description?: string
  default?: unknown
  examples?: unknown[]
  enum?: unknown[]
  pattern?: string
  'x-ui'?: string
  'x-item'?: string
  'x-quick'?: unknown[]
}

/** `$defs` 裡的物件（只列用到的鍵）。An object in `$defs`. */
interface SchemaObject {
  properties?: Record<string, { title?: string }>
}

/**
 * 一列一筆的欄位的設定：`items` 指到物件時取它的屬性當每列的格子。
 * The settings of an `x-ui: list` field.
 */
function rowsSpec(prop: SchemaProperty, defs: Record<string, SchemaObject>): RowsSpec {
  const array = (prop.anyOf ?? []).find((option) => option.type === 'array')
  const ref = array?.items?.$ref?.replace('#/$defs/', '')
  const object = ref ? defs[ref] : undefined
  const cells = object?.properties
    ? Object.entries(object.properties).map(([key, cell]) => ({ key, title: cell.title || key }))
    : null
  return {
    cells,
    multiline: prop['x-item'] === 'textarea',
    tags: !cells && prop['x-item'] === 'tag',
    quick: (prop['x-quick'] ?? []).filter((option): option is string => typeof option === 'string'),
  }
}

/** 是否為字串欄位（含可為 null 的字串）。Whether a property holds a string. */
function isStringProperty(prop: SchemaProperty): boolean {
  if (prop.type === 'string') return true
  return Array.isArray(prop.anyOf) && prop.anyOf.some((option) => option.type === 'string')
}

/**
 * 把 `create_schema` 轉成表單欄位（依 schema 的屬性順序）。
 * Turns a create schema into form fields.
 * @param schema - `create_schema`；null 時沒有表單。The schema, or null.
 * @returns 欄位與略過的（非字串）參數名。The fields and the skipped parameters.
 */
export function schemaFields(schema: Record<string, unknown> | null): { fields: FormField[]; skipped: string[] } {
  const properties = (schema?.properties ?? {}) as Record<string, SchemaProperty>
  const defs = (schema?.$defs ?? {}) as Record<string, SchemaObject>
  const required = new Set(Array.isArray(schema?.required) ? (schema.required as string[]) : [])
  const fields: FormField[] = []
  const skipped: string[] = []
  for (const [name, prop] of Object.entries(properties)) {
    if (!isStringProperty(prop)) {
      skipped.push(name)
      continue
    }
    fields.push({
      name,
      title: prop.title || name,
      description: prop.description ?? null,
      required: required.has(name),
      defaultValue: typeof prop.default === 'string' ? prop.default : '',
      examples: (prop.examples ?? []).filter((example): example is string => typeof example === 'string'),
      options: (prop.enum ?? []).filter((option): option is string => typeof option === 'string'),
      multiline: prop['x-ui'] === 'textarea',
      pattern: prop.pattern ?? null,
      rows: prop['x-ui'] === 'list' ? rowsSpec(prop, defs) : null,
    })
  }
  return { fields, skipped }
}

/**
 * 一列一筆的欄位的初始值（空清單）。Initial values of the list fields.
 * @param fields - 欄位。The fields.
 * @returns 參數名 → 空清單。Name to an empty list.
 */
export function initialRows(fields: FormField[]): Record<string, RowsValue> {
  return Object.fromEntries(fields.filter((field) => field.rows).map((field) => [field.name, []]))
}

/**
 * 把存著的值整理成表單的值：一列一筆的欄位若存的是一段文字（舊版表單留在 sessionStorage 的），轉成一列，
 * 不讓看不到的內容被送出；物件列的欄位放進第一格，標籤欄位照頓號、逗號切成一個一個標籤。
 * Normalizes stored values; text left in a list field becomes one visible row.
 * @param fields - 欄位。The fields.
 * @param values - 存著的值。The stored values.
 * @returns 整理後的值。The normalized values.
 */
export function normalizeRows(fields: FormField[], values: Record<string, FormValue>): Record<string, FormValue> {
  const result = { ...values }
  for (const field of fields) {
    const value = result[field.name]
    if (!field.rows || typeof value !== 'string') continue
    const cells = field.rows.cells
    if (!value.trim()) result[field.name] = []
    else if (field.rows.tags) result[field.name] = addTags([], value)
    else result[field.name] = cells ? [{ ...Object.fromEntries(cells.map((c) => [c.key, ''])), [cells[0].key]: value }] : [value]
  }
  return result
}

/** 一列是不是空的（字串空白，或物件每格都空白）。Whether a row is empty. */
function isEmptyRow(row: string | Record<string, string>): boolean {
  if (typeof row === 'string') return !row.trim()
  return Object.values(row).every((cell) => !String(cell ?? '').trim())
}

/**
 * 有沒有填東西（文字不是空白，或清單裡有不是空的列）。Whether a value holds any input.
 * @param value - 欄位的值。The value.
 * @returns 有沒有內容。Whether it is filled.
 */
export function hasInput(value: FormValue | undefined): boolean {
  if (value === undefined) return false
  if (typeof value === 'string') return value.trim() !== ''
  return (value as (string | Record<string, string>)[]).some((row) => !isEmptyRow(row))
}

/**
 * 送出的參數：只送表單上有的欄位（sessionStorage 可能留著已經拿掉的欄位，例如以前的「職稱」，送出會被
 * 後端拒絕），一列一筆的欄位去掉空的列。The parameters to send: the form's fields only, empty rows dropped.
 * @param fields - 表單的欄位。The form's fields.
 * @param values - 表單的值。The form values.
 * @returns 送出的參數。The parameters.
 */
export function submittedParams(fields: FormField[], values: Record<string, FormValue>): Record<string, FormValue> {
  const known = new Set(fields.map((field) => field.name))
  return Object.fromEntries(
    Object.entries(values).filter(([name]) => known.has(name)).map(([name, value]) => [
      name,
      typeof value === 'string' ? value : ((value as (string | Record<string, string>)[]).filter((row) => !isEmptyRow(row)) as RowsValue),
    ]),
  )
}

/**
 * 點快速選項：還沒有這個值時加一列（先填進空的列）；已經有了就不動。Adds a quick pick once.
 * @param rows - 目前的列（字串）。The rows.
 * @param option - 選項。The option.
 * @returns 新的列。The new rows.
 */
export function addQuickPick(rows: string[], option: string): string[] {
  if (rows.some((row) => row.trim() === option)) return rows
  const empty = rows.findIndex((row) => !row.trim())
  return empty >= 0 ? rows.map((row, i) => (i === empty ? option : row)) : [...rows, option]
}

/**
 * 加標籤：輸入的文字照頓號、逗號、換行切開（和後端切這幾個欄位的方式一樣），去掉空白與已經有的，
 * 順便清掉空的項目。Adds tags typed as text, split on 、 , ， and newlines, without duplicates.
 * @param tags - 目前的標籤。The current tags.
 * @param text - 輸入的文字。The typed text.
 * @returns 新的標籤。The new tags.
 */
export function addTags(tags: string[], text: string): string[] {
  const result = tags.map((tag) => tag.trim()).filter((tag) => tag !== '')
  for (const part of text.split(/[、,，\r\n]/)) {
    const tag = part.trim()
    if (tag && !result.includes(tag)) result.push(tag)
  }
  return result
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
export function missingRequired(fields: FormField[], values: Record<string, FormValue>): string[] {
  return fields.filter((field) => field.required && !hasInput(values[field.name])).map((field) => field.name)
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
    const msg = item.msg ?? '格式不符'
    // 一列一筆的欄位的錯誤指到第幾列（["body", "params", 欄位, 第幾列, …]）：一樣顯示在那個欄位下
    const name = loc.length >= 3 && loc[0] === 'body' && loc[1] === 'params' ? String(loc[2]) : null
    if (name !== null && known.has(name)) {
      result.fields[name] = result.fields[name] ? `${result.fields[name]}；${msg}` : msg
    } else {
      result.other.push(name !== null ? `${name}: ${msg}` : msg)
    }
  }
  return result
}
