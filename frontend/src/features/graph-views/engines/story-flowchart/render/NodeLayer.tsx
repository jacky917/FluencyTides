/**
 * 節點層：依排版結果絕對定位的 HTML 節點（D9）。每個節點都有穩定的 class 與 `data-*` 掛勾。
 * The node layer: absolutely positioned HTML nodes with stable hooks.
 *
 * 疊加物（徽章等）畫在節點之後的獨立圖層 `.sf-decorations`，對齊節點右上角：
 * 節點框的 `mask`（票券缺口、主題遮罩）、`filter`、`opacity` 不會裁掉或淡化它，
 * 後面才畫的相鄰節點、連線、章節帶也蓋不到它。
 * Decorations go on a separate layer after the nodes, anchored at each
 * node's top-right corner, so masks/filters/opacity on the node box and
 * later-painted neighbours can no longer clip or cover them.
 */

import { memo, type ReactNode } from 'react'
import type { GraphTheme } from '../../../core/theme'
import type { LaidNode } from '../core/layout'
import { localizedTitle, renderType, type NodeState, type StoryGraph, type StoryNode } from '../core/types'
import type { NodeRenderer } from './nodeRenderers'

/** {@link NodeLayer} 的 props。Props of {@link NodeLayer}. */
export interface NodeLayerProps {
  graph: StoryGraph
  nodes: LaidNode[]
  byKey: Map<string, StoryNode>
  theme: GraphTheme
  renderers: Record<string, NodeRenderer>
  lang?: string
  selectedNode?: string | null
  pathNodes: Set<string>
  nodeState?: (node: StoryNode) => NodeState
  decorations?: (node: StoryNode) => ReactNode
  onNodeClick?: (node: StoryNode) => void
  onNodeHover?: (node: StoryNode | null) => void
}

function tooltip(node: StoryNode, title: string): string {
  const parts = [title || node.condition || '']
  if (node.type === 'condition' && node.condition && title) parts.push(node.condition)
  if (node.chapter_label) parts.push(node.chapter_label)
  if (node.line_count) parts.push(`${node.line_count} 句`)
  return parts.filter(Boolean).join(' · ')
}

/**
 * 節點層。The node layer.
 * @param props - 見 {@link NodeLayerProps}。See {@link NodeLayerProps}.
 * @returns 節點 JSX。Node JSX.
 */
export const NodeLayer = memo(function NodeLayer({
  graph, nodes, byKey, theme, renderers, lang, selectedNode, pathNodes, nodeState, decorations, onNodeClick, onNodeHover,
}: NodeLayerProps) {
  const known = Object.keys(renderers)
  const decorated = decorations
    ? nodes.flatMap((laid) => {
        const node = byKey.get(laid.key)
        const content = node ? decorations(node) : null
        return node && content ? [{ laid, node, content }] : []
      })
    : []
  return (
    <>
      <div className="sf-nodes">
        {nodes.map((laid) => {
          const node = byKey.get(laid.key)
          if (!node) return null
          const type = renderType(node.type, known)
          const Renderer = renderers[type]
          const state = nodeState?.(node) ?? 'normal'
          const selected = selectedNode === node.key
          const onPath = pathNodes.has(node.key)
          const title = localizedTitle(node, lang)
          const mask = theme.assets.nodeMasks?.[type] ? 'custom' : undefined
          return (
            <div
              key={node.key}
              className={`sf-node sf-node--${type}`}
              data-type={node.type}
              data-key={node.key}
              data-state={state}
              data-selected={selected || undefined}
              data-on-path={onPath || undefined}
              data-narrow={laid.narrow || undefined}
              data-mask={mask}
              role="button"
              tabIndex={0}
              title={tooltip(node, title)}
              aria-label={tooltip(node, title)}
              aria-pressed={selected}
              style={{ left: laid.x, top: laid.y, width: laid.w, height: laid.h }}
              onClick={() => onNodeClick?.(node)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  onNodeClick?.(node)
                }
              }}
              onMouseEnter={() => onNodeHover?.(node)}
              onMouseLeave={() => onNodeHover?.(null)}
            >
              <Renderer
                node={node}
                title={title}
                state={state}
                selected={selected}
                onPath={onPath}
                narrow={laid.narrow}
                theme={theme}
                graph={graph}
                lang={lang}
              />
            </div>
          )
        })}
      </div>
      {decorated.length > 0 && (
        <div className="sf-decorations" aria-hidden="true">
          {decorated.map(({ laid, node, content }) => (
            <span key={node.key} className="sf-node__decorations" data-key={node.key} data-type={node.type}
              data-state={nodeState?.(node) ?? 'normal'} style={{ left: laid.x + laid.w, top: laid.y }}>
              {content}
            </span>
          ))}
        </div>
      )}
    </>
  )
})
