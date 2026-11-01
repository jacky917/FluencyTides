/**
 * JSON 欄位清單編輯用的純函式：把後端給的每一筆 JSON Schema 轉成表單欄位、產生新的一筆、
 * 把表單值轉回要送出的值、把 422 的錯誤對到第幾筆的哪個欄位（json_field_editor 計畫 D5）。
 * Pure helpers for editing a JSON field as a list of forms.
 */

/** 表單欄位的種類。The kind of a form control. */
export type ItemFieldKind = 'text' | 'textarea' | 'number' | 'boolean' | 'select'

/** 一筆資料的一個欄位。One field of an item. */
export interface ItemField {
  key: string
  title: string
  kind: ItemFieldKind
  required: boolean
  description?: string
  /** 選單的選項（值與顯示文字）。Select options. */
  options?: { value: string; label: string }[]
  /** 可以是空值（null）：空白時送出 null。Whether empty means null. */
  nullable: boolean
}

interface SchemaProperty {
  type?: string
  title?: string
  description?: string
  default?: unknown
  enum?: unknown[]
  anyOf?: SchemaProperty[]
  'x-ui'?: string
}

interface ItemSchema {
  properties?: Record<string, SchemaProperty>
  required?: string[]
}

/**
 * 選項的顯示文字：說明寫成「值：文字；值：文字」時照它，否則顯示值本身。
 * Option labels, taken from a "value：label；…" description when present.
 */
function optionLabels(values: unknown[], description: string | undefined): { value: string; label: string }[] {
  const labels = new Map<string, string>()
  for (const part of (description ?? '').split(/[；;]/)) {
    const m = /^\s*([^：:]+)[：:]\s*(.+?)\s*$/.exec(part)
    if (m) labels.set(m[1].trim(), m[2])
  }
  return values.map((value) => ({ value: String(value), label: labels.get(String(value)) ?? String(value) }))
}

/**
 * 每一筆的表單欄位，順序同 schema 的 properties。
 * The form fields of an item, in schema order.
 * @param schema - 後端給的每一筆 JSON Schema。The item schema.
 * @returns 表單欄位。The fields.
 */
export function itemFields(schema: Record<string, unknown>): ItemField[] {
  const { properties = {}, required = [] } = schema as ItemSchema
  return Object.entries(properties).map(([key, raw]) => {
    // Pydantic 的 `int | None` 是 anyOf [integer, null]
    const variants = raw.anyOf ?? [raw]
    const nullable = variants.some((v) => v.type === 'null')
    const main = variants.find((v) => v.type !== 'null') ?? raw
    const field: ItemField = {
      key,
      title: raw.title ?? main.title ?? key,
      kind: 'text',
      required: required.includes(key),
      description: raw.description ?? main.description,
      nullable,
    }
    if (main.enum) {
      field.kind = 'select'
      field.options = optionLabels(main.enum, field.description)
    } else if (main.type === 'boolean') {
      field.kind = 'boolean'
    } else if (main.type === 'integer' || main.type === 'number') {
      field.kind = 'number'
    } else if ((raw['x-ui'] ?? main['x-ui']) === 'textarea') {
      field.kind = 'textarea'
    }
    return field
  })
}

/**
 * 新增的一筆：有預設值的用預設值，其餘字串為空、選單取第一個、勾選框為否。
 * A new item with the schema defaults.
 * @param schema - 每一筆的 JSON Schema。The item schema.
 * @returns 新的一筆。The new item.
 */
export function newItem(schema: Record<string, unknown>): Record<string, unknown> {
  const { properties = {} } = schema as ItemSchema
  const item: Record<string, unknown> = {}
  for (const field of itemFields(schema)) {
    const fallback = properties[field.key]?.default
    if (fallback !== undefined) item[field.key] = fallback
    else if (field.kind === 'select') item[field.key] = field.options?.[0]?.value ?? ''
    else if (field.kind === 'boolean') item[field.key] = false
    else if (field.kind === 'number' || field.nullable) item[field.key] = null
    else item[field.key] = ''
  }
  return item
}

/**
 * 表單輸入轉成要存的值：數字欄位空白時為 null，其餘照原樣。
 * The stored value of a form input.
 * @param field - 欄位。The field.
 * @param input - 輸入框的字串或勾選框的值。The input.
 * @returns 要存的值。The value to store.
 */
export function inputValue(field: ItemField, input: string | boolean): unknown {
  if (field.kind === 'boolean') return Boolean(input)
  if (field.kind === 'number') {
    const text = String(input).trim()
    if (text === '') return null
    const number = Number(text)
    return Number.isFinite(number) ? number : text
  }
  return input
}

/** 422 的一項錯誤。One validation error. */
interface ValidationItem {
  loc?: unknown[]
  msg?: string
}

/**
 * 把 422 的錯誤對到第幾筆的哪個欄位（`loc` 為 `["body", "items", 第幾筆, 鍵名]`）。
 * Maps 422 errors to items and fields.
 * @param detail - 後端回應的 `detail`。The response detail.
 * @returns 第幾筆 → 欄位 → 訊息；對不到欄位的放在 `_` 下。Index → field → message.
 */
export function itemErrors(detail: unknown): Map<number, Map<string, string>> {
  const errors = new Map<number, Map<string, string>>()
  if (!Array.isArray(detail)) return errors
  for (const entry of detail as ValidationItem[]) {
    const loc = Array.isArray(entry.loc) ? entry.loc : []
    const at = loc.indexOf('items')
    const index = at >= 0 ? loc[at + 1] : undefined
    if (typeof index !== 'number') continue
    const key = typeof loc[at + 2] === 'string' ? (loc[at + 2] as string) : '_'
    const byField = errors.get(index) ?? new Map<string, string>()
    if (!byField.has(key)) byField.set(key, entry.msg ?? '格式不對')
    errors.set(index, byField)
  }
  return errors
}

/**
 * 一筆的摘要文字（卡片標題用）：第一個有值的文字欄位。
 * A short label for an item: its first non-empty text field.
 * @param item - 一筆資料。The item.
 * @param fields - 表單欄位。The fields.
 * @returns 摘要；全部空白時為空字串。The label.
 */
export function itemSummary(item: Record<string, unknown>, fields: ItemField[]): string {
  for (const field of fields) {
    if (field.kind !== 'text' && field.kind !== 'textarea') continue
    const value = item[field.key]
    if (typeof value === 'string' && value.trim()) return value.trim()
  }
  return ''
}
