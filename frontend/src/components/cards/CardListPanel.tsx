import { useRef, type FormEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Search } from 'lucide-react'
import { FluencyTidesAPI } from '../../api/client'
import { useRevealSelected } from '../../hooks/useRevealSelected'
import { retryTransient } from '../../lib/apiError'
import { BROWSE_PAGE_SIZE } from '../../lib/cardBrowse'
import { cardPreview } from '../../lib/cardFields'
import { relativeDeckPath } from '../../lib/deckTree'
import { cn } from '../../lib/utils'
import { Button } from '../ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card'
import { Input } from '../ui/input'
import { Skeleton } from '../ui/skeleton'
import { ErrorNote } from '../shared/ErrorNote'
import { STICKY_HEADER, panelScrollClass } from '../shared/panelStyles'

/** {@link CardListPanel} 的 props。Props of {@link CardListPanel}. */
interface CardListPanelProps {
  /** 面板自己捲動（標題列固定）。The panel scrolls itself, with a sticky header. */
  scrollable: boolean
  /** 搜尋框中還沒送出的文字（由呼叫端保存）。The unsubmitted search text. */
  draft: string
  onDraft: (draft: string) => void
  deck: string | null
  search: string
  page: number
  selectedCardId: number | null
  onSearch: (q: string) => void
  onPage: (page: number) => void
  onSelect: (cardId: number) => void
}

/**
 * 卡片清單面板（含 Anki 搜尋與分頁）。每張顯示完整文字，放不下兩行時才由 CSS 截斷。
 * The card list panel with Anki search and paging.
 * @returns 面板 JSX。Panel JSX.
 */
export function CardListPanel({
  scrollable,
  draft,
  onDraft,
  deck,
  search,
  page,
  selectedCardId,
  onSearch,
  onPage,
  onSelect,
}: CardListPanelProps) {
  const { data, isPending, isError, error, isFetching } = useQuery({
    queryKey: ['cards', deck, search, page],
    queryFn: () => FluencyTidesAPI.browseCards({ deck: deck ?? undefined, q: search, page, pageSize: BROWSE_PAGE_SIZE }),
    placeholderData: (previous) => previous,
    // 搜尋語法錯誤（400）重試也沒用；只在連線中斷或 5xx 時重試一次
    retry: retryTransient,
  })
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1
  // 回到頁面時，把選取的卡片捲到看得到的地方
  const panelRef = useRef<HTMLDivElement>(null)
  useRevealSelected(panelRef, data !== undefined, scrollable)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    onSearch(draft.trim())
  }

  return (
    <Card ref={panelRef} className={panelScrollClass(scrollable)}>
      <CardHeader className={cn('pb-2 space-y-3', scrollable && STICKY_HEADER)}>
        <CardTitle className="text-base break-all">{deck ?? '全部牌組'}</CardTitle>
        <form onSubmit={submit} className="flex gap-2">
          <Input
            value={draft}
            onChange={(e) => onDraft(e.target.value)}
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
                const preview = cardPreview(card.fields)
                return (
                  <li key={card.card_id}>
                    <button
                      type="button"
                      aria-current={selectedCardId === card.card_id || undefined}
                      onClick={() => onSelect(card.card_id)}
                      className={cn(
                        'w-full rounded-md border px-3 py-2 text-left transition-colors',
                        selectedCardId === card.card_id ? 'border-primary bg-primary/5' : 'hover:bg-accent',
                      )}
                    >
                      {/* 完整文字，放不下兩行時才由 CSS 截斷；滑鼠移上去看全文 */}
                      <span className="line-clamp-2 font-medium [overflow-wrap:anywhere]" title={preview || undefined}>
                        {preview || '（沒有文字欄位）'}
                      </span>
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
