/**
 * API 型別定義
 * 與後端 FastAPI (Pydantic models) 的回傳結構嚴格對齊
 */

// ============================================================================
// 卡片生成與查詢 (Cards)
// ============================================================================

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

export interface CardGenerateResponse {
  note_id: number;
  message: string;
  deck_name: string;
  model_name: string;
}

export interface AnkiModelInfo {
  model_name: string;
  model_file_name: string;
  fields: string[];
  has_llm_schema: boolean;
}

export interface AnkiDeckInfo {
  deck_name: string;
  deck_id: number;
}

export interface ErrorResponse {
  error_code: string;
  message: string;
  details?: unknown;
}

// ============================================================================
// 知識圖譜 (Knowledge Graph)
// ============================================================================

export interface GraphNode {
  id: string; // Expression
  group: number; // 群組或類型，用於顏色區分
  val: number; // 節點大小
  label: string;
  translation?: string; // 中文翻譯
  pos?: string; // 詞性
  source_deck?: string;
  note_id?: number;
}

export interface GraphLink {
  source: string;
  target: string;
  label?: string;
}

export interface CardDetail {
  note_id: number;
  model_name: string;
  tags: string[];
  fields: Record<string, string>;
}

export interface CardRelationCreate {
  source_note_id: number | null;
  target_note_id: number | null;
  relation_type: string;
  source_label: string;
  target_label: string;
}

export interface GraphData {
  nodes: GraphNode[];
  links: GraphLink[];
}
