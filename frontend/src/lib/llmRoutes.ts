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
  const status = err.response?.status
  const detail = err.response?.data?.detail
  if (typeof detail === 'string') {
    return { status, message: detail }
  }
  if (Array.isArray(detail)) {
    const parts = (detail as ValidationItem[]).map((item) => {
      const field = Array.isArray(item.loc) ? item.loc.filter((part) => part !== 'body').join('.') : ''
      return field ? `${field}: ${item.msg ?? ''}` : (item.msg ?? '')
    })
    return { status, message: parts.join('；') }
  }
  return { status, message: err.message || '發生未知錯誤。' }
}
