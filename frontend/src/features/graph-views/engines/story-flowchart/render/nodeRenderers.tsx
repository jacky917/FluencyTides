/**
 * 預設的節點渲染器，以節點類型為鍵（D9）。外框 `div.sf-node` 由 NodeLayer 負責，這裡只畫內容；
 * 形狀與顏色全部由 CSS（`.sf-node--<type>`）與主題變數決定。
 * Default node renderers keyed by type. The wrapper is drawn by NodeLayer;
 * shapes and colors come from CSS and theme variables.
 *
 * 疊加物（徽章等）不由渲染器畫：節點框可能有 `mask`（票券缺口、主題遮罩）、`filter`、`opacity`，
 * 畫在框裡會被裁掉或一起變淡，也會被後面才畫的相鄰節點蓋住。NodeLayer 把它們畫在所有節點之上的
 * 獨立圖層（`.sf-decorations`），總覽則畫在節點框外面。
 * Decorations are not drawn by renderers: node boxes may be masked, filtered
 * or translucent, and later siblings would paint over them. NodeLayer draws
 * them on a separate layer above all nodes.
 */

import type { ReactNode } from 'react'
import type { GraphTheme } from '../../../core/theme'
import type { NodeState, StoryGraph, StoryNode } from '../core/types'

/** 渲染器收到的資料。What a renderer receives. */
export interface NodeRenderProps {
  node: StoryNode
  title: string
  state: NodeState
  selected: boolean
  onPath: boolean
  narrow: boolean
  theme: GraphTheme
  graph: StoryGraph
  lang?: string
}

/** 節點渲染器。A node renderer. */
export type NodeRenderer = (props: NodeRenderProps) => ReactNode

const titleSpan = (title: string) => <span className="sf-node__title">{title}</span>

/** 一般場景、選項、合流、選項場景：標題。The common renderer. */
export const TextNode: NodeRenderer = ({ title }) => titleSpan(title)

/** 條件菱形：四格窗，不顯示文字（條件放在 tooltip）。The condition diamond. */
export const ConditionNode: NodeRenderer = () => (
  <span className="sf-node__shape" aria-hidden="true">
    <span className="sf-node__shape-fill" />
  </span>
)

/** 結局。The ending node. */
export const EndingNode: NodeRenderer = ({ title }) => (
  <>
    <span className="sf-node__ending-mark" aria-hidden="true">END</span>
    {titleSpan(title)}
  </>
)

/** 路線出口：和分頁按鈕同款的卡片（頭像＋角色名）。The route-exit card. */
export const RouteExitNode: NodeRenderer = ({ node, title, theme, graph }) => {
  const tab = graph.tabs.find((t) => t.key === node.target_tab)
  const avatar = tab?.avatar ? theme.assets.tabAvatars?.[tab.avatar] : undefined
  return (
    <>
      {avatar && <img className="sf-node__avatar" src={avatar} alt="" draggable={false} />}
      {titleSpan(title)}
      <span className="sf-node__exit-arrow" aria-hidden="true">›</span>
    </>
  )
}

/** 預設渲染器表。The default renderer table. */
export const DEFAULT_RENDERERS: Record<string, NodeRenderer> = {
  scene: TextNode,
  choice: TextNode,
  option: TextNode,
  merge: TextNode,
  condition: ConditionNode,
  ending: EndingNode,
  route_exit: RouteExitNode,
}
