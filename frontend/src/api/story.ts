/**
 * 劇情流程圖 API（`/api/v1/story`）。和 `client.ts` 一樣：不在前端帶 API 金鑰（由 nginx 注入），
 * 回應自動解包 `data`，後端的 ErrorResponse 連同 HTTP 狀態碼成為 rejection。
 * Story API client; like `client.ts`, the API key is injected by nginx.
 */

import axios from 'axios'
import { readErrorBody } from '../lib/apiError'
import type {
  StoryAnnotations,
  StoryGameSummary,
  StoryGraphResponse,
  StoryLinesPage,
  StoryLocation,
  StoryManifest,
} from '../types/story'

const storyClient = axios.create({ baseURL: '/api/v1/story', headers: { 'Content-Type': 'application/json' } })

storyClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    const data = (await readErrorBody(error.response?.data)) as { error_code?: string } | undefined
    if (data?.error_code) return Promise.reject({ ...data, status: error.response.status })
    return Promise.reject(error)
  },
)

const game = (id: string) => `/games/${encodeURIComponent(id)}`

/** 劇情流程圖 API。The story API. */
export const StoryAPI = {
  /** 上線中的遊戲。Games with an active build. */
  listGames: (): Promise<StoryGameSummary[]> => storyClient.get('/games').then((r) => r.data),

  /** 遊戲的 manifest。The manifest. */
  getManifest: (gameId: string): Promise<StoryManifest> => storyClient.get(game(gameId)).then((r) => r.data),

  /** 整份劇情圖（`build` 指定版本時可以永久快取）。The whole graph. */
  getGraph: (gameId: string, build?: number): Promise<StoryGraphResponse> =>
    storyClient.get(`${game(gameId)}/graph`, { params: { build } }).then((r) => r.data),

  /** 各節點的註記統計與搜尋索引。Annotation stats. */
  getAnnotations: (gameId: string, kinds = 'cards'): Promise<StoryAnnotations> =>
    storyClient.get(`${game(gameId)}/annotations`, { params: { kinds } }).then((r) => r.data),

  /** 節點的一頁台詞。A page of lines. */
  getLines: (gameId: string, nodeKey: string, offset = 0, limit = 300, annotatedAfter?: number): Promise<StoryLinesPage> =>
    storyClient
      .get(`${game(gameId)}/nodes/${encodeURIComponent(nodeKey)}/lines`, {
        params: { offset, limit, annotated_after: annotatedAfter },
      })
      .then((r) => r.data),

  /** 台詞或筆記的位置。Locate a line or note. */
  locate: (gameId: string, by: { noteId?: number; lineId?: number }): Promise<StoryLocation> =>
    storyClient
      .get(`${game(gameId)}/locate`, { params: { note_id: by.noteId, line_id: by.lineId } })
      .then((r) => r.data),

  /** 產生存檔：POST（JSON body，不能直接用 `<a href>`）取得 blob，再由呼叫端觸發下載；API 金鑰和其他請求一樣由 nginx 注入，前端不帶。Generate a save as a Blob. */
  createSave: (gameId: string, body: { line_id: number; slot: string; route_preset?: string | null }): Promise<Blob> =>
    storyClient.post(`${game(gameId)}/saves`, body, { responseType: 'blob' }).then((r) => r.data),

  /** 縮圖網址（同源，給 `<img>` 用）。Thumbnail URL. */
  thumbUrl: (gameId: string, thumb: string): string => `/api/v1/story${game(gameId)}/thumbs/${encodeURIComponent(thumb)}`,

  /** 語音網址（同源，給 `<audio>`／`new Audio()` 用；`voice` 是 `StoryLine.voice`）。Voice URL. */
  voiceUrl: (gameId: string, voice: string): string => `/api/v1/story${game(gameId)}/voices/${encodeURIComponent(voice)}`,

  /**
   * 重新請求一次語音，只為了在播放失敗時取得後端的錯誤訊息（音訊元素拿不到 HTTP 狀態）。
   * Re-requests a voice only to surface the backend error after a playback failure.
   */
  probeVoice: (gameId: string, voice: string): Promise<void> =>
    storyClient.get(`${game(gameId)}/voices/${encodeURIComponent(voice)}`, { responseType: 'blob' }).then(() => undefined),
}
