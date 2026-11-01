import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useLocation, useNavigate, useNavigationType, useSearchParams, type NavigateOptions } from 'react-router-dom'
import { toast } from 'sonner'
import { FluencyTidesAPI } from '../api/client'
import { describeApiError, retryTransient } from '../lib/apiError'
import {
  BROWSE_PAGE_SIZE,
  parseBrowseParams,
  pickNoteCard,
  pushTrail,
  searchToRemember,
  type CardSide,
} from '../lib/cardBrowse'
import { isBackNavigation, type BackState } from '../lib/cardFaces'

/** 記住瀏覽位置的 localStorage 鍵。localStorage key of the remembered position. */
const REMEMBERED_SEARCH_KEY = 'cards_last_search'

/** 讀出記住的位置；讀不到時為空字串。Reads the remembered position. */
function readRemembered(): string {
  try {
    return window.localStorage.getItem(REMEMBERED_SEARCH_KEY) ?? ''
  } catch {
    return ''
  }
}

/** 記下位置（存不了時略過）。Stores the position, ignoring failures. */
function writeRemembered(search: string): void {
  try {
    window.localStorage.setItem(REMEMBERED_SEARCH_KEY, search)
  } catch {
    // 只影響下次是否回到同一個位置
  }
}

/** 跳轉時放進瀏覽器歷史的狀態：從哪張卡片跳過來。History state of a jump. */
interface JumpState {
  jumpFrom?: number
}

/** react-router 在瀏覽器歷史裡記的位置（`history.state.idx`）。The history index react-router keeps. */
const historyIndex = (): unknown => (window.history.state as { idx?: unknown } | null)?.idx

/**
 * 卡片瀏覽頁的導覽：狀態都在網址（見 `lib/cardBrowse.ts`），這裡提供讀取與各種改變的方式。
 * Navigation of the card browsing page; all state lives in the URL.
 *
 * - 選牌組、搜尋、換頁、從清單選卡：清空跳轉紀錄。
 * - 卡面按鈕打開其他筆記（`openNotes`）：查出卡片後切換過去，記下目前這張並新增一筆瀏覽器歷史，
 *   所以「返回」與瀏覽器的上一頁都能回來。查詢期間網址若變了（使用者另選了卡片），結果作廢。
 * - 翻面：取代目前的歷史並保留跳轉狀態，翻面後「返回」仍能直接退回上一頁。
 * - 換卡（選卡、跳轉、返回）一律從正面開始；換卡前先把目前這筆歷史改成正面，之後用上一頁
 *   回到這張卡片時也是正面。
 * - `arrivedBack`：目前這個位置是不是往回到達的（「返回」或瀏覽器上一頁），換卡動畫據此倒著播。
 * - 記住位置：瀏覽位置（不含跳轉紀錄）存在 localStorage；從側邊欄回到這頁（網址沒有參數）時
 *   恢復上次的牌組、搜尋、頁碼與卡片。
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

  // 記住瀏覽位置。進來時網址沒有參數就恢復上次的位置；恢復完成（網址變成記下的那一串）之前
  // 不覆寫記錄，否則第一次渲染的空網址會先把記錄清掉。只在這個分頁的位置改變時寫入、不跟著
  // 其他分頁同步——兩個分頁停在不同位置時才不會互相覆寫個沒完
  const pendingRestore = useRef<string | null>(null)
  if (pendingRestore.current === null) {
    pendingRestore.current = params.toString() === '' ? readRemembered() : ''
  }
  useEffect(() => {
    if (pendingRestore.current) setParams(new URLSearchParams(pendingRestore.current), { replace: true })
    // 只在進入頁面時恢復一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const current = searchToRemember(params)
  useEffect(() => {
    if (pendingRestore.current) {
      if (current !== pendingRestore.current) return
      pendingRestore.current = ''
    }
    writeRemembered(current)
  }, [current])

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
   * 換到另一張卡片，從正面開始。目前在背面時，先把目前這筆歷史改成正面再新增一筆：之後按上一頁
   * 回到這張卡片也是正面。兩次導覽在同一個 tick，靠 React 18 的自動批次（含 await 之後的
   * openNotes）合併成一次重畫；若沒有合併，卡片面板會先看到同一張卡片翻回正面，多播一次翻面動畫。
   * Switches to another card on its front, first turning the current history
   * entry to the front; relies on React 18 batching both navigations.
   * @param changes - 要改的網址參數。The URL changes.
   * @param options - 這次導覽的選項；replace 時直接取代目前這筆（本來就會變成正面），不另外先改正面。
   *   Options of the navigation; with replace the current entry is replaced directly.
   */
  const switchCard = (changes: Record<string, string | null>, options?: NavigateOptions) => {
    if (state.side === 'answer' && !options?.replace) {
      const front = new URLSearchParams(params)
      front.delete('side')
      setParams(front, { replace: true, state: location.state })
    }
    update({ ...changes, side: null }, options)
  }

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
        switchCard({ card: String(target), trail: pushTrail(trail, cardId) }, { state: jump })
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
      const back: BackState = { back: true }
      switchCard({ card: String(previous), trail: trail.slice(0, -1).join(',') }, { replace: true, state: back })
    }
  }

  // 目前這個位置是不是往回到達的：每到一個新位置（location.key）時判斷一次，在 render 中對齊
  const navigationType = useNavigationType()
  const [arrival, setArrival] = useState(() => ({ key: location.key, index: historyIndex(), back: false }))
  if (arrival.key !== location.key) {
    const index = historyIndex()
    setArrival({ key: location.key, index, back: isBackNavigation(navigationType, location.state, index, arrival.index) })
  }

  return {
    ...state,
    arrivedBack: arrival.key === location.key && arrival.back,
    selectDeck: (deck: string | null) => update({ deck, page: null, card: null, trail: null }),
    submitSearch: (q: string) => update({ q, page: null, card: null, trail: null }),
    setPage: (page: number) => update({ page: page > 1 ? String(page) : null }),
    // 點目前這張卡片不算換卡，保留正反面
    selectCard: (id: number) =>
      id === cardId ? update({ card: String(id), trail: null }) : switchCard({ card: String(id), trail: null }),
    clearCard: () => update({ card: null, trail: null }),
    setSide: (side: CardSide) =>
      update({ side: side === 'answer' ? side : null }, { replace: true, state: location.state }),
    openNotes,
    goBack,
  }
}
