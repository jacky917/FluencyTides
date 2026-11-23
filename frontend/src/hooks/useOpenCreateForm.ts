import { useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { createFormPath, readRememberedSearch } from '../lib/cardBrowse'
import { CREATE_HANDLER_KEY } from '../lib/createStorage'
import { writeStored } from './useStoredState'

/**
 * 從別處打開卡片頁的建卡畫面並選好卡片類型（`docs/wip/retire_generate_page_FEAT_2026-11-23.md` D3）：草稿通知的
 * 「前往」、AI 整理對話框的「前往那份草稿」用它。已經在卡片頁時留在目前的牌組，否則回到上次在卡片頁看的位置。
 * Opens the card page's create form on a card type, from anywhere.
 * @returns 打開建卡畫面的函式，參數是卡片類型。Opens the form for a handler.
 */
export function useOpenCreateForm(): (handler: string) => void {
  const navigate = useNavigate()
  return useCallback(
    (handler: string) => {
      writeStored('local', CREATE_HANDLER_KEY, handler)
      // 點的當下才讀網址：通知掛在版面層級，不必跟著每次換頁重畫
      const { pathname, search } = window.location
      const path = createFormPath(pathname, search, readRememberedSearch())
      // 已經在同一個網址（建卡畫面開著）時不再多一筆歷史
      navigate(path, { replace: path === pathname + search })
    },
    [navigate],
  )
}
