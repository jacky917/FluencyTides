import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { useIsMutating, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, ChevronRight, FolderPlus, FolderTree, Loader2, Plus, RefreshCw, X } from 'lucide-react'
import { toast } from 'sonner'
import { FluencyTidesAPI } from '../../api/client'
import { useRevealSelected } from '../../hooks/useRevealSelected'
import { describeApiError, failureTitle, isBackendError } from '../../lib/apiError'
import {
  buildDeckTree,
  deckAncestors,
  deckCreateErrorPlacement,
  joinDeckName,
  validateDeckName,
  type DeckNode,
} from '../../lib/deckTree'
import { cn } from '../../lib/utils'
import type { CreateDeckResponse } from '../../types/api'
import { Button } from '../ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '../ui/collapsible'
import { Input } from '../ui/input'
import { Skeleton } from '../ui/skeleton'
import { ErrorNote } from './ErrorNote'
import { Panel } from './Panel'

/**
 * 與 AnkiWeb 同步可能帶回別處的新卡片與標籤，牌組以外這些來自 Anki 的清單也要重抓（在背景，不等）。
 * Other Anki-backed queries refetched in the background after a sync.
 */
const OTHER_SYNCED_KEYS = [['cards'], ['card-view'], ['tags'], ['graph']]

/** 新增牌組的 mutation key：面板用它知道有沒有牌組正在建立。The key of the create-deck mutation. */
const CREATE_DECK_KEY = ['create-deck']

/** 每一層的縮排（px）。Indentation per level. */
const INDENT = 12
/** 展開箭頭佔的寬度（px）；沒有子牌組的節點留同樣的空位。Width of the expand chevron. */
const CHEVRON_WIDTH = 18

/** 分類節點（Anki 裡沒有這個牌組）的說明。The explanation of a category node. */
const CATEGORY_HINT = 'Anki 裡沒有這個牌組，只是分類'

/**
 * 節點旁的小按鈕（新增子牌組、建立這一層）：滑過或聚焦到那一列才出現，不佔平常的寬度；手機寬度與
 * 觸控裝置沒有「滑過」，一直顯示。24px 見方。
 * Row buttons: shown on hover or focus only, always shown on narrow or touch screens.
 */
const ROW_BUTTON =
  'hidden h-6 w-6 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-accent-foreground ' +
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 ' +
  'group-hover:flex group-focus-within:flex max-md:flex pointer-coarse:flex'

/**
 * 新增牌組的輸入列開在哪裡（deck_create 計畫書 §6）；同一時間只有一列。
 * Where the new-deck input row is open; at most one at a time.
 *
 * - `top`：標題列的「＋」，新增最上層牌組；輸入列在「全部牌組」下方。
 * - `child`：節點的「＋」，在 `parent`（完整名稱）底下新增；輸入列在它的子層最前面。
 * - `self`：分類節點的「建立這一層」，補建 `node`（完整名稱）自己；輸入列在節點正下方，預填它這一層的名稱。
 */
type AddingTarget = { kind: 'top' } | { kind: 'child'; parent: string } | { kind: 'self'; node: string }

/**
 * 牌組樹面板（Anki 牌組名以 `::` 分層，可展開），Cards、Generate、Graph 三頁共用。展開狀態由
 * 呼叫端保存（各頁分開記）：版面模式改變時面板會重新掛載，狀態才不會遺失。
 *
 * 也在這裡新增牌組（deck_create 計畫書）：標題列的「＋」新增最上層牌組，節點的「＋」新增子牌組；
 * Anki 裡不存在的中間節點（分類節點）以淡色斜體顯示，可以按「建立這一層」補建。
 *
 * The deck tree panel shared by the three pages; the expansion state is owned
 * by the caller. Decks are created here too: the header "+" adds a top-level
 * deck, a node's "+" adds a child, and a category node can be created in place.
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

  const [adding, setAdding] = useState<AddingTarget | null>(null)
  // 有牌組正在建立時鎖住所有「＋」：換到別的輸入列會把建立中的那一列卸載，它的結果（成功後要選取、
  // 失敗的訊息）就沒有地方顯示了
  const creating = useIsMutating({ mutationKey: CREATE_DECK_KEY }) > 0
  // 建好後：展開上層、選取新牌組、提示
  const handleCreated = (result: CreateDeckResponse, listReloaded: boolean) => {
    setAdding(null)
    const parents = deckAncestors(result.deck_name).slice(0, -1)
    if (parents.length > 0) {
      onToggled((prev) => ({ ...prev, ...Object.fromEntries(parents.map((name) => [name, true])) }))
    }
    onSelect(result.deck_name)
    toast.success(`已建立牌組「${result.deck_name}」`)
    if (result.sync_warning) toast.warning(result.sync_warning)
    // 清單沒重抓成功：樹上還看不到新牌組，建卡頁也會以為它不存在，要講清楚不是沒建成
    if (!listReloaded) toast.warning('牌組清單重新載入失敗，畫面上還看不到新牌組；請按牌組旁的重新整理。')
  }

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
        <div className="flex items-center">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setAdding({ kind: 'top' })}
            disabled={data === undefined || creating}
            aria-label="新增最上層牌組"
            aria-pressed={adding?.kind === 'top'}
            title="新增最上層牌組"
          >
            <Plus className="h-4 w-4" />
          </Button>
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
        </div>
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
              <span className="inline-block" style={{ width: CHEVRON_WIDTH }} />
              <DeckButton label="全部牌組" active={selected === null} onClick={() => onSelect(null)} />
            </div>
          </li>
          {adding?.kind === 'top' && (
            <li role="none">
              <DeckNameInput
                key="top"
                depth={0}
                parent={null}
                initial=""
                onCreated={handleCreated}
                onCancel={() => setAdding(null)}
              />
            </li>
          )}
          {tree.map((node) => (
            <DeckItem
              key={node.fullName}
              node={node}
              depth={0}
              selected={selected}
              isOpen={isOpen}
              onToggle={(name, open) => onToggled((prev) => ({ ...prev, [name]: open }))}
              onSelect={onSelect}
              adding={adding}
              locked={creating}
              onAdding={setAdding}
              onCreated={handleCreated}
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
  /** 目前開著的新增輸入列。The open input row, if any. */
  adding: AddingTarget | null
  /** 有牌組正在建立：不能再開別的輸入列。A deck is being created; no other row may open. */
  locked: boolean
  /** 打開或關掉輸入列。Opens or closes the input row. */
  onAdding: (next: AddingTarget | null) => void
  /** 牌組建好了；`listReloaded` 是牌組清單有沒有重抓成功。A deck was created. */
  onCreated: (result: CreateDeckResponse, listReloaded: boolean) => void
}

/**
 * 牌組樹的一個節點（遞迴）。子牌組以 Collapsible 展開收合，有高度動畫。節點旁有「＋」新增子牌組；
 * 分類節點（Anki 裡沒有這個牌組）另有「建立這一層」。
 * One deck tree node, recursively; children expand with a height animation.
 * A "+" adds a child deck, and a category node can be created in place.
 * @returns 節點 JSX。Node JSX.
 */
function DeckItem({
  node,
  depth,
  selected,
  isOpen,
  onToggle,
  onSelect,
  adding,
  locked,
  onAdding,
  onCreated,
}: DeckItemProps) {
  const hasChildren = node.children.length > 0
  const addingChild = adding?.kind === 'child' && adding.parent === node.fullName
  const addingSelf = adding?.kind === 'self' && adding.node === node.fullName
  // 正在這個節點底下新增時，即使沒有子牌組、或被收合，也要展開讓輸入列看得到
  const open = isOpen(node) || addingChild
  const showGroup = hasChildren || addingChild

  return (
    <Collapsible asChild open={showGroup && open} onOpenChange={(next) => onToggle(node.fullName, next)}>
      <li role="treeitem" aria-selected={selected === node.fullName} aria-expanded={hasChildren ? open : undefined}>
        <div className="group flex items-center">
          {hasChildren ? (
            <CollapsibleTrigger asChild>
              <button
                type="button"
                className="p-0.5 rounded hover:bg-accent"
                style={{ marginLeft: depth * INDENT }}
                aria-label={open ? `收合 ${node.name}` : `展開 ${node.name}`}
              >
                <ChevronRight className={cn('h-3.5 w-3.5 transition-transform duration-200', open && 'rotate-90')} />
              </button>
            </CollapsibleTrigger>
          ) : (
            <span className="inline-block" style={{ width: CHEVRON_WIDTH, marginLeft: depth * INDENT }} />
          )}
          <DeckButton
            label={node.name}
            active={selected === node.fullName}
            category={!node.exists}
            onClick={() => onSelect(node.fullName)}
          />
          {!node.exists && (
            <button
              type="button"
              className={cn(ROW_BUTTON, addingSelf && 'flex')}
              disabled={locked}
              aria-label={`在 Anki 建立「${node.name}」這一層`}
              aria-pressed={addingSelf}
              title="建立這一層"
              onClick={() => onAdding({ kind: 'self', node: node.fullName })}
            >
              <FolderPlus className="h-3.5 w-3.5" />
            </button>
          )}
          <button
            type="button"
            className={cn(ROW_BUTTON, addingChild && 'flex')}
            disabled={locked}
            aria-label={`在「${node.name}」底下新增牌組`}
            aria-pressed={addingChild}
            title="新增子牌組"
            onClick={() => {
              onToggle(node.fullName, true)
              onAdding({ kind: 'child', parent: node.fullName })
            }}
          >
            <Plus className="h-3.5 w-3.5" />
          </button>
        </div>
        {addingSelf && (
          <DeckNameInput
            key={`self:${node.fullName}`}
            depth={depth}
            // 補建這一層：建在它的上層底下，預填它這一層的名稱，按 Enter 就是建立它自己
            parent={deckAncestors(node.fullName).at(-2) ?? null}
            initial={node.name}
            onCreated={onCreated}
            onCancel={() => onAdding(null)}
          />
        )}
        {showGroup && (
          <CollapsibleContent asChild>
            <ul role="group">
              {addingChild && (
                <li role="none">
                  <DeckNameInput
                    key={`child:${node.fullName}`}
                    depth={depth + 1}
                    parent={node.fullName}
                    initial=""
                    onCreated={onCreated}
                    onCancel={() => onAdding(null)}
                  />
                </li>
              )}
              {node.children.map((child) => (
                <DeckItem
                  key={child.fullName}
                  node={child}
                  depth={depth + 1}
                  selected={selected}
                  isOpen={isOpen}
                  onToggle={onToggle}
                  onSelect={onSelect}
                  adding={adding}
                  locked={locked}
                  onAdding={onAdding}
                  onCreated={onCreated}
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
 * 新增牌組的輸入列：只輸入新的這一層名稱（也可以打 `a::b` 一次建多層），Enter 建立、Esc 取消，
 * 失焦且空白時取消。名稱先在前端檢查（規則與後端相同）；後端回 400、409 時訊息顯示在輸入列下方，
 * 其他錯誤用 toast（那時牌組可能其實已經建好，標題照 `failureTitle` 的規則寫）。建立中輸入框停用並
 * 顯示轉圈。
 * The new-deck input row: Enter creates, Esc cancels, and blurring while empty cancels.
 * @param props.depth - 縮排的層數。The indentation depth.
 * @param props.parent - 新牌組的上層完整名稱；null 表示最上層。The parent's full name.
 * @param props.initial - 預填的名稱。The prefilled name.
 * @param props.onCreated - 建好後呼叫；第二個參數是牌組清單有沒有重抓成功。Called once the deck exists.
 * @param props.onCancel - 取消。Cancels.
 * @returns 輸入列 JSX。The input row JSX.
 */
function DeckNameInput({
  depth,
  parent,
  initial,
  onCreated,
  onCancel,
}: {
  depth: number
  parent: string | null
  initial: string
  onCreated: (result: CreateDeckResponse, listReloaded: boolean) => void
  onCancel: () => void
}) {
  const [value, setValue] = useState(initial)
  const [error, setError] = useState<string | null>(null)
  const errorId = useId()
  const inputRef = useRef<HTMLInputElement>(null)
  // 建立中換頁（或版面切換讓面板重新掛載）時這一列會被卸載，mutation 的結果仍會回來：卸載後的錯誤
  // 沒有輸入列可以顯示，改用 toast，不能無聲無息
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])
  const queryClient = useQueryClient()
  const create = useMutation({
    mutationKey: CREATE_DECK_KEY,
    mutationFn: async (fullName: string) => {
      const result = await FluencyTidesAPI.createDeck(fullName)
      // 等牌組清單重抓完才算結束：之後選取新牌組時它已經在清單裡，建卡頁不會閃一下錯誤提示。
      // 重抓失敗不是建立失敗（牌組已經建好），另外回報，不讓它掉進 onError 變成「無法新增」
      let listReloaded = true
      try {
        await queryClient.invalidateQueries({ queryKey: ['decks'] }, { throwOnError: true })
      } catch (err) {
        listReloaded = false
        console.error('[DeckTreePanel] 牌組已建立，但重新載入牌組清單失敗', err)
      }
      return { result, listReloaded }
    },
    onSuccess: ({ result, listReloaded }) => onCreated(result, listReloaded),
    onError: (err) => {
      const info = describeApiError(err)
      const placement = deckCreateErrorPlacement(info.status, isBackendError(err))
      if (placement === 'inline' && mounted.current) {
        setError(info.message)
        return
      }
      if (!isBackendError(err)) console.error('[DeckTreePanel] 新增牌組失敗', err)
      toast.error(failureTitle(info.status, '新增牌組'), { description: info.message })
      // 不是 400、409：牌組可能其實已經建好（例如建立後才斷線），重抓清單讓樹反映實際狀態
      if (placement === 'toast') queryClient.invalidateQueries({ queryKey: ['decks'] })
    },
  })
  // 建立中輸入框是停用的，焦點會跑掉；失敗後把焦點放回去，可以直接改名稱再試。放在 effect 裡：要等
  // 輸入框恢復可用的那次 render 完成，對停用中的輸入框呼叫 focus() 沒有作用
  useEffect(() => {
    if (create.isError && !create.isPending) inputRef.current?.focus()
  }, [create.isError, create.isPending])

  const submit = () => {
    if (create.isPending) return
    // 空白要先擋：接上上層再檢查會變成「第 N 層是空的」，使用者只輸入了一格，看不懂
    const invalid = value.trim() ? validateDeckName(joinDeckName(parent, value)) : '牌組名稱不能是空的'
    setError(invalid)
    if (!invalid) create.mutate(joinDeckName(parent, value))
  }

  const label = parent === null ? '新的最上層牌組名稱' : `在「${parent}」底下的新牌組名稱`
  const indent = depth * INDENT + CHEVRON_WIDTH
  return (
    <div className="py-0.5" style={{ marginLeft: indent }}>
      <div className="flex items-center gap-1">
        <Input
          ref={inputRef}
          autoFocus
          value={value}
          disabled={create.isPending}
          placeholder="牌組名稱（可用 :: 一次建多層）"
          aria-label={label}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
          className={cn('h-7 min-w-0 flex-1 px-2 py-1 text-sm', error && 'border-destructive')}
          onChange={(e) => {
            setValue(e.target.value)
            if (error) setError(null)
          }}
          // 預填名稱時（補建這一層）全選，直接按 Enter 或直接改都方便
          onFocus={(e) => {
            if (initial) e.currentTarget.select()
          }}
          onKeyDown={(e) => {
            // 輸入法選字中的 Enter 不算
            if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
              e.preventDefault()
              submit()
            } else if (e.key === 'Escape') {
              e.preventDefault()
              onCancel()
            }
          }}
          onBlur={() => {
            if (!value.trim() && !create.isPending) onCancel()
          }}
        />
        {create.isPending ? (
          <Loader2 className="h-4 w-4 shrink-0 animate-spin text-muted-foreground" role="status" aria-label="正在建立牌組" />
        ) : (
          <>
            <button
              type="button"
              className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              aria-label="建立牌組"
              title="建立（Enter）"
              onClick={submit}
            >
              <Check className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              aria-label="取消新增牌組"
              title="取消（Esc）"
              onClick={onCancel}
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </>
        )}
      </div>
      {error && (
        <p id={errorId} role="alert" className="mt-0.5 text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  )
}

/**
 * 牌組名稱按鈕。分類節點（Anki 裡沒有這個牌組）以淡色斜體顯示，滑鼠移上去有說明。
 * A deck name button; a category node is drawn muted and italic, with an explanation on hover.
 * @param props.category - 是否為分類節點。Whether this is a category node.
 * @returns 按鈕 JSX。Button JSX.
 */
function DeckButton({
  label,
  active,
  category = false,
  onClick,
}: {
  label: string
  active: boolean
  category?: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      aria-current={active || undefined}
      onClick={onClick}
      className={cn(
        'min-w-0 flex-1 truncate rounded-md px-2 py-1 text-left transition-colors',
        active ? 'bg-primary text-primary-foreground' : 'hover:bg-accent hover:text-accent-foreground',
        category && 'italic',
        category && !active && 'text-muted-foreground',
      )}
      title={category ? `${label}（${CATEGORY_HINT}）` : label}
    >
      {label}
      {category && <span className="sr-only">（{CATEGORY_HINT}）</span>}
    </button>
  )
}
