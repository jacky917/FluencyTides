/**
 * 全站的建卡草稿通知（`docs/wip/server_drafts_FEAT_2026-11-04.md` D9、D11），掛在版面層級、不畫任何東西。
 *
 * - 讀目前的草稿：打開網站、回到分頁時各讀一次，進行中（生成中、建立中）時每 10 秒讀一次；在建卡畫面時
 *   那邊每 3 秒讀一次，共用同一份快取。從生成中變成可建立或失敗時跳通知，附「前往」（到 Card Generator
 *   並選好那種卡片）。
 * - 記住看過的生成草稿 ID（localStorage，同一個瀏覽器的分頁共用）。後端說沒有草稿、這裡卻還記得時，表示
 *   草稿在別處建立或廢棄，或伺服器重新啟動過（草稿只存在記憶體），提示一次並忘掉。這個瀏覽器自己建立、
 *   廢棄時已先忘掉；填表建卡的佔用不記。判斷在 `draftNotice`（`lib/serverDraft.ts`）。
 *
 * Site-wide notices for the create draft; renders nothing.
 */

import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { useCurrentDraft } from '../../hooks/useServerDraft'
import { readStored, removeStored, writeStored } from '../../hooks/useStoredState'
import { CURRENT_DRAFT_ID_KEY, draftNotice } from '../../lib/serverDraft'
import type { HandlerInfo, ServerDraft } from '../../types/api'

/** 進行中時的輪詢間隔。Polling interval while in progress. */
const WATCH_POLL_MS = 10_000

/**
 * 全站的草稿通知。Site-wide draft notices.
 * @returns null。
 */
export function DraftWatcher() {
  const { data } = useCurrentDraft(WATCH_POLL_MS)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const previous = useRef<Pick<ServerDraft, 'id' | 'status'> | null>(null)

  useEffect(() => {
    if (data === undefined) return
    const before = previous.current
    previous.current = data ? { id: data.id, status: data.status } : null

    const { remember, notify } = draftNotice(before, data, readStored<string | null>('local', CURRENT_DRAFT_ID_KEY, null))
    if (remember === null) removeStored('local', CURRENT_DRAFT_ID_KEY)
    else if (remember !== undefined) writeStored('local', CURRENT_DRAFT_ID_KEY, remember)

    if (notify === 'lost') {
      toast.info('上一份建卡草稿已不在', {
        description: '可能已在其他地方建立或廢棄，或伺服器重新啟動過。生成前的輸入還在，可以重新生成。',
      })
      return
    }
    if (!notify || !data) return
    const handlers = queryClient.getQueryData<HandlerInfo[]>(['handlers'])
    const title = handlers?.find((handler) => handler.name === data.handler)?.title ?? '建卡'
    const action = {
      label: '前往',
      onClick: () => {
        writeStored('local', 'generate_handler', data.handler)
        navigate('/generate')
      },
    }
    if (notify === 'ready') {
      toast.success(`${title}草稿已生成`, { description: '回到建卡畫面勾選後建立', action })
    } else {
      toast.error(`${title}草稿生成失敗`, { description: data.error?.message, action })
    }
  }, [data, navigate, queryClient])

  return null
}
