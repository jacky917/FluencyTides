/**
 * 劇情流程圖主元件（D7、D9、D10）：節點用 HTML、連線用 SVG，兩層共用 `project` 產生的座標。
 * The story flowchart: HTML nodes over an SVG edge layer sharing the same
 * projected coordinates.
 *
 * 模組本身不知道「卡片」「存檔」：網站整合層把卡片統計轉成 `nodeState` 與 `decorations` 傳進來，
 * 互動以 callback 回報。
 * The module knows nothing about cards or saves; the host passes
 * `nodeState`/`decorations` and receives callbacks.
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { DEFAULT_THEME, themeToCssVars, type GraphTheme, type GraphThemeSize } from '../../../core/theme'
import { layoutTab, type LayoutOptions } from '../core/layout'
import { pathTo as computePath } from '../core/path'
import { localizedTitle, type NodeState, type StoryEdge, type StoryGraph, type StoryNode } from '../core/types'
import { EdgeLayer } from './EdgeLayer'
import { Minimap } from './Minimap'
import { NodeLayer } from './NodeLayer'
import { DEFAULT_RENDERERS, type NodeRenderer } from './nodeRenderers'
import { Overview } from './Overview'
import { ScrollControls } from './ScrollControls'
import { TabBar } from './TabBar'
import '../storyFlowchart.css'

/** 節點超過這個數量時，節點層與連線層只渲染可視範圍上下各一屏。Virtualization threshold. */
export const VIRTUALIZE_THRESHOLD = 500

/** {@link StoryFlowchart} 的 props。Props of {@link StoryFlowchart}. */
export interface StoryFlowchartProps {
  /** 劇情圖（schema v1）。The graph. */
  graph: StoryGraph
  /** 分頁 key；null 為總覽。Tab key, or null for the overview. */
  tab: string | null
  onTabChange?: (tab: string | null) => void
  theme?: GraphTheme
  /** 顯示語言（節點、分頁的 `titles`）。Display language. */
  lang?: string
  selectedNode?: string | null
  onNodeClick?: (node: StoryNode) => void
  onNodeHover?: (node: StoryNode | null) => void
  /** 外部決定的節點狀態。Host-decided node state. */
  nodeState?: (node: StoryNode) => NodeState
  /** 要畫成「選取路徑」的終點。End of the highlighted path. */
  pathTo?: string | null
  /** 節點上的疊加物（徽章等）。Per-node decorations. */
  decorations?: (node: StoryNode) => ReactNode
  /** 覆寫某類節點的外觀。Renderer overrides by type. */
  renderers?: Partial<Record<string, NodeRenderer>>
  /** 覆寫主題尺寸（例如窄版）。Size overrides. */
  layoutOptions?: Partial<GraphThemeSize> & LayoutOptions
  /** 分頁按鈕上的徽章（例如搜尋命中數）。Tab badges. */
  tabBadges?: Record<string, ReactNode>
  /** 要捲到的節點；`focusSeq` 改變時重新捲動。Node to scroll to. */
  focusNode?: string | null
  focusSeq?: number
  /** 進入分頁時的捲動位置（沒有 focusNode 時）。Initial scroll for a tab. */
  initialScrollTop?: number
  onScrollChange?: (tab: string, top: number) => void
  /** 小地圖要標示的節點。Nodes marked on the minimap. */
  minimapHighlight?: Set<string>
  showMinimap?: boolean
  className?: string
  style?: CSSProperties
}

/**
 * 劇情流程圖。The story flowchart.
 * @param props - 見 {@link StoryFlowchartProps}。See {@link StoryFlowchartProps}.
 * @returns 流程圖 JSX。Flowchart JSX.
 */
export function StoryFlowchart({
  graph, tab, onTabChange, theme = DEFAULT_THEME, lang, selectedNode, onNodeClick, onNodeHover, nodeState, pathTo,
  decorations, renderers, layoutOptions, tabBadges, focusNode, focusSeq = 0, initialScrollTop, onScrollChange,
  minimapHighlight, showMinimap = true, className, style,
}: StoryFlowchartProps) {
  const size = useMemo(() => ({ ...theme.size, ...(layoutOptions ?? {}) }) as GraphThemeSize, [theme.size, layoutOptions])
  const sizeKey = JSON.stringify(size)
  const activeTab = tab && graph.tabs.some((t) => t.key === tab) ? tab : null
  // 每個分頁的排版結果以 (graph, tab, size) memo
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const layout = useMemo(() => (activeTab ? layoutTab(graph, activeTab, size, layoutOptions) : null), [graph, activeTab, sizeKey])
  const byKey = useMemo(() => new Map(graph.nodes.map((n) => [n.key, n])), [graph])
  const allRenderers = useMemo(() => ({ ...DEFAULT_RENDERERS, ...(renderers ?? {}) }) as Record<string, NodeRenderer>, [renderers])
  const path = useMemo(() => (pathTo ? computePath(graph, pathTo) : null), [graph, pathTo])
  const pathNodes = useMemo(() => new Set(path && byKey.get(pathTo!)?.tab === activeTab ? path.nodes : []), [path, byKey, pathTo, activeTab])
  const pathEdges = useMemo(() => new Set<StoryEdge>(path && byKey.get(pathTo!)?.tab === activeTab ? path.edges : []), [path, byKey, pathTo, activeTab])
  const cssVars = useMemo(() => themeToCssVars(theme), [theme])

  const viewportRef = useRef<HTMLDivElement>(null)
  const [view, setView] = useState({ top: 0, left: 0, height: 800, width: 800 })
  const frame = useRef(0)
  const readView = useCallback(() => {
    const el = viewportRef.current
    if (!el) return
    setView({ top: el.scrollTop, left: el.scrollLeft, height: el.clientHeight, width: el.clientWidth })
  }, [])
  const onScroll = useCallback(() => {
    cancelAnimationFrame(frame.current)
    frame.current = requestAnimationFrame(() => {
      readView()
      if (activeTab && viewportRef.current) onScrollChange?.(activeTab, viewportRef.current.scrollTop)
    })
  }, [readView, activeTab, onScrollChange])

  useEffect(() => {
    const el = viewportRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => readView())
    ro.observe(el)
    return () => ro.disconnect()
  }, [readView])

  const scrollTo = useCallback((top: number, left?: number, smooth = true) => {
    const el = viewportRef.current
    if (!el) return
    el.scrollTo({ top, left: left ?? el.scrollLeft, behavior: smooth ? 'smooth' : 'auto' })
  }, [])

  const scrollToNode = useCallback((key: string, smooth = true) => {
    const el = viewportRef.current
    const laid = layout?.nodeMap[key]
    if (!el || !laid) return false
    scrollTo(Math.max(0, laid.y - el.clientHeight * 0.35), Math.max(0, laid.x + laid.w / 2 - el.clientWidth / 2), smooth)
    return true
  }, [layout, scrollTo])

  // 換分頁：有 focusNode 就捲到它，否則還原記住的位置
  const lastTab = useRef<string | null | undefined>(undefined)
  useLayoutEffect(() => {
    if (!layout || lastTab.current === activeTab) return
    lastTab.current = activeTab
    if (focusNode && layout.nodeMap[focusNode]) scrollToNode(focusNode, false)
    else {
      // 水平方向對準分頁起點（主欄），分支多的分頁才不會一開始就看不到節點
      const start = layout.nodeMap[graph.tabs.find((t) => t.key === activeTab)?.start ?? '']
      const width = viewportRef.current?.clientWidth ?? 0
      scrollTo(initialScrollTop ?? 0, start ? Math.max(0, start.cx - width / 2) : 0, false)
    }
    readView()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layout, activeTab])
  useLayoutEffect(() => {
    if (activeTab === null) lastTab.current = null
  }, [activeTab])
  // 同一分頁內 focusNode 改變（或 focusSeq 改變）時捲過去
  useEffect(() => {
    if (focusNode && layout?.nodeMap[focusNode] && lastTab.current === activeTab) scrollToNode(focusNode)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusNode, focusSeq])

  const handleNodeClick = useCallback((node: StoryNode) => {
    if (node.type === 'route_exit' && node.target_tab && graph.tabs.some((t) => t.key === node.target_tab)) {
      onTabChange?.(node.target_tab)
      return
    }
    onNodeClick?.(node)
  }, [graph.tabs, onNodeClick, onTabChange])

  const virtual = !!layout && layout.nodes.length > VIRTUALIZE_THRESHOLD
  const lo = view.top - view.height
  const hi = view.top + 2 * view.height
  const visibleNodes = layout ? (virtual ? layout.nodes.filter((n) => n.y + n.h >= lo && n.y <= hi) : layout.nodes) : []
  const visibleEdges = layout ? (virtual ? layout.edges.filter((e) => e.maxY >= lo && e.minY <= hi) : layout.edges) : []
  const backLabel = useCallback((key: string) => {
    const node = byKey.get(key)
    return node ? localizedTitle(node, lang) || key : key
  }, [byKey, lang])

  return (
    <div className={`sf-root${className ? ` ${className}` : ''}`} data-theme={theme.id} data-view={activeTab ? 'tab' : 'overview'}
      style={{ ...(cssVars as CSSProperties), ...style }}>
      {theme.css && <link rel="stylesheet" href={theme.css} />}
      <div className="sf-body">
        <div ref={viewportRef} className="sf-viewport" onScroll={onScroll}>
          {layout ? (
            <div className="sf-canvas" style={{ width: layout.width, height: layout.height }}>
              {layout.bands.map((b) => (
                <div key={`band ${b.rank}`} className="sf-band" style={{ top: b.y, height: b.height }}>
                  {/* 標題貼在可視範圍左緣，水平捲動時也看得到 */}
                  <span className="sf-band__head">
                    <span className="sf-band__accent" aria-hidden="true" />
                    <span className="sf-band__icon" aria-hidden="true" />
                    <span className="sf-band__label">{b.label}</span>
                  </span>
                </div>
              ))}
              <EdgeLayer width={layout.width} height={layout.height} edges={visibleEdges} backs={layout.backs}
                size={size} pathEdges={pathEdges} backLabel={backLabel} onBackClick={(k) => scrollToNode(k)} />
              <NodeLayer graph={graph} nodes={visibleNodes} byKey={byKey} theme={theme} renderers={allRenderers}
                lang={lang} selectedNode={selectedNode} pathNodes={pathNodes} nodeState={nodeState}
                decorations={decorations} onNodeClick={handleNodeClick} onNodeHover={onNodeHover} />
            </div>
          ) : (
            <Overview graph={graph} theme={theme} renderers={allRenderers} lang={lang} badges={tabBadges}
              nodeState={nodeState} decorations={decorations} onOpen={(k) => onTabChange?.(k)} />
          )}
        </div>
        {layout && (
          <ScrollControls bands={layout.bands} scrollTop={view.top} pageHeight={view.height} theme={theme}
            onScrollTo={(top) => scrollTo(top)} />
        )}
        <aside className="sf-side">
          <TabBar tabs={graph.tabs} active={activeTab} theme={theme} lang={lang} badges={tabBadges} onChange={onTabChange} />
          {layout && showMinimap && (
            <Minimap layout={layout} viewTop={view.top} viewLeft={view.left} viewHeight={view.height}
              viewWidth={view.width} highlighted={minimapHighlight} onScrollTo={(top, left) => scrollTo(top, left, false)} />
          )}
        </aside>
      </div>
    </div>
  )
}
