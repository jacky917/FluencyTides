import { describe, expect, it } from 'vitest'
import { deckBlockedReason, deckBlocksForm, formErrorsFrom, initialValues, missingRequired, schemaFields } from './createForm'

/** 後端標準建立產生的 schema（向 Anki 查欄位，第一個欄位必填）。 */
const standardSchema = {
  type: 'object',
  properties: {
    Expression: { type: 'string', title: 'Expression' },
    Target_Translation: { type: 'string', title: 'Target_Translation', default: '' },
  },
  required: ['Expression'],
  additionalProperties: true,
}

/** 對話卡的 CreateParams（Pydantic 的 model_json_schema）。 */
const speakingSchema = {
  properties: {
    prompt: { type: 'string', title: '目標台詞', description: '口說情境對白的目標台詞', 'x-ui': 'textarea' },
    context: { type: 'string', title: '情境', default: '', 'x-ui': 'rich' },
    target_language: {
      type: 'string', title: '目標語言', default: 'en-US', pattern: '^[A-Za-z]{2,3}(-[A-Za-z]{2})?$',
      examples: ['en-US', 'ja-JP', 'zh-TW'],
    },
    llm_result: { anyOf: [{ type: 'object' }, { type: 'null' }] },
    note: { anyOf: [{ type: 'string' }, { type: 'null' }] },
  },
  required: ['prompt'],
}

describe('schemaFields', () => {
  it('reads the standard schema in order with the first field required', () => {
    const { fields, skipped } = schemaFields(standardSchema)
    expect(fields.map((f) => [f.name, f.required])).toEqual([
      ['Expression', true],
      ['Target_Translation', false],
    ])
    expect(skipped).toEqual([])
  })

  it('reads titles, descriptions, defaults, examples, patterns and x-ui', () => {
    const { fields, skipped } = schemaFields(speakingSchema)
    const [prompt, context, language, note] = fields
    expect(prompt).toMatchObject({ title: '目標台詞', description: '口說情境對白的目標台詞', required: true, multiline: true })
    expect(context.multiline).toBe(false) // 不認得的 x-ui 當一般文字欄位
    expect(language).toMatchObject({ defaultValue: 'en-US', examples: ['en-US', 'ja-JP', 'zh-TW'] })
    expect(language.pattern).toBe('^[A-Za-z]{2,3}(-[A-Za-z]{2})?$')
    expect(note.name).toBe('note') // 可為 null 的字串也畫
    expect(skipped).toEqual(['llm_result'])
  })

  it('reads enum as select options (the trilingual draft audience)', () => {
    const { fields } = schemaFields({
      properties: {
        audience: { type: 'string', title: '對象', default: '不指定', enum: ['不指定', '同事', '朋友'] },
        audience_note: { type: 'string', title: '對象補充', default: '' },
      },
    })
    expect(fields[0]).toMatchObject({ defaultValue: '不指定', options: ['不指定', '同事', '朋友'] })
    expect(fields[1].options).toEqual([])
  })

  it('has no fields without a schema', () => {
    expect(schemaFields(null)).toEqual({ fields: [], skipped: [] })
  })
})

describe('values', () => {
  const { fields } = schemaFields(speakingSchema)

  it('starts from the defaults', () => {
    expect(initialValues(fields)).toEqual({ prompt: '', context: '', target_language: 'en-US', note: '' })
  })

  it('lists required fields that are blank', () => {
    expect(missingRequired(fields, { prompt: '  ' })).toEqual(['prompt'])
    expect(missingRequired(fields, { prompt: 'hi' })).toEqual([])
  })
})

describe('formErrorsFrom', () => {
  const error422 = (detail: unknown) => ({ response: { status: 422, data: { detail } } })

  it('maps body.params.<field> onto the field and keeps the rest', () => {
    const errors = formErrorsFrom(
      error422([
        { loc: ['body', 'params', 'Expression'], msg: 'String should have at least 1 character' },
        { loc: ['body', 'params', 'Expression'], msg: 'second' },
        { loc: ['body', 'params', 'Unknown'], msg: 'x' },
        { loc: ['body', 'deck'], msg: 'Field required' },
      ]),
      ['Expression'],
    )
    expect(errors).toEqual({
      fields: { Expression: 'String should have at least 1 character；second' },
      other: ['Unknown: x', 'Field required'],
    })
  })

  it('ignores errors that are not the standard 422', () => {
    expect(formErrorsFrom({ error_code: 'NOTE_TYPE_NOT_CREATABLE', status: 422, message: 'x' }, ['A'])).toBeNull()
    expect(formErrorsFrom({ response: { status: 500, data: {} } }, ['A'])).toBeNull()
    expect(formErrorsFrom(null, ['A'])).toBeNull()
  })
})

describe('deckBlockedReason', () => {
  it('固定牌組或牌組存在時不擋', () => {
    expect(deckBlockedReason('固定', null, 'all')).toBeNull()
    expect(deckBlockedReason(null, 'Default', 'ok')).toBeNull()
  })

  it('說明牌組為什麼擋住建立', () => {
    expect(deckBlockedReason(null, null, 'all')).toContain('還沒選牌組')
    expect(deckBlockedReason(null, 'Default', 'loading')).toContain('正在確認')
    expect(deckBlockedReason(null, 'Default', 'error')).toContain('無法載入牌組清單')
    expect(deckBlockedReason(null, 'Old', 'missing')).toContain('「Old」已不存在')
  })
})

describe('deckBlocksForm', () => {
  it('沒選、已不存在、清單載入失敗時擋住；載入中與固定牌組不擋', () => {
    expect(deckBlocksForm(null, null, 'all')).toBe(true)
    expect(deckBlocksForm(null, 'Old', 'missing')).toBe(true)
    expect(deckBlocksForm(null, 'Default', 'error')).toBe(true)
    expect(deckBlocksForm(null, 'Default', 'loading')).toBe(false)
    expect(deckBlocksForm(null, 'Default', 'ok')).toBe(false)
    expect(deckBlocksForm('固定', null, 'all')).toBe(false)
  })
})
