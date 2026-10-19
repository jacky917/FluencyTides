/**
 * 工具設定頁面用的純函式（來源與能力的名稱、標籤預覽、編輯區的選擇等）。
 * Pure helpers for the tool settings (capability routes) page.
 */

import type { CapabilityRoute, ProviderOption, RouteChoice, RouteSource, RouteUpdate } from '../types/api'

/** 全域設定的用途鍵。The route key of the global setting. */
export const GLOBAL_ROUTE_KEY = '*'

/** 文字生成能力。The text-generation capability. */
export const CAPABILITY_LLM = 'llm'
/** 錄音評分策略。The recording-evaluation strategy. */
export const CAPABILITY_AUDIO_EVAL = 'audio_eval'
/** 聽音檔的模型。The audio-listening model. */
export const CAPABILITY_AUDIO_LLM = 'audio_llm'
/** 語音轉文字。Speech-to-text. */
export const CAPABILITY_STT = 'stt'

/** 來源的顯示名稱（與 TG `/setconfig` 相同）。Source display names, same as TG `/setconfig`. */
export const SOURCE_LABELS: Record<RouteSource, string> = {
  route: '用途',
  parent: '流程',
  global: '全域',
  env: '.env',
}

/** 能力的顯示名稱（與 TG `/setconfig` 相同）；不在表中的能力直接顯示代號。Capability display names. */
const CAPABILITY_LABELS: Record<string, string> = {
  [CAPABILITY_LLM]: '文字 LLM',
  [CAPABILITY_AUDIO_EVAL]: '評分策略',
  [CAPABILITY_AUDIO_LLM]: '聽音檔的模型',
  [CAPABILITY_STT]: '語音轉文字',
}

/** 編輯區第一層的名稱：端點、服務或策略。The first picker's label per capability. */
const PROVIDER_LABELS: Record<string, string> = {
  [CAPABILITY_LLM]: '端點',
  [CAPABILITY_AUDIO_LLM]: '端點',
  [CAPABILITY_STT]: '服務',
  [CAPABILITY_AUDIO_EVAL]: '策略',
}

/** 某能力沒有可選項目時，說明白名單在哪裡設定。Where to configure an empty whitelist. */
const EMPTY_PROVIDER_HINTS: Record<string, string> = {
  [CAPABILITY_LLM]: '.env 的白名單都是空的（LLM_ENDPOINTS__<ID>__MODELS、LLM_CLAUDE_CODE_MODELS…）。',
  [CAPABILITY_AUDIO_LLM]: '沒有能聽音檔的端點（AUDIO_API_KEY 未設定，或 LLM_ENDPOINTS__<ID>__AUDIO_MODELS 都是空的）。',
  [CAPABILITY_STT]: 'STT_SERVER_URL 未設定，或 STT_MODEL_NAME / STT_MODELS 都是空的。',
  [CAPABILITY_AUDIO_EVAL]: '這個用途沒有允許任何評分策略。',
}

/**
 * 編輯區第一層的名稱。The first picker's label.
 * @param capability - 能力。Capability.
 * @returns 名稱。The label.
 */
export function providerLabel(capability: string): string {
  return PROVIDER_LABELS[capability] ?? 'provider'
}

/**
 * 沒有可選項目時的說明。Hint shown when nothing is selectable.
 * @param capability - 能力。Capability.
 * @returns 說明。The hint.
 */
export function emptyProviderHint(capability: string): string {
  return EMPTY_PROVIDER_HINTS[capability] ?? '白名單是空的。'
}

/**
 * 分支鍵的流程鍵（`a.b` → `a`）；不是分支時為 null。The flow key of a branch.
 * @param routeKey - 用途鍵。Route key.
 * @returns 流程鍵，或 null。The flow key, or null.
 */
export function parentKey(routeKey: string): string | null {
  const index = routeKey.indexOf('.')
  return index > 0 ? routeKey.slice(0, index) : null
}

/**
 * 這個用途鍵在這個能力上可選的 provider：評分策略依鍵的限縮過濾（分支沒有限縮時沿用流程鍵的）。
 * The providers selectable for a key and capability; strategies follow the key's restriction.
 * @param capability - 能力。Capability.
 * @param routeKey - 用途鍵。Route key.
 * @param providers - 能力 → 全部 provider。All providers per capability.
 * @param strategies - 用途鍵 → 允許的策略。Allowed strategies per key.
 * @returns 可選的 provider。The selectable providers.
 */
export function providersFor(
  capability: string,
  routeKey: string,
  providers: Record<string, ProviderOption[]>,
  strategies: Record<string, string[]> = {},
): ProviderOption[] {
  const options = providers[capability] ?? []
  if (capability !== CAPABILITY_AUDIO_EVAL) return options
  const parent = parentKey(routeKey)
  const allowed = strategies[routeKey] ?? (parent ? strategies[parent] : undefined)
  return allowed ? options.filter((option) => allowed.includes(option.id)) : options
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
 * 算出這組選擇的顯示標籤，規則與後端 `preview_label` 相同（`llm` 即寫進卡片的標籤）；
 * 僅供儲存前預覽，實際標籤以 PUT 回傳的 `label` 為準。策略（不選模型）的標籤就是策略名。
 *
 * Computes the label a choice would produce, mirroring the backend's
 * `preview_label`. Preview only — the `label` returned by PUT is
 * authoritative. A strategy's label is its name.
 *
 * @param provider - 選定的 provider。The chosen provider.
 * @param model - 模型名（策略為 null）。Model name, null for strategies.
 * @param effort - effort，或 null（不使用）。The effort, or null.
 * @returns 例如 `(yinli)gemini-3.5-flash`、`(claude-code)opus-5@high`、`listen`。The label.
 */
export function previewLabel(provider: ProviderOption, model: string | null, effort: string | null): string {
  const providerId = provider.id.trim().toLowerCase()
  if (model === null) return providerId
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
  if (!stored || !provider || provider.models.length === 0) {
    return { provider: provider?.id ?? '', model: '', effort: '' }
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

/**
 * 由編輯區的選擇組出要寫入的值；還沒選完（或選的已不在白名單）時為 null。不選模型的 provider（策略）
 * 選了就完成，模型與 effort 都送 null。
 * Builds the value to store from the picker, or null while incomplete.
 * Providers without models (strategies) are complete once picked.
 * @param providers - 可選的 provider。The selectable providers.
 * @param selection - 編輯區的選擇。The picker state.
 * @returns 要寫入的值，或 null。The value to store, or null.
 */
export function buildChoice(providers: ProviderOption[], selection: Selection): RouteUpdate | null {
  const provider = providers.find((item) => item.id === selection.provider)
  if (!provider) return null
  if (provider.models.length === 0) return { provider: provider.id, model: null, effort: null }
  const model = provider.models.find((item) => item.name === selection.model)
  if (!model) return null
  if (model.efforts.length === 0) return { provider: provider.id, model: model.name, effort: null }
  return model.efforts.includes(selection.effort)
    ? { provider: provider.id, model: model.name, effort: selection.effort }
    : null
}

/** 各評分策略用到的其他能力（與後端 `STRATEGY_CAPABILITIES` 相同）。Capabilities each strategy uses. */
const STRATEGY_CAPABILITIES: Record<string, string[]> = {
  listen: [CAPABILITY_AUDIO_LLM],
  stt_diff: [CAPABILITY_STT],
  stt_llm: [CAPABILITY_STT, CAPABILITY_LLM],
}

/**
 * 目前會用到的能力（與 TG 清單的 ⚠️ 規則相同）：有評分策略的鍵只算策略本身與策略用到的能力（全域 `*`
 * 另算文字 LLM，它也是建卡的預設），其他鍵算全部；孤兒設定一律算。
 * The capabilities currently in use, as the TG list counts them.
 * @param route - 用途鍵的狀態。The route.
 * @returns 會用到的能力。The capabilities in use.
 */
export function capabilitiesInUse(route: CapabilityRoute): Set<string> {
  const all = Object.keys(route.capabilities)
  const strategy = route.capabilities[CAPABILITY_AUDIO_EVAL]
  if (!strategy) return new Set(all)
  const used = new Set([CAPABILITY_AUDIO_EVAL, ...(STRATEGY_CAPABILITIES[strategy.effective.provider] ?? [])])
  if (route.route_key === GLOBAL_ROUTE_KEY) used.add(CAPABILITY_LLM)
  return new Set(all.filter((capability) => used.has(capability) || route.capabilities[capability].declared === false))
}
