import { useEffect, useRef, type MutableRefObject } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft } from 'lucide-react'
import { FluencyTidesAPI } from '../../api/client'
import { useFacePreference } from '../../hooks/useFacePreference'
import { retryTransient } from '../../lib/apiError'
import type { CardSide } from '../../lib/cardBrowse'
import { relativeDeckPath } from '../../lib/deckTree'
import { cn } from '../../lib/utils'
import { CardDetailModal } from '../CardDetailModal'
import { CardFrame } from '../CardFrame'
import { ElasticScrollArea, type ElasticScrollHandle } from '../ElasticScrollArea'
import { FaceTransition } from '../FaceTransition'
import { Button } from '../ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card'
import { Skeleton } from '../ui/skeleton'
import { ErrorNote } from '../shared/ErrorNote'
import { STICKY_HEADER, panelScrollClass } from '../shared/panelStyles'

/** {@link CardViewPanel} 的 props。Props of {@link CardViewPanel}. */
interface CardViewPanelProps {
  /** 與清單並排：填滿高度、自己捲動（隱藏捲軸、彈性回饋）。Sits beside the list and scrolls itself. */
  sideBySide: boolean
  className?: string
  /** 筆記欄位視窗是否打開（由呼叫端保存）。Whether the note fields dialog is open. */
  fieldsOpen: boolean
  onFieldsOpen: (open: boolean) => void
  /** 已經為哪張卡片捲動過（重新掛載時不再捲一次）。The card already scrolled to. */
  scrolledCardRef: MutableRefObject<number | null>
  /** 選取的卡片；null 時顯示提示。The selected card. */
  cardId: number | null
  /** 目前選取的牌組。The selected deck. */
  deck: string | null
  side: CardSide
  /** 跳轉紀錄有幾張（大於 0 才顯示「返回」）。Trail length. */
  trailLength: number
  onSide: (side: CardSide) => void
  /** 卡面要打開這些筆記。Opens the notes a card face asks for. */
  onBrowse: (noteIds: number[]) => void
  /** 回到跳轉前的卡片。Goes back along the trail. */
  onBack: () => void
  /** 筆記被刪除後。Called after the note is deleted. */
  onDeleted: () => void
}

/**
 * 卡面與筆記欄位面板：正反面切換（動畫見 `FaceTransition`）、點正面翻到背面、返回跳轉前的卡片。
 * 並排時卡面在 `ElasticScrollArea` 中自己捲動：隱藏捲軸，捲到頂或底再拉有彈性回饋與提示（卡面
 * iframe 內的手勢由橋接腳本轉送）；系統「減少動態效果」時不加彈性回饋。
 * The card face and note fields panel.
 * @returns 面板 JSX。Panel JSX.
 */
export function CardViewPanel({
  sideBySide,
  className,
  fieldsOpen,
  onFieldsOpen,
  scrolledCardRef,
  cardId,
  deck,
  side,
  trailLength,
  onSide,
  onBrowse,
  onBack,
  onDeleted,
}: CardViewPanelProps) {
  const panelRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<ElasticScrollHandle>(null)
  const { effective: facePreference, reducedMotion } = useFacePreference()

  // 不並排時卡面在清單下方：選卡時捲過去（瀏覽器也不更新畫面外的跨來源 iframe，卡面要進到畫面內
  // 才會回報高度）。並排時換卡捲回頂端。版面模式改變而重新掛載時，卡片沒換就不捲動。
  useEffect(() => {
    if (cardId === null || scrolledCardRef.current === cardId) {
      if (cardId === null) scrolledCardRef.current = null
      return
    }
    scrolledCardRef.current = cardId
    if (sideBySide) {
      scrollRef.current?.element?.scrollTo({ top: 0 })
    } else {
      panelRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
    // 只在換卡時捲動；版面模式改變不算
    // eslint-disable-next-line react-hooks/exhaustive-deps
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

  // 不並排時最寬 768px（與並排時的預設寬度相同）
  if (cardId === null) {
    return (
      <Card className={cn(panelScrollClass(sideBySide), !sideBySide && 'max-w-[48rem]', className)}>
        <CardContent className="pt-6 text-sm text-muted-foreground">從清單選一張卡片查看。</CardContent>
      </Card>
    )
  }

  const header = (
    <CardHeader className={cn('px-4 pb-3 space-y-2', sideBySide && STICKY_HEADER)}>
      {trailLength > 0 && (
        <Button size="sm" variant="ghost" className="-ml-2 h-7 self-start px-2" onClick={onBack}>
          <ArrowLeft className="h-4 w-4" />
          返回上一張卡片{trailLength > 1 && `（${trailLength}）`}
        </Button>
      )}
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
                  onClick={() => onSide(value)}
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
            <Button size="sm" variant="outline" onClick={() => onFieldsOpen(true)}>
              筆記欄位
            </Button>
          </div>
        </div>
      ) : (
        <CardTitle className="text-base">Card {cardId}</CardTitle>
      )}
    </CardHeader>
  )

  return (
    // 並排時卡片只負責外框，捲動與彈性回饋交給 ElasticScrollArea
    <Card ref={panelRef} className={cn(sideBySide ? 'flex h-full flex-col overflow-hidden' : 'max-w-[48rem]', className)}>
      <ElasticScrollArea
        ref={scrollRef}
        enabled={sideBySide && !reducedMotion}
        hideScrollbar={sideBySide}
        className={sideBySide ? 'min-h-0 flex-1 overflow-auto' : undefined}
        header={header}
        contentClassName="px-3 pb-3"
      >
        {isPending ? (
          <Skeleton className="h-60 w-full" />
        ) : isError ? (
          <ErrorNote title="無法載入卡片" error={error} />
        ) : (
          // 同一張卡片翻面時播動畫（偏好設定頁可選），換卡片時直接切換
          <FaceTransition
            value={side}
            resetKey={data.card_id}
            direction={side === 'answer' ? 1 : -1}
            preference={facePreference}
            waitForReady
            faceClassName="rounded-lg"
          >
            {(shown, { onReady }) => (
              <CardFrame
                key={`${data.card_id}-${shown}`}
                html={shown === 'question' ? data.question : data.answer}
                ord={data.ord}
                title={`${data.model_name} ${shown === 'question' ? '正面' : '背面'}`}
                onBrowse={onBrowse}
                // 點正面翻到背面（與 Anki 審閱相同）；背面不會翻回正面
                onFaceClick={shown === 'question' && side === 'question' ? () => onSide('answer') : undefined}
                onHeight={onReady}
                onScrollGesture={(gesture) => scrollRef.current?.handleGesture(gesture)}
              />
            )}
          </FaceTransition>
        )}
      </ElasticScrollArea>
      <CardDetailModal
        isOpen={fieldsOpen}
        onClose={() => onFieldsOpen(false)}
        cardDetail={noteDetail ?? null}
        isLoading={noteLoading && noteError === null}
        error={noteError}
        onDeleted={onDeleted}
      />
    </Card>
  )
}
