/**
 * 三語對話卡網頁建卡的預覽（計畫書 `docs/wip/trilingual_web_create_FEAT_2026-10-30.md` §3）：草稿的形狀、
 * 勾選、排序與熟識度配色。畫面在 `components/generate/TrilingualDraftPreview.tsx`。
 *
 * The trilingual web-create preview: draft shape, selection, ordering and
 * familiarity colours.
 */

import type { TrilingualCreateParams, TrilingualReference } from '../types/api'

/** 三種語言在建立參數裡的鍵與名稱，依卡片的顯示順序。The reference keys, in card order. */
export const REFERENCE_LANGS = [
  { key: 'references_zh', label: '中文' },
  { key: 'references_ja', label: '日文' },
  { key: 'references_en', label: '英文' },
] as const

export type ReferenceKey = (typeof REFERENCE_LANGS)[number]['key']

/** 每種語言每一句是否勾選。Which sayings are selected, per language. */
export type DraftSelection = Record<ReferenceKey, boolean[]>

/**
 * 熟識度 0–9 的顏色，與卡片背面 `Speaking_Trilingual_Dark_style.css` 的 `.fam-0`…`.fam-9` 相同。
 * Familiarity colours, the same as the card's CSS.
 */
export const FAMILIARITY_COLORS = [
  '#4a90c2', '#5dade2', '#6cb8e6', '#82c4ea', '#9b87f5', '#a78bfa', '#c084fc', '#ff6b9d', '#ff3d6e', '#ff1f3d',
] as const

/** 一句草稿；格式不對的項目丟掉。One saying, or null when malformed. */
function readReference(value: unknown): TrilingualReference | null {
  if (typeof value !== 'object' || value === null) return null
  const item = value as Record<string, unknown>
  if (typeof item.content !== 'string' || !item.content.trim()) return null
  const familiarity = item.familiarity
  return {
    content: item.content,
    note: typeof item.note === 'string' ? item.note : '',
    familiarity:
      typeof familiarity === 'number' && Number.isInteger(familiarity) && familiarity >= 0 && familiarity <= 9
        ? familiarity
        : null,
  }
}

/**
 * 把 `POST /handlers/speaking_trilingual/draft` 回傳的 `params` 讀成三語草稿。
 * Reads the draft endpoint's params as a trilingual draft.
 * @param params - 草稿（後端已驗證過形狀，這裡只防呆）。The draft params.
 * @returns 三語草稿。The draft.
 */
export function readDraft(params: Record<string, unknown>): TrilingualCreateParams {
  const list = (key: ReferenceKey) =>
    (Array.isArray(params[key]) ? (params[key] as unknown[]) : [])
      .map(readReference)
      .filter((item): item is TrilingualReference => item !== null)
  return {
    prompt: typeof params.prompt === 'string' ? params.prompt : '',
    context: typeof params.context === 'string' ? params.context : '',
    references_zh: list('references_zh'),
    references_ja: list('references_ja'),
    references_en: list('references_en'),
  }
}

/**
 * 預設全選。Everything selected.
 * @param draft - 草稿。The draft.
 * @returns 勾選狀態。The selection.
 */
export function selectAll(draft: TrilingualCreateParams): DraftSelection {
  return {
    references_zh: draft.references_zh.map(() => true),
    references_ja: draft.references_ja.map(() => true),
    references_en: draft.references_en.map(() => true),
  }
}

/**
 * 勾了幾句（三種語言合計）。How many sayings are selected.
 * @param selection - 勾選狀態。The selection.
 * @returns 句數。The count.
 */
export function selectedCount(selection: DraftSelection): number {
  return REFERENCE_LANGS.reduce((sum, { key }) => sum + selection[key].filter(Boolean).length, 0)
}

/**
 * 顯示順序：依熟識度由低到高，未標的排最後，同分照原順序（與卡片背面相同）。
 * Display order: by familiarity, unrated last, stable.
 * @param references - 一種語言的句子。One language's sayings.
 * @returns 原本的序號，依顯示順序。The original indexes in display order.
 */
export function familiarityOrder(references: TrilingualReference[]): number[] {
  const rank = (index: number) => references[index].familiarity ?? 99
  return references.map((_, index) => index).sort((a, b) => rank(a) - rank(b) || a - b)
}

/**
 * 建卡參數：卡面的情境與補充（可能被使用者改過）加上勾選的句子。
 * The create params: the (possibly edited) prompt and context plus the selected sayings.
 * @param draft - 草稿。The draft.
 * @param selection - 勾選狀態。The selection.
 * @param prompt - 卡面情境。The prompt.
 * @param context - 補充。The context.
 * @returns 建立參數。The create params.
 */
export function selectedParams(
  draft: TrilingualCreateParams,
  selection: DraftSelection,
  prompt: string,
  context: string,
): TrilingualCreateParams {
  const pick = (key: ReferenceKey) => draft[key].filter((_, index) => selection[key][index])
  return {
    prompt,
    context,
    references_zh: pick('references_zh'),
    references_ja: pick('references_ja'),
    references_en: pick('references_en'),
  }
}
