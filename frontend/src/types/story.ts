/**
 * 劇情流程圖 API 的型別（後端 `app/schemas/story.py`）。劇情圖本身的型別在流程圖模組。
 * Story API types; graph types live in the flowchart module.
 */

export type { StoryGraph, StoryNode, StoryEdge, StoryTab } from '@/features/graph-views/engines/story-flowchart'

/** 前端依此決定要顯示哪些功能。Feature switches. */
export interface StoryCapabilities {
  saves: boolean
  thumbs: boolean
  annotations: string[]
  route_presets: boolean
  languages: string[]
}

/** 一種卡片類型。A card type. */
export interface StoryCardType {
  card_type: string
  label: string
  note_types: string[]
}

/** `GET /story/games` 的一項。One game. */
export interface StoryGameSummary {
  game_id: string
  title: string
  titles: Record<string, string>
  build_id: number
  capabilities: StoryCapabilities
  card_types: StoryCardType[]
}

/** 存檔欄位。A save slot. */
export interface StorySaveSlot {
  id: string
  label: string
  filename: string
}

/** `GET /story/games/{game}`。The manifest. */
export interface StoryManifest {
  game_id: string
  titles: Record<string, string>
  build_id: number
  capabilities: StoryCapabilities
  languages: { code: string; label: string }[]
  line_attrs: { key: string; label: string; kind: 'flag' | 'text' }[]
  annotation_kinds: string[]
  card_types: StoryCardType[]
  save_slots: StorySaveSlot[]
  save_install_hint: string | null
  route_presets: string[]
  default_theme: string | null
  stats: Record<string, unknown>
}

/** 劇情圖回應（多一個 `build_id`）。The graph response. */
export type StoryGraphResponse = import('@/features/graph-views/engines/story-flowchart').StoryGraph & { build_id: number }

/** 各節點的註記統計與搜尋索引。Annotation stats and index. */
export interface StoryAnnotations {
  build_id: number
  /** `{node_key: {kind: {子分類: 數量}}}` */
  nodes: Record<string, Record<string, Record<string, number>>>
  /** `{kind: {索引名: {詞: [node_key]}}}` */
  index: Record<string, Record<string, Record<string, string[]>>>
}

/** 一句台詞上的卡片註記。Cards on a line. */
export interface StoryCardAnnotation {
  card_type: string
  label: string
  verb: string | null
  note_ids: number[]
}

/** 抽屜的一句台詞。A line. */
export interface StoryLine {
  line_id: number
  seq: number
  kind: 'line' | 'choice' | 'bgv' | string
  anchor_key: string | null
  point: number | null
  speaker: string | null
  speakers: Record<string, string>
  texts: Record<string, string>
  voice: string | null
  thumb: string | null
  chapter_label: string | null
  attrs: Record<string, unknown>
  annotations: { cards?: StoryCardAnnotation[] } & Record<string, unknown>
  can_save: boolean
}

/** 一頁台詞。A page of lines. */
export interface StoryLinesPage {
  node_key: string
  build_id: number
  total: number
  offset: number
  lines: StoryLine[]
  next_annotated_seq: number | null
}

/** 台詞的位置。Where a line is. */
export interface StoryLocation {
  game_id: string
  build_id: number
  tab: string
  node_key: string
  line_id: number
  seq: number
}
