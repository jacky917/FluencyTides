import { useCallback, useMemo, useSyncExternalStore } from 'react'

/**
 * 存在 sessionStorage 的狀態：切換頁面、重新整理都還在，關掉分頁才消失；各分頁各自一份（不像
 * `useLocalStorage` 會跨分頁同步）。元件卸載後也能用 {@link writeSession} 寫入，下次掛載或仍掛載的
 * 元件會立刻看到——建卡頁用它保存表單與 LLM 草稿，生成中離開頁面，結果回來時照樣存下。
 *
 * State kept in sessionStorage: survives page switches and reloads, per tab.
 * {@link writeSession} works without a mounted component, and mounted ones
 * see the change immediately.
 */

const listeners = new Map<string, Set<() => void>>()

/** 讀出原始字串；讀不到（隱私模式等）時為 null。The raw stored string, or null. */
function readRaw(key: string): string | null {
  try {
    return window.sessionStorage.getItem(key)
  } catch {
    return null
  }
}

/** 解析存的值；沒有或格式壞掉時用預設值。Parses the stored value, or the fallback. */
function parse<T>(raw: string | null, fallback: T): T {
  if (raw === null) return fallback
  try {
    return JSON.parse(raw) as T
  } catch {
    return fallback
  }
}

/**
 * 寫入（null 為刪除）並通知用到這個鍵的元件。Writes (null removes) and notifies subscribers.
 * @param key - sessionStorage 的鍵。The key.
 * @param value - 值；null 時刪除。The value, or null to remove.
 */
export function writeSession<T>(key: string, value: T | null): void {
  try {
    if (value === null) window.sessionStorage.removeItem(key)
    else window.sessionStorage.setItem(key, JSON.stringify(value))
  } catch (error) {
    console.warn(`無法寫入 sessionStorage「${key}」：`, error)
  }
  listeners.get(key)?.forEach((listener) => listener())
}

/**
 * 讀寫 sessionStorage 的一個鍵。Reads and writes one sessionStorage key.
 * @param key - 鍵。The key.
 * @param initialValue - 沒有存值時的值（每次呼叫應是同一個值或等值）。The fallback.
 * @returns [目前值, 設定函數]；設定為 null 時刪除，回到預設值。The value and setter.
 */
export function useSessionStore<T>(key: string, initialValue: T) {
  const subscribe = useCallback(
    (listener: () => void) => {
      const set = listeners.get(key) ?? new Set()
      set.add(listener)
      listeners.set(key, set)
      return () => {
        set.delete(listener)
      }
    },
    [key],
  )
  // 快照用原始字串：同一份內容是同一個值，React 才不會一直重繪
  const raw = useSyncExternalStore(subscribe, () => readRaw(key))
  // initialValue 每次 render 可能是新物件；只在存值改變時重新解析
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const value = useMemo(() => parse(raw, initialValue), [raw])
  const setValue = useCallback(
    (next: T | null | ((prev: T) => T | null)) => {
      const resolved = next instanceof Function ? next(parse(readRaw(key), initialValue)) : next
      writeSession(key, resolved)
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [key],
  )
  return [value, setValue] as const
}
