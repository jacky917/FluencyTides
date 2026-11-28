import { describe, expect, it } from 'vitest'
import {
  buildChoice,
  capabilitiesInUse,
  capabilityLabel,
  defaultEffort,
  encodeRouteKey,
  formatChoice,
  initialSelection,
  isGlobalKey,
  previewLabel,
  providersFor,
  sourceLabel,
} from './capabilityRoutes'
import type { CapabilityRoute, CapabilityState, ProviderOption } from '../types/api'

const provider = (id: string, type: string, models: ProviderOption['models'] = []): ProviderOption => ({
  id,
  type,
  models,
})

const WHITELIST: ProviderOption[] = [
  provider('claude-code', 'claude-code', [
    { name: 'claude-opus-5', efforts: ['low', 'high'] },
    { name: 'claude-sonnet-5', efforts: ['high'] },
    { name: 'claude-haiku-5', efforts: [] },
  ]),
  provider('yinli', 'openai', [{ name: 'gemini-3.5-flash', efforts: [] }]),
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
    expect(previewLabel(provider(id, type), model, effort)).toBe(expected)
  })

  it('labels audio models and Whisper with their provider, strategies by name', () => {
    expect(previewLabel(provider('audio', 'gemini-native'), 'gemini-3.5-flash', null)).toBe('(audio)gemini-3.5-flash')
    expect(previewLabel(provider('whisper', 'whisper'), 'Systran/faster-whisper-large-v3', null)).toBe(
      '(whisper)Systran/faster-whisper-large-v3',
    )
    expect(previewLabel(provider('listen', 'strategy'), null, null)).toBe('listen')
  })
})

describe('formatChoice', () => {
  it('omits the effort when there is none', () => {
    expect(formatChoice({ provider: 'yinli', model: 'm1', effort: null })).toBe('yinli / m1')
    expect(formatChoice({ provider: 'claude-code', model: 'opus', effort: 'high' })).toBe('claude-code / opus @high')
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
    expect(initialSelection(null, WHITELIST)).toEqual({ provider: '', model: '', effort: '' })
  })

  it('pre-fills a stored choice that is still whitelisted', () => {
    const stored = { provider: 'claude-code', model: 'claude-opus-5', effort: 'low' }
    expect(initialSelection(stored, WHITELIST)).toEqual({ provider: 'claude-code', model: 'claude-opus-5', effort: 'low' })
  })

  it('keeps only the still-whitelisted part', () => {
    expect(initialSelection({ provider: 'gone', model: 'm', effort: null }, WHITELIST)).toEqual({
      provider: '',
      model: '',
      effort: '',
    })
    expect(initialSelection({ provider: 'yinli', model: 'removed', effort: null }, WHITELIST)).toEqual({
      provider: 'yinli',
      model: '',
      effort: '',
    })
    // effort 已不在白名單：只有一個 effort 時改選它
    expect(initialSelection({ provider: 'claude-code', model: 'claude-sonnet-5', effort: 'max' }, WHITELIST)).toEqual({
      provider: 'claude-code',
      model: 'claude-sonnet-5',
      effort: 'high',
    })
  })
})

describe('capabilityLabel', () => {
  it('names known capabilities and falls back to the code', () => {
    expect(capabilityLabel('llm')).toBe('文字 LLM')
    expect(capabilityLabel('audio_eval')).toBe('評分策略')
    expect(capabilityLabel('stt')).toBe('語音轉文字')
    expect(capabilityLabel('tts')).toBe('tts')
  })
})

describe('formatChoice without a model', () => {
  it('shows only the provider for model-less capabilities', () => {
    expect(formatChoice({ provider: 'stt_diff', model: null, effort: null })).toBe('stt_diff')
  })
})

const STRATEGIES: ProviderOption[] = ['listen', 'stt_diff', 'stt_llm'].map((id) => provider(id, 'strategy'))

describe('initialSelection for strategies', () => {
  it('pre-selects a stored strategy, which has no model', () => {
    expect(initialSelection({ provider: 'stt_llm', model: null, effort: null }, STRATEGIES)).toEqual({
      provider: 'stt_llm',
      model: '',
      effort: '',
    })
  })
})

describe('buildChoice', () => {
  it('is complete once a strategy is picked', () => {
    expect(buildChoice(STRATEGIES, { provider: 'stt_diff', model: '', effort: '' })).toEqual({
      provider: 'stt_diff',
      model: null,
      effort: null,
    })
  })

  it('needs a model, and an effort when the model takes one', () => {
    expect(buildChoice(WHITELIST, { provider: 'claude-code', model: '', effort: '' })).toBeNull()
    expect(buildChoice(WHITELIST, { provider: 'claude-code', model: 'claude-opus-5', effort: '' })).toBeNull()
    expect(buildChoice(WHITELIST, { provider: 'claude-code', model: 'claude-opus-5', effort: 'low' })).toEqual({
      provider: 'claude-code',
      model: 'claude-opus-5',
      effort: 'low',
    })
    expect(buildChoice(WHITELIST, { provider: 'yinli', model: 'gemini-3.5-flash', effort: '' })).toEqual({
      provider: 'yinli',
      model: 'gemini-3.5-flash',
      effort: null,
    })
    expect(buildChoice(WHITELIST, { provider: 'gone', model: 'x', effort: '' })).toBeNull()
  })
})

describe('providersFor', () => {
  const all = { llm: WHITELIST, audio_eval: STRATEGIES }

  it('filters strategies by the key itself only (no inheritance from a flow key)', () => {
    const strategies = { shadowing: ['stt_diff'] }
    expect(providersFor('audio_eval', 'shadowing', all, strategies).map((item) => item.id)).toEqual(['stt_diff'])
    expect(providersFor('audio_eval', 'shadowing.x', all, strategies)).toHaveLength(3)
    expect(providersFor('audio_eval', 'recording_eval.speaking_coach', all, strategies)).toHaveLength(3)
    expect(providersFor('llm', 'shadowing', all, strategies)).toBe(WHITELIST)
    expect(providersFor('stt', 'shadowing', all, strategies)).toEqual([])
  })
})

describe('isGlobalKey', () => {
  it('recognizes the text and voice globals only', () => {
    expect(isGlobalKey('*')).toBe(true)
    expect(isGlobalKey('*voice')).toBe(true)
    expect(isGlobalKey('recording_eval.speaking_trilingual')).toBe(false)
    expect(isGlobalKey('constructor')).toBe(false)
  })
})

describe('sourceLabel', () => {
  it('names the winning layer, which global, and whether the switch forced it', () => {
    expect(sourceLabel({ source: 'route', global_key: '*', forced: false })).toBe('自己設定')
    expect(sourceLabel({ source: 'global', global_key: '*', forced: false })).toBe('沿用全域（文字）')
    expect(sourceLabel({ source: 'global', global_key: '*voice', forced: false })).toBe('沿用全域（語音）')
    expect(sourceLabel({ source: 'global', global_key: '*voice', forced: true })).toBe('全域（語音，全局生效）')
    expect(sourceLabel({ source: 'env', global_key: '*voice', forced: true })).toBe('.env 預設（全局生效）')
    expect(sourceLabel({ source: 'env', global_key: '*', forced: false })).toBe('.env 預設')
    // 全域鍵自己的設定生效時就是「自己設定」
    expect(sourceLabel({ source: 'global', global_key: '*voice', forced: false }, '*voice')).toBe('自己設定')
  })
})

describe('capabilitiesInUse', () => {
  const state = (provider: string, declared = true): CapabilityState => ({
    source: 'env',
    effective: { provider, model: null, effort: null },
    label: provider,
    stored: null,
    rejected: [],
    db_unavailable: false,
    problem: null,
    declared,
    global_key: '*voice',
    forced: false,
  })
  const route = (key: string, strategy: string, extra: Record<string, CapabilityState> = {}): CapabilityRoute => ({
    route_key: key,
    description: null,
    declared: true,
    capabilities: {
      llm: state('audio'),
      audio_eval: state(strategy),
      audio_llm: state('audio'),
      stt: state('whisper'),
      ...extra,
    },
  })

  it('counts the strategy and what it uses', () => {
    const key = 'recording_eval.speaking_trilingual'
    expect([...capabilitiesInUse(route(key, 'listen'))].sort()).toEqual(['audio_eval', 'audio_llm'])
    expect([...capabilitiesInUse(route(key, 'stt_llm'))].sort()).toEqual(['audio_eval', 'llm', 'stt'])
    // 語音全域和其他錄音評分鍵一樣，只算策略用到的（不再特別算文字 LLM）
    expect([...capabilitiesInUse(route('*voice', 'stt_diff'))].sort()).toEqual(['audio_eval', 'stt'])
  })

  it('counts everything without a strategy, and orphan rows always', () => {
    const plain: CapabilityRoute = { route_key: 'jp', description: null, declared: true, capabilities: { llm: state('x') } }
    expect([...capabilitiesInUse(plain)]).toEqual(['llm'])
    const orphan = route('recording_eval.speaking_trilingual', 'listen', { tts: state('x', false) })
    expect(capabilitiesInUse(orphan).has('tts')).toBe(true)
  })
})
