import { describe, expect, it } from 'vitest'
import {
  expressionParams,
  initialSelection,
  markedSegments,
  microEntries,
  readExpressionDraft,
  selectableEntries,
  skippedCheckReason,
} from './expressionDraft'

const point = (target_phrase: string) => ({
  original_phrase: '',
  target_phrase,
  native_translation: '翻譯',
  context_hint: '提示',
  context_sentence: `${target_phrase}。`,
  error_hint: '說明',
})

/** 後端 `execute_draft` 回傳的建立參數。 */
const params = {
  original_text: '私は昨日に学校へ行きました',
  context: '',
  source_tag: '',
  native_language: '中文',
  target_language: '日文',
  user_grammar_correction: '',
  user_reorganization: '',
  llm_result: {
    error_comparison: '私は<u>昨日に</u>学校へ行きました',
    grammar_correction: '私は昨日学校へ行きました',
    reorganized_expression: '昨日は学校に行ってきました',
    grammar_micro_points: [point('昨日学校へ')],
    reorganized_micro_points: [point('行ってきました'), point('昨日は')],
  },
  selected_indices: null,
  allow_duplicate: false,
  replace_note_id: null,
}

describe('readExpressionDraft', () => {
  it('讀出建立參數', () => {
    const draft = readExpressionDraft(params)!
    expect(draft.original_text).toBe('私は昨日に学校へ行きました')
    expect(draft.llm_result.reorganized_micro_points).toHaveLength(2)
    expect(draft.selected_indices).toBeNull()
    expect(draft.replace_note_id).toBeNull()
  })

  it('沒有 LLM 結果時為 null', () => {
    expect(readExpressionDraft({ ...params, llm_result: null })).toBeNull()
    expect(readExpressionDraft({})).toBeNull()
  })

  it('覆蓋、允許重複的決定照讀', () => {
    const draft = readExpressionDraft({ ...params, allow_duplicate: true, replace_note_id: 20 })!
    expect(draft.allow_duplicate).toBe(true)
    expect(draft.replace_note_id).toBe(20)
  })
})

describe('子卡的編號與勾選', () => {
  it('編號與後端相同：文法修正在前、重新組織在後，從 1 起算', () => {
    const entries = microEntries(readExpressionDraft(params)!.llm_result)
    expect(entries.map((e) => [e.number, e.label, e.point.target_phrase])).toEqual([
      [1, '文法修正', '昨日学校へ'],
      [2, '重新組織', '行ってきました'],
      [3, '重新組織', '昨日は'],
    ])
  })

  it('格式不對的知識點保留編號、不能勾選', () => {
    const draft = readExpressionDraft({
      ...params,
      llm_result: { ...params.llm_result, grammar_micro_points: [{ target_phrase: '' }, point('A')] },
    })!
    expect(microEntries(draft.llm_result).map((e) => e.number)).toEqual([1, 2, 3, 4])
    expect(selectableEntries(draft.llm_result).map((e) => e.number)).toEqual([2, 3, 4])
    expect(initialSelection(draft)).toEqual([2, 3, 4])
  })

  it('預設全選；草稿有指定就照它', () => {
    expect(initialSelection(readExpressionDraft(params)!)).toEqual([1, 2, 3])
    expect(initialSelection(readExpressionDraft({ ...params, selected_indices: [3, 1, 9] })!)).toEqual([1, 3])
  })

  it('建立參數一律明確列出勾選（遞增）；空陣列表示只建母卡', () => {
    const draft = readExpressionDraft(params)!
    expect(expressionParams(draft, [3, 1]).selected_indices).toEqual([1, 3])
    expect(expressionParams(draft, []).selected_indices).toEqual([])
    expect(expressionParams(draft, [2]).original_text).toBe(draft.original_text)
  })
})

describe('markedSegments', () => {
  it('切出 <u> 標示的錯誤片段', () => {
    expect(markedSegments('私は<u>昨日に</u>学校へ<U>行き</U>')).toEqual([
      { text: '私は', marked: false },
      { text: '昨日に', marked: true },
      { text: '学校へ', marked: false },
      { text: '行き', marked: true },
    ])
  })

  it('其他標籤當純文字', () => {
    expect(markedSegments('a<b>c')).toEqual([{ text: 'a<b>c', marked: false }])
    expect(markedSegments('')).toEqual([])
  })
})

describe('skippedCheckReason', () => {
  it('讀出略過查重複的原因；沒有或不是文字時為 null', () => {
    expect(skippedCheckReason({ original_text: 'x', duplicate_check_skipped: '無法確認（timeout）' })).toBe(
      '無法確認（timeout）',
    )
    expect(skippedCheckReason({ original_text: 'x' })).toBeNull()
    expect(skippedCheckReason({ duplicate_check_skipped: '  ' })).toBeNull()
    expect(skippedCheckReason(null)).toBeNull()
  })
})
