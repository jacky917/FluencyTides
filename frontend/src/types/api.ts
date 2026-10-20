/**
 * FluencyTides API 型別定義。
 * FluencyTides API type definitions.
 *
 * 與後端的 Pydantic 模型對齊；handler 與筆記的部分見 `backend/app/schemas/notes.py`
 * （handlers 計畫書 §4）。
 * Mirrors the backend Pydantic models.
 */

// ============================================================================
// Handler 與能力（GET /handlers）
// ============================================================================

/** 建卡管道。Where a card can be created from. */
export type CreateChannel = 'web' | 'tg' | 'script';

/** 一項操作是否開放；不開放時 `reason` 必有值。Whether an operation is offered. */
export interface Operation {
  supported: boolean;
  reason: string | null;
}

/** 建卡能力（計畫書 D2）。The create capability. */
export interface CreateCapability extends Operation {
  /** 含 web 可選；非空但不含 web 反灰並顯示 reason；空陣列不顯示。 */
  channels: CreateChannel[];
  flow: 'form' | 'preview' | null;
  deck: 'selected' | 'fixed' | null;
  fixed_deck: string | null;
  /** 建卡建出的筆記類型。The note type created. */
  note_type: string | null;
}

/** 圖譜能力；`links` 表示有關聯連線。The graph capability. */
export interface GraphCapability extends Operation {
  links: boolean;
}

/** 一個 handler 的全部能力。All capabilities of a handler. */
export interface HandlerCapabilities {
  create: CreateCapability;
  read: Operation;
  update_fields: Operation;
  delete: Operation;
  actions: string[];
  graph: GraphCapability;
}

/** `GET /handlers` 的一項。One handler. */
export interface HandlerInfo {
  name: string;
  /** 中文名稱。Display title. */
  title: string;
  /** 負責的筆記類型。The note types it owns. */
  note_types: string[];
  capabilities: HandlerCapabilities;
  /** 網頁建卡表單的 JSON Schema；沒有表單時為 null。The create form schema, or null. */
  create_schema: Record<string, unknown> | null;
  /** JSON 欄位：不能直接修改，網頁唯讀顯示。JSON fields, read-only. */
  json_fields: string[];
  /** 媒體欄位：同上。Media fields, read-only. */
  media_fields: string[];
}

// ============================================================================
// 筆記（/notes、/handlers/{name}/notes）
// ============================================================================

/** 一個欄位（依筆記類型的順序）。A field, in note-type order. */
export interface NoteField {
  name: string;
  value: string;
}

/** 單一筆記（handler 讀取的回應）。A single note. */
export interface NoteDetail {
  note_id: number;
  note_type: string;
  tags: string[];
  cards: number[];
  fields: NoteField[];
}

/** 能不能做某件事；不允許時 `reason` 必有值。Whether an action is allowed. */
export interface Permission {
  allowed: boolean;
  reason: string | null;
}

/** 對這張筆記能做的事。What may be done to a note. */
export interface NotePermissions {
  update_fields: Permission;
  delete: Permission;
}

/** `GET /notes/{note_id}`：附上負責的 handler 與權限。The generic read. */
export interface GenericNoteDetail extends NoteDetail {
  /** 負責的 handler；沒有 handler 負責這種筆記類型時為 null。The owning handler, or null. */
  handler: string | null;
  permissions: NotePermissions;
}

/** `POST /handlers/{name}/notes` 的請求。The create request. */
export interface CreateNoteRequest {
  /** 牌組；handler 的建卡牌組固定時忽略，不存在時後端自動建立。The deck. */
  deck: string;
  /** 必須是該 handler 的 `create.note_type`。Must be the handler's create note type. */
  note_type: string;
  /** 標準建立是 `{欄位名: 值}`；其他 handler 依 `create_schema`。The parameters. */
  params: Record<string, unknown>;
}

/** 建卡結果。The created notes. */
export interface CreateNoteResponse {
  note_ids: number[];
  /** 實際建立的牌組。The deck actually used. */
  deck: string;
  /** 寫入後同步 AnkiWeb 失敗時的提醒；卡片仍已建立。The sync warning, if any. */
  sync_warning: string | null;
}

/** `PATCH` 的回應：修改後的完整筆記與同步提醒。The updated note and sync warning. */
export interface UpdateNoteResponse extends NoteDetail {
  sync_warning: string | null;
}

/** `DELETE` 的回應。The delete result. */
export interface DeleteNoteResponse {
  sync_warning: string | null;
}

// ============================================================================
// 牌組（GET /decks）
// ============================================================================

/** Anki 牌組基本資訊。Basic Anki deck information. */
export interface AnkiDeckInfo {
  deck_name: string;
  deck_id: number;
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
// 關聯同步 (Relation Sync)
// ============================================================================

/** `POST /relations/sync` 的回應。Response of the relation sync. */
export interface RelationSyncResponse {
  deleted_count: number;
  scanned_notes: number;
  orphan_notes: number;
  /** false＝清理前與 AnkiWeb 同步失敗，以本機收藏為準。False when the AnkiWeb sync before cleanup failed. */
  ankiweb_synced: boolean;
}

// ============================================================================
// 能力路由 (Capability Routes)
// ============================================================================

/** 生效的層：用途自己的設定、分支沿用的流程鍵、全域 `*`、`.env` 預設。The winning layer. */
export type RouteSource = 'route' | 'parent' | 'global' | 'env';

/** 一組選擇。A choice of provider / model / effort. */
export interface RouteChoice {
  /**
   * 由誰提供：`llm` / `audio_llm` 為端點 ID、`stt` 為 `whisper`、`audio_eval` 為策略名。
   * Who provides it: an endpoint ID, `whisper`, or a strategy name.
   */
  provider: string;
  /** 模型名；不需要模型的能力為 null。Model name, or null. */
  model: string | null;
  effort: string | null;
}

/** 寫入用的選擇（`PUT /capability-routes/{key}/{capability}`）。A choice to store. */
export interface RouteUpdate {
  provider: string;
  /** 模型名；策略（`audio_eval`）為 null。Model name; null for strategies. */
  model: string | null;
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
  /** 這個鍵是否宣告了這個能力；false＝孤兒設定，只能刪除。false rows can only be deleted. */
  declared: boolean;
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
  /** 端點類型、`whisper`，或策略的 `strategy`。Endpoint type, `whisper` or `strategy`. */
  type: string;
  /** 可選的模型；空＝這個 provider 不選模型（策略）。Empty for providers without a model. */
  models: ModelOption[];
  /** 說明（策略才有）。Description, for strategies. */
  title?: string | null;
}

/** `GET /capability-routes` 的回應。Response of `GET /capability-routes`. */
export interface CapabilityRoutesResponse {
  routes: CapabilityRoute[];
  /** 能力 → 可選的 provider。Capability to its selectable providers. */
  providers: Record<string, ProviderOption[]>;
  /** 有限縮評分策略的用途鍵 → 允許的策略。Route keys restricting the strategies. */
  strategies?: Record<string, string[]>;
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

/**
 * 知識圖譜節點。標準圖譜的節點只有 `id`、`note_id`、`note_type`、`label`（與宣告了副標時的
 * `translation`）；關聯圖譜另有 `group`、`val`、`pos`、`status`，幽靈節點沒有 `note_id`。
 * Knowledge graph node; relation-graph nodes carry more keys.
 */
export interface GraphNode {
  id: string;
  label?: string;
  note_id?: number | null;
  note_type?: string;
  group?: number;
  val?: number;
  translation?: string;
  pos?: string;
  status?: string;
}

/** 知識圖譜連線。Knowledge graph link. */
export interface GraphLink {
  source: string;
  target: string;
  label?: string;
  relation_id?: number;
}

/** 知識圖譜完整資料（`GET /handlers/{name}/graph`）。Complete knowledge graph data. */
export interface GraphData {
  nodes: GraphNode[];
  links: GraphLink[];
  /** 可選的名稱欄位；關聯圖譜為空陣列。Label fields to choose from. */
  label_fields: string[];
  /** 實際採用的名稱欄位；null 表示顯示 nid。The label field used, or null for the note ID. */
  label_field: string | null;
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
