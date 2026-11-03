/**
 * 劇情圖（schema v1）的型別，與後端 `app/schemas/story.py` 對應。
 * Story graph (schema v1) types mirroring the backend schema.
 *
 * 本模組不依賴網站（API、路由、卡片）：資料由 props 傳入。
 * The module is site-independent; data comes in through props.
 */

/** 核心節點類型；另外允許 `x-<名稱>` 的自訂類型。Core node types (plus `x-*`). */
export type CoreNodeType = 'scene' | 'choice' | 'option' | 'merge' | 'condition' | 'ending' | 'route_exit'
/** 節點類型。A node type. */
export type NodeType = CoreNodeType | `x-${string}`

/** 邊的種類。Edge kinds. */
export type EdgeKind = 'normal' | 'branch' | 'merge' | 'cross_tab' | 'back'
/** 邊的旗標。Edge flags. */
export type EdgeFlag = 'inferred' | 'unlock'

/** 一個分頁。A tab. */
export interface StoryTab {
  key: string
  title: string
  titles?: Record<string, string> | null
  order: number
  start: string
  avatar?: string | null
}

/** 一個節點。A node. */
export interface StoryNode {
  key: string
  tab: string
  type: NodeType
  title?: string | null
  titles?: Record<string, string> | null
  chapter?: string | null
  chapter_label?: string | null
  line_count?: number
  condition?: string | null
  /** `route_exit` 指向的分頁。Target tab of a route exit. */
  target_tab?: string | null
  meta?: Record<string, unknown>
}

/** 選這條邊會怎麼改變數。An edge effect. */
export interface EdgeEffect {
  var: string
  op: 'set' | 'add'
  value: number | string
}

/** 一條邊（JSON 鍵是 `from`）。An edge. */
export interface StoryEdge {
  from: string
  to: string
  kind?: EdgeKind
  order?: number | null
  label?: string | null
  flags?: EdgeFlag[]
  effects?: EdgeEffect[]
  condition_vars?: Record<string, unknown> | null
}

/** 整份劇情圖。The whole graph. */
export interface StoryGraph {
  schema_version: number
  build_id?: number
  tabs: StoryTab[]
  nodes: StoryNode[]
  edges: StoryEdge[]
}

/** 外部決定的節點狀態（D10）。Node state decided by the host. */
export type NodeState = 'normal' | 'dim' | 'highlight'

/** 核心節點類型清單。The core node types. */
export const CORE_NODE_TYPES: readonly CoreNodeType[] = [
  'scene', 'choice', 'option', 'merge', 'condition', 'ending', 'route_exit',
]

/**
 * 節點的渲染類型：自訂類型找不到渲染器時退回 `scene`。
 * The render type; unknown custom types fall back to `scene`.
 * @param type - 節點類型。The node type.
 * @param known - 有渲染器的類型。Types that have a renderer.
 * @returns 要用的類型。The type to render as.
 */
export function renderType(type: string, known: Iterable<string> = CORE_NODE_TYPES): string {
  for (const k of known) if (k === type) return type
  return 'scene'
}

/**
 * 依語言取顯示文字：`titles[lang]` → `title`。
 * Picks the localized title.
 * @param node - 節點或分頁。A node or tab.
 * @param lang - 語言代碼。Language code.
 * @returns 文字或空字串。The text, or ''.
 */
export function localizedTitle(
  node: { title?: string | null; titles?: Record<string, string> | null },
  lang?: string,
): string {
  if (lang && node.titles?.[lang]) return node.titles[lang]
  return node.title ?? ''
}
