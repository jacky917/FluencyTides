import { useState, useMemo, useRef, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import ForceGraph2D from 'react-force-graph-2d'
import { RefreshCw, Settings, Link as LinkIcon, Loader2, X } from 'lucide-react'
import { FluencyTidesAPI } from '../api/client'
import { useLocalStorage } from '../hooks/useLocalStorage'
import { CardDetailModal } from '../components/CardDetailModal'
import { Card } from '../components/ui/card'
import { Skeleton } from '../components/ui/skeleton'
import { Button } from '../components/ui/button'
import { Input } from '../components/ui/input'

const FONT_STYLES = [
  { id: 'classic', name: 'Classic White', fill: '#ffffff', stroke: '#000000', strokeWidth: 3 },
  { id: 'slate', name: 'Slate Gray', fill: '#f8fafc', stroke: '#334155', strokeWidth: 3 },
  { id: 'neon', name: 'Neon Cyan', fill: '#22d3ee', stroke: '#1e3a8a', strokeWidth: 3 },
  { id: 'sunset', name: 'Sunset Gold', fill: '#fbbf24', stroke: '#7f1d1d', strokeWidth: 3 },
  { id: 'mint', name: 'Mint Green', fill: '#6ee7b7', stroke: '#064e3b', strokeWidth: 3 },
  { id: 'cyber', name: 'Cyber Pink', fill: '#f472b6', stroke: '#4c1d95', strokeWidth: 3 },
];

const FONT_SIZES = [
  { id: 'small', name: 'Small (80%)', value: 0.8 },
  { id: 'medium', name: 'Medium (100%)', value: 1.0 },
  { id: 'large', name: 'Large (140%)', value: 1.4 },
  { id: 'xlarge', name: 'Extra Large (200%)', value: 2.0 },
];

export default function KnowledgeGraph() {
  const defaultDeck = import.meta.env.VITE_DEFAULT_DECK || 'All Decks'
  const [selectedDeck, setSelectedDeck] = useLocalStorage('kg_selectedDeck', defaultDeck)
  const [selectedNoteId, setSelectedNoteId] = useState<number | null>(null)
  
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

  const queryClient = useQueryClient()
  const fgRef = useRef<any>();

  // Need to fetch decks first for the dropdown
  const { data: decks } = useQuery({ queryKey: ['decks'], queryFn: FluencyTidesAPI.listDecks })
  
  const { data: relationTypesData } = useQuery({ 
    queryKey: ['relationTypes'], 
    queryFn: FluencyTidesAPI.getRelationTypes 
  })
  const relationTypes = relationTypesData || ['synonym', 'collocation'];

  const { data: graphData, isLoading, isError } = useQuery({
    queryKey: ['graph', selectedDeck],
    queryFn: () => FluencyTidesAPI.getKnowledgeGraph(selectedDeck),
  })

  // Fetch individual card details when a node is selected
  const { data: cardDetail, isLoading: isLoadingCard } = useQuery({
    queryKey: ['card', selectedNoteId],
    queryFn: () => FluencyTidesAPI.getCard(selectedNoteId!),
    enabled: selectedNoteId !== null && !isLinkMode,
  })

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
      alert('關聯建立成功！')
    },
    onError: (err: any) => {
      alert(`建立關聯失敗: ${err.message || '未知錯誤'}`)
    }
  })

  const deleteRelationMutation = useMutation({
    mutationFn: FluencyTidesAPI.deleteRelation,
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['graph'] })
      queryClient.invalidateQueries({ queryKey: ['relationTypes'] })
      alert(`已成功移除 ${data.deleted_count} 筆關聯！`)
    },
    onError: (err: any) => {
      alert(`刪除關聯失敗: ${err.message || '未知錯誤'}`)
    }
  })

  const syncMutation = useMutation({
    mutationFn: FluencyTidesAPI.syncRelations,
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['graph'] })
      alert(`Sync complete! Cleaned up ${data.deleted_count} orphaned relations.`)
    },
    onError: () => {
      alert('Failed to sync with Anki. Is Anki running?')
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

  // Adjust Graph Physics to prevent nodes from sticking too close together
  useEffect(() => {
    if (fgRef.current) {
      // Increase distance between linked nodes
      fgRef.current.d3Force('link').distance(80);
      // Increase repulsive force between all nodes
      fgRef.current.d3Force('charge').strength(-300);
    }
  }, [formattedGraphData]);

  return (
    <div className="flex flex-col h-[calc(100vh-6rem)]">
      <div className="flex justify-between items-end mb-4">
        <div>
          <h2 className="text-3xl font-bold tracking-tight">Knowledge Graph</h2>
          <p className="text-muted-foreground mt-2">
            Visualize word relationships (Synonyms, Collocations) in your knowledge base.
          </p>
        </div>
        <div className="flex gap-4 items-end">
          <div className="w-64">
            <label className="text-sm font-medium mb-1 block">Select Deck</label>
            <select 
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              value={selectedDeck}
              onChange={(e) => setSelectedDeck(e.target.value)}
            >
              <option value="All Decks">All Decks</option>
              {decks?.map(d => (
                <option key={d.deck_id} value={d.deck_name}>{d.deck_name}</option>
              ))}
            </select>
          </div>
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

          <Button 
            onClick={() => syncMutation.mutate()} 
            disabled={syncMutation.isPending || isLinkMode}
            variant="outline"
          >
            <RefreshCw className={`mr-2 h-4 w-4 ${syncMutation.isPending ? 'animate-spin' : ''}`} />
            Sync
          </Button>

          <div className="relative">
            <Button 
              variant="outline" 
              size="icon" 
              onClick={() => setIsSettingsOpen(!isSettingsOpen)}
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
        </div>
      </div>

      <Card className="flex-1 overflow-hidden flex flex-col">
        {isLoading ? (
          <div className="flex-1 p-6 flex flex-col gap-4">
            <Skeleton className="w-full h-full rounded-xl" />
          </div>
        ) : isError ? (
          <div className="flex-1 flex items-center justify-center text-destructive">
            <p>Failed to load graph data. Make sure the backend is running.</p>
          </div>
        ) : formattedGraphData.nodes.length === 0 ? (
          <div className="flex-1 flex items-center justify-center text-muted-foreground">
            <p>No relational data found. Generate some cards first!</p>
          </div>
        ) : (
          <div className="flex-1 relative bg-slate-50/50 dark:bg-slate-900/50 rounded-xl overflow-hidden m-1">
            <ForceGraph2D
              ref={fgRef}
              graphData={formattedGraphData}
              nodeLabel={(node: any) => {
                if (node.translation) return `${node.label} (${node.translation}) - ${node.status || 'ghost'}`
                return `${node.label} - ${node.status || 'ghost'}`
              }}
              linkDirectionalArrowLength={(link: any) => link.isBidirectional ? 0 : 3.5}
              linkDirectionalArrowRelPos={1}
              linkCurvature={(link: any) => link.curvature}
              linkLabel="label"
              nodeCanvasObjectMode={() => 'after'}
              nodeCanvasObject={(node: any, ctx, globalScale) => {
                const label = node.label;
                
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
                const sLabel = link.source.label || link.source.id || link.source;
                const tLabel = link.target.label || link.target.id || link.target;
                if (window.confirm(`確定要刪除「${sLabel}」與「${tLabel}」之間的「${link.label}」關聯嗎？\n(若為雙向同名關係，將一併刪除)`)) {
                  deleteRelationMutation.mutate({
                    source_label: sLabel,
                    target_label: tLabel,
                    relation_type: (link.label || '').toLowerCase()
                  });
                }
              }}
            />
          </div>
        )}
      </Card>

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
        cardDetail={cardDetail || null}
        isLoading={isLoadingCard}
      />
    </div>
  )
}
