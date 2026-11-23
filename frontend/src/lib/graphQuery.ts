/**
 * Knowledge Graph 頁的選擇規則（handlers 計畫書 D12、§6）：能畫圖譜的 handler、選單的預設值、
 * 視角記憶的範圍、節點的提示文字。
 *
 * Selection rules of the Knowledge Graph page.
 */

import type { GraphNode, HandlerInfo } from '../types/api'

/** 單字卡：原本寫死的圖譜，視角記憶沿用舊的鍵。The former hard-coded graph. */
export const LEGACY_GRAPH_HANDLER = 'vocabulary_mining'

/**
 * 開放圖譜的 handler（本次只有單字卡）。Handlers offering a graph.
 * @param handlers - `GET /handlers`。The handlers.
 * @returns 依註冊順序。In registration order.
 */
export function graphHandlers(handlers: HandlerInfo[] | undefined): HandlerInfo[] {
  return (handlers ?? []).filter((handler) => handler.capabilities.graph.supported)
}

/**
 * 要顯示哪個 handler 的圖譜：記住的那個還能畫就用它，否則用第一個。
 * Which handler's graph to show.
 * @param available - 開放圖譜的 handler。Handlers offering a graph.
 * @param stored - 記住的 handler 名稱。The remembered handler.
 * @returns handler；沒有任何 handler 開放圖譜時為 undefined。The handler, if any.
 */
export function pickGraphHandler(available: HandlerInfo[], stored: string | null): HandlerInfo | undefined {
  return available.find((handler) => handler.name === stored) ?? available[0]
}

/**
 * 視角記憶（縮放、中心、節點位置）的範圍。單字卡、不限筆記類型時沿用原本以牌組為鍵的紀錄，
 * 其他組合各自分開記。名稱欄位不影響節點的 id，所以不分開。
 * The scope of the remembered view; the vocabulary graph keeps its old key.
 * @param handlerName - handler 名稱。The handler.
 * @param deck - 牌組（全部牌組時為頁面的特殊值）。The deck.
 * @param noteType - 只看的筆記類型。The note type filter.
 * @returns 範圍字串。The scope.
 */
export function graphViewScope(handlerName: string, deck: string, noteType: string | null): string {
  if (handlerName === LEGACY_GRAPH_HANDLER && noteType === null) return deck
  return `${handlerName}|${noteType ?? '*'}|${deck}`
}

/**
 * 節點的提示文字：名稱、副標，關聯圖譜另加學習狀態（沒有狀態的幽靈節點標「未建卡」）。
 * The node tooltip.
 * @param node - 節點。The node.
 * @param relationGraph - 是否為關聯圖譜。Whether this is a relation graph.
 * @returns 提示文字。The tooltip.
 */
export function nodeTooltip(node: GraphNode, relationGraph: boolean): string {
  const label = node.label ?? node.id
  const head = node.translation ? `${label} (${node.translation})` : label
  if (!relationGraph) return head
  return `${head} - ${node.status ?? '未建卡'}`
}

/**
 * 圖譜查詢的鍵：`['graph', handler, 牌組, 筆記類型, 名稱欄位, 層數]`。
 * The graph query key.
 * @param handlerName - handler 名稱。The handler.
 * @param deck - 牌組。The deck.
 * @param noteType - 只看的筆記類型。The note type filter.
 * @param labelField - 節點名稱欄位。The label field.
 * @param depth - 樹狀圖譜展開的層數；不是樹狀圖譜時為 null。The tree depth, or null.
 * @returns 查詢鍵。The query key.
 */
export function graphQueryKey(
  handlerName: string | null,
  deck: string,
  noteType: string | null,
  labelField: string | null,
  depth: number | null,
) {
  return ['graph', handlerName, deck, noteType, labelField, depth] as const
}

/**
 * 換查詢時要不要先留著上一份圖：只有層數不同（同一棵樹展開多或少）時留著，新的回來再換，畫面不會先變成
 * 讀取中的灰框、圖也不會重新掛載。換牌組、換卡片類型時不留，免得先看到別的牌組或形狀不同的圖。
 * Whether to keep showing the previous graph while the next loads: only when just the depth changed.
 * @param previous - 上一個查詢鍵（沒有時為 undefined）。The previous key.
 * @param next - 這次的查詢鍵。The next key.
 * @returns 只有層數不同時為 true。True when only the depth differs.
 */
export function onlyDepthChanged(previous: readonly unknown[] | undefined, next: readonly unknown[]): boolean {
  if (!previous || previous.length !== next.length) return false
  const last = next.length - 1
  return previous.slice(0, last).every((part, index) => part === next[index]) && previous[last] !== next[last]
}
