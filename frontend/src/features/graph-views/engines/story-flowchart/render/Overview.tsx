/**
 * オーバービュー（總覽）：每個分頁一列，左邊分頁卡、右邊該分頁的第一個節點（flowchart_spec §6）。
 * The overview: one row per tab with its card and first node.
 */

import type { ReactNode } from 'react'
import type { GraphTheme } from '../../../core/theme'
import { localizedTitle, renderType, type NodeState, type StoryGraph, type StoryNode } from '../core/types'
import type { NodeRenderer } from './nodeRenderers'

/** {@link Overview} 的 props。Props of {@link Overview}. */
export interface OverviewProps {
  graph: StoryGraph
  theme: GraphTheme
  renderers: Record<string, NodeRenderer>
  lang?: string
  badges?: Record<string, ReactNode>
  nodeState?: (node: StoryNode) => NodeState
  decorations?: (node: StoryNode) => ReactNode
  onOpen: (tab: string) => void
}

/**
 * 總覽。The overview.
 * @param props - 見 {@link OverviewProps}。See {@link OverviewProps}.
 * @returns 總覽 JSX。Overview JSX.
 */
export function Overview({ graph, theme, renderers, lang, badges, nodeState, decorations, onOpen }: OverviewProps) {
  const byKey = new Map(graph.nodes.map((n) => [n.key, n]))
  const tabs = [...graph.tabs].sort((a, b) => a.order - b.order)
  const known = Object.keys(renderers)
  return (
    <div className="sf-overview">
      {tabs.map((tab) => {
        const node = byKey.get(tab.start)
        const lines = graph.nodes.filter((n) => n.tab === tab.key).reduce((s, n) => s + (n.line_count ?? 0), 0)
        const avatar = tab.avatar ? theme.assets.tabAvatars?.[tab.avatar] : undefined
        const type = node ? renderType(node.type, known) : 'scene'
        const Renderer = renderers[type]
        return (
          <div key={tab.key} className="sf-overview__row" data-tab={tab.key}>
            <button type="button" className="sf-tab sf-overview__tab" onClick={() => onOpen(tab.key)}>
              {avatar && <img className="sf-tab__avatar" src={avatar} alt="" draggable={false} />}
              <span className="sf-tab__title">{localizedTitle(tab, lang)}</span>
              {badges?.[tab.key] != null && <span className="sf-tab__badge">{badges[tab.key]}</span>}
            </button>
            {node && (
              <div
                className={`sf-node sf-node--${type} sf-overview__node`}
                data-type={node.type}
                data-state={nodeState?.(node) ?? 'normal'}
                role="button"
                tabIndex={0}
                onClick={() => onOpen(tab.key)}
                onKeyDown={(e) => e.key === 'Enter' && onOpen(tab.key)}
                title={`${localizedTitle(node, lang)} · ${lines.toLocaleString()} 句`}
              >
                <Renderer node={node} title={localizedTitle(node, lang)} state="normal" selected={false}
                  onPath={false} narrow={false} decorations={decorations?.(node)} theme={theme} graph={graph} lang={lang} />
              </div>
            )}
            <span className="sf-overview__meta">{lines.toLocaleString()} 句</span>
          </div>
        )
      })}
    </div>
  )
}
