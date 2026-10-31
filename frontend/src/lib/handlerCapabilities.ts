/**
 * Handler 能力的顯示規則（handlers 計畫書 D2、§6）：建卡管道怎麼呈現、各項能力的文字。
 * 前端不自行判斷能不能做，只把 `GET /handlers` 的宣告轉成畫面要的形狀。
 *
 * Display rules for handler capabilities; the frontend only presents what
 * `GET /handlers` declares.
 */

import type { CreateCapability, CreateChannel, HandlerInfo, Operation } from '../types/api'

/** 建卡管道的中文名稱。Display names of the channels. */
export const CHANNEL_LABELS: Record<CreateChannel, string> = {
  web: '網頁',
  tg: 'TG',
  script: '本機腳本',
}

/** Generate 頁上一個 handler 的呈現。How a handler appears on the Generate page. */
export type CreateAvailability =
  | { kind: 'selectable' }
  | { kind: 'disabled'; reason: string }
  | { kind: 'hidden' }

/**
 * 依 `create.channels` 決定 Generate 頁的呈現（§6）：含 web 可選；非空但不含 web 反灰並顯示原因；
 * 空陣列不顯示。
 * How the Generate page shows a handler, from `create.channels`.
 * @param create - 建卡能力。The create capability.
 * @returns 可選、反灰（附原因）或不顯示。The availability.
 */
export function createAvailability(create: CreateCapability): CreateAvailability {
  if (create.channels.includes('web')) return { kind: 'selectable' }
  if (create.channels.length > 0) return { kind: 'disabled', reason: create.reason ?? '網頁不能建立' }
  return { kind: 'hidden' }
}

/**
 * 建卡流程的說明。The create flow, in words.
 * @param create - 建卡能力。The create capability.
 * @returns 說明；不開放時為 null。The description, or null.
 */
export function flowLabel(create: CreateCapability): string | null {
  if (create.flow === 'form') return '填表後建立'
  if (create.flow === 'preview') return '先預覽 LLM 結果再勾選'
  return null
}

/**
 * 牌組規則的說明。The deck rule, in words.
 * @param create - 建卡能力。The create capability.
 * @returns 說明；不開放時為 null。The description, or null.
 */
export function deckRuleLabel(create: CreateCapability): string | null {
  if (create.deck === 'selected') return '建在選取的牌組'
  if (create.deck === 'fixed') return `固定建在 ${create.fixed_deck ?? '（未指定）'}`
  return null
}

/** 一項能力在設定頁的一列。One capability row on the settings page. */
export interface CapabilityRow {
  label: string
  supported: boolean
  /** 開放時的補充（例如管道、流程），或不開放的原因。Details, or the reason. */
  detail: string | null
}

/**
 * 設定頁「Handler 能力」區塊的各列：建立、讀取、改欄位、刪除、圖譜、動作。
 * The rows of the handler capability section.
 * @param handler - handler 資訊。The handler.
 * @returns 各列。The rows.
 */
export function capabilityRows(handler: HandlerInfo): CapabilityRow[] {
  const { create, read, update_fields: update, delete: remove, graph, actions } = handler.capabilities
  const operation = (label: string, op: Operation, detail: string | null = null): CapabilityRow => ({
    label,
    supported: op.supported,
    detail: op.supported ? detail : op.reason,
  })
  const channels = create.channels.length > 0 ? create.channels.map((c) => CHANNEL_LABELS[c]).join('、') : '無'
  const createDetails = [
    `管道：${channels}`,
    flowLabel(create),
    deckRuleLabel(create),
    create.note_type && `建立 ${create.note_type}`,
  ].filter((part): part is string => Boolean(part))
  // 不開放但有管道（例如只能在 TG 或本機腳本建）：原因與管道都要看得到
  const createRow: CapabilityRow = create.supported
    ? { label: '建立', supported: true, detail: [...createDetails, create.reason].filter(Boolean).join('；') }
    : { label: '建立', supported: false, detail: [create.reason, `管道：${channels}`].filter(Boolean).join('；') }
  return [
    createRow,
    operation('讀取', read),
    operation('改欄位', update),
    operation('刪除', remove),
    operation('圖譜', graph, graph.links ? '含關聯連線' : '單點'),
    { label: '動作', supported: actions.length > 0, detail: actions.length > 0 ? actions.join('、') : '無' },
  ]
}
