import { describe, expect, it } from 'vitest'
import {
  addQuickPick,
  addTags,
  deckBlockedReason,
  deckBlocksForm,
  formErrorsFrom,
  formParams,
  hasInput,
  initialRows,
  initialValues,
  missingRequired,
  normalizeRows,
  schemaFields,
} from './createForm'

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

  it('分類節點和「已不存在」分開說明，並指出可以在牌組樹補建', () => {
    const reason = deckBlockedReason(null, 'FluencyTides::Speaking_Trilingual', 'category')
    expect(reason).toBe(
      '「FluencyTides::Speaking_Trilingual」在 Anki 裡不是牌組（只是分類），請選它底下的牌組，或在牌組樹按 ＋ 建立它。',
    )
    expect(reason).not.toContain('已不存在')
    expect(deckBlockedReason('固定', 'FluencyTides', 'category')).toBeNull()
  })
})

describe('deckBlocksForm', () => {
  it('沒選、只是分類、已不存在、清單載入失敗時擋住；載入中與固定牌組不擋', () => {
    expect(deckBlocksForm(null, null, 'all')).toBe(true)
    expect(deckBlocksForm(null, 'FluencyTides', 'category')).toBe(true)
    expect(deckBlocksForm(null, 'Old', 'missing')).toBe(true)
    expect(deckBlocksForm(null, 'Default', 'error')).toBe(true)
    expect(deckBlocksForm(null, 'Default', 'loading')).toBe(false)
    expect(deckBlocksForm(null, 'Default', 'ok')).toBe(false)
    expect(deckBlocksForm('固定', null, 'all')).toBe(false)
  })
})

describe('媒體欄位（x-ui: audio、image）', () => {
  // 單字卡的標準建立表單：媒體欄位照列，標成上傳元件（media_upload 計畫書 D3）
  const schema = {
    properties: {
      Expression: { type: 'string', title: 'Expression', minLength: 1 },
      Word_Audio: { type: 'string', title: 'Word_Audio', default: '', 'x-ui': 'audio' },
      Photo: { type: 'string', title: 'Photo', default: '', 'x-ui': 'image' },
      Note: { type: 'string', title: 'Note', default: '', 'x-ui': 'textarea' },
    },
    required: ['Expression'],
  }

  it('標出種類；其他欄位為 null', () => {
    const { fields, skipped } = schemaFields(schema)
    expect(skipped).toEqual([])
    expect(Object.fromEntries(fields.map((field) => [field.name, field.media]))).toEqual({
      Expression: null,
      Word_Audio: 'audio',
      Photo: 'image',
      Note: null,
    })
  })

  it('送出的是上傳取得的檔名；沒上傳時是空字串', () => {
    const { fields } = schemaFields(schema)
    const values = { ...initialValues(fields), Expression: 'defer', Word_Audio: 'web_audio_20261114_010203_0a1b2c.mp3' }
    expect(formParams(fields, values)).toEqual({
      Expression: 'defer',
      Word_Audio: 'web_audio_20261114_010203_0a1b2c.mp3',
      Photo: '',
      Note: '',
    })
  })
})

describe('一行一筆的陣列欄位（x-ui: lines）', () => {
  const schema = {
    properties: {
      prompt: { type: 'string', title: '目標台詞' },
      references: { type: 'array', items: { type: 'string' }, title: '參考回答', 'x-ui': 'lines', maxItems: 100 },
      tags: { type: 'array', items: { type: 'string' } },
    },
    required: ['prompt'],
  }

  it('畫成多行文字框；沒有 x-ui: lines 的陣列照樣略過', () => {
    const { fields, skipped } = schemaFields(schema)
    expect(fields.map((f) => f.name)).toEqual(['prompt', 'references'])
    expect(fields[1]).toMatchObject({ title: '參考回答', multiline: true, lines: true, defaultValue: '' })
    expect(fields[0].lines).toBe(false)
    expect(skipped).toEqual(['tags'])
  })

  it('送出時拆成陣列、去掉前後空白與空行，其餘欄位原樣', () => {
    const { fields } = schemaFields(schema)
    expect(formParams(fields, { prompt: ' Hi ', references: ' A \r\n\n  \nB\rC ' })).toEqual({
      prompt: ' Hi ',
      references: ['A', 'B', 'C'],
    })
    expect(formParams(fields, { prompt: 'x' })).toEqual({ prompt: 'x', references: [] })
  })

  it('陣列某一筆的 422 標在欄位上，註明第幾筆', () => {
    const error = {
      response: {
        status: 422,
        data: { detail: [{ loc: ['body', 'params', 'references', 1], msg: '太長' }] },
      },
    }
    expect(formErrorsFrom(error, ['prompt', 'references'])).toEqual({
      fields: { references: '第 2 筆：太長' },
      other: [],
    })
  })
})

/** 人脈卡建立參數的節錄（Pydantic 的 model_json_schema）：一列一筆的欄位。 */
const personSchema = {
  $defs: {
    PositionInput: {
      properties: { company: { title: '公司', type: 'string' }, title: { title: '職稱', type: 'string' } },
      type: 'object',
    },
  },
  properties: {
    Display_Name: { title: '稱呼', type: 'string', minLength: 1 },
    Residence: { default: '', title: '現居地', type: 'string' },
    Languages: {
      anyOf: [{ type: 'string' }, { items: { type: 'string' }, type: 'array' }],
      default: '',
      title: '語言',
      'x-ui': 'list',
      'x-item': 'tag',
      'x-quick': ['中文', '日文', '英文'],
    },
    Company: {
      anyOf: [{ type: 'string' }, { items: { $ref: '#/$defs/PositionInput' }, type: 'array' }],
      default: '',
      title: '職位',
      'x-ui': 'list',
    },
    Work_Detail: {
      anyOf: [{ type: 'string' }, { items: { type: 'string' }, type: 'array' }],
      default: '',
      title: '工作內容',
      'x-ui': 'list',
      'x-item': 'textarea',
    },
  },
  required: ['Display_Name'],
}

describe('rows fields', () => {
  const { fields } = schemaFields(personSchema)
  const byName = Object.fromEntries(fields.map((field) => [field.name, field]))

  it('reads list fields, their cells and quick picks', () => {
    expect(byName.Residence.rows).toBeNull()
    expect(byName.Languages.rows).toEqual({ cells: null, multiline: false, tags: true, quick: ['中文', '日文', '英文'] })
    expect(byName.Company.rows?.cells).toEqual([
      { key: 'company', title: '公司' },
      { key: 'title', title: '職稱' },
    ])
    expect(byName.Work_Detail.rows).toMatchObject({ multiline: true, tags: false })
  })

  it('starts list fields empty', () => {
    expect(initialRows(fields)).toEqual({ Languages: [], Company: [], Work_Detail: [] })
  })

  it('drops empty rows and stale keys when submitting', () => {
    expect(
      formParams(fields, {
        Title: '社長',
        Display_Name: '田中さん',
        Languages: ['中文', ' ', ''],
        Company: [{ company: 'Geekly', title: '' }, { company: ' ', title: '' }],
      }),
    ).toEqual({ Display_Name: '田中さん', Languages: ['中文'], Company: [{ company: 'Geekly', title: '' }] })
  })

  it('knows whether a value holds input', () => {
    expect(hasInput(undefined)).toBe(false)
    expect(hasInput(' ')).toBe(false)
    expect(hasInput([' ', ''])).toBe(false)
    expect(hasInput([{ company: '', title: ' ' }])).toBe(false)
    expect(hasInput([{ company: '', title: 'PM' }])).toBe(true)
    expect(missingRequired(fields, { Display_Name: '', Languages: ['中文'] })).toEqual(['Display_Name'])
  })

  it('turns text left by the old form into a visible row', () => {
    expect(normalizeRows(fields, { Languages: '中文、日文', Company: 'Geekly', Work_Detail: ' ', Residence: '東京' })).toEqual({
      Languages: ['中文', '日文'],
      Company: [{ company: 'Geekly', title: '' }],
      Work_Detail: [],
      Residence: '東京',
    })
  })

  it('adds a quick pick once, filling an empty row first', () => {
    expect(addQuickPick([], '中文')).toEqual(['中文'])
    expect(addQuickPick(['日文', ''], '中文')).toEqual(['日文', '中文'])
    expect(addQuickPick(['中文'], '中文')).toEqual(['中文'])
  })

  it('adds typed tags split like the backend, without duplicates', () => {
    expect(addTags([], ' 韓文 ')).toEqual(['韓文'])
    expect(addTags(['中文', ' '], '日文、韓文, 中文，法文')).toEqual(['中文', '日文', '韓文', '法文'])
    expect(addTags(['中文'], ' 、 ')).toEqual(['中文'])
  })

  it('maps row errors onto the field', () => {
    const error = {
      response: {
        status: 422,
        data: { detail: [{ loc: ['body', 'params', 'Company', 'list[PositionInput]', 0], msg: '公司與職稱至少要填一個' }] },
      },
    }
    expect(formErrorsFrom(error, ['Company'])?.fields.Company).toBe('第 1 筆：公司與職稱至少要填一個')
  })
})
