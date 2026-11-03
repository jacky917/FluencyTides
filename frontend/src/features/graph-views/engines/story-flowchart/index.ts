/**
 * 劇情流程圖模組的唯一公開入口（D7）。模組內不得 import 網站的 API、頁面、hooks、路由
 * （ESLint `no-restricted-imports` 強制），可以整包搬走。
 * The single public entry of the story-flowchart module. It must not import
 * the site's API, pages, hooks or router (enforced by ESLint).
 */

export { StoryFlowchart, VIRTUALIZE_THRESHOLD } from './render/StoryFlowchart'
export type { StoryFlowchartProps } from './render/StoryFlowchart'
export { DEFAULT_RENDERERS } from './render/nodeRenderers'
export type { NodeRenderer, NodeRenderProps } from './render/nodeRenderers'
export { assign, project, layoutTab, nodeWidth } from './core/layout'
export type { StoryGrid, StoryLayout, LaidNode, LaidEdge, LayoutOptions } from './core/layout'
export { pathTo, pathVariables, reachableInTab } from './core/path'
export type { StoryPath } from './core/path'
export * from './core/types'
export * from '../../core/theme'
