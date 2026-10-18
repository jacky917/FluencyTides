/**
 * LLM 用途設定頁面用的純函式：來源名稱、標籤預覽、錯誤訊息整理。
 * Pure helpers for the LLM routes page: source names, label preview, and
 * error-message extraction.
 */

import type { LLMEndpointOption, LLMRouteChoice, LLMRouteSource } from '../types/api'

/** 全域設定的用途鍵。The route key of the global setting. */
export const GLOBAL_ROUTE_KEY = '*'

/** 來源的顯示名稱（與 TG `/models` 相同）。Source display names, same as TG `/models`. */
export const SOURCE_LABELS: Record<LLMRouteSource, string> = {
  route: '用途',
  global: '全域',
  env: '.env',
}

/**
 * 標籤不加 `(provider)` 前綴的端點 ID（對應後端 `labels.NO_PREFIX_PROVIDERS`）。
 * Endpoint IDs whose label stays the bare model name (backend `NO_PREFIX_PROVIDERS`).
 */
const NO_PREFIX_ENDPOINTS = new Set(['google', 'openai', ''])

/**
 * 各端點類型要從模型名去掉的前綴（對應後端客戶端的 `MODEL_LABEL_STRIP_PREFIX`）。
 * Model-name prefix stripped per endpoint type (backend `MODEL_LABEL_STRIP_PREFIX`).
 */
const STRIP_PREFIX_BY_TYPE: Record<string, string> = {
  'claude-code': 'claude-',
}

/**
 * 算出這組選擇會寫進卡片的標籤，規則與後端 `build_model_label` 相同；
 * 僅供儲存前預覽，實際標籤以 PUT 回傳的 `label` 為準。
 *
 * Computes the card label a choice would produce, mirroring the backend's
 * `build_model_label`. Preview only — the `label` returned by PUT is
 * authoritative.
 *
 * @param endpoint - 選定的端點。The chosen endpoint.
 * @param model - 模型名。Model name.
 * @param effort - effort，或 null（不使用）。The effort, or null.
 * @returns 例如 `(yinli)gemini-3.5-flash`、`(claude-code)opus-5@high`。The label.
 */
export function previewLabel(endpoint: LLMEndpointOption, model: string, effort: string | null): string {
  const provider = endpoint.id.trim().toLowerCase()
  const prefix = STRIP_PREFIX_BY_TYPE[endpoint.type] ?? ''
  const shownModel = prefix && model.startsWith(prefix) ? model.slice(prefix.length) : model
  let label = NO_PREFIX_ENDPOINTS.has(provider) ? shownModel : `(${provider})${shownModel}`
  const shownEffort = (effort ?? '').trim().toLowerCase()
  if (shownEffort) {
    label = `${label}@${shownEffort}`
  }
  return label
}

/**
 * 一組選擇的簡短寫法：`端點 / 模型 @effort`（與 TG 詳情畫面相同）。
 * Short form of a choice: `endpoint / model @effort`, as on the TG detail screen.
 * @param choice - 選擇。The choice.
 * @returns 顯示用字串。Display string.
 */
export function formatChoice(choice: LLMRouteChoice): string {
  return `${choice.endpoint} / ${choice.model}${choice.effort ? ` @${choice.effort}` : ''}`
}

/** 整理後的 API 錯誤。A normalized API error. */
export interface ApiErrorInfo {
  /** HTTP 狀態碼；網路錯誤等拿不到時為 undefined。HTTP status, if known. */
  status?: number
  /** 後端的 message 原文（或可讀的替代說明）。The backend message verbatim, or a fallback. */
  message: string
}

/** FastAPI 欄位驗證錯誤的一項。One FastAPI validation error item. */
interface ValidationItem {
  loc?: unknown[]
  msg?: string
}

/**
 * 把 API client 丟出的錯誤整理成 `{status, message}`。
 * 後端錯誤（`{error_code, message}`，攔截器附上 `status`）原樣取 message；
 * FastAPI 的欄位驗證錯誤（`{detail}`）把每一項的欄位與訊息接起來；其他情況用 axios 的訊息。
 *
 * Normalizes an error thrown by the API client: backend errors keep their
 * `message` verbatim; FastAPI validation errors (`{detail}`) are joined as
 * `field: msg`; anything else falls back to the axios message.
 *
 * @param error - 捕捉到的錯誤。The caught error.
 * @returns 狀態碼與訊息。Status and message.
 */
export function describeApiError(error: unknown): ApiErrorInfo {
  if (typeof error !== 'object' || error === null) {
    return { message: String(error) }
  }
  const err = error as {
    error_code?: string
    message?: string
    status?: number
    response?: { status?: number; data?: { detail?: unknown } }
  }
  if (err.error_code) {
    return { status: err.status, message: err.message || err.error_code }
  }
  if (!err.response) {
    // 沒有回應：請求可能根本沒送到，也可能已送到但回應途中斷線
    return { message: `連不到後端（網路或代理中斷）：${err.message || '沒有回應'}` }
  }
  const status = err.response.status
  const detail = err.response.data?.detail
  if (typeof detail === 'string' && detail) {
    return { status, message: detail }
  }
  if (Array.isArray(detail)) {
    const parts = (detail as ValidationItem[])
      .map((item) => {
        const field = Array.isArray(item.loc) ? item.loc.filter((part) => part !== 'body').join('.') : ''
        const msg = item.msg ?? ''
        return field && msg ? `${field}: ${msg}` : msg
      })
      .filter(Boolean)
    if (parts.length > 0) {
      return { status, message: parts.join('；') }
    }
  }
  // 沒有後端錯誤格式（例如代理回的 HTML 502/504）
  return { status, message: `後端或代理回應 HTTP ${status ?? '?'}：${err.message || '沒有錯誤說明'}` }
}

/**
 * 是否為後端的業務錯誤（`{error_code, message}`）；其他錯誤（網路、代理、程式例外）值得留在
 * console 供除錯。
 * Whether the error is a backend business error; anything else (network,
 * proxy, a JS exception) is worth logging to the console.
 * @param error - 捕捉到的錯誤。The caught error.
 * @returns 是否為業務錯誤。Whether it is a business error.
 */
export function isBackendError(error: unknown): boolean {
  return typeof error === 'object' && error !== null && 'error_code' in error
}

/**
 * 寫入失敗時的標題。只有 4xx（請求被拒）能確定沒有寫入；其餘——沒有回應、5xx、閘道逾時——
 * 寫入都可能已經發生，一律說結果未知。503 也涵蓋「寫入前就讀不到資料庫」的情況（兩者共用
 * 同一個 error_code），所以措辭不斷言寫入已發生。
 *
 * Title for a failed write. Only a 4xx means nothing was written; any other
 * failure may have committed, so it is reported as unknown.
 *
 * @param status - HTTP 狀態碼；沒有回應時為 undefined。HTTP status, if any.
 * @param action - 動作名稱，例如「儲存」。Action name.
 * @returns 標題。The title.
 */
export function failureTitle(status: number | undefined, action: string): string {
  const rejected = status !== undefined && status >= 400 && status < 500
  return rejected ? `無法${action}` : `${action}未完成或結果未知，請重新整理查看`
}

/**
 * 把用途鍵編碼成 URL path 片段；`encodeURIComponent` 不編碼 `*`，全域鍵另外轉成 `%2A`。
 * Encodes a route key as a URL path segment; `encodeURIComponent` leaves `*`
 * alone, so the global key is escaped to `%2A` explicitly.
 * @param routeKey - 用途鍵。Route key.
 * @returns 編碼後的片段。The encoded segment.
 */
export function encodeRouteKey(routeKey: string): string {
  return encodeURIComponent(routeKey).replace(/\*/g, '%2A')
}

/**
 * 選定模型後預設的 effort：只有一個時自動選上，否則留空（等使用者選；沒有 effort 時也是空）。
 * The effort pre-selected after picking a model: the only one, else unset.
 * @param efforts - 該模型的 effort 白名單。The model's efforts.
 * @returns effort，或空字串。The effort, or ''.
 */
export function defaultEffort(efforts: string[]): string {
  return efforts.length === 1 ? efforts[0] : ''
}

/** 編輯區的選擇（空字串＝未選）。Picker state ('' = unset). */
export interface Selection {
  endpoint: string
  model: string
  effort: string
}

/**
 * 編輯區的初始選擇：本用途已存的設定中仍在白名單內的部分，其餘留空。
 * Initial picker state: the stored choice, as far as it is still whitelisted.
 * @param stored - 已存的設定。The stored choice.
 * @param endpoints - 白名單。The whitelist.
 * @returns 初始選擇。The initial selection.
 */
export function initialSelection(stored: LLMRouteChoice | null, endpoints: LLMEndpointOption[]): Selection {
  const endpoint = stored ? endpoints.find((item) => item.id === stored.endpoint) : undefined
  if (!stored || !endpoint) {
    return { endpoint: '', model: '', effort: '' }
  }
  const model = endpoint.models.find((item) => item.name === stored.model)
  if (!model) {
    return { endpoint: endpoint.id, model: '', effort: '' }
  }
  if (stored.effort && model.efforts.includes(stored.effort)) {
    return { endpoint: endpoint.id, model: model.name, effort: stored.effort }
  }
  return { endpoint: endpoint.id, model: model.name, effort: defaultEffort(model.efforts) }
}
