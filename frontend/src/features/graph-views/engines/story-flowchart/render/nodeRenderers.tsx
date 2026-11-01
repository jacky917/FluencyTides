/**
 * 預設的節點渲染器，以節點類型為鍵（D9）。外框 `div.sf-node` 由 NodeLayer 負責，這裡只畫內容；
 * 形狀與顏色全部由 CSS（`.sf-node--<type>`）與主題變數決定。
 * Default node renderers keyed by type. The wrapper is drawn by NodeLayer;
 * shapes and colors come from CSS and theme variables.
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
  decorations: ReactNode
  theme: GraphTheme
  graph: StoryGraph
  lang?: string
}

/** 節點渲染器。A node renderer. */
export type NodeRenderer = (props: NodeRenderProps) => ReactNode

const titleSpan = (title: string) => <span className="sf-node__title">{title}</span>

const decorationSpan = (decorations: ReactNode) =>
  decorations ? <span className="sf-node__decorations">{decorations}</span> : null

/** 一般場景、選項、合流、選項場景：標題＋疊加物。The common renderer. */
export const TextNode: NodeRenderer = ({ title, decorations }) => (
  <>
    {titleSpan(title)}
    {decorationSpan(decorations)}
  </>
)

/** 條件菱形：四格窗，不顯示文字（條件放在 tooltip）。The condition diamond. */
export const ConditionNode: NodeRenderer = ({ decorations }) => (
  <>
    <span className="sf-node__shape" aria-hidden="true">
      <span className="sf-node__shape-fill" />
    </span>
    {decorationSpan(decorations)}
  </>
)

/** 結局。The ending node. */
export const EndingNode: NodeRenderer = ({ title, decorations }) => (
  <>
    <span className="sf-node__ending-mark" aria-hidden="true">END</span>
    {titleSpan(title)}
    {decorationSpan(decorations)}
  </>
)

/** 路線出口：和分頁按鈕同款的卡片（頭像＋角色名）。The route-exit card. */
export const RouteExitNode: NodeRenderer = ({ node, title, theme, graph, decorations }) => {
  const tab = graph.tabs.find((t) => t.key === node.target_tab)
  const avatar = tab?.avatar ? theme.assets.tabAvatars?.[tab.avatar] : undefined
  return (
    <>
      {avatar && <img className="sf-node__avatar" src={avatar} alt="" draggable={false} />}
      {titleSpan(title)}
      <span className="sf-node__exit-arrow" aria-hidden="true">›</span>
      {decorationSpan(decorations)}
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
