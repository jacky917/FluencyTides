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
  /** 網頁以表單編輯 JSON 欄位（格式見 `json_field_specs`）。Editing JSON fields as forms. */
  update_json?: Operation;
  delete: Operation;
  actions: string[];
  graph: GraphCapability;
  /** 卡片頁的欄位搜尋（欄位按鈕見 `search_fields`）。Field search on the card page. */
  search?: Operation;
}

/** 欄位搜尋的一個欄位按鈕。One field-search button. */
export interface SearchField {
  /** API 用的鍵；`*` 保留給「全部欄位」。The API key. */
  key: string;
  /** 按鈕上的名稱。The label. */
  title: string;
}

/** 欄位搜尋的一個條件。One field-search condition. */
export interface SearchCondition {
  /** 欄位按鈕的鍵；`*` 表示任何欄位。The button key, or `*`. */
  field: string;
  /** 關鍵字（部分符合）。The keyword. */
  value: string;
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
  /**
   * 網頁預覽流程的草稿表單（JSON Schema），送到 `POST /handlers/{name}/draft`；沒有這個流程時為 null。
   * The draft form schema of the web preview flow, or null.
   */
  draft_schema?: Record<string, unknown> | null;
  /** JSON 欄位：不能直接修改，網頁唯讀顯示。JSON fields, read-only. */
  json_fields: string[];
  /** 能以表單編輯的 JSON 欄位：標題與每一筆的 JSON Schema。Editable JSON fields. */
  json_field_specs?: Record<string, JsonFieldSpec>;
  /** 媒體欄位：同上。Media fields, read-only. */
  media_fields: string[];
  /** 欄位搜尋的欄位按鈕；不開放時為空。The field-search buttons. */
  search_fields?: SearchField[];
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
  /** 以表單編輯 JSON 欄位。Editing JSON fields as forms. */
  update_json?: Permission;
  delete: Permission;
}

/** 一個能以表單編輯的 JSON 欄位。An editable JSON field. */
export interface JsonFieldSpec {
  title: string;
  /** 每一筆的 JSON Schema。The JSON Schema of one item. */
  item_schema: Record<string, unknown>;
}

/** 一個 JSON 欄位目前的內容。A JSON field's current content. */
export interface JsonFieldState {
  /** 解析好的清單；欄位壞掉時為空。The items; empty when the field is corrupted. */
  items: unknown[];
  /** 欄位原始字串的雜湊；寫入時送回，用來偵測別處的修改。The version to send back. */
  version: string;
  /** 欄位壞掉時的原因（這時不能編輯）。Why the field is unreadable. */
  error: string | null;
}

/** `PUT .../json/{field}` 的回應。The JSON field write result. */
export interface UpdateJsonResponse extends JsonFieldState {
  sync_warning: string | null;
}

/** `GET /notes/{note_id}`：附上負責的 handler 與權限。The generic read. */
export interface GenericNoteDetail extends NoteDetail {
  /** 負責的 handler；沒有 handler 負責這種筆記類型時為 null。The owning handler, or null. */
  handler: string | null;
  permissions: NotePermissions;
  /** 能以表單編輯的 JSON 欄位目前的內容。The editable JSON fields. */
  json_fields?: Record<string, JsonFieldState>;
}

/** `POST /handlers/{name}/notes` 的請求。The create request. */
export interface CreateNoteRequest {
  /** 牌組；handler 的建卡牌組固定時忽略，不存在時後端自動建立。The deck. */
  deck: string;
  /** 必須是該 handler 的 `create.note_type`。Must be the handler's create note type. */
  note_type: string;
  /** 標準建立是 `{欄位名: 值}`；其他 handler 依 `create_schema`。The parameters. */
  params: Record<string, unknown>;
  /** 使用者加的標籤（不能有空白、不能是系統標籤）；建卡後另外加上。User tags. */
  tags?: string[];
  /**
   * 從預覽流程的草稿建立時帶上（ID 與讀到的版本成對）；成功後後端清空草稿，版本不符時回 409。
   * The draft being created and the version read.
   */
  draft?: { id: string; version: number };
}

/** 這種卡片用過的一個標籤與次數。A tag and how many notes use it. */
export interface TagCount {
  tag: string;
  count: number;
}

/** 草稿建議的標籤。A tag suggested with a draft. */
export interface SuggestedTag {
  tag: string;
  /** 這種卡片是否用過；false 表示建卡時會新增一個標籤。Whether it already exists. */
  existing: boolean;
}

/** 草稿的生成結果：預先填好的建立參數。The generated create parameters. */
export interface DraftResult {
  params: Record<string, unknown>;
  /** LLM 建議的標籤（建卡時放進請求的 `tags`）。Suggested tags. */
  suggested_tags: SuggestedTag[];
}

/**
 * 草稿的狀態：生成中、可建立、生成失敗、從草稿建立中，以及填表建卡暫時佔用（沒有輸入與生成結果）。
 * Draft status; `form_creating` is a form create holding the slot.
 */
export type DraftStatus = 'generating' | 'ready' | 'failed' | 'creating' | 'form_creating';

/**
 * 後端的建卡草稿：全站同一時間最多一份（`docs/wip/server_drafts_FEAT_2026-11-04.md` §4.1）。
 * The backend create draft; at most one site-wide.
 */
export interface ServerDraft {
  id: string;
  /** handler 名稱。The handler. */
  handler: string;
  status: DraftStatus;
  /** 生成的輸入；填表建卡佔用時為 null。The generation input. */
  input: Record<string, unknown> | null;
  /** 生成結果，只有 ready、creating 時有值（form_creating 沒有）。The result. */
  result: DraftResult | null;
  /** 網頁的編輯狀態，第一次存檔前為 null。The web editing state. */
  state: Record<string, unknown> | null;
  /** 只有 failed 時有值。Why the generation failed. */
  error: { error_code: string; message: string } | null;
  /** 每次轉換加一；修改、重試、建立時帶上。Bumped on every change. */
  version: number;
  created_at: string;
  updated_at: string;
  generation_started_at: string | null;
}

/** 三語對話卡的一句參考答案（草稿與建卡參數）。One trilingual reference answer. */
export interface TrilingualReference {
  content: string;
  /** 說明：對象與場合、語氣的關鍵或場景補充。The note. */
  note: string;
  /** 熟識度 0–9；null 表示未標。Familiarity, or null. */
  familiarity: number | null;
}

/** 三語對話卡的建立參數（草稿就是這個形狀）。Trilingual create parameters. */
export interface TrilingualCreateParams {
  prompt: string;
  context: string;
  references_zh: TrilingualReference[];
  references_ja: TrilingualReference[];
  references_en: TrilingualReference[];
}

/**
 * 生成前發現 Anki 已有同一張卡時的決定：`overwrite` 新卡建好後刪掉舊卡、`allow` 允許重複。
 * The decision on a duplicate found before generating.
 */
export type DuplicateDecision = 'overwrite' | 'allow';

/** 409 `DUPLICATE_FOUND` 附上的舊卡（外語糾錯：同一句原文的母卡，純文字）。The existing card. */
export interface DuplicateCard {
  note_id: number;
  original_text?: string;
  correct_answer?: string;
  reorganized_expression?: string;
}

/** 外語糾錯 LLM 萃取的一個知識點（子卡）。One micro point (a micro card). */
export interface ExpressionMicroPoint {
  original_phrase: string;
  target_phrase: string;
  native_translation: string;
  context_hint: string;
  context_sentence: string;
  error_hint: string;
}

/** 外語糾錯的 LLM 結果。The expression correction LLM result. */
export interface ExpressionCorrectionResult {
  /** 原文，錯的片段以 `<u>…</u>` 標出。The original with errors marked by `<u>`. */
  error_comparison: string;
  grammar_correction: string;
  reorganized_expression: string;
  grammar_micro_points: ExpressionMicroPoint[];
  reorganized_micro_points: ExpressionMicroPoint[];
}

/** 外語糾錯的建立參數（草稿就是這個形狀）。Expression correction create parameters. */
export interface ExpressionCreateParams {
  original_text: string;
  context: string;
  source_tag: string;
  native_language: string;
  target_language: string;
  user_grammar_correction: string;
  user_reorganization: string;
  llm_result: ExpressionCorrectionResult;
  /** 要建立的子卡編號（1 起算，文法修正在前）；null 表示全部。Micro cards to create. */
  selected_indices: number[] | null;
  allow_duplicate: boolean;
  /** 覆蓋：新卡建好後刪掉的舊母卡。The old master to replace. */
  replace_note_id: number | null;
}

/** 建卡結果。The created notes. */
export interface CreateNoteResponse {
  note_ids: number[];
  /** 實際建立的牌組。The deck actually used. */
  deck: string;
  /**
   * 建立後要注意的事（加標籤失敗、建立後的後續處理例如刪舊卡失敗、同步 AnkiWeb 失敗），以換行分隔；
   * 卡片仍已建立。Warnings after creating, if any.
   */
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
// 牌組（GET /decks、POST /decks）
// ============================================================================

/** Anki 牌組基本資訊。Basic Anki deck information. */
export interface AnkiDeckInfo {
  deck_name: string;
  deck_id: number;
}

/** `POST /decks` 的回應（deck_create 計畫書 §5.3）。The create-deck response. */
export interface CreateDeckResponse {
  /** Anki 裡實際的牌組名稱（後端整理過空白），用它選取新牌組。The name as stored in Anki. */
  deck_name: string;
  deck_id: number;
  /** 同步 AnkiWeb 失敗時的提醒；牌組仍已建立。A warning when the sync failed. */
  sync_warning: string | null;
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
