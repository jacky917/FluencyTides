/**
 * 開發模式的 `/story/_playground`（D15）：用 fixtures 的劇情圖套任意主題，做主題時不必連後端。
 * Dev-only playground: fixture graphs with any theme, no backend needed.
 */

import { useState } from 'react'
import { StoryFlowchart, pathTo, pathVariables, type StoryGraph } from '@/features/graph-views/engines/story-flowchart'
import { sabbatFixture } from '@/features/graph-views/engines/story-flowchart/fixtures/sabbat'
import { senrenCh3Fixture } from '@/features/graph-views/engines/story-flowchart/fixtures/senrenCh3'
import { useStoryTheme, useStoryThemeList } from '../hooks/useStoryGame'

const FIXTURES: Record<string, { label: string; graph: StoryGraph }> = {
  sabbat: { label: '魔女的夜宴（片段）', graph: sabbatFixture },
  senren: { label: '千恋＊万花 CH2–3（手工）', graph: senrenCh3Fixture },
}

/**
 * 主題與排版的預覽頁。The theme and layout playground.
 * @returns 頁面 JSX。Page JSX.
 */
export default function StoryPlayground() {
  const [fixture, setFixture] = useState('sabbat')
  const graph = FIXTURES[fixture].graph
  const [tab, setTab] = useState<string | null>(graph.tabs[0].key)
  const [node, setNode] = useState<string | null>(null)
  const [narrow, setNarrow] = useState(false)
  const { theme, themeId, choose } = useStoryTheme('_playground', 'sabbat-night')
  const themes = useStoryThemeList()
  const vars = pathVariables(node ? pathTo(graph, node) : null)
  return (
    <div className="flex h-[calc(100dvh-3rem-3rem)] flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <strong>Playground</strong>
        <select className="h-8 rounded border bg-background px-2" value={fixture}
          onChange={(e) => { setFixture(e.target.value); setTab(FIXTURES[e.target.value].graph.tabs[0].key); setNode(null) }}>
          {Object.entries(FIXTURES).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
        </select>
        <select className="h-8 rounded border bg-background px-2" value={themeId} onChange={(e) => choose(e.target.value)}>
          {(themes.data ?? []).map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
        </select>
        <label className="flex items-center gap-1"><input type="checkbox" checked={narrow} onChange={(e) => setNarrow(e.target.checked)} />窄版尺寸</label>
        {node && <span className="text-muted-foreground">選取 {node}；路徑變數 {JSON.stringify(vars)}</span>}
      </div>
      <div className="min-h-0 flex-1 overflow-hidden rounded-lg border">
        <StoryFlowchart graph={graph} tab={tab} onTabChange={(t) => { setTab(t); setNode(null) }} theme={theme} lang="ja-JP"
          selectedNode={node} pathTo={node} onNodeClick={(n) => setNode(n.key === node ? null : n.key)}
          layoutOptions={narrow ? { nodeW: 180, colGap: 210, rowGap: 96 } : undefined}
          decorations={(n) => (n.line_count && n.line_count > 600 ? <span className="sf-badge">{n.line_count}</span> : null)} />
      </div>
    </div>
  )
}
