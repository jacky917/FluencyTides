/**
 * 卡片瀏覽頁面。
 * Card browsing page.
 *
 * 左：牌組樹（Anki 牌組名以 `::` 分層，可展開）；中：所選牌組（含子牌組）的卡片清單，
 * 可加 Anki 搜尋語句；右：Anki 渲染好的卡面（sandbox iframe，見 `lib/ankiFrame.ts` 的限制）
 * 與筆記欄位。不經過 task handler，任何筆記類型都能看。選取狀態放在網址（見
 * `lib/cardBrowse.ts`），可以重新整理或分享連結。
 *
 * Left: the deck tree; middle: the cards of the selected deck (sub-decks
 * included); right: the Anki-rendered face and the note fields. Selection
 * lives in the URL.
 */

import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { ChevronDown, ChevronRight, FolderTree, RefreshCw, Search } from 'lucide-react'
import { FluencyTidesAPI } from '../api/client'
import { CardDetailModal } from '../components/CardDetailModal'
import { CardFrame } from '../components/CardFrame'
import { Button } from '../components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card'
import { Input } from '../components/ui/input'
import { Skeleton } from '../components/ui/skeleton'
import { describeApiError, retryTransient } from '../lib/apiError'
import { parseBrowseParams } from '../lib/cardBrowse'
import { cardPreview } from '../lib/cardFields'
import { buildDeckTree, deckAncestors, relativeDeckPath, type DeckNode } from '../lib/deckTree'
import { cn } from '../lib/utils'

const PAGE_SIZE = 50

/**
 * 錯誤提示。An inline error.
 * @param props.title - 標題。Title.
 * @param props.error - 錯誤。The error.
 * @returns 提示 JSX。Error JSX.
 */
function ErrorNote({ title, error }: { title: string; error: unknown }) {
  return (
    <div role="alert" className="rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm">
      <p className="font-semibold text-destructive">{title}</p>
      <p className="break-words">{describeApiError(error).message}</p>
    </div>
  )
}

/**
 * 卡片瀏覽頁面。Card browsing page.
 * @returns 頁面 JSX。Page JSX.
 */
export default function Cards() {
  const [params, setParams] = useSearchParams()
  const { deck, search, page, cardId } = parseBrowseParams(params)

  /** 改網址參數；值為 null 或空字串時移除。Update URL params; null or '' removes one. */
  const update = (changes: Record<string, string | null>) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev)
      for (const [key, value] of Object.entries(changes)) {
        if (value === null || value === '') next.delete(key)
        else next.set(key, value)
      }
      return next
    })

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-3xl font-bold tracking-tight">卡片瀏覽</h2>
        <p className="text-muted-foreground mt-2">
          選一個牌組查看其中（含子牌組）的卡片；卡面由 Anki 渲染，與 Anki 中看到的大致相同。
        </p>
      </div>

      <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[16rem_22rem_minmax(0,1fr)]">
        <DeckPanel selected={deck} onSelect={(name) => update({ deck: name, page: null, card: null })} />
        <CardListPanel
          deck={deck}
          search={search}
          page={page}
          selectedCardId={cardId}
          onSearch={(q) => update({ q, page: null, card: null })}
          onPage={(next) => update({ page: next > 1 ? String(next) : null })}
          onSelect={(id) => update({ card: String(id) })}
        />
        <CardViewPanel cardId={cardId} deck={deck} onDeleted={() => update({ card: null })} />
      </div>
    </div>
  )
}

/**
 * 牌組樹面板。The deck tree panel.
 * @param props.selected - 目前選取的牌組；null 為全部。The selected deck.
 * @param props.onSelect - 選取牌組。Selects a deck.
 * @returns 面板 JSX。Panel JSX.
 */
function DeckPanel({ selected, onSelect }: { selected: string | null; onSelect: (deck: string | null) => void }) {
  const { data, isPending, isError, error, refetch, isFetching } = useQuery({
    queryKey: ['decks'],
    queryFn: FluencyTidesAPI.listDecks,
  })
  const tree = useMemo(() => buildDeckTree((data ?? []).map((item) => item.deck_name)), [data])
  // 使用者手動切換過的節點以手動為準；沒動過的，目前選取牌組的上層預設展開
  const [toggled, setToggled] = useState<Record<string, boolean>>({})
  const ancestors = useMemo(() => new Set(selected ? deckAncestors(selected).slice(0, -1) : []), [selected])
  const isOpen = (node: DeckNode) => toggled[node.fullName] ?? ancestors.has(node.fullName)

  return (
    <Card className="lg:max-h-[calc(100vh-10rem)] lg:overflow-auto">
      <CardHeader className="pb-2 flex-row items-center justify-between space-y-0">
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
                onToggle={(name, open) => setToggled((prev) => ({ ...prev, [name]: open }))}
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

/** {@link CardListPanel} 的 props。Props of {@link CardListPanel}. */
interface CardListPanelProps {
  deck: string | null
  search: string
  page: number
  selectedCardId: number | null
  onSearch: (q: string) => void
  onPage: (page: number) => void
  onSelect: (cardId: number) => void
}

/**
 * 卡片清單面板（含 Anki 搜尋與分頁）。The card list panel.
 * @returns 面板 JSX。Panel JSX.
 */
function CardListPanel({ deck, search, page, selectedCardId, onSearch, onPage, onSelect }: CardListPanelProps) {
  const [draft, setDraft] = useState(search)
  const { data, isPending, isError, error, isFetching } = useQuery({
    queryKey: ['cards', deck, search, page],
    queryFn: () => FluencyTidesAPI.browseCards({ deck: deck ?? undefined, q: search, page, pageSize: PAGE_SIZE }),
    placeholderData: (previous) => previous,
    // 搜尋語法錯誤（400）重試也沒用；只在連線中斷或 5xx 時重試一次
    retry: retryTransient,
  })
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1

  const submit = (event: FormEvent) => {
    event.preventDefault()
    onSearch(draft.trim())
  }

  return (
    <Card className="lg:max-h-[calc(100vh-10rem)] lg:overflow-auto">
      <CardHeader className="pb-2 space-y-3">
        <CardTitle className="text-base truncate" title={deck ?? '全部牌組'}>
          {deck ?? '全部牌組'}
        </CardTitle>
        <form onSubmit={submit} className="flex gap-2">
          <Input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Anki 搜尋，例如 note:Speaking_Coach_Dark"
            aria-label="Anki 搜尋"
          />
          <Button type="submit" size="icon" variant="outline" aria-label="搜尋">
            <Search className="h-4 w-4" />
          </Button>
        </form>
        {data && (
          <p className="text-xs text-muted-foreground">
            共 {data.total} 張{isFetching && '，更新中…'}
          </p>
        )}
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        {isPending ? (
          <div className="space-y-2">
            {[0, 1, 2, 3].map((index) => (
              <Skeleton key={index} className="h-12 w-full" />
            ))}
          </div>
        ) : isError && !data ? (
          <ErrorNote title="無法載入卡片" error={error} />
        ) : data && data.cards.length === 0 && data.total > 0 ? (
          <p className="text-muted-foreground">
            第 {page} 頁超出範圍（共 {pages} 頁）。
            <Button variant="link" className="h-auto p-0 pl-1" onClick={() => onPage(pages)}>
              到最後一頁
            </Button>
          </p>
        ) : data && data.cards.length === 0 ? (
          <p className="text-muted-foreground">這裡沒有卡片。</p>
        ) : (
          <>
            {isError && <ErrorNote title="更新失敗，以下為上次載入的清單" error={error} />}
            <ul className="space-y-1">
              {data?.cards.map((card) => {
                const subDeck = relativeDeckPath(card.deck_name, deck)
                return (
                  <li key={card.card_id}>
                    <button
                      type="button"
                      onClick={() => onSelect(card.card_id)}
                      className={cn(
                        'w-full rounded-md border px-3 py-2 text-left transition-colors',
                        selectedCardId === card.card_id ? 'border-primary bg-primary/5' : 'hover:bg-accent',
                      )}
                    >
                      <span className="block truncate font-medium">{cardPreview(card.fields) || '（沒有文字欄位）'}</span>
                      <span className="block truncate text-xs text-muted-foreground">
                        {card.model_name}
                        {subDeck && ` · ${subDeck}`}
                      </span>
                    </button>
                  </li>
                )
              })}
            </ul>
            {pages > 1 && (
              <div className="flex items-center justify-between pt-2">
                <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => onPage(page - 1)}>
                  上一頁
                </Button>
                <span className="text-xs text-muted-foreground">
                  {page} / {pages}
                </span>
                <Button size="sm" variant="outline" disabled={page >= pages} onClick={() => onPage(page + 1)}>
                  下一頁
                </Button>
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  )
}

/**
 * 卡面與筆記欄位面板。The card face and note fields panel.
 * @param props.cardId - 選取的卡片；null 時顯示提示。The selected card.
 * @param props.deck - 目前選取的牌組。The selected deck.
 * @param props.onDeleted - 筆記被刪除後。Called after the note is deleted.
 * @returns 面板 JSX。Panel JSX.
 */
function CardViewPanel({ cardId, deck, onDeleted }: { cardId: number | null; deck: string | null; onDeleted: () => void }) {
  const [side, setSide] = useState<'question' | 'answer'>('question')
  const [fieldsOpen, setFieldsOpen] = useState(false)
  const panelRef = useRef<HTMLDivElement>(null)

  // 窄畫面三欄疊成一欄，卡面在清單下方：選卡時捲過去。瀏覽器也不會更新畫面外的
  // 跨來源 iframe，卡面要進到畫面內才會回報高度。
  useEffect(() => {
    if (cardId !== null && window.matchMedia('(max-width: 1023px)').matches) {
      panelRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }, [cardId])
  const { data, isPending, isError, error } = useQuery({
    queryKey: ['card-view', cardId],
    queryFn: () => FluencyTidesAPI.getCardView(cardId!),
    enabled: cardId !== null,
    retry: retryTransient,
  })
  const noteId = data?.note_id ?? null
  const {
    data: noteDetail,
    isPending: noteLoading,
    error: noteError,
  } = useQuery({
    queryKey: ['card', noteId],
    queryFn: () => FluencyTidesAPI.getCard(noteId!),
    enabled: fieldsOpen && noteId !== null,
    retry: retryTransient,
  })

  if (cardId === null) {
    return (
      <Card>
        <CardContent className="pt-6 text-sm text-muted-foreground">從清單選一張卡片查看。</CardContent>
      </Card>
    )
  }

  return (
    <Card ref={panelRef}>
      <CardHeader className="pb-3">
        {data ? (
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0 space-y-1">
              <CardTitle className="text-base break-all">{data.model_name}</CardTitle>
              <p className="text-xs text-muted-foreground break-all">
                {relativeDeckPath(data.deck_name, deck) || data.deck_name} · Card {data.card_id} · Note {data.note_id}
              </p>
            </div>
            <div className="flex gap-2">
              <div className="inline-flex rounded-md border p-0.5" role="group" aria-label="卡面">
                {(['question', 'answer'] as const).map((value) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => setSide(value)}
                    aria-pressed={side === value}
                    className={cn(
                      'rounded px-3 py-1 text-sm',
                      side === value ? 'bg-primary text-primary-foreground' : 'hover:bg-accent',
                    )}
                  >
                    {value === 'question' ? '正面' : '背面'}
                  </button>
                ))}
              </div>
              <Button size="sm" variant="outline" onClick={() => setFieldsOpen(true)}>
                筆記欄位
              </Button>
            </div>
          </div>
        ) : (
          <CardTitle className="text-base">Card {cardId}</CardTitle>
        )}
      </CardHeader>
      <CardContent>
        {isPending ? (
          <Skeleton className="h-60 w-full" />
        ) : isError ? (
          <ErrorNote title="無法載入卡片" error={error} />
        ) : (
          <CardFrame
            key={`${data.card_id}-${side}`}
            html={side === 'question' ? data.question : data.answer}
            ord={data.ord}
            title={`${data.model_name} ${side === 'question' ? '正面' : '背面'}`}
          />
        )}
      </CardContent>
      <CardDetailModal
        isOpen={fieldsOpen}
        onClose={() => setFieldsOpen(false)}
        cardDetail={noteDetail ?? null}
        isLoading={noteLoading && noteError === null}
        error={noteError}
        onDeleted={onDeleted}
      />
    </Card>
  )
}
