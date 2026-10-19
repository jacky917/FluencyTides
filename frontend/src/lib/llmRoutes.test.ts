import { describe, expect, it } from 'vitest'
import {
  defaultEffort,
  encodeRouteKey,
  formatChoice,
  initialSelection,
  previewLabel,
} from './llmRoutes'
import type { LLMEndpointOption } from '../types/api'

const endpoint = (id: string, type: string, models: LLMEndpointOption['models'] = []): LLMEndpointOption => ({
  id,
  type,
  models,
})

const WHITELIST: LLMEndpointOption[] = [
  endpoint('claude-code', 'claude-code', [
    { name: 'claude-opus-5', efforts: ['low', 'high'] },
    { name: 'claude-sonnet-5', efforts: ['high'] },
    { name: 'claude-haiku-5', efforts: [] },
  ]),
  endpoint('yinli', 'openai', [{ name: 'gemini-3.5-flash', efforts: [] }]),
]

describe('previewLabel', () => {
  // 預期值由後端 build_model_label 產生（labels.py），兩邊規則改動時要一起改
  it.each([
    ['claude-code', 'claude-code', 'claude-opus-5', 'high', '(claude-code)opus-5@high'],
    ['claude-code', 'claude-code', 'opus', null, '(claude-code)opus'],
    ['google', 'openai', 'gemini-3.5-flash', null, 'gemini-3.5-flash'],
    ['openai', 'openai', 'gpt-5', null, 'gpt-5'],
    ['yinli', 'openai', 'deepseek/deepseek-v4', null, '(yinli)deepseek/deepseek-v4'],
    ['antigravity', 'antigravity', 'claude-opus-4', 'MAX', '(antigravity)claude-opus-4@max'],
  ])('%s (%s) %s @%s → %s', (id, type, model, effort, expected) => {
    expect(previewLabel(endpoint(id, type), model, effort)).toBe(expected)
  })
})

describe('formatChoice', () => {
  it('omits the effort when there is none', () => {
    expect(formatChoice({ endpoint: 'yinli', model: 'm1', effort: null })).toBe('yinli / m1')
    expect(formatChoice({ endpoint: 'claude-code', model: 'opus', effort: 'high' })).toBe('claude-code / opus @high')
  })
})

describe('encodeRouteKey', () => {
  it('escapes the global key', () => {
    expect(encodeRouteKey('*')).toBe('%2A')
    expect(encodeRouteKey('jp_verb_pair')).toBe('jp_verb_pair')
  })
})

describe('defaultEffort', () => {
  it('selects the only effort and nothing otherwise', () => {
    expect(defaultEffort(['high'])).toBe('high')
    expect(defaultEffort(['low', 'high'])).toBe('')
    expect(defaultEffort([])).toBe('')
  })
})

describe('initialSelection', () => {
  it('starts empty without a stored choice', () => {
    expect(initialSelection(null, WHITELIST)).toEqual({ endpoint: '', model: '', effort: '' })
  })

  it('pre-fills a stored choice that is still whitelisted', () => {
    const stored = { endpoint: 'claude-code', model: 'claude-opus-5', effort: 'low' }
    expect(initialSelection(stored, WHITELIST)).toEqual({ endpoint: 'claude-code', model: 'claude-opus-5', effort: 'low' })
  })

  it('keeps only the still-whitelisted part', () => {
    expect(initialSelection({ endpoint: 'gone', model: 'm', effort: null }, WHITELIST)).toEqual({
      endpoint: '',
      model: '',
      effort: '',
    })
    expect(initialSelection({ endpoint: 'yinli', model: 'removed', effort: null }, WHITELIST)).toEqual({
      endpoint: 'yinli',
      model: '',
      effort: '',
    })
    // effort 已不在白名單：只有一個 effort 時改選它
    expect(initialSelection({ endpoint: 'claude-code', model: 'claude-sonnet-5', effort: 'max' }, WHITELIST)).toEqual({
      endpoint: 'claude-code',
      model: 'claude-sonnet-5',
      effort: 'high',
    })
  })
})
