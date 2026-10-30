import { useState, useMemo, useRef, useEffect, type ReactNode } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import ForceGraph2D from 'react-force-graph-2d'
import { RefreshCw, Settings, Link as LinkIcon, Loader2, X, Maximize2, Minimize2 } from 'lucide-react'
import { toast } from 'sonner'
import { FluencyTidesAPI } from '../api/client'
import { useElementSize } from '../hooks/useElementSize'
import { useLocalStorage } from '../hooks/useStoredState'
import { usePanelWidths } from '../hooks/usePanelWidths'
import { collectNodePositions, readGraphView, seedNodePositions, writeGraphCamera, writeGraphView } from '../lib/graphView'
import { GRAPH_LAYOUT } from '../lib/cardLayout'
import { permissionsFromCapabilities } from '../lib/cardFields'
import { graphHandlers, graphViewScope, nodeTooltip, pickGraphHandler } from '../lib/graphQuery'
import { cn } from '../lib/utils'
import type { GenericNoteDetail, GraphNode } from '../types/api'
import { CardDetailModal } from '../components/CardDetailModal'
import { ConfirmDialog, type ConfirmDialogProps } from '../components/shared/ConfirmDialog'
import { DeckTreePanel } from '../components/shared/DeckTreePanel'
import { PageHeader } from '../components/shared/PageHeader'
import { SplitLayout } from '../components/shared/SplitLayout'
import { Card } from '../components/ui/card'
import { Skeleton } from '../components/ui/skeleton'
import { Button } from '../components/ui/button'
import { Input } from '../components/ui/input'
import { Toaster } from '../components/ui/sonner'
import { describeApiError, isBackendError, retryTransient } from '../lib/apiError'

/**
 * 節點文字標籤的可選字型樣式。
 * Selectable font styles for node text labels.
 */
const FONT_STYLES = [
  { id: 'classic', name: 'Classic White', fill: '#ffffff', stroke: '#000000', strokeWidth: 3 },
  { id: 'slate', name: 'Slate Gray', fill: '#f8fafc', stroke: '#334155', strokeWidth: 3 },
  { id: 'neon', name: 'Neon Cyan', fill: '#22d3ee', stroke: '#1e3a8a', strokeWidth: 3 },
  { id: 'sunset', name: 'Sunset Gold', fill: '#fbbf24', stroke: '#7f1d1d', strokeWidth: 3 },
  { id: 'mint', name: 'Mint Green', fill: '#6ee7b7', stroke: '#064e3b', strokeWidth: 3 },
  { id: 'cyber', name: 'Cyber Pink', fill: '#f472b6', stroke: '#4c1d95', strokeWidth: 3 },
];

/**
 * 節點文字標籤的可選字級倍率。
 * Selectable font size multipliers for node text labels.
 */
const FONT_SIZES = [
  { id: 'small', name: 'Small (80%)', value: 0.8 },
  { id: 'medium', name: 'Medium (100%)', value: 1.0 },
  { id: 'large', name: 'Large (140%)', value: 1.4 },
  { id: 'xlarge', name: 'Extra Large (200%)', value: 2.0 },
];

/**
 * 選取的牌組存成這個值代表全部牌組（沿用既有的 localStorage 值）。`api/client.ts` 的
 * `getHandlerGraph` 也把這個值當成「不篩選」，兩邊要一起改。
 * The stored value for all decks; `getHandlerGraph` treats it as no filter too.
 */
const ALL_DECKS = 'All Decks'
/** 圖的區塊最窄的寬度。Narrowest graph area. */
const GRAPH_MIN = 320

const SELECT_CLASS =
  'h-9 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'

/**
 * 這個瀏覽器能不能讓元素全螢幕：iPhone 的 Safari 沒有 `Element.requestFullscreen`，內嵌視窗
 * 沒有允許時 `document.fullscreenEnabled` 為 false。
 * Whether the browser can make an element fullscreen.
 */
const CAN_FULLSCREEN =
  typeof document !== 'undefined' &&
  document.fullscreenEnabled === true &&
  typeof HTMLElement.prototype.requestFullscreen === 'function'

/** 等待使用者確認的操作（顯示頁面內的確認視窗）。An action waiting for confirmation. */
type PendingConfirm = Omit<ConfirmDialogProps, 'onCancel' | 'message'> & { message: ReactNode }

/**
 * 知識圖譜頁面：牌組樹決定要看哪一層（含子牌組），另一側以力導向圖畫出所選 handler 的筆記
 * （handlers 計畫書 D12）。
 *
 * - handler 選單只列開放圖譜的；負責多種筆記類型時可再選一種；標準圖譜（單點）可選節點名稱用的欄位。
 * - 關聯圖譜（`capabilities.graph.links`，例如單字卡）才有建立、刪除連線與清理失效關聯。
 * - 點節點以該 handler 讀取筆記，打開卡片詳情（權限依 handler 的能力）；幽靈節點沒有筆記，不開。
 *
 * 全螢幕時只看得到被放大的區塊，所以這頁的提示用 toast（全螢幕時在區塊內另掛一個 Toaster）、確認用
 * 頁面內的視窗，不用 `alert`／`confirm`：瀏覽器顯示原生對話框前會退出全螢幕。
 *
 * Knowledge graph page: the deck tree picks the deck; the graph shows the
 * selected handler's notes, with relation editing only for relation graphs.
 * @returns 知識圖譜頁面 JSX。Knowledge graph page JSX.
 */
export default function KnowledgeGraph() {
  const defaultDeck = import.meta.env.VITE_DEFAULT_DECK || ALL_DECKS
  const [selectedDeck, setSelectedDeck] = useLocalStorage('kg_selectedDeck', defaultDeck)
  const [deckToggled, setDeckToggled] = useLocalStorage<Record<string, boolean>>('graph_deck_toggled', {})
  const panels = usePanelWidths(GRAPH_LAYOUT)
  const [selectedNoteId, setSelectedNoteId] = useState<number | null>(null)
  // 選的 handler、筆記類型與各 handler 的名稱欄位（空字串＝後端預設）
  const [storedHandler, setStoredHandler] = useLocalStorage<string | null>('kg_handler', null)
  const [noteTypeChoice, setNoteTypeChoice] = useState<string | null>(null)
  const [labelFieldChoices, setLabelFieldChoices] = useLocalStorage<Record<string, string>>('kg_label_fields', {})

  const [fontStyleId, setFontStyleId] = useLocalStorage('kg_fontStyleId', 'classic');
  const [fontSizeMultiplier, setFontSizeMultiplier] = useLocalStorage('kg_fontSizeMultiplier', 1.0);
  const [textVisibilityThreshold, setTextVisibilityThreshold] = useLocalStorage('kg_textVisibilityThreshold', 1.0);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);

  // Link Mode State
  const [isLinkMode, setIsLinkMode] = useState(false);
  const [linkSourceNode, setLinkSourceNode] = useState<any | null>(null);
  const [showRelationModal, setShowRelationModal] = useState(false);
  const [linkTargetNode, setLinkTargetNode] = useState<any | null>(null);
  const [customRelation, setCustomRelation] = useState('');
  const [selectedRelationType, setSelectedRelationType] = useState('synonym');
  const [pendingConfirm, setPendingConfirm] = useState<PendingConfirm | null>(null)

  const queryClient = useQueryClient()
  const fgRef = useRef<any>();
  // 圖的容器（graphArea）會在版面模式切換、或載入／錯誤／空資料狀態切換時重新掛載；
  // 「回到上次的縮放與中心點」與「Adjust Graph Physics」兩個 effect 依它重跑，才會套到新的圖上
  const [graphAreaRef, graphSize, graphArea] = useElementSize()

  // 全螢幕：放大整個圖的區塊（含工具列與彈窗）；按 Esc 或再按一次按鈕退出
  const fullscreenRef = useRef<HTMLDivElement>(null)
  const [isFullscreen, setIsFullscreen] = useState(false)
  useEffect(() => {
    // 只認這張圖的區塊；卡面裡的影片等其他元素進入全螢幕不算
    const onChange = () =>
      setIsFullscreen(document.fullscreenElement !== null && document.fullscreenElement === fullscreenRef.current)
    document.addEventListener('fullscreenchange', onChange)
    return () => document.removeEventListener('fullscreenchange', onChange)
  }, [])
  const toggleFullscreen = () => {
    if (document.fullscreenElement) {
      // 退出失敗時畫面仍停在全螢幕，使用者可以再按 Esc；只記下來方便除錯
      document.exitFullscreen().catch((err: unknown) => console.warn('[KnowledgeGraph] 退出全螢幕失敗', err))
      return
    }
    const element = fullscreenRef.current
    if (!element) return
    element.requestFullscreen().catch((err: unknown) => {
      console.warn('[KnowledgeGraph] 進入全螢幕失敗', err)
      toast.error('無法進入全螢幕', {
        description: '瀏覽器拒絕了全螢幕（常見於內嵌視窗或權限限制）。請改在一般的瀏覽器分頁開啟這一頁，或改用瀏覽器本身的全螢幕（F11）。',
      })
    })
  }

  // 與牌組樹共用同一份查詢：記住的牌組可能已在 Anki 被刪除或改名
  const { data: decks } = useQuery({ queryKey: ['decks'], queryFn: FluencyTidesAPI.listDecks })
  const deckMissing =
    selectedDeck !== ALL_DECKS && decks !== undefined && !decks.some((d) => d.deck_name === selectedDeck)

  const { data: relationTypesData } = useQuery({
    queryKey: ['relationTypes'],
    queryFn: FluencyTidesAPI.getRelationTypes
  })
  const relationTypes = relationTypesData || ['synonym', 'collocation'];

  const {
    data: handlers,
    isPending: handlersPending,
    error: handlersError,
  } = useQuery({ queryKey: ['handlers'], queryFn: FluencyTidesAPI.listHandlers, retry: retryTransient })
  const available = graphHandlers(handlers)
  const currentHandler = pickGraphHandler(available, storedHandler)
  const handlerName = currentHandler?.name ?? null
  const relationGraph = currentHandler?.capabilities.graph.links ?? false
  // 換 handler 後之前選的筆記類型不一定還適用
  const noteType = noteTypeChoice !== null && currentHandler?.note_types.includes(noteTypeChoice) ? noteTypeChoice : null
  // 關聯圖譜的節點名稱固定，不能選欄位
  const labelField = handlerName && !relationGraph ? labelFieldChoices[handlerName] || null : null
  const viewScope = graphViewScope(handlerName ?? '', selectedDeck, noteType)

  const {
    data: graphData,
    isLoading,
    error: graphError,
  } = useQuery({
    queryKey: ['graph', handlerName, selectedDeck, noteType, labelField],
    queryFn: () => FluencyTidesAPI.getHandlerGraph(handlerName!, { deck: selectedDeck, noteType, labelField }),
    enabled: handlerName !== null,
  })

  // 點節點：以這個 handler 讀取筆記（成組卡片之後會附上整組），權限依它的能力
  const {
    data: noteFromHandler,
    isLoading: isLoadingCard,
    error: noteError,
  } = useQuery({
    queryKey: ['note', 'handler', handlerName, selectedNoteId],
    queryFn: () => FluencyTidesAPI.readNote(handlerName!, selectedNoteId!),
    enabled: selectedNoteId !== null && handlerName !== null && !isLinkMode,
  })
  const selectedNote = useMemo<GenericNoteDetail | null>(
    () =>
      noteFromHandler && currentHandler
        ? { ...noteFromHandler, handler: currentHandler.name, permissions: permissionsFromCapabilities(currentHandler) }
        : null,
    [noteFromHandler, currentHandler],
  )

  const chooseHandler = (name: string) => {
    setStoredHandler(name)
    setNoteTypeChoice(null)
    setIsLinkMode(false)
    setLinkSourceNode(null)
  }

  const createRelationMutation = useMutation({
    mutationFn: FluencyTidesAPI.createRelation,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['graph'] })
      queryClient.invalidateQueries({ queryKey: ['relationTypes'] })
      setIsLinkMode(false)
      setLinkSourceNode(null)
      setLinkTargetNode(null)
      setShowRelationModal(false)
      setCustomRelation('')
      toast.success('關聯建立成功')
    },
    onError: (err) => {
      if (!isBackendError(err)) console.error('[KnowledgeGraph] 建立關聯失敗', err)
      toast.error('建立關聯失敗', { description: describeApiError(err).message })
    }
  })

  const deleteRelationMutation = useMutation({
    mutationFn: FluencyTidesAPI.deleteRelation,
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['graph'] })
      queryClient.invalidateQueries({ queryKey: ['relationTypes'] })
      toast.success(`已移除 ${data.deleted_count} 筆關聯`)
    },
    onError: (err) => {
      if (!isBackendError(err)) console.error('[KnowledgeGraph] 刪除關聯失敗', err)
      toast.error('刪除關聯失敗', { description: describeApiError(err).message })
    }
  })

  const syncMutation = useMutation({
    mutationFn: (force: boolean) => FluencyTidesAPI.syncRelations(force),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['graph'] })
      // 顯示掃描張數：Anki 回很少張卻刪很多時，使用者看得出不對勁
      const summary = `掃描 ${data.scanned_notes} 張筆記，移除 ${data.deleted_count} 筆失效關聯。`
      if (data.ankiweb_synced) {
        toast.success('同步完成', { description: summary })
      } else {
        toast.warning('同步完成，但與 AnkiWeb 同步失敗', { description: `${summary}這次以本機 Anki 的收藏為準。` })
      }
    },
    // 後端拒絕清理（409）時沒有刪除任何資料：要刪超過一半時讓使用者確認後強制，Anki 回 0 張時只說明原因
    onError: (err) => {
      const { message } = describeApiError(err)
      const code = (err as { error_code?: string }).error_code
      if (code === 'RELATION_SYNC_NEEDS_FORCE') {
        setPendingConfirm({
          title: '仍要清理關聯嗎？',
          message: `${message}\n\n確定 Anki 已完整載入、仍要清理嗎？`,
          confirmLabel: '仍要清理',
          destructive: true,
          onConfirm: () => syncMutation.mutate(true),
        })
        return
      }
      if (!isBackendError(err)) console.error('[KnowledgeGraph] 同步關聯失敗', err)
      toast.error(code?.startsWith('RELATION_SYNC') ? '沒有清理' : '同步失敗', { description: message })
    }
  })

  const getStatusColor = (status?: string, group?: number) => {
    if (group === 4) return '#94a3b8' // Slate 400 for ghost nodes
    if (status === 'learning') return '#ea580c' // Orange for learning/relearning
    if (status === 'review') return '#84cc16' // Green for mature/review
    if (status === 'suspended') return '#eab308' // Yellow for suspended
    return '#0ea5e9' // Sky blue for new cards
  }

  // Format data for the graph
  const formattedGraphData = useMemo(() => {
    if (!graphData) return { nodes: [], links: [] }
    return {
      nodes: graphData.nodes.map((n: any) => ({
        ...n,
        color: getStatusColor(n.status, n.group),
      })),
      links: graphData.links.map((l: any) => {
        // Check for reverse links
        const sId = l.source.id || l.source;
        const tId = l.target.id || l.target;

        const reverseSame = graphData.links.some((rev: any) =>
          (rev.source.id || rev.source) === tId &&
          (rev.target.id || rev.target) === sId &&
          rev.label === l.label
        );

        const reverseDiff = graphData.links.some((rev: any) =>
          (rev.source.id || rev.source) === tId &&
          (rev.target.id || rev.target) === sId &&
          rev.label !== l.label
        );

        return {
          ...l,
          color: '#64748b',
          isBidirectional: reverseSame,
          curvature: reverseDiff ? 0.2 : 0
        };
      })
    }
  }, [graphData])

  // 視角記憶（見 lib/graphView.ts）：節點從上次的位置開始；每個節點都有記下的位置時，第一次
  // 顯示不跑排版（畫面和上次一樣），之後拖動節點照常排版、鄰近節點會跟著調整。
  // 節點位置只在排版停下（已穩定）時存；縮放、平移只存視角，半途的位置不會被存下來凍住
  const restoredView = useMemo(() => {
    const view = readGraphView(viewScope)
    return { view, allSeeded: seedNodePositions(formattedGraphData.nodes, view) }
  }, [formattedGraphData, viewScope])
  const [thawedView, setThawedView] = useState<typeof restoredView | null>(null)
  const frozen = restoredView.allSeeded && thawedView !== restoredView

  const saveLayout = () => {
    const graph = fgRef.current
    if (!graph || formattedGraphData.nodes.length === 0) return
    if (frozen) {
      // 第一次顯示時沒跑排版就停下：位置沒變不必存，之後恢復一般排版
      setThawedView(restoredView)
      return
    }
    writeGraphView(viewScope, {
      zoom: graph.zoom(),
      center: graph.centerAt(),
      nodes: collectNodePositions(formattedGraphData.nodes),
    })
  }
  const saveCamera = () => {
    const graph = fgRef.current
    if (!graph || formattedGraphData.nodes.length === 0) return
    writeGraphCamera(viewScope, { zoom: graph.zoom(), center: graph.centerAt() })
  }

  // 回到上次的縮放與中心點（圖掛上之後才能設定）。圖重新掛載時也再套一次，而且讀最新存下的視角
  // （縮放、平移後 saveCamera 會更新），不是載入資料當下的那一份
  useEffect(() => {
    const view = readGraphView(viewScope) ?? restoredView.view
    if (!view || !graphArea) return
    const timer = window.setTimeout(() => {
      fgRef.current?.zoom(view.zoom, 0)
      fgRef.current?.centerAt(view.center.x, view.center.y, 0)
    }, 0)
    return () => window.clearTimeout(timer)
  }, [restoredView, graphArea, viewScope])

  // Adjust Graph Physics to prevent nodes from sticking too close together
  useEffect(() => {
    if (graphArea && fgRef.current) {
      // Increase distance between linked nodes
      fgRef.current.d3Force('link').distance(80);
      // Increase repulsive force between all nodes
      fgRef.current.d3Force('charge').strength(-300);
    }
  }, [formattedGraphData, graphArea]);

  /**
   * 右側：工具列＋圖。全螢幕放大的就是這個區塊，所以兩個彈窗也放在裡面，全螢幕時才看得到。
   * The right column: toolbar and graph; it is what goes fullscreen, so the
   * modals live inside it.
   */
  const graphPanel = (sideBySide: boolean) => (
    <Card
      ref={fullscreenRef}
      className={cn(
        'flex flex-col overflow-hidden bg-card',
        sideBySide ? 'h-full' : 'h-[70svh] min-h-[24rem]',
        isFullscreen && 'rounded-none border-0',
      )}
    >
      <div className="flex flex-wrap items-center gap-2 border-b p-3">
        {available.length > 0 && (
          <select
            className={SELECT_CLASS}
            aria-label="卡片類型"
            value={handlerName ?? ''}
            onChange={(e) => chooseHandler(e.target.value)}
          >
            {available.map((handler) => (
              <option key={handler.name} value={handler.name}>
                {handler.title}
              </option>
            ))}
          </select>
        )}
        {currentHandler && currentHandler.note_types.length > 1 && (
          <select
            className={SELECT_CLASS}
            aria-label="筆記類型"
            value={noteType ?? ''}
            onChange={(e) => setNoteTypeChoice(e.target.value || null)}
          >
            <option value="">全部筆記類型</option>
            {currentHandler.note_types.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>
        )}
        {handlerName && !relationGraph && graphData && (
          <select
            className={SELECT_CLASS}
            aria-label="節點名稱欄位"
            title="節點名稱用的欄位；該欄位為空的筆記顯示 Note ID"
            value={labelField ?? ''}
            onChange={(e) => setLabelFieldChoices((prev) => ({ ...prev, [handlerName]: e.target.value }))}
          >
            <option value="">名稱：預設（{graphData.label_field ?? 'Note ID'}）</option>
            {graphData.label_fields.map((field) => (
              <option key={field} value={field}>
                名稱：{field}
              </option>
            ))}
          </select>
        )}
        <span className="mr-auto truncate text-sm text-muted-foreground" title={selectedDeck}>
          牌組：{selectedDeck === ALL_DECKS ? '全部牌組' : selectedDeck}
          {deckMissing && <span className="text-destructive">（已不存在，請重新選擇）</span>}
        </span>
        {relationGraph && (
          <Button
            onClick={() => {
              if (isLinkMode) {
                // Cancel link mode
                setIsLinkMode(false)
                setLinkSourceNode(null)
              } else {
                setIsLinkMode(true)
              }
            }}
            variant={isLinkMode ? "default" : "outline"}
            className={isLinkMode ? "bg-primary text-primary-foreground animate-pulse" : ""}
          >
            <LinkIcon className="mr-2 h-4 w-4" />
            {isLinkMode ? (linkSourceNode ? 'Select Target...' : 'Select Source...') : '🔗 建立連線'}
          </Button>
        )}

        {relationGraph && (
          <Button
            onClick={() => syncMutation.mutate(false)}
            disabled={syncMutation.isPending || isLinkMode}
            variant="outline"
            title="清理指向已刪除筆記的關聯"
          >
            <RefreshCw className={`mr-2 h-4 w-4 ${syncMutation.isPending ? 'animate-spin' : ''}`} />
            Sync
          </Button>
        )}

        <div className="relative">
          <Button
            variant="outline"
            size="icon"
            onClick={() => setIsSettingsOpen(!isSettingsOpen)}
            aria-label="標籤樣式與字級"
          >
            <Settings className="w-4 h-4" />
          </Button>

          {isSettingsOpen && (
            <div className="absolute right-0 top-full mt-2 w-48 bg-background border shadow-xl rounded-xl p-2 z-50">
              <p className="text-xs font-semibold text-muted-foreground px-2 py-1 mb-1">Label Style</p>
              {FONT_STYLES.map(style => (
                <button
                  key={style.id}
                  onClick={() => {
                    setFontStyleId(style.id)
                    setIsSettingsOpen(false)
                  }}
                  className={`w-full text-left px-3 py-2 text-sm rounded-md transition-colors ${fontStyleId === style.id ? 'bg-primary/10 text-primary font-medium' : 'hover:bg-slate-100 dark:hover:bg-slate-800'}`}
                >
                  {style.name}
                </button>
              ))}
              <p className="text-xs font-semibold text-muted-foreground px-2 py-1 mb-1 border-t mt-2 pt-2">Font Size</p>
              {FONT_SIZES.map(size => (
                <button
                  key={size.id}
                  onClick={() => {
                    setFontSizeMultiplier(size.value)
                    setIsSettingsOpen(false)
                  }}
                  className={`w-full text-left px-3 py-2 text-sm rounded-md transition-colors ${fontSizeMultiplier === size.value ? 'bg-primary/10 text-primary font-medium' : 'hover:bg-slate-100 dark:hover:bg-slate-800'}`}
                >
                  {size.name}
                </button>
              ))}
              <p className="text-xs font-semibold text-muted-foreground px-2 py-1 mb-1 border-t mt-2 pt-2">Label Appears At (Zoom)</p>
              <div className="flex items-center justify-between px-3 py-1 mb-2">
                <button
                  onClick={() => setTextVisibilityThreshold(prev => Math.max(0.2, Number((prev - 0.1).toFixed(1))))}
                  className="w-6 h-6 flex items-center justify-center bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 rounded text-sm font-bold"
                >
                  -
                </button>
                <span className="text-sm font-medium w-12 text-center">
                  {Math.round(textVisibilityThreshold * 100)}%
                </span>
                <button
                  onClick={() => setTextVisibilityThreshold(prev => Math.min(3.0, Number((prev + 0.1).toFixed(1))))}
                  className="w-6 h-6 flex items-center justify-center bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 rounded text-sm font-bold"
                >
                  +
                </button>
              </div>
            </div>
          )}
        </div>

        <Button
          variant="outline"
          size="icon"
          onClick={toggleFullscreen}
          disabled={!CAN_FULLSCREEN}
          aria-label={isFullscreen ? '退出全螢幕' : '全螢幕'}
          title={
            !CAN_FULLSCREEN
              ? '這個瀏覽器或內嵌視窗不支援全螢幕'
              : isFullscreen
                ? '退出全螢幕（Esc）'
                : '全螢幕'
          }
        >
          {isFullscreen ? <Minimize2 className="w-4 h-4" /> : <Maximize2 className="w-4 h-4" />}
        </Button>
      </div>

      {handlersPending || isLoading ? (
        <div className="flex-1 p-6 flex flex-col gap-4">
          <Skeleton className="w-full h-full rounded-xl" />
        </div>
      ) : handlersError || graphError ? (
        <div className="flex-1 flex flex-col items-center justify-center gap-3 p-6 text-center">
          <p className="font-semibold text-destructive">{handlersError ? '無法載入卡片類型' : '無法載入圖譜'}</p>
          <p className="break-words text-sm text-muted-foreground">{describeApiError(handlersError ?? graphError).message}</p>
          {/* 記住的名稱欄位可能已不存在（筆記類型改過欄位）：給一個回到預設的出口 */}
          {graphError && labelField && handlerName && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => setLabelFieldChoices((prev) => ({ ...prev, [handlerName]: '' }))}
            >
              名稱欄位改回預設
            </Button>
          )}
        </div>
      ) : !currentHandler ? (
        <div className="flex-1 flex items-center justify-center p-6 text-center text-muted-foreground">
          <p>目前沒有開放圖譜的卡片類型（可在「工具設定」的 Handler 能力查看）。</p>
        </div>
      ) : formattedGraphData.nodes.length === 0 ? (
        <div className="flex-1 flex items-center justify-center p-6 text-center text-muted-foreground">
          <p>
            {deckMissing
              ? `牌組「${selectedDeck}」已不存在，請在牌組樹重新選擇。`
              : `這個牌組沒有「${currentHandler.title}」的筆記。`}
          </p>
        </div>
      ) : (
        <div ref={graphAreaRef} className="flex-1 min-h-0 relative bg-slate-50/50 dark:bg-slate-900/50 rounded-xl overflow-hidden m-1">
          <ForceGraph2D
            ref={fgRef}
            width={graphSize.width || undefined}
            height={graphSize.height || undefined}
            graphData={formattedGraphData}
            cooldownTicks={frozen ? 0 : undefined}
            onEngineStop={saveLayout}
            onZoomEnd={saveCamera}
            nodeLabel={(node: any) => nodeTooltip(node as GraphNode, relationGraph)}
            linkDirectionalArrowLength={(link: any) => link.isBidirectional ? 0 : 3.5}
            linkDirectionalArrowRelPos={1}
            linkCurvature={(link: any) => link.curvature}
            linkLabel="label"
            nodeCanvasObjectMode={() => 'after'}
            nodeCanvasObject={(node: any, ctx, globalScale) => {
              const label = node.label ?? node.id;

              // 畫選擇框 (如果處於連線模式且被選中)
              if (isLinkMode && linkSourceNode && node.id === linkSourceNode.id) {
                const nodeSize = Math.sqrt(node.val || 20) * 1.5;
                ctx.beginPath();
                ctx.arc(node.x, node.y, nodeSize + 4, 0, 2 * Math.PI, false);
                ctx.lineWidth = 2 / globalScale;
                ctx.strokeStyle = '#3b82f6';
                ctx.stroke();
              }

              // 當縮放比例超過設定的閾值時顯示文字 (100% = 1.0)
              if (globalScale >= textVisibilityThreshold) {
                const fontSize = (12 * fontSizeMultiplier) / globalScale;
                // 使用加粗字體讓邊框效果更好
                ctx.font = `bold ${fontSize}px Sans-Serif`;
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';

                const activeStyle = FONT_STYLES.find(s => s.id === fontStyleId) || FONT_STYLES[0];

                // 將文字繪製在節點正下方
                const nodeSize = Math.sqrt(node.val || 20) * 1.5;
                const textY = node.y + nodeSize + (fontSize / 2);

                // 1. 畫黑色邊框 (Stroke) 讓文字在任何背景都清晰
                ctx.lineWidth = activeStyle.strokeWidth / globalScale;
                ctx.strokeStyle = activeStyle.stroke;
                ctx.lineJoin = "round";
                ctx.strokeText(label, node.x, textY);

                // 2. 畫內部顏色 (Fill)
                ctx.fillStyle = activeStyle.fill;
                ctx.fillText(label, node.x, textY);
              }
            }}
            onNodeClick={node => {
              if (isLinkMode) {
                if (!linkSourceNode) {
                  setLinkSourceNode(node)
                } else if (linkSourceNode.id !== node.id) {
                  // Always Open Modal to create new relation (allowing bidirectional of different types)
                  setLinkTargetNode(node)
                  setShowRelationModal(true)
                }
              } else if (node.note_id) {
                setSelectedNoteId(node.note_id as number)
              }
            }}
            onLinkClick={(link: any) => {
              if (!relationGraph) return
              const sLabel = link.source.label || link.source.id || link.source;
              const tLabel = link.target.label || link.target.id || link.target;
              setPendingConfirm({
                title: '刪除關聯',
                message: `確定要刪除「${sLabel}」與「${tLabel}」之間的「${link.label}」關聯嗎？\n（若為雙向同名關係，將一併刪除）`,
                confirmLabel: '刪除',
                destructive: true,
                onConfirm: () =>
                  deleteRelationMutation.mutate({
                    source_label: sLabel,
                    target_label: tLabel,
                    relation_type: (link.label || '').toLowerCase()
                  }),
              })
            }}
          />
        </div>
      )}

      {/* Relation Type Modal */}
      {showRelationModal && linkSourceNode && linkTargetNode && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 backdrop-blur-sm">
          <div className="bg-background border shadow-lg rounded-xl w-full max-w-sm p-6 flex flex-col relative">
            <button
              onClick={() => {
                setShowRelationModal(false)
                setLinkTargetNode(null)
              }}
              className="absolute right-4 top-4 p-1 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-full"
            >
              <X className="w-4 h-4 text-muted-foreground" />
            </button>
            <h3 className="text-lg font-bold mb-4">Select Relation Type</h3>
            <p className="text-sm text-muted-foreground mb-4">
              Linking <b>{linkSourceNode.label}</b> ➜ <b>{linkTargetNode.label}</b>
            </p>
            <div className="flex flex-col gap-4">
              <div>
                <label className="text-sm font-medium mb-1 block">選擇已知關聯</label>
                <select
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  value={selectedRelationType}
                  onChange={(e) => {
                    setSelectedRelationType(e.target.value)
                    if (e.target.value !== 'custom') {
                      setCustomRelation('')
                    }
                  }}
                >
                  {relationTypes.map(rt => (
                    <option key={rt} value={rt}>{rt.charAt(0).toUpperCase() + rt.slice(1)}</option>
                  ))}
                  <option value="custom">-- 新增自訂關聯 --</option>
                </select>
              </div>

              {selectedRelationType === 'custom' && (
                <div>
                  <label className="text-sm font-medium mb-1 block text-blue-500">自訂關聯名稱</label>
                  <Input
                    placeholder="輸入新關聯 (例如: Travel)"
                    value={customRelation}
                    onChange={(e) => setCustomRelation(e.target.value)}
                    className="flex-1 h-10 border-blue-300 dark:border-blue-700"
                    autoFocus
                  />
                </div>
              )}

              <Button
                variant="default"
                className="w-full h-10 mt-2"
                disabled={
                  createRelationMutation.isPending ||
                  (selectedRelationType === 'custom' && !customRelation.trim())
                }
                onClick={() => {
                  const relationTypeToUse = selectedRelationType === 'custom'
                    ? customRelation.trim().toLowerCase()
                    : selectedRelationType;

                  createRelationMutation.mutate({
                    source_note_id: linkSourceNode.note_id || null,
                    target_note_id: linkTargetNode.note_id || null,
                    relation_type: relationTypeToUse,
                    source_label: linkSourceNode.label,
                    target_label: linkTargetNode.label
                  })
                }}
              >
                確認連線
              </Button>
            </div>
            {createRelationMutation.isPending && (
              <div className="absolute inset-0 bg-background/80 flex items-center justify-center rounded-xl">
                <Loader2 className="w-6 h-6 animate-spin text-primary" />
              </div>
            )}
          </div>
        </div>
      )}

      <CardDetailModal
        isOpen={selectedNoteId !== null}
        onClose={() => setSelectedNoteId(null)}
        note={selectedNote}
        isLoading={isLoadingCard}
        error={noteError}
      />

      {pendingConfirm && (
        <ConfirmDialog
          {...pendingConfirm}
          onCancel={() => setPendingConfirm(null)}
          onConfirm={() => {
            setPendingConfirm(null)
            pendingConfirm.onConfirm()
          }}
        />
      )}

      {/* App 根部的 Toaster 在全螢幕元素之外，全螢幕時看不到；這段期間在區塊內另掛一個 */}
      {isFullscreen && <Toaster />}
    </Card>
  )

  return (
    <SplitLayout
      header={
        <PageHeader
          title="Knowledge Graph"
          description="依卡片類型畫出筆記；單字卡另有同義詞、搭配詞等關聯連線。點節點查看卡片。"
        />
      }
      panels={panels}
      lockMode={isFullscreen}
      columns={[
        {
          id: 'deck',
          resizable: 'deck',
          render: (sideBySide) => (
            <DeckTreePanel
              scrollable={sideBySide}
              toggled={deckToggled}
              onToggled={setDeckToggled}
              selected={selectedDeck === ALL_DECKS ? null : selectedDeck}
              onSelect={(deck) => setSelectedDeck(deck ?? ALL_DECKS)}
            />
          ),
        },
        { id: 'graph', minWidth: GRAPH_MIN, render: graphPanel },
      ]}
    />
  )
}
