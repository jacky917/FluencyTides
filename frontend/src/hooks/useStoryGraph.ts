/**
 * 劇情圖：以 `build_id` 為鍵、永不過期（同一個版本內容不變，D11）。
 * The story graph keyed by build id; never stale.
 */

import { useQuery } from '@tanstack/react-query'
import { StoryAPI } from '../api/story'

/**
 * 讀取某個版本的劇情圖。Reads the graph of a build.
 * @param gameId - 遊戲。The game.
 * @param buildId - 版本（manifest 的 `build_id`）；未知時不讀。The build id.
 * @returns React Query 結果。The query.
 */
export function useStoryGraph(gameId: string, buildId: number | undefined) {
  return useQuery({
    queryKey: ['story', 'graph', gameId, buildId],
    queryFn: () => StoryAPI.getGraph(gameId, buildId),
    enabled: buildId !== undefined,
    staleTime: Infinity,
    gcTime: Infinity,
  })
}
