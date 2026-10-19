/**
 * 工具設定頁面用的純函式（來源與能力的名稱、標籤預覽、編輯區的選擇等）。
 * Pure helpers for the tool settings (capability routes) page.
 */

import type { ProviderOption, RouteChoice, RouteSource } from '../types/api'

/** 全域設定的用途鍵。The route key of the global setting. */
export const GLOBAL_ROUTE_KEY = '*'

/** 文字生成能力。The text-generation capability. */
export const CAPABILITY_LLM = 'llm'

/** 來源的顯示名稱（與 TG `/models` 相同）。Source display names, same as TG `/models`. */
export const SOURCE_LABELS: Record<RouteSource, string> = {
  route: '用途',
  global: '全域',
  env: '.env',
}

/** 能力的顯示名稱；不在表中的能力直接顯示代號。Capability display names. */
const CAPABILITY_LABELS: Record<string, string> = {
  [CAPABILITY_LLM]: '文字生成（LLM）',
}

/**
 * 能力的顯示名稱。The display name of a capability.
 * @param capability - 能力代號，例如 `llm`。Capability, e.g. `llm`.
 * @returns 顯示名稱；沒有對應時為代號本身。The display name, or the code itself.
 */
export function capabilityLabel(capability: string): string {
  return CAPABILITY_LABELS[capability] ?? capability
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
 * 算出 `llm` 能力的這組選擇會寫進卡片的標籤，規則與後端 `build_model_label` 相同；
 * 僅供儲存前預覽，實際標籤以 PUT 回傳的 `label` 為準。
 *
 * Computes the card label an `llm` choice would produce, mirroring the
 * backend's `build_model_label`. Preview only — the `label` returned by PUT
 * is authoritative.
 *
 * @param provider - 選定的端點。The chosen endpoint.
 * @param model - 模型名。Model name.
 * @param effort - effort，或 null（不使用）。The effort, or null.
 * @returns 例如 `(yinli)gemini-3.5-flash`、`(claude-code)opus-5@high`。The label.
 */
export function previewLabel(provider: ProviderOption, model: string, effort: string | null): string {
  const providerId = provider.id.trim().toLowerCase()
  const prefix = STRIP_PREFIX_BY_TYPE[provider.type] ?? ''
  const shownModel = prefix && model.startsWith(prefix) ? model.slice(prefix.length) : model
  let label = NO_PREFIX_ENDPOINTS.has(providerId) ? shownModel : `(${providerId})${shownModel}`
  const shownEffort = (effort ?? '').trim().toLowerCase()
  if (shownEffort) {
    label = `${label}@${shownEffort}`
  }
  return label
}

/**
 * 一組選擇的簡短寫法：`provider / 模型 @effort`（與 TG 詳情畫面相同）；沒有模型時只有 provider。
 * Short form of a choice, as on the TG detail screen.
 * @param choice - 選擇。The choice.
 * @returns 顯示用字串。Display string.
 */
export function formatChoice(choice: RouteChoice): string {
  const model = choice.model ? ` / ${choice.model}` : ''
  return `${choice.provider}${model}${choice.effort ? ` @${choice.effort}` : ''}`
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
  provider: string
  model: string
  effort: string
}

/**
 * 編輯區的初始選擇：本用途已存的設定中仍在白名單內的部分，其餘留空。
 * Initial picker state: the stored choice, as far as it is still whitelisted.
 * @param stored - 已存的設定。The stored choice.
 * @param providers - 白名單。The whitelist.
 * @returns 初始選擇。The initial selection.
 */
export function initialSelection(stored: RouteChoice | null, providers: ProviderOption[]): Selection {
  const provider = stored ? providers.find((item) => item.id === stored.provider) : undefined
  if (!stored || !provider) {
    return { provider: '', model: '', effort: '' }
  }
  const model = provider.models.find((item) => item.name === stored.model)
  if (!model) {
    return { provider: provider.id, model: '', effort: '' }
  }
  if (stored.effort && model.efforts.includes(stored.effort)) {
    return { provider: provider.id, model: model.name, effort: stored.effort }
  }
  return { provider: provider.id, model: model.name, effort: defaultEffort(model.efforts) }
}
