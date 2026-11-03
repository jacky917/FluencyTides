import { useMemo, useRef } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronRight, FolderTree, RefreshCw } from 'lucide-react'
import { toast } from 'sonner'
import { FluencyTidesAPI } from '../../api/client'
import { useRevealSelected } from '../../hooks/useRevealSelected'
import { describeApiError, isBackendError } from '../../lib/apiError'
import { buildDeckTree, deckAncestors, type DeckNode } from '../../lib/deckTree'
import { cn } from '../../lib/utils'
import { Button } from '../ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '../ui/collapsible'
import { Skeleton } from '../ui/skeleton'
import { ErrorNote } from './ErrorNote'
import { Panel } from './Panel'

/**
 * 與 AnkiWeb 同步可能帶回別處的新卡片與標籤，牌組以外這些來自 Anki 的清單也要重抓（在背景，不等）。
 * Other Anki-backed queries refetched in the background after a sync.
 */
const OTHER_SYNCED_KEYS = [['cards'], ['card-view'], ['tags'], ['graph']]

/**
 * 牌組樹面板（Anki 牌組名以 `::` 分層，可展開），Cards、Generate、Graph 三頁共用。展開狀態由
 * 呼叫端保存（各頁分開記）：版面模式改變時面板會重新掛載，狀態才不會遺失。
 * The deck tree panel shared by the three pages; the expansion state is owned
 * by the caller.
 * @param props.scrollable - 面板自己捲動（標題列固定）。The panel scrolls itself.
 * @param props.toggled - 使用者手動展開或收合過的節點。Nodes the user toggled by hand.
 * @param props.selected - 目前選取的牌組；null 為全部。The selected deck.
 * @param props.onSelect - 選取牌組。Selects a deck.
 * @returns 面板 JSX。Panel JSX.
 */
export function DeckTreePanel({
  scrollable,
  toggled,
  onToggled,
  selected,
  onSelect,
}: {
  scrollable: boolean
  toggled: Record<string, boolean>
  onToggled: (update: (prev: Record<string, boolean>) => Record<string, boolean>) => void
  selected: string | null
  onSelect: (deck: string | null) => void
}) {
  const { data, isPending, isError, error, isFetching } = useQuery({
    queryKey: ['decks'],
    queryFn: FluencyTidesAPI.listDecks,
  })
  const queryClient = useQueryClient()
  // 重新整理：先讓 Anki 與 AnkiWeb 同步（例如手機上新增的牌組），不論成敗都再重抓牌組；
  // 同步失敗時清單仍是本機收藏的內容，另以 toast 說明。mutationFn 等同步與牌組重抓都完成才結束，
  // 圖示轉到那時才停。
  const refresh = useMutation({
    mutationFn: async () => {
      let syncError: unknown = null
      try {
        await FluencyTidesAPI.syncAnki()
      } catch (err) {
        syncError = err
      }
      for (const queryKey of OTHER_SYNCED_KEYS) queryClient.invalidateQueries({ queryKey })
      await queryClient.invalidateQueries({ queryKey: ['decks'] })
      if (syncError) throw syncError
    },
    onError: (err) => {
      if (!isBackendError(err)) console.error('[DeckTreePanel] 與 AnkiWeb 同步失敗', err)
      toast.error('與 AnkiWeb 同步失敗', { description: `${describeApiError(err).message}（牌組為本機 Anki 的內容）` })
    },
  })
  const busy = refresh.isPending || isFetching
  const tree = useMemo(() => buildDeckTree((data ?? []).map((item) => item.deck_name)), [data])
  // 使用者手動切換過的節點以手動為準；沒動過的，目前選取牌組的上層預設展開
  const ancestors = useMemo(() => new Set(selected ? deckAncestors(selected).slice(0, -1) : []), [selected])
  const isOpen = (node: DeckNode) => toggled[node.fullName] ?? ancestors.has(node.fullName)
  // 回到頁面時，把選取的牌組捲到看得到的地方
  const panelRef = useRef<HTMLDivElement>(null)
  useRevealSelected(panelRef, data !== undefined, scrollable)

  return (
    <Panel
      ref={panelRef}
      scrollable={scrollable}
      title={
        <>
          <FolderTree className="h-4 w-4" />
          牌組
        </>
      }
      actions={
        <Button
          variant="ghost"
          size="icon"
          onClick={() => refresh.mutate()}
          disabled={busy}
          aria-label={refresh.isPending ? '正在與 AnkiWeb 同步' : '與 AnkiWeb 同步並重新整理牌組'}
          title="與 AnkiWeb 同步並重新整理牌組"
        >
          <RefreshCw className={cn('h-4 w-4', busy && 'animate-spin')} />
        </Button>
      }
      contentClassName="text-sm"
    >
      {isPending ? (
        <div className="space-y-2">
          <Skeleton className="h-5 w-full" />
          <Skeleton className="h-5 w-3/4" />
          <Skeleton className="h-5 w-2/3" />
        </div>
      ) : isError ? (
        <ErrorNote title="無法載入牌組" error={error} />
      ) : (
        <ul role="tree" aria-label="牌組">
          <li role="treeitem" aria-selected={selected === null}>
            <div className="flex items-center">
              <span className="inline-block w-[18px]" />
              <DeckButton label="全部牌組" active={selected === null} onClick={() => onSelect(null)} />
            </div>
          </li>
          {tree.map((node) => (
            <DeckItem
              key={node.fullName}
              node={node}
              depth={0}
              selected={selected}
              isOpen={isOpen}
              onToggle={(name, open) => onToggled((prev) => ({ ...prev, [name]: open }))}
              onSelect={onSelect}
            />
          ))}
        </ul>
      )}
    </Panel>
  )
}

/** {@link DeckItem} 的 props。Props of {@link DeckItem}. */
interface DeckItemProps {
  node: DeckNode
  depth: number
  selected: string | null
  isOpen: (node: DeckNode) => boolean
  onToggle: (fullName: string, open: boolean) => void
  onSelect: (deck: string) => void
}

/**
 * 牌組樹的一個節點（遞迴）。子牌組以 Collapsible 展開收合，有高度動畫。
 * One deck tree node, recursively; children expand with a height animation.
 * @returns 節點 JSX。Node JSX.
 */
function DeckItem({ node, depth, selected, isOpen, onToggle, onSelect }: DeckItemProps) {
  const open = isOpen(node)
  const hasChildren = node.children.length > 0
  return (
    <Collapsible asChild open={hasChildren && open} onOpenChange={(next) => onToggle(node.fullName, next)}>
      <li role="treeitem" aria-selected={selected === node.fullName} aria-expanded={hasChildren ? open : undefined}>
        <div className="flex items-center">
          {hasChildren ? (
            <CollapsibleTrigger asChild>
              <button
                type="button"
                className="p-0.5 rounded hover:bg-accent"
                style={{ marginLeft: depth * 12 }}
                aria-label={open ? `收合 ${node.name}` : `展開 ${node.name}`}
              >
                <ChevronRight className={cn('h-3.5 w-3.5 transition-transform duration-200', open && 'rotate-90')} />
              </button>
            </CollapsibleTrigger>
          ) : (
            <span className="inline-block w-[18px]" style={{ marginLeft: depth * 12 }} />
          )}
          <DeckButton label={node.name} active={selected === node.fullName} onClick={() => onSelect(node.fullName)} />
        </div>
        {hasChildren && (
          <CollapsibleContent asChild>
            <ul role="group">
              {node.children.map((child) => (
                <DeckItem
                  key={child.fullName}
                  node={child}
                  depth={depth + 1}
                  selected={selected}
                  isOpen={isOpen}
                  onToggle={onToggle}
                  onSelect={onSelect}
                />
              ))}
            </ul>
          </CollapsibleContent>
        )}
      </li>
    </Collapsible>
  )
}

/**
 * 牌組名稱按鈕。A deck name button.
 * @returns 按鈕 JSX。Button JSX.
 */
function DeckButton({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      aria-current={active || undefined}
      onClick={onClick}
      className={cn(
        'flex-1 truncate rounded-md px-2 py-1 text-left transition-colors',
        active ? 'bg-primary text-primary-foreground' : 'hover:bg-accent hover:text-accent-foreground',
      )}
      title={label}
    >
      {label}
    </button>
  )
}
