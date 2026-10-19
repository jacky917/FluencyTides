/**
 * FluencyTides API 型別定義。
 * FluencyTides API type definitions.
 *
 * Phase 9 更新：新增 Handler-based API 型別，
 * 與後端 FastAPI (Pydantic models + HandlerRegistry) 的回傳結構嚴格對齊。
 */

// ============================================================================
// Handler-Based API (Phase 9)
// ============================================================================

/** 後端 HandlerRegistry 回傳的處理器資訊。Handler info returned by the backend HandlerRegistry. */
export interface HandlerInfo {
  handler_name: string;
  supported_models: string[];
  input_schema: Record<string, unknown>;
}

/** 透過 Handler 建立卡片的請求 Payload。Request payload for creating a card through a handler. */
export interface HandlerCreatePayload {
  deck_name: string;
  model_name: string;
  parameters: Record<string, unknown>;
}

/** 建立卡片成功的回應。Response for a successful card creation. */
export interface HandlerCreateResponse {
  note_id: number;
  message: string;
}

/** 通用的操作結果回應。Generic operation result response. */
export interface HandlerActionResponse {
  message: string;
}

/** 後端卡片更新的 parameters (Speaking Coach 範例)。Parameters for backend card updates (Speaking Coach example). */
export interface HandlerUpdatePayload {
  action: string;
  recording?: Record<string, unknown>;
  index?: number;
  fields?: Record<string, string>;
}

// ============================================================================
// Anki 基礎查詢 (仍可透過 Handler 或直接端點取得)
// ============================================================================

/** Anki 牌組基本資訊。Basic Anki deck information. */
export interface AnkiDeckInfo {
  deck_name: string;
  deck_id: number;
}

/** Anki 模型 (Note Type) 資訊。Anki model (note type) information. */
export interface AnkiModelInfo {
  model_name: string;
  model_file_name: string;
  fields: string[];
  has_llm_schema: boolean;
}

// ============================================================================
// 統一錯誤回應
// ============================================================================

/** 後端統一錯誤回應結構。Unified backend error response structure. */
export interface ErrorResponse {
  error_code: string;
  message: string;
  details?: unknown;
  /** HTTP 狀態碼（由 API client 的攔截器補上）。HTTP status, added by the API client interceptor. */
  status?: number;
}

// ============================================================================
// 能力路由 (Capability Routes)
// ============================================================================

/** 生效的層：用途自己的設定、全域 `*`、`.env` 預設。The winning layer. */
export type RouteSource = 'route' | 'global' | 'env';

/** 一組選擇。A choice of provider / model / effort. */
export interface RouteChoice {
  /** 由誰提供；`llm` 能力為端點 ID。Who provides it; the endpoint ID for `llm`. */
  provider: string;
  /** 模型名；不需要模型的能力為 null。Model name, or null. */
  model: string | null;
  effort: string | null;
}

/** 寫入用的選擇（`PUT /capability-routes/{key}/{capability}`）。A choice to store. */
export interface RouteUpdate {
  provider: string;
  model: string;
  effort: string | null;
}

/** 解析時被跳過的一層與原因。A skipped layer and why. */
export interface RejectedLayer {
  route_key: string;
  reason: string;
}

/** 一個用途鍵在一種能力上的狀態。The state of one key for one capability. */
export interface CapabilityState {
  source: RouteSource;
  effective: RouteChoice;
  /** 生效設定的顯示標籤（`llm` 為寫進卡片的模型標籤）。The display label. */
  label: string;
  stored: RouteChoice | null;
  rejected: RejectedLayer[];
  db_unavailable: boolean;
  /** 生效設定目前無法使用的原因。Why the effective setting is unusable right now. */
  problem: string | null;
}

/** 一個用途鍵與它各能力的狀態（`*` 為全域）。One route key and its capabilities. */
export interface CapabilityRoute {
  route_key: string;
  description: string | null;
  /** false＝DB 中的孤兒鍵，只能刪除。false for orphan rows, which can only be deleted. */
  declared: boolean;
  /** 能力 → 狀態。Capability to state. */
  capabilities: Record<string, CapabilityState>;
}

/** 白名單中的模型；efforts 為空＝不使用 effort。A whitelisted model; empty efforts = no effort. */
export interface ModelOption {
  name: string;
  efforts: string[];
}

/** 可選的 provider 與白名單。A selectable provider and its whitelist. */
export interface ProviderOption {
  id: string;
  type: string;
  models: ModelOption[];
}

/** `GET /capability-routes` 的回應。Response of `GET /capability-routes`. */
export interface CapabilityRoutesResponse {
  routes: CapabilityRoute[];
  /** 能力 → 可選的 provider。Capability to its selectable providers. */
  providers: Record<string, ProviderOption[]>;
  /** 讀不到資料庫：各列為 `.env` 預設，暫時無法修改。DB unreadable; rows show `.env` defaults. */
  db_unavailable: boolean;
}

/** `DELETE /capability-routes/{key}/{capability}` 的回應。The delete response. */
export interface RouteDeleteResponse {
  /** 是否真的刪了一列。Whether a row was removed. */
  deleted: boolean;
  /** 刪除後的狀態；孤兒鍵刪除後或組不出狀態時為 null。The state afterwards, or null. */
  route: CapabilityRoute | null;
  /** 刪除已提交但組不出之後的狀態。The delete committed but the state could not be built. */
  route_unavailable: boolean;
}

// ============================================================================
// 知識圖譜 (Knowledge Graph)
// ============================================================================

/** 知識圖譜節點。Knowledge graph node. */
export interface GraphNode {
  id: string;
  group: number;
  val: number;
  label: string;
  translation?: string;
  pos?: string;
  source_deck?: string;
  note_id?: number;
  status?: string;
}

/** 知識圖譜連線。Knowledge graph link. */
export interface GraphLink {
  source: string;
  target: string;
  label?: string;
  relation_id?: number;
}

/** 知識圖譜完整資料。Complete knowledge graph data. */
export interface GraphData {
  nodes: GraphNode[];
  links: GraphLink[];
}

// ============================================================================
// 卡片詳情 (Card Detail / RUD)
// ============================================================================

/**
 * 單一筆記的完整詳情（`GET /handlers/cards/{note_id}`）。欄位名沿用 AnkiConnect `notesInfo`
 * 的 camelCase，與其他 API 的 snake_case 不同。
 * Full details of a single note; keys keep AnkiConnect's camelCase.
 */
export interface CardDetail {
  noteId: number;
  modelName: string;
  tags: string[];
  /** 欄位名 → 值與在筆記類型中的順序。Field name → value and position. */
  fields: Record<string, { value: string; order: number }>;
  cards: number[];
}

// ============================================================================
// 卡片瀏覽 (Card Browse)
// ============================================================================

/** 清單中的一張卡（`GET /cards`）。A card in the browse list. */
export interface CardSummary {
  card_id: number;
  note_id: number;
  /** 所在牌組的完整名稱（`::` 分層）。Full deck name. */
  deck_name: string;
  model_name: string;
  /** 卡片模板序號（0 起算）。Template ordinal. */
  ord: number;
  /**
   * 欄位名 → 原始值，依筆記類型的欄位順序（JSON 物件的鍵序；欄位名是整數字串時 JS 會把它
   * 排到前面）。Raw field values in note-type order.
   */
  fields: Record<string, string>;
}

/** 一頁卡片。A page of cards. */
export interface CardPage {
  cards: CardSummary[];
  total: number;
  page: number;
  page_size: number;
}

/** 單一卡片與 Anki 渲染好的卡面（`GET /cards/{card_id}`）。A card with its rendered faces. */
export interface CardView extends CardSummary {
  /** 正面 HTML（含 `<style>` 與模板腳本；播放標記已換成 `data-anki-sound` 按鈕）。Front HTML. */
  question: string;
  /** 背面 HTML（模板用 `{{FrontSide}}` 時含正面）。Back HTML. */
  answer: string;
}

// ============================================================================
// 關聯 (Relations)
// ============================================================================

/** 建立卡片關聯的請求 DTO。Request DTO for creating a card relation. */
export interface CardRelationCreate {
  source_note_id: number | null;
  target_note_id: number | null;
  relation_type: string;
  source_label: string;
  target_label: string;
}

// ============================================================================
// 已棄用 (Deprecated) — 保留供舊代碼過渡期使用
// ============================================================================

/** @deprecated Phase 9 後不再使用。改用 HandlerCreatePayload。No longer used after Phase 9; use HandlerCreatePayload instead. */
export interface CardGenerateRequest {
  user_input: string;
  deck_name: string;
  model_file_name: string;
  model_name: string;
  primary_field_name?: string;
  system_prompt?: string | null;
  extra_fields?: Record<string, string> | null;
  tags?: string[] | null;
}

/** @deprecated Phase 9 後不再使用。改用 HandlerCreateResponse。No longer used after Phase 9; use HandlerCreateResponse instead. */
export interface CardGenerateResponse {
  note_id: number;
  message: string;
  deck_name: string;
  model_name: string;
}
