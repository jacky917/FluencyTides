/**
 * 外語糾錯網頁建卡的預覽（`docs/wip/server_drafts_FEAT_2026-11-04.md` D10.2）：草稿的形狀、子卡的編號與勾選、
 * 錯誤標示的切分。畫面在 `components/generate/ExpressionDraftPreview.tsx`。
 *
 * The expression correction web-create preview: draft shape, micro card
 * numbering and selection, and the error marks.
 */

import type { ExpressionCorrectionResult, ExpressionCreateParams, ExpressionMicroPoint } from '../types/api'

/** 一張子卡：編號（1 起算，與後端相同：文法修正在前、重新組織在後）。One micro card. */
export interface MicroEntry {
  /** 建卡時 `selected_indices` 用的編號。The number used by `selected_indices`. */
  number: number
  kind: 'grammar' | 'reorganized'
  /** 子卡建在哪一個子牌組。The sub-deck label. */
  label: '文法修正' | '重新組織'
  point: ExpressionMicroPoint
}

/** 文字；不是字串時為空字串。A string, or empty. */
function text(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

/** 一個知識點；格式不對時為 null。One micro point, or null when malformed. */
function readPoint(value: unknown): ExpressionMicroPoint | null {
  if (typeof value !== 'object' || value === null) return null
  const item = value as Record<string, unknown>
  if (!text(item.target_phrase).trim()) return null
  return {
    original_phrase: text(item.original_phrase),
    target_phrase: text(item.target_phrase),
    native_translation: text(item.native_translation),
    context_hint: text(item.context_hint),
    context_sentence: text(item.context_sentence),
    error_hint: text(item.error_hint),
  }
}

/**
 * 把草稿的 `params` 讀成外語糾錯的建立參數；沒有 LLM 結果時為 null。格式不對（含 `target_phrase` 空白）的
 * 知識點換成空的知識點、留在原位：子卡編號要與後端一致。空的不顯示、不能勾選（後端已驗證過形狀，正常不會發生）。
 * Reads the draft params as expression create parameters, or null.
 * @param params - 草稿（後端已驗證過形狀，這裡只防呆）。The draft params.
 * @returns 建立參數，或 null。The parameters, or null.
 */
export function readExpressionDraft(params: Record<string, unknown>): ExpressionCreateParams | null {
  const raw = params.llm_result
  if (typeof raw !== 'object' || raw === null) return null
  const result = raw as Record<string, unknown>
  const points = (value: unknown) =>
    (Array.isArray(value) ? value : []).map(
      (item) =>
        readPoint(item) ?? {
          original_phrase: '',
          target_phrase: '',
          native_translation: '',
          context_hint: '',
          context_sentence: '',
          error_hint: '',
        },
    )
  const llm: ExpressionCorrectionResult = {
    error_comparison: text(result.error_comparison),
    grammar_correction: text(result.grammar_correction),
    reorganized_expression: text(result.reorganized_expression),
    grammar_micro_points: points(result.grammar_micro_points),
    reorganized_micro_points: points(result.reorganized_micro_points),
  }
  return {
    original_text: text(params.original_text),
    context: text(params.context),
    source_tag: text(params.source_tag),
    native_language: text(params.native_language),
    target_language: text(params.target_language),
    user_grammar_correction: text(params.user_grammar_correction),
    user_reorganization: text(params.user_reorganization),
    llm_result: llm,
    selected_indices: Array.isArray(params.selected_indices)
      ? params.selected_indices.filter((n): n is number => Number.isInteger(n))
      : null,
    allow_duplicate: params.allow_duplicate === true,
    replace_note_id: typeof params.replace_note_id === 'number' ? params.replace_note_id : null,
  }
}

/**
 * 子卡清單（含空的知識點：編號照算，畫面略過）。The micro cards, numbered like the backend.
 * @param result - LLM 結果。The LLM result.
 * @returns 子卡。The entries.
 */
export function microEntries(result: ExpressionCorrectionResult): MicroEntry[] {
  const grammar = result.grammar_micro_points.map((point) => ({ kind: 'grammar' as const, label: '文法修正' as const, point }))
  const reorganized = result.reorganized_micro_points.map((point) => ({
    kind: 'reorganized' as const,
    label: '重新組織' as const,
    point,
  }))
  return [...grammar, ...reorganized].map((entry, index) => ({ ...entry, number: index + 1 }))
}

/**
 * 可以勾選的子卡（略過空的知識點）。The micro cards that can be selected.
 * @param result - LLM 結果。The LLM result.
 * @returns 子卡。The entries.
 */
export function selectableEntries(result: ExpressionCorrectionResult): MicroEntry[] {
  return microEntries(result).filter((entry) => entry.point.target_phrase.trim() !== '')
}

/**
 * 預設的勾選：草稿有指定就用它，否則全選。The default selection.
 * @param draft - 建立參數。The draft.
 * @returns 勾選的編號（遞增）。Selected numbers, ascending.
 */
export function initialSelection(draft: ExpressionCreateParams): number[] {
  const all = selectableEntries(draft.llm_result).map((entry) => entry.number)
  if (draft.selected_indices === null) return all
  const chosen = new Set(draft.selected_indices)
  return all.filter((n) => chosen.has(n))
}

/**
 * 建卡送出的參數：草稿加上這裡的勾選（一律明確列出，空陣列表示只建母卡）。
 * The create parameters with the selection.
 * @param draft - 建立參數。The draft.
 * @param selected - 勾選的編號。The selected numbers.
 * @returns 參數。The parameters.
 */
export function expressionParams(draft: ExpressionCreateParams, selected: number[]): ExpressionCreateParams {
  return { ...draft, selected_indices: [...selected].sort((a, b) => a - b) }
}

/** 錯誤標示切出的一段。A segment of the error comparison. */
export interface MarkedSegment {
  text: string
  /** 錯的片段（LLM 以 `<u>…</u>` 標出）。Whether it is marked as wrong. */
  marked: boolean
}

/**
 * 把錯誤對照切成一般文字與標示錯誤的片段：只認 `<u>`、`</u>`，其他內容都當純文字顯示（不當 HTML）。
 * Splits the error comparison into plain and marked segments.
 * @param value - 錯誤對照。The error comparison.
 * @returns 片段（空的略過）。The segments.
 */
export function markedSegments(value: string): MarkedSegment[] {
  const segments: MarkedSegment[] = []
  let marked = false
  for (const part of value.split(/(<\/?u>)/i)) {
    const tag = part.toLowerCase()
    if (tag === '<u>') marked = true
    else if (tag === '</u>') marked = false
    else if (part) segments.push({ text: part, marked })
  }
  return segments
}

/**
 * 外語糾錯預覽的編輯狀態（存回後端的 `state`，是 JSON 物件，所以用 type 而不是 interface）。
 * The expression preview's editing state.
 */
export type ExpressionDraftState = {
  /** 草稿；生成結果的格式不對時為 null（只能廢棄重來）。The draft, or null when malformed. */
  draft: ExpressionCreateParams | null
  /** 勾選的子卡編號。The selected micro cards. */
  selected: number[]
  /**
   * 生成前查重複失敗、略過了檢查時的原因（草稿輸入的 `duplicate_check_skipped`）；沒略過時為 null。
   * 這時預覽提醒，並可以改成允許重複（真的重複時建立會被 Anki 擋下）。Why the duplicate check was skipped.
   */
  checkSkipped: string | null
  /** 要加的標籤。The tags to add. */
  tags: string[]
}

/**
 * 草稿輸入裡「略過了查重複」的原因。Why the duplicate check was skipped, from the draft input.
 * @param input - 草稿的輸入。The draft input.
 * @returns 原因，或 null。The reason, or null.
 */
export function skippedCheckReason(input: Record<string, unknown> | null): string | null {
  const reason = input?.duplicate_check_skipped
  return typeof reason === 'string' && reason.trim() ? reason : null
}
