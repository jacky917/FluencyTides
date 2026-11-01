import { useCallback, useMemo, useSyncExternalStore } from 'react'

/**
 * 存在瀏覽器儲存區的 React 狀態，localStorage 與 sessionStorage 共用同一個核心。
 * React state kept in browser storage; one core for localStorage and sessionStorage.
 *
 * - `useLocalStorage`：永久保存、跨分頁同步（例如選的牌組、卡片類型、版面設定）。
 * - `useSessionStorage`：只在這個分頁，切換頁面、重新整理都還在，關掉分頁才消失（例如建卡頁填到一半的
 *   內容與 LLM 草稿：兩個分頁各填各的，不會互相覆蓋）。
 * - 同一分頁裡用到同一個鍵的元件即時同步；元件卸載後也能用 {@link writeStored}、{@link removeStored}
 *   寫入（例如生成中離開頁面，草稿完成時照樣存下）。
 * - 儲存區不能用（隱私模式、容量滿）時退回記憶體，這次開啟期間照常運作，只是不會留下來。
 */

/** 儲存區。The storage area. */
export type StorageArea = 'local' | 'session'

const listeners = new Map<string, Set<() => void>>()
/** 寫不進儲存區的值（記憶體備援）；undefined 表示沒有備援。Values that could not be persisted. */
const fallback = new Map<string, string | null>()

const slot = (area: StorageArea, key: string) => `${area}:${key}`

function storageOf(area: StorageArea): Storage {
  return area === 'local' ? window.localStorage : window.sessionStorage
}

function notify(area: StorageArea, key: string): void {
  listeners.get(slot(area, key))?.forEach((listener) => listener())
}

/** 讀出原始字串；沒有存值時為 null。The raw stored string, or null. */
function readRaw(area: StorageArea, key: string): string | null {
  const id = slot(area, key)
  if (fallback.has(id)) return fallback.get(id) ?? null
  try {
    return storageOf(area).getItem(key)
  } catch {
    return null
  }
}

function writeRaw(area: StorageArea, key: string, raw: string | null): void {
  const id = slot(area, key)
  try {
    if (raw === null) storageOf(area).removeItem(key)
    else storageOf(area).setItem(key, raw)
    fallback.delete(id)
  } catch (error) {
    console.warn(`無法寫入 ${area}Storage「${key}」，改存在記憶體：`, error)
    fallback.set(id, raw)
  }
  notify(area, key)
}

/** 解析存的值；沒有或格式壞掉時用預設值。Parses the stored value, or the fallback. */
export function parseStored<T>(raw: string | null, initialValue: T): T {
  if (raw === null) return initialValue
  try {
    return JSON.parse(raw) as T
  } catch {
    return initialValue
  }
}

/**
 * 讀一個鍵（不需要元件）。Reads a key outside React.
 * @param area - 儲存區。The storage area.
 * @param key - 鍵。The key.
 * @param initialValue - 沒有存值或格式壞掉時的值。The fallback.
 * @returns 存的值。The stored value.
 */
export function readStored<T>(area: StorageArea, key: string, initialValue: T): T {
  return parseStored(readRaw(area, key), initialValue)
}

/**
 * 寫入一個鍵並通知用到它的元件（不需要元件掛著）。Writes a key and notifies subscribers.
 * @param area - 儲存區。The storage area.
 * @param key - 鍵。The key.
 * @param value - 值（以 JSON 存）。The value, stored as JSON.
 */
export function writeStored<T>(area: StorageArea, key: string, value: T): void {
  writeRaw(area, key, JSON.stringify(value))
}

/**
 * 刪除一個鍵（用到它的元件回到預設值）。Removes a key; subscribers fall back to their initial value.
 * @param area - 儲存區。The storage area.
 * @param key - 鍵。The key.
 */
export function removeStored(area: StorageArea, key: string): void {
  writeRaw(area, key, null)
}

// 其他分頁改了 localStorage：瀏覽器只通知其他分頁，同一分頁的寫入由 writeRaw 自己通知
if (typeof window !== 'undefined') {
  window.addEventListener('storage', (event) => {
    if (event.key === null || event.storageArea !== window.localStorage) return
    fallback.delete(slot('local', event.key))
    notify('local', event.key)
  })
}

/**
 * 讀寫儲存區的一個鍵。Reads and writes one stored key.
 * @param area - 儲存區。The storage area.
 * @param key - 鍵。The key.
 * @param initialValue - 沒有存值時的值。The value when nothing is stored.
 * @returns [目前值, 設定函數]；設定函數也接受 `(prev) => next`。The value and setter.
 */
export function useStoredState<T>(area: StorageArea, key: string, initialValue: T) {
  const subscribe = useCallback(
    (listener: () => void) => {
      const id = slot(area, key)
      const set = listeners.get(id) ?? new Set()
      set.add(listener)
      listeners.set(id, set)
      return () => {
        set.delete(listener)
      }
    },
    [area, key],
  )
  // 快照用原始字串：內容沒變就是同一個值，React 不會重繪
  const raw = useSyncExternalStore(subscribe, () => readRaw(area, key))
  // initialValue 常是每次 render 新建的物件；只在存值或鍵改變時重新解析
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const value = useMemo(() => parseStored(raw, initialValue), [raw, area, key])
  const setValue = useCallback(
    (next: T | ((prev: T) => T)) => {
      const resolved = next instanceof Function ? next(readStored(area, key, initialValue)) : next
      writeStored(area, key, resolved)
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [area, key],
  )
  return [value, setValue] as const
}

/**
 * localStorage 的狀態：永久保存、跨分頁同步。State in localStorage, synced across tabs.
 * @param key - 鍵。The key.
 * @param initialValue - 沒有存值時的值。The value when nothing is stored.
 * @returns [目前值, 設定函數]。The value and setter.
 */
export function useLocalStorage<T>(key: string, initialValue: T) {
  return useStoredState('local', key, initialValue)
}

/**
 * sessionStorage 的狀態：只在這個分頁，關掉分頁才消失。State in sessionStorage, per tab.
 * @param key - 鍵。The key.
 * @param initialValue - 沒有存值時的值。The value when nothing is stored.
 * @returns [目前值, 設定函數]。The value and setter.
 */
export function useSessionStorage<T>(key: string, initialValue: T) {
  return useStoredState('session', key, initialValue)
}
