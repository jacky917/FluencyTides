/**
 * FluencyTides API 客戶端模組。
 * FluencyTides API client module.
 *
 * 筆記的建、改、刪經 handler（`/handlers/{name}/notes/...`）；讀取任一筆記用通用的
 * `/notes/{note_id}`，它回傳負責的 handler 與權限，前端再依此組 handler 的網址
 * （handlers 計畫書 D5、§4）。
 * Writes go through a handler; the generic note read tells which handler
 * owns a note and what is allowed.
 */

import axios from 'axios';
import type {
  AnkiDeckInfo,
  GraphData,
  CardPage,
  CardView,
  CardRelationCreate,
  CreateNoteRequest,
  CreateNoteResponse,
  DeleteNoteResponse,
  GenericNoteDetail,
  HandlerInfo,
  NoteDetail,
  UpdateNoteResponse,
  CapabilityRoute,
  CapabilityRoutesResponse,
  RouteDeleteResponse,
  RouteUpdate,
  RelationSyncResponse,
} from '../types/api';
import { readErrorBody } from '../lib/apiError';
import { encodeRouteKey } from '../lib/capabilityRoutes';

/**
 * Axios 實例，baseURL 對應後端 `/api/v1`。
 * Axios instance whose baseURL maps to the backend `/api/v1`.
 * Vite 的 proxy 會自動轉發到後端。
 * The Vite proxy automatically forwards requests to the backend.
 *
 * 認證設計（S009）：後端的 `/api/v1/*`（`/handlers`、`/relations`、`/cards`、`/anki-media`、
 * `/capability-routes` 等）需要 `X-API-Key`，
 * 但此處**刻意不設定**該 header——金鑰由 nginx 於反向代理層注入
 * （見 `frontend/nginx.conf.template`）。若改在前端帶金鑰，它會被打包進 JS
 * bundle，任何人開 DevTools 都看得到。請勿在此加入 API 金鑰。
 *
 * Auth design (S009): the backend's `/api/v1/*` endpoints require
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
 * 以 `responseType: 'blob'` 下載時錯誤的 body 也是 Blob，先讀成 JSON 才看得到 `error_code`。
 * Response interceptor: automatically unwraps `response.data`,
 * and extracts the backend ErrorResponse structure (plus the HTTP `status`)
 * as the rejection payload; Blob error bodies are parsed first.
 */
apiClient.interceptors.response.use(
  (response) => response.data,
  async (error) => {
    const data = (await readErrorBody(error.response?.data)) as { error_code?: string } | undefined;
    if (data?.error_code) {
      return Promise.reject({ ...data, status: error.response.status });
    }
    return Promise.reject(error);
  }
);

/**
 * FluencyTides 後端 API 的統一呼叫介面。
 * Unified call interface for the FluencyTides backend API.
 */
export const FluencyTidesAPI = {
  // ======================================================================
  // Handlers 與筆記
  // ======================================================================

  /**
   * 所有 handler、能力與建卡表單（依註冊順序）。
   * Every handler with its capabilities and create form.
   * @returns Handler 資訊陣列。Array of handler info objects.
   */
  listHandlers: (): Promise<HandlerInfo[]> =>
    apiClient.get('/handlers'),

  /**
   * 透過 handler 建卡；寫入後後端會同步 AnkiWeb 並等它完成才回應。
   * Creates notes through a handler; the backend syncs before responding.
   * @param handlerName - handler 名稱。Handler name.
   * @param body - 牌組、筆記類型與建立參數。Deck, note type and parameters.
   * @returns 建立的筆記 ID、實際的牌組與同步提醒。The created notes.
   */
  createNote: (handlerName: string, body: CreateNoteRequest): Promise<CreateNoteResponse> =>
    apiClient.post(`/handlers/${encodeURIComponent(handlerName)}/notes`, body),

  /**
   * 通用讀取：任何筆記類型，附上負責的 handler 與能不能改、刪。
   * Reads any note with its handler and permissions.
   * @param noteId - 筆記 ID。Note ID.
   * @returns 筆記、handler 與權限。The note.
   */
  getNote: (noteId: number): Promise<GenericNoteDetail> =>
    apiClient.get(`/notes/${noteId}`),

  /**
   * 透過 handler 讀取筆記（成組卡片的 handler 之後會附上整組）。
   * Reads a note through its handler.
   * @param handlerName - handler 名稱。Handler name.
   * @param noteId - 筆記 ID。Note ID.
   * @returns 筆記。The note.
   */
  readNote: (handlerName: string, noteId: number): Promise<NoteDetail> =>
    apiClient.get(`/handlers/${encodeURIComponent(handlerName)}/notes/${noteId}`),

  /**
   * 修改筆記欄位（只送有變的欄位）；回應是修改後的完整筆記與同步提醒。
   * Updates fields; only send the changed ones.
   * @param handlerName - handler 名稱（取自 `getNote` 的 `handler`）。Handler name.
   * @param noteId - 筆記 ID。Note ID.
   * @param fields - 欄位名 → 新值。Field name to new value.
   * @returns 修改後的筆記與同步提醒。The updated note.
   */
  updateNoteFields: (handlerName: string, noteId: number, fields: Record<string, string>): Promise<UpdateNoteResponse> =>
    apiClient.patch(`/handlers/${encodeURIComponent(handlerName)}/notes/${noteId}`, { fields }),

  /**
   * 刪除筆記與它的關聯。
   * Deletes a note and its relations.
   * @param handlerName - handler 名稱。Handler name.
   * @param noteId - 筆記 ID。Note ID.
   * @returns 同步提醒。The sync warning.
   */
  deleteNote: (handlerName: string, noteId: number): Promise<DeleteNoteResponse> =>
    apiClient.delete(`/handlers/${encodeURIComponent(handlerName)}/notes/${noteId}`),

  /**
   * 讀取 handler 的圖譜。
   * Reads a handler's graph.
   * @param handlerName - handler 名稱。Handler name.
   * @param options.deck - 牌組；省略或 `All Decks` 為全部牌組。The deck, or all.
   * @param options.noteType - 只看其中一種筆記類型。One of the handler's note types.
   * @param options.labelField - 節點名稱用的欄位；省略為 handler 的預設。The label field.
   * @returns 節點、連線與可選的名稱欄位。The graph.
   */
  getHandlerGraph: (
    handlerName: string,
    options: { deck?: string | null; noteType?: string | null; labelField?: string | null } = {},
  ): Promise<GraphData> =>
    apiClient.get(`/handlers/${encodeURIComponent(handlerName)}/graph`, {
      params: {
        deck: options.deck && options.deck !== 'All Decks' ? options.deck : undefined,
        note_type: options.noteType || undefined,
        label_field: options.labelField || undefined,
      },
    }),

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
  getKnowledgeGraph: (deckName?: string): Promise<Pick<GraphData, 'nodes' | 'links'>> =>
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
   * 同步清理孤兒關聯。後端拒絕時回 409：`RELATION_SYNC_EMPTY_ANKI`（不能強制）或
   * `RELATION_SYNC_NEEDS_FORCE`（確認後帶 force 重送）。
   * Syncs relations with Anki; pass `force` only after the user confirmed.
   */
  syncRelations: (force = false): Promise<RelationSyncResponse> =>
    apiClient.post('/relations/sync', null, { params: { force } }),

  // ======================================================================
  // 牌組
  // ======================================================================

  /**
   * 查詢所有牌組。
   * Lists all Anki decks.
   * @returns 牌組資訊陣列。Array of deck info objects.
   */
  listDecks: (): Promise<AnkiDeckInfo[]> =>
    apiClient.get('/decks'),

  // ======================================================================
  // 卡片瀏覽 (Card Browse)：不經過 handler，任何筆記類型都能看
  // ======================================================================

  /**
   * 列出一頁卡片（新到舊）；牌組包含子牌組。
   * Lists one page of cards, newest first; a deck includes its sub-decks.
   * @param params.deck - 牌組完整名稱；省略為全部。Full deck name, or all decks.
   * @param params.q - 額外的 Anki 搜尋語句。Extra Anki search.
   * @param params.page - 頁碼（1 起算）。Page number.
   * @param params.pageSize - 每頁張數（上限 200）。Page size.
   * @returns 這一頁與總數。The page and the total.
   */
  browseCards: (params: { deck?: string; q?: string; page?: number; pageSize?: number }): Promise<CardPage> =>
    apiClient.get('/cards', {
      params: {
        deck: params.deck || undefined,
        q: params.q?.trim() || undefined,
        page: params.page,
        page_size: params.pageSize,
      },
    }),

  /**
   * 讀取單一卡片與 Anki 渲染好的正反面。
   * Reads one card with its rendered faces.
   * @param cardId - Anki 卡片 ID。Card ID.
   * @returns 卡片與卡面 HTML。The card and its faces.
   */
  getCardView: (cardId: number): Promise<CardView> =>
    apiClient.get(`/cards/${cardId}`),

  /**
   * 讀取 Anki 媒體資料夾中的檔案（音檔、頭像）。
   * Reads a file from Anki's media folder.
   * @param filename - 單一檔名。A bare file name.
   * @returns 檔案內容（帶 MIME 類型；只有音訊、影片、圖片照實際類型，其他為 octet-stream）。
   *   The file as a Blob.
   */
  getAnkiMedia: (filename: string): Promise<Blob> =>
    apiClient.get(`/anki-media/${encodeURIComponent(filename)}`, { responseType: 'blob' }),

  // ======================================================================
  // 能力路由 (Capability Routes)
  // ======================================================================

  /**
   * 列出全部用途鍵各能力的生效設定，以及各能力可選的 provider 與模型。
   * Lists every route key's per-capability state plus the selectable providers.
   * @returns 用途清單、可選項目與 DB 是否可讀。Routes, providers, and DB availability.
   */
  listCapabilityRoutes: (): Promise<CapabilityRoutesResponse> =>
    apiClient.get('/capability-routes'),

  /**
   * 為一個用途（或全域 `*`）在一種能力上設定 provider、模型與 effort。
   * Stores the provider / model / effort of a route key (or `*`) for one capability.
   * @param routeKey - 用途鍵。Route key.
   * @param capability - 能力，例如 `llm`。Capability, e.g. `llm`.
   * @param choice - 選擇；模型不使用 effort 時 effort 為 null。The choice.
   * @returns 寫入後這個用途鍵的狀態。The key's state after the write.
   */
  putCapabilityRoute: (routeKey: string, capability: string, choice: RouteUpdate): Promise<CapabilityRoute> =>
    apiClient.put(`/capability-routes/${encodeRouteKey(routeKey)}/${encodeURIComponent(capability)}`, choice),

  /**
   * 恢復一個用途在一種能力上的預設（刪除該列；孤兒鍵也可刪除）。
   * Restores a route key's default for one capability (orphan keys included).
   * @param routeKey - 用途鍵。Route key.
   * @param capability - 能力。Capability.
   * @returns 刪除結果與之後的狀態。The delete result and the state afterwards.
   */
  deleteCapabilityRoute: (routeKey: string, capability: string): Promise<RouteDeleteResponse> =>
    apiClient.delete(`/capability-routes/${encodeRouteKey(routeKey)}/${encodeURIComponent(capability)}`),

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
