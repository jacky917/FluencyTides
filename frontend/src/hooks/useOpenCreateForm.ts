import { useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { createFormPath, isCreateFormOpen, readRememberedSearch } from '../lib/cardBrowse'
import { CREATE_HANDLER_KEY } from '../lib/createStorage'
import { writeStored } from './useStoredState'

/**
 * 從別處打開卡片頁的建卡畫面並選好卡片類型（`docs/wip/retire_generate_page_FEAT_2026-11-23.md` D3）：草稿通知的
 * 「前往」、AI 整理對話框的「前往那份草稿」用它。已經在卡片頁時留在目前的位置，否則回到上次在卡片頁看的位置。
 * Opens the card page's create form on a card type, from anywhere.
 * @returns 打開建卡畫面的函式，參數是卡片類型。Opens the form for a handler.
 */
export function useOpenCreateForm(): (handler: string) => void {
  const navigate = useNavigate()
  return useCallback(
    (handler: string) => {
      writeStored('local', CREATE_HANDLER_KEY, handler)
      // 讀點的當下的網址，不是 render 時的網址
      const { pathname, search } = window.location
      // 建卡畫面已經開著：卡片類型上面已經寫入，建卡畫面經 storage 通知自己切過去。不能再導頁——
      // 取代這筆歷史會蓋掉「＋」打開時留下的 createOpened，之後按 ✕ 就退不回原本的卡片
      if (isCreateFormOpen(pathname, search)) return
      navigate(createFormPath(pathname, search, readRememberedSearch()))
    },
    [navigate],
  )
}
