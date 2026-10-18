/**
 * FluencyTides API 客戶端模組。
 * FluencyTides API client module.
 *
 * Phase 9 重構：所有卡片 CRUD 操作改為透過 Handler-based API
 * Phase 9 refactor: all card CRUD operations go through the Handler-based API
 * (`/api/v1/handlers/{handler_name}/*`) 進行。
 *
 * 設計決策：
 * - 前端不再需要知道 Anki 內部的模型欄位結構。
 * - 透過 `listHandlers()` 動態取得可用處理器與其 input_schema。
 * - Relations 相關 API 暫時保留原有路由 (`/api/v1/relations/*`)。
 */

import axios from 'axios';
import type {
  AnkiDeckInfo,
  GraphData,
  CardDetail,
  CardRelationCreate,
  HandlerInfo,
  HandlerCreatePayload,
  HandlerCreateResponse,
  HandlerActionResponse,
  HandlerUpdatePayload,
  LLMRouteChoice,
  LLMRouteDeleteResponse,
  LLMRoute,
  LLMRoutesResponse,
} from '../types/api';

/**
 * Axios 實例，baseURL 對應後端 `/api/v1`。
 * Axios instance whose baseURL maps to the backend `/api/v1`.
 * Vite 的 proxy 會自動轉發到後端。
 * The Vite proxy automatically forwards requests to the backend.
 *
 * 認證設計（S009）：後端的 `/handlers/*` 與 `/relations/*` 需要 `X-API-Key`，
 * 但此處**刻意不設定**該 header——金鑰由 nginx 於反向代理層注入
 * （見 `frontend/nginx.conf.template`）。若改在前端帶金鑰，它會被打包進 JS
 * bundle，任何人開 DevTools 都看得到。請勿在此加入 API 金鑰。
 *
 * Auth design (S009): the backend's `/handlers/*` and `/relations/*` require
 * `X-API-Key`, but this client deliberately does NOT set it — the key is
 * injected by nginx at the reverse-proxy layer (see
 * `frontend/nginx.conf.template`). Sending it from the frontend would bake
 * the secret into the JS bundle, visible in DevTools. Do not add the API key
 * here.
 */
const apiClient = axios.create({
  baseURL: '/api/v1',
  headers: {
    'Content-Type': 'application/json',
  },
});

/**
 * Response 攔截器：自動解包 `response.data`，
 * 並將後端的 ErrorResponse 結構（附上 HTTP 狀態碼 `status`）提取為 rejection payload。
 * Response interceptor: automatically unwraps `response.data`,
 * and extracts the backend ErrorResponse structure (plus the HTTP `status`)
 * as the rejection payload.
 */
apiClient.interceptors.response.use(
  (response) => response.data,
  (error) => {
    if (error.response?.data?.error_code) {
      return Promise.reject({ ...error.response.data, status: error.response.status });
    }
    return Promise.reject(error);
  }
);

/**
 * 把用途鍵編碼成 URL path 片段；`encodeURIComponent` 不編碼 `*`，全域鍵另外轉成 `%2A`。
 * Encodes a route key as a URL path segment; `encodeURIComponent` leaves `*`
 * alone, so the global key is escaped to `%2A` explicitly.
 * @param routeKey - 用途鍵。Route key.
 * @returns 編碼後的片段。The encoded segment.
 */
function encodeRouteKey(routeKey: string): string {
  return encodeURIComponent(routeKey).replace(/\*/g, '%2A');
}

/**
 * FluencyTides 後端 API 的統一呼叫介面。
 * Unified call interface for the FluencyTides backend API.
 */
export const FluencyTidesAPI = {
  // ======================================================================
  // Handlers (Phase 9 核心 API)
  // ======================================================================

  /**
   * 取得所有可用的任務處理器清單。
   * Fetches the list of all available task handlers.
   * 前端可根據 input_schema 動態建構 UI 表單。
   * The frontend can dynamically build UI forms based on input_schema.
   * @returns Handler 資訊陣列。Array of handler info objects.
   */
  listHandlers: (): Promise<HandlerInfo[]> =>
    apiClient.get('/handlers/'),

  /**
   * 透過指定 Handler 建立卡片。
   * Creates a card through the specified handler.
   *
   * @param handlerName - 處理器名稱 (如 'vocabulary_mining', 'speaking_coach')。Handler name (e.g. 'vocabulary_mining', 'speaking_coach').
   * @param payload - 包含 deck_name, model_name, parameters 的物件。Object containing deck_name, model_name, and parameters.
   * @returns 建立結果 (note_id 與訊息)。Creation result (note_id and message).
   */
  createCard: (
    handlerName: string,
    payload: HandlerCreatePayload
  ): Promise<HandlerCreateResponse> =>
    apiClient.post(`/handlers/${handlerName}/create`, payload),

  /**
   * 讀取指定 Handler 管轄的卡片列表。
   * Reads the list of cards managed by the specified handler.
   *
   * @param handlerName - 處理器名稱。Handler name.
   * @param deckName - 可選的牌組篩選。Optional deck filter.
   * @returns 卡片資料陣列。Array of card data records.
   */
  listCards: (
    handlerName: string,
    deckName?: string
  ): Promise<Record<string, unknown>[]> =>
    apiClient.get(`/handlers/${handlerName}/cards`, {
      params: deckName ? { deck_name: deckName } : {},
    }),

  /**
   * 讀取指定 Handler 專屬的知識圖譜。
   * Reads the knowledge graph owned by the specified handler.
   *
   * @param handlerName - 處理器名稱。Handler name.
   * @param deckName - 可選的牌組篩選。Optional deck filter.
   * @returns 圖譜資料 (nodes 與 links)。Graph data (nodes and links).
   */
  getHandlerGraph: (
    handlerName: string,
    deckName?: string
  ): Promise<GraphData> =>
    apiClient.get(`/handlers/${handlerName}/graph`, {
      params: deckName && deckName !== 'All Decks'
        ? { deck_name: deckName }
        : {},
    }),

  /**
   * 透過指定 Handler 執行客製化的卡片更新。
   * Performs a customized card update through the specified handler.
   *
   * @param handlerName - 處理器名稱。Handler name.
   * @param noteId - 目標 Note ID。Target note ID.
   * @param parameters - 更新參數 (如 action, fields 等)。Update parameters (e.g. action, fields).
   * @returns 操作結果訊息。Operation result message.
   */
  updateCard: (
    handlerName: string,
    noteId: number,
    parameters: HandlerUpdatePayload
  ): Promise<HandlerActionResponse> =>
    apiClient.put(`/handlers/${handlerName}/cards/${noteId}`, parameters),

  /**
   * 透過指定 Handler 刪除卡片。
   * Deletes a card through the specified handler.
   *
   * @param handlerName - 處理器名稱。Handler name.
   * @param noteId - 目標 Note ID。Target note ID.
   * @returns 操作結果訊息。Operation result message.
   */
  deleteCard: (
    handlerName: string,
    noteId: number
  ): Promise<HandlerActionResponse> =>
    apiClient.delete(`/handlers/${handlerName}/cards/${noteId}`),

  // ======================================================================
  // Relations (暫時保留原有路由)
  // ======================================================================

  /**
   * 取得知識圖譜 (Legacy: 直接透過 relations 端點)。
   * Fetches the knowledge graph (legacy: directly via the relations endpoint).
   * @deprecated Phase 9 後建議使用 getHandlerGraph。Prefer getHandlerGraph after Phase 9.
   * @param deckName - 可選的牌組篩選。Optional deck filter.
   * @returns 圖譜資料。Graph data.
   */
  getKnowledgeGraph: (deckName?: string): Promise<GraphData> =>
    apiClient.get('/relations/graph', {
      params: deckName && deckName !== 'All Decks'
        ? { deck_name: deckName }
        : {},
    }),

  /**
   * 手動建立卡片關聯。
   * Manually creates a card relation.
   * @param relation - 關聯建立請求。Relation creation request.
   * @returns 建立結果。Creation result.
   */
  createRelation: (relation: CardRelationCreate) =>
    apiClient.post('/relations/', relation),

  /**
   * 刪除指定兩節點間的關聯。
   * Deletes the relation between two specified nodes.
   * @param relation - 來源/目標標籤與關聯類型。Source/target labels and relation type.
   * @returns 刪除筆數。Number of deleted relations.
   */
  deleteRelation: (
    relation: { source_label: string; target_label: string; relation_type: string }
  ): Promise<{ deleted_count: number }> =>
    apiClient.post('/relations/delete', relation),

  /**
   * 取得所有已註冊的關聯類型。
   * Fetches all registered relation types.
   * @returns 關聯類型名稱陣列。Array of relation type names.
   */
  getRelationTypes: (): Promise<string[]> =>
    apiClient.get('/relations/types'),

  /**
   * 與 Anki 同步並清除孤兒關聯。
   * Syncs with Anki and cleans up orphaned relations.
   * @returns 清除筆數。Number of deleted relations.
   */
  syncRelations: (): Promise<{ deleted_count: number }> =>
    apiClient.post('/relations/sync'),

  // ======================================================================
  // Anki 基礎查詢 (透過 Handler 或直接端點)
  // ======================================================================

  /**
   * 查詢所有牌組。
   * Lists all Anki decks.
   * @returns 牌組資訊陣列。Array of deck info objects.
   */
  listDecks: (): Promise<AnkiDeckInfo[]> =>
    apiClient.get('/handlers/decks'),

  /**
   * 讀取單一卡片詳情。
   * Reads the details of a single card.
   * @param noteId - 目標 Note ID。Target note ID.
   * @returns 卡片詳情。Card detail.
   */
  getCard: (noteId: number): Promise<CardDetail> =>
    apiClient.get(`/handlers/cards/${noteId}`),

  // ======================================================================
  // LLM 用途設定 (LLM Routes)
  // ======================================================================

  /**
   * 列出全部用途鍵的生效設定，以及白名單內可選的端點與模型。
   * Lists every route key's effective setting plus the selectable endpoints.
   * @returns 用途清單、可選端點與 DB 是否可讀。Routes, endpoints, and DB availability.
   */
  listLLMRoutes: (): Promise<LLMRoutesResponse> =>
    apiClient.get('/llm-routes'),

  /**
   * 為一個用途（或全域 `*`）設定端點、模型與 effort。
   * Stores the endpoint / model / effort for a route key (or the global `*`).
   * @param routeKey - 用途鍵。Route key.
   * @param choice - 選擇；模型不使用 effort 時 effort 為 null。The choice; effort is null when unused.
   * @returns 寫入後的狀態。The state after the write.
   */
  putLLMRoute: (routeKey: string, choice: LLMRouteChoice): Promise<LLMRoute> =>
    apiClient.put(`/llm-routes/${encodeRouteKey(routeKey)}`, choice),

  /**
   * 恢復一個用途的預設（刪除其設定；孤兒鍵也可刪除）。
   * Restores a route key's default by deleting its row (orphan keys included).
   * @param routeKey - 用途鍵。Route key.
   * @returns 刪除結果與之後的狀態。The delete result and the state afterwards.
   */
  deleteLLMRoute: (routeKey: string): Promise<LLMRouteDeleteResponse> =>
    apiClient.delete(`/llm-routes/${encodeRouteKey(routeKey)}`),

  // ======================================================================
  // Health
  // ======================================================================

  /**
   * 檢查後端健康狀態。
   * Checks the backend health status.
   * @returns 健康狀態物件。Health status object.
   */
  checkHealth: (): Promise<{ status: string }> =>
    axios.get('/api/health').then((res) => res.data),
};
