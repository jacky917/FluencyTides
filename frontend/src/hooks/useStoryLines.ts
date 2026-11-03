/**
 * 節點的台詞（分頁讀取）。句數上千的節點分頁載入，捲到底再讀下一頁（D12）。
 * A node's lines, loaded page by page.
 */

import { useInfiniteQuery } from '@tanstack/react-query'
import { StoryAPI } from '../api/story'

/** 每頁幾句。Lines per page. */
export const STORY_LINES_PAGE = 300

/**
 * 一頁最多幾句；和後端 `services/story/service.py` 的 `MAX_LINES_PAGE`（API 的 `limit` 上限）相同，
 * 超過會被後端以 422 拒絕。
 * Max lines per request; must match the backend `MAX_LINES_PAGE`.
 */
export const MAX_LINES_PAGE = 2000

/**
 * 第一頁要讀幾句：至少一頁，盡量涵蓋到 `minSeq`，但不超過 {@link MAX_LINES_PAGE}。
 * 更後面的句子由呼叫端逐頁往下讀（抽屜的 pending 機制）。
 * Size of the first page: covers `minSeq` when possible, capped at the backend max.
 * @param minSeq - 想要第一頁就載入到的 seq。The seq the first page should reach.
 * @returns 第一頁的句數。The first page size.
 */
export function firstPageSize(minSeq: number): number {
  const wanted = Math.ceil((Math.max(0, minSeq) + 1) / STORY_LINES_PAGE) * STORY_LINES_PAGE
  return Math.min(MAX_LINES_PAGE, Math.max(STORY_LINES_PAGE, wanted))
}

/**
 * 讀取節點的台詞。Reads a node's lines.
 * @param gameId - 遊戲。The game.
 * @param buildId - 版本。The build.
 * @param nodeKey - 節點；null 時不讀。The node, or null.
 * @param minSeq - 第一頁盡量載入到這個 seq（網址 `line=` 是 line_id，抽屜先用 locate 換成 seq 再傳進來）；
 *   超過 {@link MAX_LINES_PAGE} 的部分要逐頁往下讀。
 *   Try to load up to this seq in the first page (the URL's `line=` is a line_id the drawer
 *   resolves to a seq via locate); anything beyond the cap is fetched page by page.
 * @returns React Query 結果。The query.
 */
export function useStoryLines(gameId: string, buildId: number | undefined, nodeKey: string | null, minSeq = 0) {
  const first = firstPageSize(minSeq)
  return useInfiniteQuery({
    queryKey: ['story', 'lines', gameId, buildId, nodeKey, first],
    enabled: !!nodeKey && buildId !== undefined,
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      StoryAPI.getLines(gameId, nodeKey!, pageParam, pageParam === 0 ? first : STORY_LINES_PAGE, pageParam === 0 ? -1 : undefined),
    getNextPageParam: (last) => {
      const next = last.offset + last.lines.length
      return next < last.total ? next : undefined
    },
    staleTime: 60 * 1000,
  })
}
