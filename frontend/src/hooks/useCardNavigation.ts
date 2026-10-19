import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useLocation, useNavigate, useSearchParams, type NavigateOptions } from 'react-router-dom'
import { toast } from 'sonner'
import { FluencyTidesAPI } from '../api/client'
import { describeApiError, retryTransient } from '../lib/apiError'
import { BROWSE_PAGE_SIZE, parseBrowseParams, pickNoteCard, pushTrail, type CardSide } from '../lib/cardBrowse'

/** 跳轉時放進瀏覽器歷史的狀態：從哪張卡片跳過來。History state of a jump. */
interface JumpState {
  jumpFrom?: number
}

/**
 * 卡片瀏覽頁的導覽：狀態都在網址（見 `lib/cardBrowse.ts`），這裡提供讀取與各種改變的方式。
 * Navigation of the card browsing page; all state lives in the URL.
 *
 * - 選牌組、搜尋、換頁、從清單選卡：清空跳轉紀錄。
 * - 卡面按鈕打開其他筆記（`openNotes`）：查出卡片後切換過去，記下目前這張並新增一筆瀏覽器歷史，
 *   所以「返回」與瀏覽器的上一頁都能回來。查詢期間網址若變了（使用者另選了卡片），結果作廢。
 * - 翻面：取代目前的歷史並保留跳轉狀態，翻面後「返回」仍能直接退回上一頁。
 *
 * @returns 目前的狀態與改變它的函式。The state and the actions.
 */
export function useCardNavigation() {
  const [params, setParams] = useSearchParams()
  const state = parseBrowseParams(params)
  const { cardId, trail } = state
  const location = useLocation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  // 非同步完成時要拿最新的網址比對；跳轉中不再接受新的跳轉；同一筆歷史只退一次
  const latestParams = useRef(params.toString())
  const jumping = useRef(false)
  const backFrom = useRef<string | null>(null)
  useEffect(() => {
    latestParams.current = params.toString()
  })

  /** 改網址參數；值為 null 或空字串時移除。Update URL params; null or '' removes one. */
  const update = (changes: Record<string, string | null>, options?: NavigateOptions) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev)
      for (const [key, value] of Object.entries(changes)) {
        if (value === null || value === '') next.delete(key)
        else next.set(key, value)
      }
      return next
    }, options)

  /**
   * 卡面要打開這些筆記：找出它們的卡片，切換過去並記下目前這張。
   * Opens the notes a card face asks for, remembering the current card.
   */
  const openNotes = async (noteIds: number[]) => {
    if (cardId === null || jumping.current) return
    jumping.current = true
    const startedAt = latestParams.current
    try {
      const result = await queryClient.fetchQuery({
        queryKey: ['note-cards', noteIds.join(',')],
        queryFn: () => FluencyTidesAPI.browseCards({ q: `nid:${noteIds.join(',')}`, pageSize: BROWSE_PAGE_SIZE }),
        staleTime: 30_000,
        retry: retryTransient,
      })
      if (latestParams.current !== startedAt) return
      const target = pickNoteCard(result.cards, noteIds)
      if (target === null) {
        toast.error('找不到這張筆記', { description: `筆記 ${noteIds.join('、')} 不存在，可能已被刪除。` })
      } else if (target !== cardId) {
        const jump: JumpState = { jumpFrom: cardId }
        update({ card: String(target), trail: pushTrail(trail, cardId) }, { state: jump })
      }
    } catch (error) {
      if (latestParams.current === startedAt) {
        toast.error('無法打開這張筆記', { description: describeApiError(error).message })
      }
    } finally {
      jumping.current = false
    }
  }

  /**
   * 回到跳轉前的卡片：上一筆瀏覽器歷史就是跳轉前的狀態時直接返回（前進也能用），否則（例如
   * 重新整理或從連結打開）改網址。
   * Goes back to the card before the last jump.
   */
  const goBack = () => {
    const previous = trail[trail.length - 1]
    if (previous === undefined || backFrom.current === location.key) return
    backFrom.current = location.key
    if ((location.state as JumpState | null)?.jumpFrom === previous) {
      navigate(-1)
    } else {
      update({ card: String(previous), trail: trail.slice(0, -1).join(',') }, { replace: true })
    }
  }

  return {
    ...state,
    selectDeck: (deck: string | null) => update({ deck, page: null, card: null, trail: null }),
    submitSearch: (q: string) => update({ q, page: null, card: null, trail: null }),
    setPage: (page: number) => update({ page: page > 1 ? String(page) : null }),
    selectCard: (id: number) => update({ card: String(id), trail: null }),
    clearCard: () => update({ card: null, trail: null }),
    setSide: (side: CardSide) =>
      update({ side: side === 'answer' ? side : null }, { replace: true, state: location.state }),
    openNotes,
    goBack,
  }
}
