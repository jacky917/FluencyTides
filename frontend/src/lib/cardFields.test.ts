import { describe, expect, it } from 'vitest'
import { cardPreview, changedFields, fieldEditPolicy, handlerForModel, isStructuredField, orderedFields } from './cardFields'
import type { CardDetail, HandlerInfo } from '../types/api'

const handlers: HandlerInfo[] = [
  { handler_name: 'speaking_coach', supported_models: ['Speaking_Coach_Dark'], input_schema: {} },
  { handler_name: 'jp_core_verb', supported_models: ['JP_CoreVerb_Master_Dark'], input_schema: {} },
]

describe('handlerForModel', () => {
  it('finds the handler owning a note type', () => {
    expect(handlerForModel(handlers, 'JP_CoreVerb_Master_Dark')?.handler_name).toBe('jp_core_verb')
    expect(handlerForModel(handlers, 'AI_QA_Dark')).toBeUndefined()
    expect(handlerForModel(undefined, 'Speaking_Coach_Dark')).toBeUndefined()
  })
})

describe('fieldEditPolicy', () => {
  it('lets update_fields handlers edit and delete', () => {
    expect(fieldEditPolicy({ handlers, handlersError: null, modelName: 'Speaking_Coach_Dark' })).toEqual({
      handlerName: 'speaking_coach',
      canEdit: true,
      canDelete: true,
      notice: null,
    })
  })

  it('never sends field edits to handlers whose update is a no-op', () => {
    const policy = fieldEditPolicy({ handlers, handlersError: null, modelName: 'JP_CoreVerb_Master_Dark' })

    expect(policy).toMatchObject({ handlerName: 'jp_core_verb', canEdit: false, canDelete: true })
    expect(policy.notice).toContain('不支援直接修改欄位')
  })

  it('blocks writes without an owning handler, while loading, or when the list failed', () => {
    const orphan = fieldEditPolicy({ handlers, handlersError: null, modelName: 'AI_QA_Dark' })
    const loading = fieldEditPolicy({ handlers: undefined, handlersError: null, modelName: 'AI_QA_Dark' })
    const failed = fieldEditPolicy({ handlers: undefined, handlersError: '連不到後端', modelName: 'Speaking_Coach_Dark' })

    for (const policy of [orphan, loading, failed]) {
      expect(policy).toMatchObject({ handlerName: null, canEdit: false, canDelete: false })
    }
    expect(orphan.notice).toContain('沒有 handler 負責 AI_QA_Dark')
    expect(loading.notice).toBeNull()
    expect(failed.notice).toContain('連不到後端')
  })
})

describe('isStructuredField', () => {
  it.each([
    ['Word_Data_JSON', 'anything', true],
    ['Recordings', '[{"date":"2026"}]', true],
    ['References', '<div>&quot;x&quot;</div>', false],
    ['References', '<div>[{&quot;content&quot;:1}]</div>', true],
    ['Word_Audio', '[sound:01-01.mp3]', true], // 刻意：只放音檔的欄位也唯讀
    ['Prompt', 'こんにちは', false],
  ])('treats %s=%s as structured: %s', (name, value, expected) => {
    expect(isStructuredField(name, value)).toBe(expected)
  })
})

describe('field values', () => {
  it('orders fields and sends only the changed ones', () => {
    const detail: CardDetail = {
      noteId: 1,
      modelName: 'M',
      tags: [],
      cards: [2],
      fields: { Back: { value: 'b', order: 1 }, Front: { value: 'f', order: 0 } },
    }
    expect(orderedFields(detail)).toEqual([
      ['Front', 'f'],
      ['Back', 'b'],
    ])
    expect(changedFields({ Front: 'f', Back: 'b' }, { Front: 'f', Back: 'B' })).toEqual({ Back: 'B' })
  })

  it('previews the first readable field without markup or brackets', () => {
    expect(cardPreview({ Data_JSON: '[1]', Word: ' 絡[から]まる<br>' })).toBe('絡まる')
    expect(cardPreview({ Prompt: '<b>Hello</b>&nbsp;&amp; bye [sound:a.mp3]' })).toBe('Hello & bye')
    expect(cardPreview({ A: '', B: '  ' })).toBe('')
    expect(cardPreview({ A: 'x'.repeat(300) })).toBe('x'.repeat(300))
  })
})
