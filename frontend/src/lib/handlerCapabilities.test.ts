import { describe, expect, it } from 'vitest'
import { capabilityRows, createAvailability, deckRuleLabel, flowLabel } from './handlerCapabilities'
import type { CreateCapability, HandlerInfo } from '../types/api'

const closedCreate = (channels: CreateCapability['channels'], reason: string): CreateCapability => ({
  supported: false, reason, channels, flow: null, deck: null, fixed_deck: null, note_type: null,
})

const vocabCreate: CreateCapability = {
  supported: true, reason: null, channels: ['web', 'tg'], flow: 'form', deck: 'selected',
  fixed_deck: null, note_type: 'TOEIC_Coach_Dark_v2',
}

describe('createAvailability', () => {
  it('selectable when the web is a channel', () => {
    expect(createAvailability(vocabCreate)).toEqual({ kind: 'selectable' })
  })

  it('greyed out with the reason when created elsewhere', () => {
    expect(createAvailability(closedCreate(['script'], '子卡由本機批次腳本產生'))).toEqual({
      kind: 'disabled',
      reason: '子卡由本機批次腳本產生',
    })
    const expression: CreateCapability = {
      ...vocabCreate, channels: ['tg'], flow: 'preview', deck: 'fixed', fixed_deck: 'D',
      reason: '目前只能在 TG 建立',
    }
    expect(createAvailability(expression)).toEqual({ kind: 'disabled', reason: '目前只能在 TG 建立' })
  })

  it('hidden without any channel', () => {
    expect(createAvailability(closedCreate([], '網頁建卡尚未開放'))).toEqual({ kind: 'hidden' })
  })
})

describe('labels', () => {
  it('describes the flow and the deck rule', () => {
    expect(flowLabel(vocabCreate)).toBe('填表後建立')
    expect(deckRuleLabel(vocabCreate)).toBe('建在選取的牌組')
    expect(deckRuleLabel({ ...vocabCreate, deck: 'fixed', fixed_deck: '日本語::外語糾錯::母卡片' })).toBe(
      '固定建在 日本語::外語糾錯::母卡片',
    )
    expect(flowLabel(closedCreate([], 'x'))).toBeNull()
  })
})

describe('capabilityRows', () => {
  const jp: HandlerInfo = {
    name: 'jp_verb_pair',
    title: '自他動詞',
    note_types: ['JP_VerbPair_Master_Dark'],
    capabilities: {
      create: closedCreate(['script'], '子卡由本機批次腳本 generate_child_cards.py 產生'),
      read: { supported: true, reason: null },
      update_fields: { supported: false, reason: '由本機批次腳本管理，網頁只能檢視' },
      delete: { supported: false, reason: '請用 delete_child_cards.py' },
      actions: [],
      graph: { supported: false, reason: '尚未實作', links: false },
    },
    create_schema: null,
    json_fields: [],
    media_fields: [],
  }

  it('lists every capability with reasons for closed ones', () => {
    expect(capabilityRows(jp)).toEqual([
      { label: '建立', supported: false, detail: '子卡由本機批次腳本 generate_child_cards.py 產生；管道：本機腳本' },
      { label: '讀取', supported: true, detail: null },
      { label: '改欄位', supported: false, detail: '由本機批次腳本管理，網頁只能檢視' },
      { label: '刪除', supported: false, detail: '請用 delete_child_cards.py' },
      { label: '圖譜', supported: false, detail: '尚未實作' },
      { label: '動作', supported: false, detail: '無' },
    ])
  })

  it('describes an open create with channels, flow, deck and note type', () => {
    const [create, , , , graph] = capabilityRows({
      ...jp,
      capabilities: {
        ...jp.capabilities,
        create: vocabCreate,
        graph: { supported: true, reason: null, links: true },
      },
    })
    expect(create).toEqual({
      label: '建立',
      supported: true,
      detail: '管道：網頁、TG；填表後建立；建在選取的牌組；建立 TOEIC_Coach_Dark_v2',
    })
    expect(graph).toEqual({ label: '圖譜', supported: true, detail: '含關聯連線' })
  })
})
