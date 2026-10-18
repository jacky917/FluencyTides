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
// LLM 用途設定 (LLM Routes)
// ============================================================================

/** 生效的層：用途自己的設定、全域 `*`、`.env` 預設。The winning layer. */
export type LLMRouteSource = 'route' | 'global' | 'env';

/** 一組選擇。A choice of endpoint / model / effort. */
export interface LLMRouteChoice {
  endpoint: string;
  model: string;
  effort: string | null;
}

/** 解析時被跳過的一層與原因。A skipped layer and why. */
export interface LLMRejectedLayer {
  route_key: string;
  reason: string;
}

/** 一個用途鍵的狀態（`*` 為全域）。The state of one route key (`*` is global). */
export interface LLMRoute {
  route_key: string;
  description: string | null;
  /** false＝DB 中的孤兒鍵，只能刪除。false for orphan rows, which can only be deleted. */
  declared: boolean;
  source: LLMRouteSource;
  effective: LLMRouteChoice;
  /** 生效設定對應的卡片標籤。The card label of the effective setting. */
  label: string;
  stored: LLMRouteChoice | null;
  rejected: LLMRejectedLayer[];
  db_unavailable: boolean;
  /** 生效設定目前無法使用的原因。Why the effective setting is unusable right now. */
  problem: string | null;
}

/** 白名單中的模型；efforts 為空＝不使用 effort。A whitelisted model; empty efforts = no effort. */
export interface LLMModelOption {
  name: string;
  efforts: string[];
}

/** 可選的端點與白名單。A selectable endpoint and its whitelist. */
export interface LLMEndpointOption {
  id: string;
  type: string;
  models: LLMModelOption[];
}

/** `GET /llm-routes` 的回應。Response of `GET /llm-routes`. */
export interface LLMRoutesResponse {
  routes: LLMRoute[];
  endpoints: LLMEndpointOption[];
  /** 讀不到資料庫：各列為 `.env` 預設，暫時無法修改。DB unreadable; rows show `.env` defaults. */
  db_unavailable: boolean;
}

/** `DELETE /llm-routes/{key}` 的回應。Response of `DELETE /llm-routes/{key}`. */
export interface LLMRouteDeleteResponse {
  /** 是否真的刪了一列。Whether a row was removed. */
  deleted: boolean;
  /** 刪除後的狀態；孤兒鍵刪除後或組不出狀態時為 null。The state afterwards, or null. */
  route: LLMRoute | null;
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

/** 單一卡片的完整詳情。Full details of a single card. */
export interface CardDetail {
  note_id: number;
  model_name: string;
  tags: string[];
  fields: Record<string, string>;
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
