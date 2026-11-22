import { describe, expect, it } from 'vitest'
import { familiarityOrder, readDraft, selectAll, selectedCount, selectedParams, titleProblem } from './trilingualDraft'

const params = {
  prompt: '朋友約你爬山',
  context: '',
  references_zh: [{ content: '下週好不好？', note: '對朋友', familiarity: 8 }],
  references_ja: [
    { content: 'あいにく先約がございまして。', note: '對客人', familiarity: 1 },
    { content: '今週はちょっと…', note: '', familiarity: 7 },
    { content: 'ごめん無理！', note: '', familiarity: null },
    { content: '   ', note: '空白的丟掉', familiarity: 3 },
  ],
  references_en: 'not a list',
}

describe('readDraft', () => {
  it('keeps well-formed sayings and drops the rest', () => {
    const draft = readDraft(params)
    expect(draft.prompt).toBe('朋友約你爬山')
    expect(draft.references_ja.map((r) => r.content)).toEqual(['あいにく先約がございまして。', '今週はちょっと…', 'ごめん無理！'])
    expect(draft.references_en).toEqual([])
  })

  it('treats out-of-range or non-integer familiarity as unrated', () => {
    const draft = readDraft({ references_zh: [{ content: 'a', familiarity: 12 }, { content: 'b', familiarity: 2.5 }] })
    expect(draft.references_zh.map((r) => r.familiarity)).toEqual([null, null])
    expect(draft.references_zh[0].note).toBe('')
  })
})

describe('selection', () => {
  const draft = readDraft(params)

  it('selects everything by default', () => {
    const selection = selectAll(draft)
    expect(selectedCount(selection)).toBe(4)
  })

  it('builds the create params from the selected sayings and the edited title and prompt', () => {
    const selection = selectAll(draft)
    selection.references_ja[1] = false
    selection.references_zh[0] = false
    const result = selectedParams(draft, selection, '婉拒爬山', '改過的情境', '補充')
    expect(result.title).toBe('婉拒爬山')
    expect(result.prompt).toBe('改過的情境')
    expect(result.context).toBe('補充')
    expect(result.references_zh).toEqual([])
    expect(result.references_ja.map((r) => r.content)).toEqual(['あいにく先約がございまして。', 'ごめん無理！'])
  })
})

describe('familiarityOrder', () => {
  it('sorts by familiarity with unrated last, keeping ties in order', () => {
    const order = familiarityOrder([
      { content: 'a', note: '', familiarity: 7 },
      { content: 'b', note: '', familiarity: null },
      { content: 'c', note: '', familiarity: 0 },
      { content: 'd', note: '', familiarity: 7 },
    ])
    expect(order).toEqual([2, 0, 3, 1])
  })
})

describe('titleProblem', () => {
  it('accepts a short title, ignoring surrounding spaces', () => {
    expect(titleProblem('  婉拒加班 ')).toBeNull()
    expect(titleProblem('a'.repeat(40))).toBeNull()
  })

  it.each([
    ['', '空白'],
    ['   ', '空白'],
    ['工作::加班', '::'],
    ['說"不"', '"'],
    ['a'.repeat(41), '最多 40 字'],
    ['兩\n行', '換行'],
  ])('rejects %j (%s), as the backend does', (title, fragment) => {
    expect(titleProblem(title)).toContain(fragment)
  })

  it('reads the suggested title from the draft, defaulting to empty', () => {
    expect(readDraft({ title: '婉拒爬山', prompt: 'p' }).title).toBe('婉拒爬山')
    expect(readDraft({ prompt: 'p' }).title).toBe('')
  })
})
