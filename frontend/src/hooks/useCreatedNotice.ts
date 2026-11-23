import type { CreateNoteResponse } from '../types/api'
import { createdNoticeKey } from '../lib/createStorage'
import { removeStored, useSessionStorage, writeStored } from './useStoredState'

/**
 * 建卡表單「建立成功」提示的內容，存在這個分頁的 sessionStorage（`create_created:{handler}`）：面板因切換
 * 版面而重新掛載、或請求在面板卸載後才完成，提示都還在（提示上有「查看新卡片」）。
 * The result shown in a create form's success notice, kept in sessionStorage so
 * it survives remounts.
 * @param handlerName - 卡片類型。The handler name.
 * @returns 目前的結果與設定它的函式（null 清掉提示；元件卸載後呼叫也有效）。The result and its setter.
 */
export function useCreatedNotice(handlerName: string) {
  const key = createdNoticeKey(handlerName)
  const [stored] = useSessionStorage<CreateNoteResponse | null>(key, null)
  const setResult = (value: CreateNoteResponse | null) => {
    if (value === null) removeStored('session', key)
    else writeStored('session', key, value)
  }
  return [stored, setResult] as const
}
