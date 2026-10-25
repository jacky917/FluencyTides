import { describe, expect, it } from 'vitest'
import type { HandlerInfo, Operation } from '../types/api'
import {
  ANY_FIELD,
  EMPTY_FIELD_SEARCH,
  addCondition,
  fieldTitle,
  removeCondition,
  requestConditions,
  restoreFieldSearch,
  searchableHandlers,
  selectHandler,
  setConditionValue,
} from './fieldSearch'

const open: Operation = { supported: true, reason: null }
const closed: Operation = { supported: false, reason: '尚未開放' }

function handler(name: string, search: Operation | undefined, fields = [{ key: 'relationship', title: '關係' }]) {
  return {
    name,
    title: name,
    note_types: [],
    capabilities: { search },
    create_schema: null,
    json_fields: [],
    media_fields: [],
    search_fields: search?.supported ? fields : [],
  } as unknown as HandlerInfo
}

describe('searchableHandlers', () => {
  it('只列開放而且有欄位按鈕的 handler', () => {
    const list = [handler('person', open), handler('verb', closed), handler('old', undefined), handler('empty', open, [])]
    expect(searchableHandlers(list).map((h) => h.name)).toEqual(['person'])
    expect(searchableHandlers(undefined)).toEqual([])
  })
})

describe('conditions', () => {
  it('可以任意疊加，同一個欄位也能加好幾次', () => {
    let state = selectHandler('person')
    const first = addCondition(state, 'relationship')
    const second = addCondition(first.state, 'relationship')
    const third = addCondition(second.state, ANY_FIELD)
    state = third.state
    expect(state.conditions.map((c) => c.field)).toEqual(['relationship', 'relationship', ANY_FIELD])
    expect(new Set([first.id, second.id, third.id]).size).toBe(3)

    state = setConditionValue(state, first.id, ' 客戶 ')
    state = setConditionValue(state, third.id, '餐廳')
    // 空白的關鍵字不送；送出前去掉前後空白
    expect(requestConditions(state)).toEqual([
      { field: 'relationship', value: '客戶' },
      { field: ANY_FIELD, value: '餐廳' },
    ])

    state = removeCondition(state, first.id)
    expect(state.conditions.map((c) => c.id)).toEqual([second.id, third.id])
  })

  it('換類型時清掉條件', () => {
    const { state } = addCondition(selectHandler('person'), 'relationship')
    expect(selectHandler('verb')).toEqual({ handler: 'verb', conditions: [] })
    expect(state.conditions).toHaveLength(1)
  })
})

describe('fieldTitle', () => {
  it('* 是全部欄位；找不到時顯示鍵', () => {
    const person = handler('person', open)
    expect(fieldTitle(person, ANY_FIELD)).toBe('全部欄位')
    expect(fieldTitle(person, 'relationship')).toBe('關係')
    expect(fieldTitle(person, 'gone')).toBe('gone')
  })
})

describe('restoreFieldSearch', () => {
  it('格式不對時回到空狀態，壞掉的條件丟掉', () => {
    expect(restoreFieldSearch(null)).toEqual(EMPTY_FIELD_SEARCH)
    expect(restoreFieldSearch({ handler: 3 })).toEqual(EMPTY_FIELD_SEARCH)
    expect(restoreFieldSearch({ handler: 'person' })).toEqual({ handler: 'person', conditions: [] })
    expect(
      restoreFieldSearch({ handler: 'person', conditions: [{ id: 1, field: 'a', value: 'x' }, { id: 'x' }, null] }),
    ).toEqual({ handler: 'person', conditions: [{ id: 1, field: 'a', value: 'x' }] })
  })
})
