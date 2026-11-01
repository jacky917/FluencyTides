import { beforeEach, describe, expect, it, vi } from 'vitest'

/** 記憶體版的 Storage；`failWrites` 時寫入拋錯（模擬隱私模式或容量滿）。 */
class FakeStorage {
  items = new Map<string, string>()
  failWrites = false
  getItem(key: string) {
    return this.items.get(key) ?? null
  }
  setItem(key: string, value: string) {
    if (this.failWrites) throw new Error('QuotaExceededError')
    this.items.set(key, value)
  }
  removeItem(key: string) {
    if (this.failWrites) throw new Error('QuotaExceededError')
    this.items.delete(key)
  }
}

const local = new FakeStorage()
const session = new FakeStorage()
// 測試環境沒有 DOM：模組載入前先放一個最小的 window
vi.stubGlobal('window', { localStorage: local, sessionStorage: session, addEventListener: vi.fn() })
const { parseStored, readStored, removeStored, writeStored } = await import('./useStoredState')

beforeEach(() => {
  local.items.clear()
  session.items.clear()
  local.failWrites = false
  session.failWrites = false
})

describe('stored state', () => {
  it('round-trips values as JSON in the chosen area only', () => {
    writeStored('session', 'draft', { prompt: 'p', count: 2 })
    expect(session.items.get('draft')).toBe('{"prompt":"p","count":2}')
    expect(local.items.has('draft')).toBe(false)
    expect(readStored('session', 'draft', null)).toEqual({ prompt: 'p', count: 2 })
  })

  it('keeps null as a value, and remove falls back to the initial value', () => {
    writeStored('local', 'deck', null)
    expect(readStored('local', 'deck', 'Default')).toBeNull()
    removeStored('local', 'deck')
    expect(readStored('local', 'deck', 'Default')).toBe('Default')
  })

  it('falls back to the initial value on corrupted data', () => {
    session.items.set('form', '{not json')
    expect(readStored('session', 'form', {})).toEqual({})
    expect(parseStored(null, 1)).toBe(1)
  })

  it('keeps working in memory when the storage refuses writes', () => {
    session.failWrites = true
    writeStored('session', 'form', { background: '半成品' })
    expect(readStored('session', 'form', {})).toEqual({ background: '半成品' })
    // 儲存區恢復後寫入成功，記憶體備援就不再用
    session.failWrites = false
    writeStored('session', 'form', { background: '新的' })
    expect(session.items.get('form')).toBe('{"background":"新的"}')
    expect(readStored('session', 'form', {})).toEqual({ background: '新的' })
  })
})
