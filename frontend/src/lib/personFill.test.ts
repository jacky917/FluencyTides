import { describe, expect, it } from 'vitest'
import type { FillResultParams, ServerDraft } from '../types/api'
import {
  closePersonFill,
  fillParams,
  fieldSections,
  findEdit,
  hasPending,
  historyItemKeys,
  keyLabel,
  initialFillState,
  isFillDraft,
  openPersonFill,
  personFillSnapshot,
  plainField,
  revertEdit,
  selectedIndexes,
  upsertEdit,
} from './personFill'

function draft(input: Record<string, unknown> | null, params?: Record<string, unknown>): ServerDraft {
  return {
    id: 'd1',
    handler: 'person_profile',
    status: params ? 'ready' : 'generating',
    input,
    result: params ? { params, suggested_tags: [] } : null,
    state: null,
    error: null,
    version: 2,
    created_at: '',
    updated_at: '',
    generation_started_at: null,
  }
}

const CREATE: FillResultParams = {
  mode: 'create',
  note_id: null,
  deck: '人脈',
  known: null,
  labels: {},
  proposals: [
    { kind: 'display_name', data: { value: '田中さん' } },
    { kind: 'interaction', data: { date: '2026-11-23', topics: '登山' } },
    { kind: 'field', data: { field: 'Hometown', value: '福岡', mode: 'replace' } },
    { kind: 'family', data: { relation: '弟弟', note: '上上個月來日本' } },
  ],
  saved: [],
  errors: {},
}

describe('isFillDraft / fillParams', () => {
  it('recognizes "AI 整理" drafts by their purpose', () => {
    expect(isFillDraft(draft({ purpose: 'fill', text: 'x' }))).toBe(true)
    expect(isFillDraft(draft({ background: '剪髮' }))).toBe(false)
    expect(isFillDraft(draft(null))).toBe(false)
    expect(isFillDraft(null)).toBe(false)
  })

  it('reads the result, filling in missing progress', () => {
    expect(fillParams(draft({ purpose: 'fill' }))).toBeNull()
    const params = fillParams(draft({ purpose: 'fill' }, { mode: 'update', note_id: 5, proposals: [] }))
    expect(params).toEqual({
      mode: 'update', note_id: 5, deck: null, proposals: [], known: null, labels: {}, saved: [], errors: {},
    })
  })
})

describe('editing state', () => {
  it('ticks everything by default and copies the data', () => {
    const state = initialFillState(CREATE)
    expect(state.selected).toEqual([true, true, true, true])
    expect(state.proposals[0].data).not.toBe(CREATE.proposals[0].data)
  })

  it('always sends the display name of a new card', () => {
    const state = { ...initialFillState(CREATE), selected: [false, false, true, false] }
    expect(selectedIndexes(state, CREATE)).toEqual([0, 2])
    expect(selectedIndexes(state, { ...CREATE, mode: 'update' })).toEqual([2])
  })

  it('has something pending until every ticked one is saved', () => {
    const state = initialFillState(CREATE)
    expect(hasPending(state, CREATE)).toBe(true)
    expect(hasPending(state, { ...CREATE, saved: [0, 1, 2, 3] })).toBe(false)
  })
})

describe('fieldSections', () => {
  it('groups proposals by the field they write to, in the kind order', () => {
    const proposals = [
      { kind: 'field' as const, data: { field: 'Notes', value: 'a', mode: 'append' } },
      { kind: 'history_add' as const, data: { field: 'Interests', item: { value: '登山' } } },
      { kind: 'family' as const, data: { relation: '弟弟' } },
      { kind: 'field' as const, data: { field: 'Notes', value: 'b', mode: 'append' } },
      { kind: 'history_end' as const, data: { field: 'Interests', index: 0, fingerprint: 'f' } },
      { kind: 'edit_field' as const, data: { field: 'Notes', value: '改過的' } },
    ]
    expect(fieldSections(proposals)).toEqual([
      { field: 'Family_JSON', indexes: [2] },
      { field: 'Notes', indexes: [0, 3] },
      { field: 'Interests', indexes: [1, 4] },
    ])
  })
})

describe('edits of the original content', () => {
  it('adds an edit once and updates it in place, keeping the indexes', () => {
    const state = initialFillState(CREATE)
    const first = upsertEdit(state, { kind: 'edit_field', data: { field: 'Notes', value: 'x' } })
    expect(first.proposals).toHaveLength(5)
    expect(findEdit(first, 'Notes')).toBe(4)
    const second = upsertEdit(revertEdit(first, 4), { kind: 'edit_field', data: { field: 'Notes', value: 'y' } })
    expect(second.proposals).toHaveLength(5)
    expect(second.proposals[4].data.value).toBe('y')
    expect(second.selected[4]).toBe(true)
  })

  it('tells entries of a list apart by their index', () => {
    const state = upsertEdit(initialFillState(CREATE), {
      kind: 'edit_item',
      data: { field: 'Interests', index: 2, fingerprint: 'f', values: {} },
    })
    expect(findEdit(state, 'Interests', 2)).toBe(4)
    expect(findEdit(state, 'Interests', 0)).toBe(-1)
    expect(findEdit(state, 'Interests')).toBe(-1)
  })
})

describe('historyItemKeys', () => {
  it('lists every key the backend named, filled or not, in its order', () => {
    const labels = { value: '內容', since: '取得', until: '到期', note: '說明' }
    expect(historyItemKeys({ value: '工作簽證', added: '2026-01-01' }, labels)).toEqual(['value', 'since', 'until', 'note'])
  })

  it('falls back to the keys an entry has on old drafts', () => {
    expect(historyItemKeys({ note: 'n', company: 'Sony', title: 'PM', added: '2026-01-01' })).toEqual([
      'company',
      'title',
      'note',
    ])
  })
})

describe('keyLabel', () => {
  it("uses the backend's name for the field first", () => {
    expect(keyLabel({ since: '取得', until: '到期' }, 'since')).toBe('取得')
  })

  it('falls back when there are no names, or the names miss the key', () => {
    expect(keyLabel(undefined, 'since')).toBe('開始於')
    expect(keyLabel({ value: '內容' }, 'note')).toBe('說明')
    expect(keyLabel(undefined, 'unknown_key')).toBe('unknown_key')
  })
})

describe('dialog store', () => {
  it('keeps the last target when reopened without one', () => {
    openPersonFill({ noteId: 3, label: '田中さん' })
    closePersonFill()
    expect(personFillSnapshot()).toEqual({ open: false, target: { noteId: 3, label: '田中さん' } })
    openPersonFill()
    expect(personFillSnapshot()).toEqual({ open: true, target: { noteId: 3, label: '田中さん' } })
    openPersonFill({ deck: '人脈' })
    expect(personFillSnapshot().target).toEqual({ deck: '人脈' })
  })
})

describe('plainField', () => {
  it('turns field HTML into text with line breaks', () => {
    expect(plainField('第一行<br>第二行 &amp; <b>粗</b>')).toBe('第一行\n第二行 & 粗')
    expect(plainField('')).toBe('')
  })
})

