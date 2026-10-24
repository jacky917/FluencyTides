import { describe, expect, it } from 'vitest'
import { inputValue, itemErrors, itemFields, itemSummary, newItem } from './jsonItemForm'

/** 後端 Followup 模型產生的 schema（節錄自 Pydantic 的 model_json_schema）。 */
const FOLLOWUP = {
  properties: {
    item: { minLength: 1, title: '內容', type: 'string' },
    type: {
      default: 'ask',
      description: 'ask：下次要問；my_promise：我答應的；their_promise：對方答應的',
      enum: ['ask', 'my_promise', 'their_promise'],
      title: '種類',
      type: 'string',
    },
    added: { default: '', pattern: '^$|^\\d{4}-\\d{2}-\\d{2}$', title: '記下的日期', type: 'string' },
  },
  required: ['item'],
  type: 'object',
}

const FAMILY = {
  properties: {
    relation: { minLength: 1, title: '關係', type: 'string' },
    birth_year: { anyOf: [{ maximum: 2100, minimum: 1900, type: 'integer' }, { type: 'null' }], default: null, title: '出生年' },
    note: { default: '', title: '備註', type: 'string', 'x-ui': 'textarea' },
  },
  required: ['relation'],
}

const KEY_DATE = {
  properties: {
    label: { title: '名稱', type: 'string' },
    yearly: { default: false, title: '每年', type: 'boolean' },
  },
}

describe('itemFields', () => {
  it('maps schema types to controls', () => {
    const fields = itemFields(FOLLOWUP)
    expect(fields.map((f) => [f.key, f.kind, f.required])).toEqual([
      ['item', 'text', true],
      ['type', 'select', false],
      ['added', 'text', false],
    ])
    expect(fields[1].options).toEqual([
      { value: 'ask', label: '下次要問' },
      { value: 'my_promise', label: '我答應的' },
      { value: 'their_promise', label: '對方答應的' },
    ])
  })

  it('handles nullable numbers, textareas and booleans', () => {
    const family = itemFields(FAMILY)
    expect(family[1]).toMatchObject({ key: 'birth_year', kind: 'number', nullable: true, title: '出生年' })
    expect(family[2].kind).toBe('textarea')
    expect(itemFields(KEY_DATE)[1].kind).toBe('boolean')
  })
})

describe('newItem', () => {
  it('uses schema defaults', () => {
    expect(newItem(FOLLOWUP)).toEqual({ item: '', type: 'ask', added: '' })
    expect(newItem(FAMILY)).toEqual({ relation: '', birth_year: null, note: '' })
    expect(newItem(KEY_DATE)).toEqual({ label: '', yearly: false })
  })
})

describe('inputValue', () => {
  const [, birthYear] = itemFields(FAMILY)
  it('turns numbers into numbers and blanks into null', () => {
    expect(inputValue(birthYear, '2021')).toBe(2021)
    expect(inputValue(birthYear, ' ')).toBeNull()
    expect(inputValue(birthYear, '二〇二一')).toBe('二〇二一')
  })
})

describe('itemErrors', () => {
  it('maps 422 locations to items and fields', () => {
    const errors = itemErrors([
      { loc: ['body', 'items', 1, 'type'], msg: 'bad type' },
      { loc: ['body', 'items', 1, 'item'], msg: 'too short' },
      { loc: ['body', 'items', 0], msg: 'not an object' },
      { loc: ['body', 'version'], msg: 'missing' },
    ])
    expect(errors.get(1)?.get('type')).toBe('bad type')
    expect(errors.get(1)?.get('item')).toBe('too short')
    expect(errors.get(0)?.get('_')).toBe('not an object')
    expect(errors.size).toBe(2)
  })

  it('ignores non-array detail', () => {
    expect(itemErrors('oops').size).toBe(0)
  })
})

describe('itemSummary', () => {
  it('uses the first non-empty text field', () => {
    const fields = itemFields(FAMILY)
    expect(itemSummary({ relation: ' 妻子 ', note: 'x' }, fields)).toBe('妻子')
    expect(itemSummary({ relation: '' }, fields)).toBe('')
  })
})
