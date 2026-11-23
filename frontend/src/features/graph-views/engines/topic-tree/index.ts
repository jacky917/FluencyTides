/**
 * 主題樹引擎的唯一公開入口（比照劇情流程圖 D7）：三語對話卡「卡片＋同名子牌組」的樹。模組內不 import 網站的
 * API、頁面、hooks、路由（ESLint `no-restricted-imports` 強制），顏色來自共用的圖主題。
 * The single public entry of the topic tree engine; it does not import the site and is coloured by the shared
 * graph theme.
 */

export { MIN_TEXT_PX, TopicTreeGraph, topicTooltip, treeColors } from './render/TopicTreeGraph'
export type { TopicTreeGraphProps } from './render/TopicTreeGraph'
export {
  LEVEL_GAP,
  NODE_HEIGHT,
  NODE_WIDTH,
  ROOT_ID,
  ROW_GAP,
  deckCrumbs,
  isFolded,
  layoutTree,
  linkEnd,
  nodeAction,
} from './core/layout'
export type { TreePosition } from './core/layout'
export type { TopicTreeData, TopicTreeLink, TopicTreeNode } from './core/types'
