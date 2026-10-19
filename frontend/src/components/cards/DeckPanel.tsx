import { useMemo, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronDown, ChevronRight, FolderTree, RefreshCw } from 'lucide-react'
import { FluencyTidesAPI } from '../../api/client'
import { useRevealSelected } from '../../hooks/useRevealSelected'
import { buildDeckTree, deckAncestors, type DeckNode } from '../../lib/deckTree'
import { cn } from '../../lib/utils'
import { Button } from '../ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card'
import { Skeleton } from '../ui/skeleton'
import { ErrorNote } from './ErrorNote'
import { STICKY_HEADER, panelScrollClass } from './panelStyles'

/**
 * 牌組樹面板（Anki 牌組名以 `::` 分層，可展開）。展開狀態由呼叫端保存：版面模式改變時面板會
 * 重新掛載，狀態才不會遺失。
 * The deck tree panel; the expansion state is owned by the caller.
 * @param props.scrollable - 面板自己捲動（標題列固定）。The panel scrolls itself.
 * @param props.toggled - 使用者手動展開或收合過的節點。Nodes the user toggled by hand.
 * @param props.selected - 目前選取的牌組；null 為全部。The selected deck.
 * @param props.onSelect - 選取牌組。Selects a deck.
 * @returns 面板 JSX。Panel JSX.
 */
export function DeckPanel({
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
  const { data, isPending, isError, error, refetch, isFetching } = useQuery({
    queryKey: ['decks'],
    queryFn: FluencyTidesAPI.listDecks,
  })
  const tree = useMemo(() => buildDeckTree((data ?? []).map((item) => item.deck_name)), [data])
  // 使用者手動切換過的節點以手動為準；沒動過的，目前選取牌組的上層預設展開
  const ancestors = useMemo(() => new Set(selected ? deckAncestors(selected).slice(0, -1) : []), [selected])
  const isOpen = (node: DeckNode) => toggled[node.fullName] ?? ancestors.has(node.fullName)
  // 回到頁面時，把選取的牌組捲到看得到的地方
  const panelRef = useRef<HTMLDivElement>(null)
  useRevealSelected(panelRef, data !== undefined, scrollable)

  return (
    <Card ref={panelRef} className={panelScrollClass(scrollable)}>
      <CardHeader className={cn('pb-2 flex-row items-center justify-between space-y-0', scrollable && STICKY_HEADER)}>
        <CardTitle className="text-base flex items-center gap-2">
          <FolderTree className="h-4 w-4" />
          牌組
        </CardTitle>
        <Button variant="ghost" size="icon" onClick={() => refetch()} disabled={isFetching} aria-label="重新整理牌組">
          <RefreshCw className={cn('h-4 w-4', isFetching && 'animate-spin')} />
        </Button>
      </CardHeader>
      <CardContent className="text-sm">
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
      </CardContent>
    </Card>
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
 * 牌組樹的一個節點（遞迴）。One deck tree node, recursively.
 * @returns 節點 JSX。Node JSX.
 */
function DeckItem({ node, depth, selected, isOpen, onToggle, onSelect }: DeckItemProps) {
  const open = isOpen(node)
  const hasChildren = node.children.length > 0
  return (
    <li role="treeitem" aria-selected={selected === node.fullName} aria-expanded={hasChildren ? open : undefined}>
      <div className="flex items-center">
        {hasChildren ? (
          <button
            type="button"
            className="p-0.5 rounded hover:bg-accent"
            style={{ marginLeft: depth * 12 }}
            onClick={() => onToggle(node.fullName, !open)}
            aria-label={open ? `收合 ${node.name}` : `展開 ${node.name}`}
          >
            {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
          </button>
        ) : (
          <span className="inline-block w-[18px]" style={{ marginLeft: depth * 12 }} />
        )}
        <DeckButton label={node.name} active={selected === node.fullName} onClick={() => onSelect(node.fullName)} />
      </div>
      {hasChildren && open && (
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
      )}
    </li>
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
