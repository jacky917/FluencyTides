import { useState } from 'react'
import type { CreateNoteResponse } from '../types/api'
import { removeStored, useSessionStorage, writeStored } from './useStoredState'

/**
 * 建卡表單「建立成功」提示的內容。
 * The result shown in a create form's success notice.
 *
 * `persist` 為 true 時存在這個分頁的 sessionStorage（`generate_created:{handler}`）：面板因切換版面而
 * 重新掛載、或請求在面板卸載後才完成，提示都還在（卡片瀏覽頁的建卡畫面，提示上有「查看新卡片」）。
 * 為 false 時只是元件狀態（Card Generator，行為與以前相同）。
 * With `persist`, the result lives in sessionStorage so it survives remounts.
 * @param handlerName - 卡片類型。The handler name.
 * @param persist - 是否存在 sessionStorage。Whether to keep it in sessionStorage.
 * @returns 目前的結果與設定它的函式（null 清掉提示；元件卸載後呼叫也有效）。The result and its setter.
 */
export function useCreatedNotice(handlerName: string, persist: boolean) {
  const key = `generate_created:${handlerName}`
  const [stored] = useSessionStorage<CreateNoteResponse | null>(key, null)
  const [local, setLocal] = useState<CreateNoteResponse | null>(null)
  const setResult = (value: CreateNoteResponse | null) => {
    if (!persist) setLocal(value)
    else if (value === null) removeStored('session', key)
    else writeStored('session', key, value)
  }
  return [persist ? stored : local, setResult] as const
}
