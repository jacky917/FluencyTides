import { useRef, useState, type FormEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronRight, Plus, Search } from 'lucide-react'
import { FluencyTidesAPI } from '../../api/client'
import { useDeckStatus } from '../../hooks/useDeckStatus'
import { useRevealSelected } from '../../hooks/useRevealSelected'
import { retryTransient } from '../../lib/apiError'
import { searchableHandlers, selectHandler, type FieldSearchState } from '../../lib/fieldSearch'
import { BROWSE_PAGE_SIZE, browseNoteTypes } from '../../lib/cardBrowse'
import { cardPreview } from '../../lib/cardFields'
import { relativeDeckPath } from '../../lib/deckTree'
import { cn } from '../../lib/utils'
import { Button } from '../ui/button'
import { Card, CardContent, CardHeader } from '../ui/card'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '../ui/collapsible'
import { Input } from '../ui/input'
import { Skeleton } from '../ui/skeleton'
import { DeckCrumbs, DeckScopeSelects } from '../shared/DeckScopeBar'
import { ErrorNote } from '../shared/ErrorNote'
import { STICKY_HEADER, panelScrollClass } from '../shared/panelStyles'
import { FieldSearchPanel } from './FieldSearchPanel'

/** 「全部卡片類型」在選單裡的值。The value of "all card types". */
const ALL_CARD_TYPES = ''

/** {@link CardListPanel} 的 props。Props of {@link CardListPanel}. */
interface CardListPanelProps {
  /** 面板自己捲動（標題列固定）。The panel scrolls itself, with a sticky header. */
  scrollable: boolean
  /** 搜尋框中還沒送出的文字（由呼叫端保存）。The unsubmitted search text. */
  draft: string
  onDraft: (draft: string) => void
  /**
   * 欄位搜尋的狀態（由呼叫端保存）；`handler` 也是條件列選的卡片類型（null＝全部卡片類型）。
   * The field-search state; its handler is also the card type chosen in the scope bar.
   */
  fieldSearch: FieldSearchState
  onFieldSearch: (state: FieldSearchState) => void
  /** 條件列選的筆記類型；null 為全部。The chosen note type, or null for all. */
  noteType: string | null
  onNoteType: (noteType: string | null) => void
  /** 包含幾層牌組（1＝牌組本身，0＝全部子牌組）。Deck levels, 0 meaning all. */
  depth: number
  onDepth: (depth: number) => void
  deck: string | null
  /** 點麵包屑回到上層牌組（null＝全部牌組）。Goes to another deck from the breadcrumb. */
  onDeck: (deck: string | null) => void
  search: string
  page: number
  selectedCardId: number | null
  onSearch: (q: string) => void
  onPage: (page: number) => void
  onSelect: (cardId: number) => void
  /** 右欄是否正在顯示建卡表單（「＋」顯示為按下）。Whether the create form is open. */
  creating: boolean
  /** 按「＋」：打開或關閉建卡表單。Toggles the create form. */
  onToggleCreate: () => void
}

/**
 * 卡片清單面板（`docs/wip/cards_page_revamp_FEAT_2026-11-23.md` D5）。標題區由上到下：麵包屑與「＋」建卡按鈕
 * → 牌組條件列（卡片類型、筆記類型、層數，與知識圖譜共用）→ 欄位搜尋（卡片類型開放時）→ Anki 語法 → 張數。
 * 清單每張顯示完整文字，放不下兩行時才由 CSS 截斷；「＋」在右欄打開建卡表單
 * （`docs/archive/card_list_create_FEAT_2026-10-30.md` D6）。
 * The card list panel with the shared scope bar, field search, Anki search and paging.
 * @returns 面板 JSX。Panel JSX.
 */
export function CardListPanel({
  scrollable,
  draft,
  onDraft,
  fieldSearch,
  onFieldSearch,
  noteType,
  onNoteType,
  depth,
  onDepth,
  deck,
  onDeck,
  search,
  page,
  selectedCardId,
  onSearch,
  onPage,
  onSelect,
  creating,
  onToggleCreate,
}: CardListPanelProps) {
  // 卡片類型的選項；讀不到時只有「全部卡片類型」
  const { data: handlers, isError: handlersFailed } = useQuery({
    queryKey: ['handlers'],
    queryFn: FluencyTidesAPI.listHandlers,
    retry: retryTransient,
  })
  const cardTypes = (handlers ?? []).filter((h) => h.note_types.length > 0)
  // 記住的卡片類型已經不存在時，當成「全部卡片類型」
  const cardType = cardTypes.find((h) => h.name === fieldSearch.handler)
  const noteTypes = browseNoteTypes(cardType?.note_types, noteType)
  const searchable = searchableHandlers(handlers).find((h) => h.name === cardType?.name)

  const { data, isPending, isError, error, isFetching } = useQuery({
    queryKey: ['cards', deck, search, page, noteTypes ?? null, depth],
    queryFn: () =>
      FluencyTidesAPI.browseCards({ deck: deck ?? undefined, q: search, page, pageSize: BROWSE_PAGE_SIZE, noteTypes, depth }),
    placeholderData: (previous) => previous,
    // 記住了卡片類型時，等卡片類型讀到（或讀取失敗）才查，免得先閃一次沒篩的清單
    enabled: !fieldSearch.handler || handlers !== undefined || handlersFailed,
    // 搜尋語法錯誤（400）重試也沒用；只在連線中斷或 5xx 時重試一次
    retry: retryTransient,
  })
  const deckMissing = useDeckStatus(deck) === 'missing'
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1
  // 用欄位搜尋時 Anki 語法收起來（展開可看、可改組好的查詢）；有語句時預設展開，看得到目前套用的條件
  const [syntaxOpen, setSyntaxOpen] = useState(() => search !== '')
  // 回到頁面時，把選取的卡片捲到看得到的地方
  const panelRef = useRef<HTMLDivElement>(null)
  useRevealSelected(panelRef, data !== undefined, scrollable)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    onSearch(draft.trim())
  }
  // 換條件回到第 1 頁；換卡片類型時欄位條件與筆記類型清空（欄位不同）
  const chooseCardType = (value: string) => {
    onFieldSearch(selectHandler(value === ALL_CARD_TYPES ? null : value))
    onNoteType(null)
    onPage(1)
  }

  // 觸控時輸入框 16px 字（iOS Safari 聚焦小於 16px 的輸入框會放大整頁），按鈕跟著加高
  const ankiSearch = (
    <form onSubmit={submit} className="flex gap-2">
      <Input
        value={draft}
        onChange={(e) => onDraft(e.target.value)}
        placeholder="Anki 搜尋，例如 note:Speaking_Coach_Dark"
        aria-label="Anki 搜尋"
        className="pointer-coarse:text-base"
      />
      <Button type="submit" size="icon" variant="outline" className="shrink-0" aria-label="搜尋">
        <Search className="h-4 w-4" />
      </Button>
    </form>
  )

  return (
    // data-card-list-panel／data-card-list：卡片頁的「回到清單」靠它們找到清單與選取的卡片
    <Card ref={panelRef} className={panelScrollClass(scrollable)} data-card-list-panel>
      <CardHeader className={cn('space-y-3 pb-2', scrollable && STICKY_HEADER)}>
        {/* 牌組名很長時換行，「＋」固定在右上角不被擠掉 */}
        <div className="flex items-start justify-between gap-2">
          <DeckCrumbs deck={deck} onDeck={onDeck} missing={deckMissing} className="pt-1.5" />
          <Button
            type="button"
            size="icon"
            variant={creating ? 'default' : 'outline'}
            className="h-8 w-8 shrink-0"
            aria-pressed={creating}
            // 名稱固定，開關狀態由 aria-pressed 表達；提示文字說明再按一次會關閉
            aria-label="在這個牌組建立卡片"
            title={creating ? '在這個牌組建立卡片（再按一次關閉）' : '在這個牌組建立卡片'}
            onClick={onToggleCreate}
          >
            <Plus className="h-4 w-4" />
          </Button>
        </div>

        <div className="flex flex-wrap items-center gap-2" role="group" aria-label="篩選條件">
          <DeckScopeSelects
            cardTypes={[
              { value: ALL_CARD_TYPES, label: '全部卡片類型' },
              ...cardTypes.map((h) => ({ value: h.name, label: h.title })),
            ]}
            cardType={cardType?.name ?? ALL_CARD_TYPES}
            onCardType={chooseCardType}
            noteTypes={cardType?.note_types ?? []}
            noteType={noteType && cardType?.note_types.includes(noteType) ? noteType : null}
            onNoteType={(value) => {
              onNoteType(value)
              onPage(1)
            }}
            depth={depth}
            onDepth={(value) => {
              onDepth(value)
              onPage(1)
            }}
          />
        </div>

        {searchable && (
          <FieldSearchPanel
            handler={searchable}
            state={fieldSearch}
            onChange={onFieldSearch}
            onQuery={(query) => {
              onDraft(query)
              onSearch(query)
            }}
          />
        )}

        {/* 用欄位搜尋時 Anki 語法收起來、可展開（有動畫）；沒用時直接顯示 */}
        {searchable ? (
          <Collapsible open={syntaxOpen} onOpenChange={setSyntaxOpen}>
            <CollapsibleTrigger className="group flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground pointer-coarse:py-1.5">
              <ChevronRight className="h-3.5 w-3.5 transition-transform group-data-[state=open]:rotate-90 motion-reduce:transition-none" />
              進階：Anki 語法
            </CollapsibleTrigger>
            {/* 內距讓輸入框的焦點框不被收合動畫的 overflow 裁掉 */}
            <CollapsibleContent className="motion-reduce:animate-none">
              <div className="p-0.5 pt-2">{ankiSearch}</div>
            </CollapsibleContent>
          </Collapsible>
        ) : (
          ankiSearch
        )}

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
            <ul className="space-y-1" data-card-list>
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
