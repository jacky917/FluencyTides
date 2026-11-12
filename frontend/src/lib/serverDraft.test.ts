import { describe, expect, it } from 'vitest'
import type { ServerDraft } from '../types/api'
import {
  classifySaveError,
  draftFromError,
  draftNotice,
  duplicateFromError,
  errorCode,
  initialEditState,
  inputAsValues,
  isInProgress,
  pollInterval,
  rebaseDecision,
  shouldRemember,
} from './serverDraft'

function draft(overrides: Partial<ServerDraft> = {}): ServerDraft {
  return {
    id: 'd1',
    handler: 'speaking_trilingual',
    status: 'ready',
    input: { background: '剪髮' },
    result: { params: {}, suggested_tags: [] },
    state: null,
    error: null,
    version: 3,
    created_at: '2026-11-07T09:00:00Z',
    updated_at: '2026-11-07T09:00:30Z',
    generation_started_at: '2026-11-07T09:00:00Z',
    ...overrides,
  }
}

const placeholder = () =>
  draft({ id: 'f1', handler: 'vocabulary_mining', status: 'form_creating', input: null, result: null, generation_started_at: null })

describe('isInProgress / pollInterval', () => {
  it('生成中、建立中、填表佔用才輪詢', () => {
    expect(isInProgress('generating')).toBe(true)
    expect(isInProgress('creating')).toBe(true)
    expect(isInProgress('form_creating')).toBe(true)
    expect(isInProgress('ready')).toBe(false)
    expect(isInProgress('failed')).toBe(false)
    expect(pollInterval(draft({ status: 'generating' }), 3000)).toBe(3000)
    expect(pollInterval(draft({ status: 'ready' }), 3000)).toBe(false)
    expect(pollInterval(null, 3000)).toBe(false)
    expect(pollInterval(undefined, 3000)).toBe(false)
  })
})

describe('shouldRemember', () => {
  it('只記生成草稿，填表佔用不記', () => {
    expect(shouldRemember(draft())).toBe(true)
    expect(shouldRemember(draft({ status: 'generating' }))).toBe(true)
    expect(shouldRemember(placeholder())).toBe(false)
    expect(shouldRemember(null)).toBe(false)
  })
})

describe('draftFromError', () => {
  it('草稿狀態的 409 取出附上的草稿', () => {
    const latest = draft({ version: 4 })
    for (const code of ['CREATE_SESSION_BUSY', 'DRAFT_BUSY', 'DRAFT_VERSION_CONFLICT', 'DRAFT_NOT_FAILED']) {
      expect(draftFromError({ error_code: code, message: 'x', draft: latest })).toBe(latest)
    }
  })

  it('草稿已不在是 null；附草稿的錯誤碼卻沒附草稿也當成已不在', () => {
    expect(draftFromError({ error_code: 'DRAFT_NOT_FOUND', message: 'x' })).toBeNull()
    expect(draftFromError({ error_code: 'DRAFT_BUSY', message: 'x' })).toBeNull()
  })

  it('其他錯誤是 undefined', () => {
    expect(draftFromError({ error_code: 'DUPLICATE_CARD', message: 'x' })).toBeUndefined()
    expect(draftFromError(new Error('network'))).toBeUndefined()
    expect(draftFromError(null)).toBeUndefined()
  })

  it('duplicateFromError：生成前發現重複時附上的舊卡', () => {
    const card = { note_id: 20, correct_answer: '修正' }
    expect(duplicateFromError({ error_code: 'DUPLICATE_FOUND', status: 409, duplicate: card })).toBe(card)
    // 沒附舊卡也讓使用者決定
    expect(duplicateFromError({ error_code: 'DUPLICATE_FOUND', status: 409 })).toEqual({ note_id: 0 })
    expect(duplicateFromError({ error_code: 'DUPLICATE_CARD', status: 409 })).toBeNull()
    expect(duplicateFromError(new Error('x'))).toBeNull()
    // 不是草稿狀態的錯誤：draftFromError 不會把它當成草稿已不在
    expect(draftFromError({ error_code: 'DUPLICATE_FOUND', duplicate: card })).toBeUndefined()
  })

  it('errorCode', () => {
    expect(errorCode({ error_code: 'DRAFT_BUSY' })).toBe('DRAFT_BUSY')
    expect(errorCode('x')).toBeUndefined()
  })
})

describe('classifySaveError', () => {
  it('版本衝突與忙碌附上最新的草稿', () => {
    const latest = draft({ version: 5 })
    expect(classifySaveError({ error_code: 'DRAFT_VERSION_CONFLICT', status: 409, draft: latest })).toEqual({
      kind: 'conflict',
      latest,
    })
    expect(classifySaveError({ error_code: 'DRAFT_BUSY', status: 409, draft: latest })).toEqual({ kind: 'busy', latest })
  })

  it('草稿已不在', () => {
    expect(classifySaveError({ error_code: 'DRAFT_NOT_FOUND', status: 404 })).toEqual({ kind: 'gone' })
  })

  it('沒有回應、5xx 是暫時的，會重送', () => {
    expect(classifySaveError(new Error('Network Error'))).toEqual({ kind: 'transient' })
    expect(classifySaveError({ response: { status: 502 } })).toEqual({ kind: 'transient' })
    expect(classifySaveError({ error_code: 'INTERNAL_SERVER_ERROR', status: 500 })).toEqual({ kind: 'transient' })
  })

  it('其他 4xx（例如內容太大、未授權）重送也不會成功', () => {
    expect(classifySaveError({ error_code: 'DRAFT_STATE_TOO_LARGE', status: 413 })).toEqual({ kind: 'rejected' })
    expect(classifySaveError({ response: { status: 401 } })).toEqual({ kind: 'rejected' })
  })
})

describe('initialEditState', () => {
  const fallback = () => ({ chosen: [] as number[] })

  it('有備份就用備份，標成沒存，版本用備份依據的', () => {
    const result = initialEditState(draft({ state: { chosen: [1] } }), { version: 3, state: { chosen: [2] } }, fallback)
    expect(result).toEqual({ state: { chosen: [2] }, version: 3, dirty: true })
  })

  it('備份是舊版本的也不丟：用它的版本，送出時會走衝突讓使用者選', () => {
    const result = initialEditState(draft({ state: { chosen: [1] } }), { version: 2, state: { chosen: [2] } }, fallback)
    expect(result).toEqual({ state: { chosen: [2] }, version: 2, dirty: true })
  })

  it('沒有備份：用後端存的，再沒有用預設', () => {
    expect(initialEditState(draft({ state: { chosen: [1] } }), null, fallback)).toEqual({
      state: { chosen: [1] },
      version: 3,
      dirty: false,
    })
    expect(initialEditState(draft(), null, fallback)).toEqual({ state: { chosen: [] }, version: 3, dirty: false })
  })
})

describe('rebaseDecision', () => {
  const known = JSON.stringify({ chosen: [1] })

  it('版本沒有更新：不動', () => {
    expect(rebaseDecision(draft({ version: 3, state: { chosen: [9] } }), 3, known, true)).toBe('none')
  })

  it('內容沒變（只是建立中、退回讓版本加了）：只對齊版本，有沒存的修改也一樣', () => {
    expect(rebaseDecision(draft({ version: 5, state: { chosen: [1] } }), 3, known, true)).toBe('rebase')
    expect(rebaseDecision(draft({ version: 5, state: null }), 3, 'null', false)).toBe('rebase')
  })

  it('別處改了內容：沒有沒存的修改就採用，有的話進入衝突（不默默蓋掉）', () => {
    expect(rebaseDecision(draft({ version: 4, state: { chosen: [2] } }), 3, known, false)).toBe('adopt')
    expect(rebaseDecision(draft({ version: 4, state: { chosen: [2] } }), 3, known, true)).toBe('conflict')
  })
})

describe('draftNotice', () => {
  it('看到生成草稿就記住 ID', () => {
    expect(draftNotice(null, draft(), null)).toEqual({ remember: 'd1', notify: null })
    expect(draftNotice(null, draft(), 'd1')).toEqual({ remember: undefined, notify: null })
  })

  it('填表佔用不記住，結束後也不會誤報已不在', () => {
    const during = draftNotice(null, placeholder(), null)
    expect(during).toEqual({ remember: undefined, notify: null })
    expect(draftNotice({ id: 'f1', status: 'form_creating' }, null, null)).toEqual({ remember: undefined, notify: null })
  })

  it('後端沒有草稿、本機卻記得：通知已不在並忘掉', () => {
    expect(draftNotice(null, null, 'd1')).toEqual({ remember: null, notify: 'lost' })
  })

  it('同一份從生成中變成可建立或失敗才通知', () => {
    const generating = { id: 'd1', status: 'generating' as const }
    expect(draftNotice(generating, draft(), 'd1').notify).toBe('ready')
    expect(draftNotice(generating, draft({ status: 'failed', result: null }), 'd1').notify).toBe('failed')
    expect(draftNotice(generating, draft({ status: 'generating' }), 'd1').notify).toBeNull()
    expect(draftNotice({ id: 'd1', status: 'ready' }, draft(), 'd1').notify).toBeNull()
    expect(draftNotice({ id: 'other', status: 'generating' }, draft(), 'd1').notify).toBeNull()
  })
})

describe('inputAsValues', () => {
  it('轉成字串、略過空值', () => {
    expect(inputAsValues({ background: '剪髮', count: 2, note: null })).toEqual({ background: '剪髮', count: '2' })
    expect(inputAsValues(null)).toEqual({})
  })
})
