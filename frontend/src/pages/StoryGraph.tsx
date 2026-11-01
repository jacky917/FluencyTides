/**
 * 劇情流程圖頁 `/story/:gameId`（D7 網站整合層）：取資料、網址狀態（D16）、卡片篩選與動詞搜尋、
 * 台詞抽屜、主題選擇。流程圖模組本身不知道卡片與存檔，由這裡轉成 `nodeState` 與 `decorations`。
 * The story page: data, URL state, card filter and verb search, the line
 * drawer and theme selection; the flowchart module stays site-agnostic.
 *
 * `/story` 列出遊戲（只有一款時直接導向）。`/story` lists games (redirects when only one).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, Navigate, useParams, useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import { AlertTriangle, ChevronLeft, ChevronRight, Loader2, Search, X } from 'lucide-react'
import {
  StoryFlowchart,
  localizedTitle,
  type NodeState,
  type StoryNode,
} from '@/features/graph-views/engines/story-flowchart'
import { StoryLineDrawer } from '../components/story/StoryLineDrawer'
import { ErrorNote } from '../components/shared/ErrorNote'
import { useStoryAnnotations, useStoryGames, useStoryManifest, useStoryTheme, useStoryThemeList } from '../hooks/useStoryGame'
import { useStoryGraph } from '../hooks/useStoryGraph'
import { describeApiError } from '../lib/apiError'
import { formatStoryParams, parseStoryParams, scrollStorageKey, shouldPushHistory, verbHits, type StoryParams } from '../lib/storyParams'
import { cn } from '../lib/utils'

const PAGE_HEIGHT = 'h-[calc(100dvh-3rem-2rem)] md:h-[calc(100dvh-3rem-3rem)]'
const NODE_LANG = 'ja-JP'

function readScroll(key: string): number | undefined {
  try {
    const raw = sessionStorage.getItem(key)
    return raw ? Number(raw) : undefined
  } catch {
    return undefined
  }
}

/**
 * 遊戲清單；只有一款時直接導向。The game list, redirecting when there is one game.
 * @returns 頁面 JSX。Page JSX.
 */
export function StoryIndex() {
  const games = useStoryGames()
  if (games.isLoading) return <p className="text-sm text-muted-foreground">載入中…</p>
  if (games.isError) return <ErrorNote title="無法載入劇情流程圖" error={games.error} />
  const list = games.data ?? []
  if (list.length === 1) return <Navigate to={`/story/${encodeURIComponent(list[0].game_id)}`} replace />
  return (
    <div className="space-y-4">
      <h2 className="text-3xl font-bold tracking-tight">劇情流程圖</h2>
      {list.length === 0 && <p className="text-muted-foreground">還沒有匯入任何遊戲的流程圖。</p>}
      <ul className="space-y-2">
        {list.map((g) => (
          <li key={g.game_id}>
            <Link className="text-primary underline-offset-4 hover:underline" to={`/story/${encodeURIComponent(g.game_id)}`}>
              {g.title}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  )
}

/**
 * 劇情流程圖頁。The story flowchart page.
 * @returns 頁面 JSX。Page JSX.
 */
export default function StoryGraph() {
  const { gameId = '' } = useParams()
  const [search, setSearch] = useSearchParams()
  const params = useMemo(() => parseStoryParams(search), [search])
  const manifest = useStoryManifest(gameId)
  const graphQuery = useStoryGraph(gameId, manifest.data?.build_id)
  const graph = graphQuery.data
  const annotations = useStoryAnnotations(gameId, manifest.data?.build_id, !!manifest.data?.capabilities.annotations.includes('cards'))
  const { theme, themeId, choose, isError: themeFailed } = useStoryTheme(gameId, manifest.data?.default_theme)
  const themes = useStoryThemeList()
  const [verbInput, setVerbInput] = useState(params.verb)
  const [focus, setFocus] = useState<{ key: string | null; seq: number }>({ key: params.node, seq: 0 })
  const [hovered, setHovered] = useState<StoryNode | null>(null)
  const hitIndex = useRef(-1)
  const removedWarned = useRef<string | null>(null)

  const update = useCallback((patch: Partial<StoryParams>) => {
    const next = { ...params, ...patch }
    setSearch(formatStoryParams(next), { replace: !shouldPushHistory(params, next) })
  }, [params, setSearch])

  const byKey = useMemo(() => new Map((graph?.nodes ?? []).map((n) => [n.key, n])), [graph])

  // 網址的節點在新版本已移除（D6）：關掉抽屜、留在同一個分頁（捲動位置照 sessionStorage 記的），並提示一次。
  // 用取代而不是新增歷史，否則按上一頁又回到已移除的節點、再被導回來，出不去。
  // 只有節點沒有分頁時補上分頁（同樣取代）。
  useEffect(() => {
    if (!graph || !params.node) return
    const node = byKey.get(params.node)
    if (!node) {
      if (removedWarned.current !== params.node) {
        removedWarned.current = params.node
        toast.warning('這個節點在新版本中已移除')
      }
      setSearch(formatStoryParams({ ...params, node: null, line: null }), { replace: true })
    } else if (params.tab !== node.tab) {
      setSearch(formatStoryParams({ ...params, tab: node.tab }), { replace: true })
    }
  }, [graph, byKey, params, setSearch])

  useEffect(() => setVerbInput(params.verb), [params.verb])
  useEffect(() => {
    if (params.node) setFocus((f) => ({ key: params.node, seq: f.seq + 1 }))
  }, [params.node])

  const cardIndex = annotations.data?.index.cards
  const stats = annotations.data?.nodes
  const hits = useMemo(() => verbHits(cardIndex, params.verb, params.kind, (k) => byKey.get(k)?.tab), [cardIndex, params.verb, params.kind, byKey])
  const verbs = useMemo(() => Object.keys(cardIndex?.verbs ?? {}), [cardIndex])

  const nodeState = useCallback((node: StoryNode): NodeState => {
    if (params.verb && hits.nodes.has(node.key)) return 'highlight'
    if (params.kind && node.line_count) return stats?.[node.key]?.cards?.[params.kind] ? 'normal' : 'dim'
    return 'normal'
  }, [params.verb, params.kind, hits, stats])

  const decorations = useCallback((node: StoryNode) => {
    const counts = stats?.[node.key]?.cards
    if (!counts) return null
    const n = params.kind ? counts[params.kind] ?? 0 : Object.values(counts).reduce((s, v) => s + v, 0)
    return n > 0 ? <span className="sf-badge" title="有卡片的台詞句數">{n}</span> : null
  }, [stats, params.kind])

  const tabBadges = useMemo(() => (params.verb ? hits.perTab : undefined), [params.verb, hits])
  const tabHits = useMemo(() => {
    if (!graph || !params.tab) return [] as string[]
    return graph.nodes.filter((n) => n.tab === params.tab && hits.nodes.has(n.key)).map((n) => n.key)
  }, [graph, params.tab, hits])

  const stepHit = (dir: 1 | -1) => {
    if (!tabHits.length) return
    hitIndex.current = (hitIndex.current + dir + tabHits.length) % tabHits.length
    setFocus((f) => ({ key: tabHits[hitIndex.current], seq: f.seq + 1 }))
  }
  useEffect(() => { hitIndex.current = -1 }, [params.verb, params.tab, params.kind])

  const onScrollChange = useCallback((tab: string, top: number) => {
    try { sessionStorage.setItem(scrollStorageKey(gameId, tab), String(Math.round(top))) } catch { /* 不能存就算了 */ }
  }, [gameId])

  if (manifest.isLoading || (manifest.data && graphQuery.isLoading)) {
    return <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />載入劇情流程圖…</div>
  }
  if (manifest.isError) return <ErrorNote title="無法載入這款遊戲的流程圖" error={manifest.error} />
  if (graphQuery.isError || !graph || !manifest.data) return <ErrorNote title="無法載入劇情圖" error={graphQuery.error} />

  const m = manifest.data
  const selected = params.node ? byKey.get(params.node) ?? null : null
  const cardTypes = m.card_types

  return (
    <div className={cn('flex flex-col gap-2', PAGE_HEIGHT)}>
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        <h2 className="mr-2 text-xl font-bold tracking-tight">{m.titles['zh-Hant'] ?? m.titles['ja-JP'] ?? m.game_id}</h2>
        {cardTypes.length > 0 && (
          <select className="h-8 rounded-md border bg-background px-2 text-sm" value={params.kind ?? ''} aria-label="卡片類型篩選"
            onChange={(e) => update({ kind: e.target.value || null })}>
            <option value="">全部卡片</option>
            {cardTypes.map((t) => <option key={t.card_type} value={t.card_type}>{t.label}</option>)}
          </select>
        )}
        {cardTypes.length > 0 && (
          <form className="flex items-center gap-1" onSubmit={(e) => { e.preventDefault(); update({ verb: verbInput.trim() }) }}>
            <div className="relative">
              <Search className="pointer-events-none absolute left-2 top-2 h-4 w-4 text-muted-foreground" />
              <input className="h-8 w-40 rounded-md border bg-background pl-7 pr-7 text-sm" list="story-verbs" placeholder="搜尋動詞（例：見る）"
                value={verbInput} onChange={(e) => setVerbInput(e.target.value)} aria-label="搜尋動詞"
                onBlur={() => verbInput.trim() !== params.verb && update({ verb: verbInput.trim() })} />
              {params.verb && (
                <button type="button" className="absolute right-1.5 top-1.5 text-muted-foreground hover:text-foreground" aria-label="清除搜尋"
                  onClick={() => { setVerbInput(''); update({ verb: '' }) }}>
                  <X className="h-4 w-4" />
                </button>
              )}
              <datalist id="story-verbs">{verbs.slice(0, 2000).map((v) => <option key={v} value={v} />)}</datalist>
            </div>
            {params.verb && (
              <span className="flex items-center gap-1 text-xs text-muted-foreground">
                {hits.nodes.size} 個節點
                {params.tab && tabHits.length > 0 && (
                  <>
                    <button type="button" className="rounded p-0.5 hover:bg-accent" aria-label="上一個命中" onClick={() => stepHit(-1)}><ChevronLeft className="h-4 w-4" /></button>
                    <button type="button" className="rounded p-0.5 hover:bg-accent" aria-label="下一個命中" onClick={() => stepHit(1)}><ChevronRight className="h-4 w-4" /></button>
                  </>
                )}
              </span>
            )}
          </form>
        )}
        {annotations.isError && (
          <span role="alert" className="flex items-center gap-1 text-xs text-destructive"
            title={describeApiError(annotations.error).message}>
            <AlertTriangle className="h-3.5 w-3.5" />卡片統計與動詞搜尋讀取失敗（不是沒有卡）
          </span>
        )}
        {themeFailed && (
          <span role="alert" className="flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400">
            <AlertTriangle className="h-3.5 w-3.5" />主題 {themeId} 讀取失敗，暫用預設主題
          </span>
        )}
        <select className="ml-auto h-8 rounded-md border bg-background px-2 text-sm" value={themeId} aria-label="主題"
          onChange={(e) => choose(e.target.value === m.default_theme ? null : e.target.value)}>
          {(themes.data ?? [{ id: themeId, label: themeId }]).map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
        </select>
      </div>

      <div className="relative min-h-0 flex-1 overflow-hidden rounded-lg border">
        <StoryFlowchart
          graph={graph}
          tab={params.tab}
          onTabChange={(tab) => update({ tab, node: null, line: null })}
          theme={theme}
          lang={NODE_LANG}
          selectedNode={params.node}
          onNodeClick={(node) => update({ node: node.key === params.node ? null : node.key, line: null, tab: node.tab })}
          onNodeHover={setHovered}
          nodeState={nodeState}
          pathTo={params.node}
          decorations={decorations}
          tabBadges={tabBadges}
          focusNode={focus.key}
          focusSeq={focus.seq}
          initialScrollTop={params.tab ? readScroll(scrollStorageKey(gameId, params.tab)) : undefined}
          onScrollChange={onScrollChange}
          minimapHighlight={params.verb ? hits.nodes : undefined}
        />
        {hovered && (
          <div className="pointer-events-none absolute bottom-2 left-2 z-20 max-w-[60%] rounded-md bg-black/70 px-3 py-1.5 text-xs text-white">
            {localizedTitle(hovered, NODE_LANG) || hovered.condition}
            {hovered.type === 'condition' && hovered.condition && localizedTitle(hovered, NODE_LANG) ? ` · ${hovered.condition}` : ''}
            {hovered.line_count ? ` · ${hovered.line_count} 句` : ''}
            {stats?.[hovered.key]?.cards && ` · 有卡 ${Object.entries(stats[hovered.key].cards).map(([k, v]) => `${cardTypes.find((t) => t.card_type === k)?.label ?? k} ${v}`).join('、')}`}
          </div>
        )}
        {selected && (
          <StoryLineDrawer
            gameId={gameId}
            manifest={m}
            graph={graph}
            node={selected}
            selectedLine={params.line}
            kindFilter={params.kind}
            onSelectLine={(line) => update({ line })}
            onOpenNode={(key) => update({ node: key, line: null, tab: byKey.get(key)?.tab ?? params.tab })}
            onClose={() => update({ node: null, line: null })}
          />
        )}
      </div>
    </div>
  )
}
