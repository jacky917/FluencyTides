/**
 * 人脈卡「AI 整理」的規則（`docs/wip/person_llm_fill_FEAT_2026-11-23.md`）：哪份草稿是 AI 整理的、編輯狀態、
 * 建議的分組與標籤，以及全站一個對話框的開關。
 *
 * 對話框掛在版面層級（`PersonFillDialog`），從卡面、建卡畫面或全站通知的「前往」都打開同一個，畫面跟著目前的草稿走。
 *
 * The rules of the person card "AI 整理": which draft is one, the editing state, grouping and labels, and the
 * open state of the single site-wide dialog.
 */

import { useSyncExternalStore } from 'react'
import type { FillProposal, FillProposalKind, FillResultParams, ServerDraft } from '../types/api'

/** 開放 AI 整理的 handler。The handler offering "AI 整理". */
export const PERSON_FILL_HANDLER = 'person_profile'

/**
 * 這份草稿是不是 AI 整理的（後端在輸入裡標 `purpose: "fill"`）。
 * Whether a draft is an "AI 整理" draft.
 * @param draft - 草稿。The draft.
 * @returns 是不是。Whether it is.
 */
export function isFillDraft(draft: ServerDraft | null | undefined): boolean {
  return draft?.input?.purpose === 'fill'
}

/**
 * AI 整理草稿的生成結果；還沒生成好（或不是 AI 整理的）時為 null。
 * The generated result of an "AI 整理" draft, or null.
 * @param draft - 草稿。The draft.
 * @returns 生成結果或 null。The result or null.
 */
export function fillParams(draft: ServerDraft | null | undefined): FillResultParams | null {
  if (!draft || !isFillDraft(draft) || !draft.result) return null
  const params = draft.result.params as Partial<FillResultParams>
  return {
    mode: params.mode === 'create' ? 'create' : 'update',
    note_id: typeof params.note_id === 'number' ? params.note_id : null,
    deck: typeof params.deck === 'string' ? params.deck : null,
    proposals: Array.isArray(params.proposals) ? params.proposals : [],
    known: params.known && typeof params.known === 'object' ? params.known : null,
    labels: params.labels && typeof params.labels === 'object' ? params.labels : {},
    saved: Array.isArray(params.saved) ? params.saved : [],
    errors: params.errors && typeof params.errors === 'object' ? params.errors : {},
  }
}

/** 對話框的編輯狀態（存回草稿的 `state`）。The editing state saved into the draft. */
export interface FillEditState extends Record<string, unknown> {
  /** 編輯後的建議，順序同草稿。The edited proposals. */
  proposals: FillProposal[]
  /** 每一項有沒有勾選。Whether each is ticked. */
  selected: boolean[]
}

/**
 * 沒有存過編輯狀態時的預設：全部勾選。
 * The default editing state: everything ticked.
 * @param params - 生成結果。The result.
 * @returns 編輯狀態。The editing state.
 */
export function initialFillState(params: FillResultParams): FillEditState {
  return {
    proposals: params.proposals.map((proposal) => ({ kind: proposal.kind, data: { ...proposal.data } })),
    selected: params.proposals.map(() => true),
  }
}

/**
 * 要送出寫入的序號：勾選的，加上建新卡時一定要有的稱呼。
 * The indexes to write: the ticked ones, plus the display name of a new card.
 * @param state - 編輯狀態。The editing state.
 * @param params - 生成結果。The result.
 * @returns 序號。The indexes.
 */
export function selectedIndexes(state: FillEditState, params: FillResultParams): number[] {
  return state.proposals
    .map((proposal, index) => ({ proposal, index }))
    .filter(
      ({ proposal, index }) =>
        state.selected[index] || (proposal.kind === 'display_name' && params.mode === 'create'),
    )
    .map(({ index }) => index)
}

/**
 * 還有沒寫入的勾選項目（「寫入勾選的」按鈕可不可以按）。
 * Whether something ticked is still to be written.
 * @param state - 編輯狀態。The editing state.
 * @param params - 生成結果。The result.
 * @returns 有沒有。Whether there is.
 */
export function hasPending(state: FillEditState, params: FillResultParams): boolean {
  return selectedIndexes(state, params).some((index) => !params.saved.includes(index))
}

/**
 * 每項建議寫進哪一個欄位（分段用）：互動、待追蹤、目標、家人各是自己的清單，其他看 `data.field`。
 * The field a proposal writes to.
 * @param proposal - 建議。The proposal.
 * @returns 欄位名。The field name.
 */
export function proposalField(proposal: FillProposal): string {
  switch (proposal.kind) {
    case 'display_name':
      return 'Display_Name'
    case 'interaction':
      return 'Interactions_JSON'
    case 'followup':
      return 'Followups_JSON'
    case 'goal':
      return 'Goals_JSON'
    case 'family':
      return 'Family_JSON'
    default:
      return typeof proposal.data.field === 'string' ? proposal.data.field : ''
  }
}

/** 建議種類的順序（分段的先後，同後端 `proposals_from`）。The order of the kinds. */
const KIND_ORDER: FillProposalKind[] = [
  'display_name',
  'interaction',
  'followup',
  'goal',
  'family',
  'field',
  'history_add',
  'history_end',
]

/** 一段：一個欄位，和寫進它的 AI 建議。One section: a field and the proposals writing to it. */
export interface FillSection {
  field: string
  /** AI 建議的序號（不含修改原本內容的項目）。The indexes of the AI proposals. */
  indexes: number[]
}

/**
 * 依欄位分段：同一個欄位的建議放在一起，段的先後依建議種類的順序。修改原本內容的項目（`edit_*`）不在這裡，
 * 它們顯示在那一段的「卡片原本」裡。
 * Groups proposals by the field they write to.
 * @param proposals - 建議。The proposals.
 * @returns 各段。The sections.
 */
export function fieldSections(proposals: FillProposal[]): FillSection[] {
  const sections = new Map<string, { rank: number; indexes: number[] }>()
  proposals.forEach((proposal, index) => {
    const rank = KIND_ORDER.indexOf(proposal.kind)
    if (rank < 0) return
    const field = proposalField(proposal)
    const section = sections.get(field) ?? { rank, indexes: [] }
    section.rank = Math.min(section.rank, rank)
    section.indexes.push(index)
    sections.set(field, section)
  })
  return [...sections.entries()]
    .sort(([, a], [, b]) => a.rank - b.rank || a.indexes[0] - b.indexes[0])
    .map(([field, { indexes }]) => ({ field, indexes }))
}

/** 清單欄位的中文名稱（文字欄位與會變的資料取自建卡表單）。Titles of the list fields. */
export const LIST_TITLES: Record<string, string> = {
  Display_Name: '稱呼',
  Interactions_JSON: '互動紀錄',
  Followups_JSON: '待追蹤',
  Goals_JSON: '目標',
  Family_JSON: '家人',
}

/**
 * 後端沒給名稱時的後備：新的草稿由後端帶各欄位的名稱（`FillResultParams.labels`），這裡給沒有這份名稱的舊草稿，
 * 以及後端名稱缺了某個鍵的情況。
 * Fallback key labels when the backend gave none (old drafts, or a missing key).
 */
export const ITEM_KEY_LABELS: Record<string, string> = {
  value: '內容',
  company: '公司',
  title: '職稱',
  start: '開始',
  end: '結束',
  finish: '畢業',
  since: '開始於',
  until: '到期',
  when: '時間',
  note: '說明',
  relation: '關係',
  name: '名字',
  birth_year: '出生年',
  kind: '類型',
  item: '內容',
  target: '目標時間',
  type: '類型',
  due: '期限',
  date: '日期',
  topics: '聊了什麼',
  updates: '對方的近況',
  place: '地點',
  quote: '記住的一句話',
}

/**
 * 找修改原本內容的那一項（文字欄位一個欄位一項、清單一筆一項）；沒有時為 -1。
 * Finds the edit of an original field or entry.
 * @param state - 編輯狀態。The editing state.
 * @param field - 欄位。The field.
 * @param itemIndex - 清單裡的序號；文字欄位省略。The entry index, or none for a text field.
 * @returns 序號或 -1。The index, or -1.
 */
export function findEdit(state: FillEditState, field: string, itemIndex?: number): number {
  return state.proposals.findIndex((proposal) =>
    itemIndex === undefined
      ? proposal.kind === 'edit_field' && proposal.data.field === field
      : proposal.kind === 'edit_item' && proposal.data.field === field && proposal.data.index === itemIndex,
  )
}

/**
 * 加上或更新一項修改（勾選，寫入時一起送出）。已有同一個欄位或同一筆的修改時換掉內容，不另外加一項：
 * 序號不會變，已寫入、錯誤的標記才對得上。
 * Adds or updates an edit of the original content, ticked.
 * @param state - 編輯狀態。The editing state.
 * @param edit - 修改。The edit.
 * @returns 新的編輯狀態。The new state.
 */
export function upsertEdit(state: FillEditState, edit: FillProposal): FillEditState {
  const field = String(edit.data.field)
  const existing = findEdit(state, field, edit.kind === 'edit_item' ? Number(edit.data.index) : undefined)
  if (existing < 0) return { proposals: [...state.proposals, edit], selected: [...state.selected, true] }
  return {
    proposals: state.proposals.map((proposal, i) => (i === existing ? edit : proposal)),
    selected: state.selected.map((value, i) => (i === existing ? true : value)),
  }
}

/**
 * 還原一項修改：取消勾選（不刪掉，序號才不會變）。
 * Reverts an edit by unticking it.
 * @param state - 編輯狀態。The editing state.
 * @param index - 那一項的序號。The edit's index.
 * @returns 新的編輯狀態。The new state.
 */
export function revertEdit(state: FillEditState, index: number): FillEditState {
  return { ...state, selected: state.selected.map((value, i) => (i === index ? false : value)) }
}

/** 待追蹤的類型。The follow-up types. */
export const FOLLOWUP_TYPES: { value: string; label: string }[] = [
  { value: 'ask', label: '下次要問他' },
  { value: 'my_promise', label: '我答應的' },
  { value: 'their_promise', label: '他答應的' },
]

/**
 * 鍵的名稱：後端帶來的（那個欄位的）名稱優先，沒有時用後備。
 * The name of a key: the backend's name for that field first, then the fallback.
 * @param labels - 那個欄位各鍵的名稱（可能沒有）。The field's labels, if any.
 * @param key - 鍵。The key.
 * @returns 名稱。The name.
 */
export function keyLabel(labels: Record<string, string> | undefined, key: string): string {
  return labels?.[key] ?? ITEM_KEY_LABELS[key] ?? key
}

/** 舊草稿（沒有後端的名稱）時，會變的資料一筆裡可以改的鍵的順序。Key order for old drafts. */
const LEGACY_HISTORY_KEYS = ['value', 'company', 'title', 'start', 'end', 'finish', 'since', 'when', 'note']

/**
 * 欄位的 HTML 轉成顯示用的純文字（換行保留）。A field's HTML as plain text.
 * @param html - 欄位內容。The field HTML.
 * @returns 純文字。The text.
 */
export function plainField(html: string): string {
  return html
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/(div|p|li)>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;/g, ' ')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, '&')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

/**
 * 一筆會變的資料裡要顯示的輸入框：有後端的名稱時是那個欄位所有能改的鍵（沒填的也列出來，才補得上，例如到期日）；
 * 舊草稿只列這一筆已經有的鍵。
 * The keys to show for an entry: every editable key of the field when the backend named them, else the keys it has.
 * @param item - 那一筆。The entry.
 * @param labels - 那個欄位各鍵的名稱（可能沒有）。The field's labels, if any.
 * @returns 鍵。The keys.
 */
export function historyItemKeys(item: Record<string, unknown>, labels?: Record<string, string>): string[] {
  if (labels && Object.keys(labels).length > 0) return Object.keys(labels)
  return LEGACY_HISTORY_KEYS.filter((key) => key in item)
}

// =============================================================================
// 全站一個對話框的開關
// =============================================================================

/**
 * 打開對話框時的目標：要補的卡或建新卡的牌組，加上顯示用的名稱。草稿已經存在時，畫面跟著草稿走、不看這裡。
 * Where the dialog is opened for; an existing draft takes precedence.
 */
export interface FillTarget {
  /** 要補的卡；建新卡時省略。The card to add to. */
  noteId?: number
  /** 建新卡的牌組。The deck of a new card. */
  deck?: string | null
  /** 顯示用的名稱（例如稱呼）。A label to show. */
  label?: string
}

interface FillDialogState {
  open: boolean
  target: FillTarget | null
}

let dialogState: FillDialogState = { open: false, target: null }
const listeners = new Set<() => void>()

function setDialogState(next: FillDialogState) {
  dialogState = next
  listeners.forEach((listener) => listener())
}

/**
 * 打開 AI 整理的對話框。Opens the "AI 整理" dialog.
 * @param target - 目標；省略時只打開（接著目前的草稿）。The target, or none to resume the current draft.
 */
export function openPersonFill(target: FillTarget | null = null) {
  setDialogState({ open: true, target: target ?? dialogState.target })
}

/** 關閉 AI 整理的對話框（草稿留著）。Closes the dialog; the draft stays. */
export function closePersonFill() {
  setDialogState({ ...dialogState, open: false })
}

/**
 * 對話框目前的開關與目標（React 之外讀）。The dialog state, outside React.
 * @returns 目前的狀態。The state.
 */
export function personFillSnapshot(): FillDialogState {
  return dialogState
}

/**
 * 對話框的開關與目標。The dialog's open state and target.
 * @returns 目前的狀態。The state.
 */
export function usePersonFillDialog(): FillDialogState {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    personFillSnapshot,
  )
}
