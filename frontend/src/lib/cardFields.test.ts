import { describe, expect, it } from 'vitest'
import {
  cardPreview,
  changedFields,
  fieldValues,
  isStructuredField,
  noteEditPolicy,
  permissionsFromCapabilities,
} from './cardFields'
import type { GenericNoteDetail, HandlerInfo, Operation } from '../types/api'

const open: Operation = { supported: true, reason: null }
const closed = (reason: string): Operation => ({ supported: false, reason })

function handlerInfo(overrides: Partial<HandlerInfo> = {}): HandlerInfo {
  return {
    name: 'vocabulary_mining',
    title: '單字卡',
    note_types: ['TOEIC_Coach_Dark_v2'],
    capabilities: {
      create: {
        supported: true, reason: null, channels: ['web', 'tg'], flow: 'form', deck: 'selected',
        fixed_deck: null, note_type: 'TOEIC_Coach_Dark_v2',
      },
      read: open,
      update_fields: open,
      delete: open,
      actions: [],
      graph: { supported: true, reason: null, links: true },
    },
    create_schema: null,
    json_fields: ['Synonyms_JSON'],
    media_fields: ['Word_Audio'],
    ...overrides,
  }
}

function note(overrides: Partial<GenericNoteDetail> = {}): GenericNoteDetail {
  return {
    note_id: 1,
    note_type: 'TOEIC_Coach_Dark_v2',
    tags: [],
    cards: [2],
    fields: [
      { name: 'Expression', value: 'anyway' },
      { name: 'Word_Audio', value: '[sound:a.mp3]' },
    ],
    handler: 'vocabulary_mining',
    permissions: {
      update_fields: { allowed: true, reason: null },
      delete: { allowed: true, reason: null },
    },
    ...overrides,
  }
}

describe('noteEditPolicy', () => {
  it('follows the backend permissions and locks declared JSON and media fields', () => {
    const policy = noteEditPolicy({ note: note(), handler: handlerInfo(), handlersError: null })

    expect(policy).toMatchObject({
      handlerName: 'vocabulary_mining',
      canEdit: true,
      canDelete: true,
      editNotice: null,
      deleteNotice: null,
    })
    expect(Object.fromEntries(policy.lockedFields)).toEqual({ Synonyms_JSON: 'JSON', Word_Audio: '媒體' })
  })

  it('shows the backend reasons when editing or deleting is not allowed', () => {
    const jp = note({
      note_type: 'JP_Context_Dark',
      handler: 'jp_verb_pair',
      permissions: {
        update_fields: { allowed: false, reason: '由本機批次腳本管理，網頁只能檢視' },
        delete: { allowed: false, reason: '請用 delete_child_cards.py' },
      },
    })
    const policy = noteEditPolicy({ note: jp, handler: undefined, handlersError: null })

    expect(policy).toMatchObject({
      handlerName: 'jp_verb_pair',
      canEdit: false,
      canDelete: false,
      editNotice: '由本機批次腳本管理，網頁只能檢視',
      deleteNotice: '請用 delete_child_cards.py',
    })
  })

  it('blocks writes for note types without a handler', () => {
    const reason = '沒有 handler 負責 Notion_SRS_Dark'
    const orphan = note({
      note_type: 'Notion_SRS_Dark',
      handler: null,
      permissions: { update_fields: { allowed: false, reason }, delete: { allowed: false, reason } },
    })
    const policy = noteEditPolicy({ note: orphan, handler: undefined, handlersError: null })

    expect(policy).toMatchObject({ handlerName: null, canEdit: false, canDelete: false, editNotice: reason })
  })

  it('does not edit before knowing which fields are read-only', () => {
    const loading = noteEditPolicy({ note: note(), handler: undefined, handlersError: null })
    const failed = noteEditPolicy({ note: note(), handler: undefined, handlersError: '連不到後端' })

    expect(loading).toMatchObject({ canEdit: false, canDelete: true, editNotice: null })
    expect(failed.canEdit).toBe(false)
    expect(failed.editNotice).toContain('連不到後端')
    expect(failed.canDelete).toBe(true) // 刪除不需要欄位設定
  })
})

describe('permissionsFromCapabilities', () => {
  it('mirrors the backend permission rule', () => {
    const handler = handlerInfo({
      capabilities: {
        ...handlerInfo().capabilities,
        update_fields: closed('對話卡的欄位修改尚未開放'),
      },
    })
    expect(permissionsFromCapabilities(handler)).toEqual({
      update_fields: { allowed: false, reason: '對話卡的欄位修改尚未開放' },
      delete: { allowed: true, reason: null },
    })
  })
})

describe('isStructuredField', () => {
  it.each([
    ['Word_Data_JSON', 'anything', true],
    ['Recordings', '[{"date":"2026"}]', true],
    ['References', '<div>&quot;x&quot;</div>', false],
    ['References', '<div>[{&quot;content&quot;:1}]</div>', true],
    ['Word_Audio', '[sound:01-01.mp3]', true],
    ['Prompt', 'こんにちは', false],
  ])('treats %s=%s as structured: %s', (name, value, expected) => {
    expect(isStructuredField(name, value)).toBe(expected)
  })
})

describe('field values', () => {
  it('keeps the backend order and sends only the changed ones', () => {
    expect(Object.entries(fieldValues(note()))).toEqual([
      ['Expression', 'anyway'],
      ['Word_Audio', '[sound:a.mp3]'],
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
