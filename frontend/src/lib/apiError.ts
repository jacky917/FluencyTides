/**
 * API 錯誤的整理：把 API client 丟出的各種錯誤變成可以直接顯示的訊息。
 * Normalizes errors thrown by the API client into displayable messages.
 */

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
 * 後端錯誤（`{error_code, message}`，攔截器附上 `status`；Blob 回應也已由攔截器先解析）原樣取 message；
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
 * 寫入都可能已經發生，一律說結果未知（後端的 503 有「寫入前就讀不到資料庫」與「寫入時異常」
 * 兩種，共用同一個 error_code，所以措辭不斷言寫入已發生）。
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
 * 查詢的重試條件：只重試一次，而且只在可能是暫時性的錯誤（沒有回應或 5xx）時；4xx 是確定的
 * 結果（找不到、搜尋語法錯誤），重試只會延後顯示錯誤。
 *
 * Query retry rule: one retry, only for possibly transient failures.
 *
 * @param failureCount - 已失敗的次數。Failures so far.
 * @param error - 這次的錯誤。The error.
 * @returns 是否重試。Whether to retry.
 */
export function retryTransient(failureCount: number, error: unknown): boolean {
  const { status } = describeApiError(error)
  return failureCount < 1 && (status === undefined || status >= 500)
}

/**
 * 把錯誤回應的 body 讀成物件：以 `responseType: 'blob'` 下載時，錯誤回應的 body 也是 Blob，
 * 要先讀出 JSON 才拿得到後端的 `{error_code, message}`。不是 JSON 的 Blob（例如代理回的
 * HTML）原樣傳回。
 *
 * Reads an error body; with `responseType: 'blob'` the backend's JSON error
 * arrives as a Blob and must be parsed first.
 *
 * @param data - axios 錯誤回應的 `data`。The error response body.
 * @returns 解析後的物件，或原本的值。The parsed body, or the input.
 */
export async function readErrorBody(data: unknown): Promise<unknown> {
  if (typeof Blob === 'undefined' || !(data instanceof Blob) || !data.type.includes('json')) {
    return data
  }
  try {
    return JSON.parse(await data.text())
  } catch {
    // 宣稱是 JSON 卻解析不了：保留原 Blob，呼叫端會退回 HTTP 狀態碼的說明
    return data
  }
}
