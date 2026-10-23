import { useEffect, useRef, useState, type MutableRefObject } from 'react'
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft } from 'lucide-react'
import { FluencyTidesAPI } from '../../api/client'
import { useFaceSettings } from '../../hooks/useFaceSettings'
import { retryTransient } from '../../lib/apiError'
import type { CardSide } from '../../lib/cardBrowse'
import { nextShownFace, parseFaceValue, rememberLatest, type FaceValue, type ShownFace } from '../../lib/cardFaces'
import { relativeDeckPath } from '../../lib/deckTree'
import { facePreference } from '../../lib/faceTransitions'
import { cn } from '../../lib/utils'
import type { CardView } from '../../types/api'
import { CardDetailModal } from '../CardDetailModal'
import { CardFrame, CardFrameMessages, type CardFrameStatus } from '../CardFrame'
import { ElasticScrollArea, type ElasticScrollHandle } from '../ElasticScrollArea'
import { FaceTransition, type FaceControls } from '../FaceTransition'
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
  /** 目前的位置是不是往回到達的（「返回」或瀏覽器上一頁），換卡時倒著播。Whether we arrived by going back. */
  arrivedBack: boolean
  onSide: (side: CardSide) => void
  /** 卡面要打開這些筆記。Opens the notes a card face asks for. */
  onBrowse: (noteIds: number[]) => void
  /** 回到跳轉前的卡片。Goes back along the trail. */
  onBack: () => void
  /** 筆記被刪除後。Called after the note is deleted. */
  onDeleted: () => void
}

/**
 * 最多記住幾面的狀態訊息：切換時新舊兩面都要有，再多留兩面給快速連續切換（剛離開的一面可能
 * 很快又切回來）。How many faces' messages to keep: both faces of a switch, plus room for quick toggling.
 */
const STATUS_LIMIT = 4
const NO_STATUS: CardFrameStatus = { failedMedia: [], notices: [], frameErrors: [] }

/** 狀態訊息的內容（內容一樣時提示不重新淡入）。The content of the messages, for the fade-in key. */
const statusSignature = ({ failedMedia, notices, frameErrors }: CardFrameStatus) =>
  JSON.stringify([failedMedia.map((item) => item.name), notices, frameErrors])

/**
 * 卡面與筆記欄位面板：翻面與換卡（動畫見 `FaceTransition`，兩種情境各自的動畫在偏好設定頁選）、
 * 點正面翻到背面、返回跳轉前的卡片。換卡時新卡片載入完成前留著舊的一張，載入後再播換卡動畫；
 * 卡面的提示畫在動畫外框下方，隨卡面伸長或縮短移動。
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
  arrivedBack,
  onSide,
  onBrowse,
  onBack,
  onDeleted,
}: CardViewPanelProps) {
  const panelRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<ElasticScrollHandle>(null)
  const { effective: faceSettings, reducedMotion } = useFaceSettings()
  const queryClient = useQueryClient()

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

  const { data, isError, error, isPlaceholderData } = useQuery({
    queryKey: ['card-view', cardId],
    queryFn: () => FluencyTidesAPI.getCardView(cardId!),
    enabled: cardId !== null,
    retry: retryTransient,
    // 換卡時新卡片載入完成前留著舊的一張，換卡動畫才有東西可以接
    placeholderData: keepPreviousData,
  })
  // 選取的這張卡片的資料（舊卡片的暫代資料不算）
  const card = data !== undefined && !isPlaceholderData ? data : undefined
  const noteId = card?.note_id ?? null
  const {
    data: noteDetail,
    isPending: noteLoading,
    error: noteError,
  } = useQuery({
    queryKey: ['note', noteId],
    queryFn: () => FluencyTidesAPI.getNote(noteId!),
    enabled: fieldsOpen && noteId !== null,
    retry: retryTransient,
  })

  // 要顯示的那一面跟著選取的卡片與正反面走，但要等新卡片載入完成才換（在 render 中對齊）。
  // 新卡片載入期間網址不會再變，arrivedBack 仍是這次換卡的導覽方向
  const [shown, setShown] = useState<ShownFace | null>(null)
  if (cardId === null) {
    if (shown !== null) setShown(null)
  } else if (card) {
    const next = nextShownFace(shown, card.card_id, side, arrivedBack)
    if (next !== shown) setShown(next)
  }

  // 各面回報的狀態訊息（只留最近幾面）
  const [statuses, setStatuses] = useState<ReadonlyMap<FaceValue, CardFrameStatus>>(() => new Map())
  const recordStatus = (face: FaceValue, status: CardFrameStatus) =>
    setStatuses((prev) => rememberLatest(prev, face, status, STATUS_LIMIT))

  /**
   * 畫出某一面。卡片的資料從快取取：換卡時舊卡片已不是目前的查詢，但在 gcTime（main.tsx）內仍在
   * 快取裡；找不到時不畫。Renders a face from the query cache; renders nothing when it is gone.
   */
  const renderFace = (face: FaceValue, { onReady }: FaceControls) => {
    const { cardId: faceCard, side: faceSide } = parseFaceValue(face)
    const view = queryClient.getQueryData<CardView>(['card-view', faceCard])
    if (!view) return null
    return (
      <CardFrame
        key={face}
        html={faceSide === 'question' ? view.question : view.answer}
        ord={view.ord}
        title={`${view.model_name} ${faceSide === 'question' ? '正面' : '背面'}`}
        onBrowse={onBrowse}
        // 點正面翻到背面（與 Anki 審閱相同）；背面不會翻回正面，換卡動畫中的舊卡片也不能點
        onFaceClick={
          faceCard === cardId && faceSide === 'question' && side === 'question' ? () => onSide('answer') : undefined
        }
        onHeight={onReady}
        onScrollGesture={(gesture) => scrollRef.current?.handleGesture(gesture)}
        onStatus={(status) => recordStatus(face, status)}
      />
    )
  }

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
      {card ? (
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 space-y-1">
            <CardTitle className="text-base break-all">{card.model_name}</CardTitle>
            <p className="text-xs text-muted-foreground break-all">
              {relativeDeckPath(card.deck_name, deck) || card.deck_name} · Card {card.card_id} · Note {card.note_id}
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
        {isError ? (
          <ErrorNote title="無法載入卡片" error={error} />
        ) : shown === null ? (
          <Skeleton className="h-60 w-full" />
        ) : (
          <FaceTransition
            value={shown.value}
            // 換卡也要播動畫，所以不用 resetKey：卡片與正反面一起編進 value
            resetKey={null}
            direction={shown.direction}
            preference={facePreference(faceSettings, shown.occasion)}
            waitForReady
            faceClassName="rounded-lg"
            footer={(face) => {
              const status = statuses.get(face) ?? NO_STATUS
              return (
                <CardFrameMessages
                  key={statusSignature(status)}
                  status={status}
                  className="mt-2 animate-in fade-in duration-300"
                />
              )
            }}
          >
            {renderFace}
          </FaceTransition>
        )}
      </ElasticScrollArea>
      <CardDetailModal
        isOpen={fieldsOpen}
        onClose={() => onFieldsOpen(false)}
        note={noteDetail ?? null}
        isLoading={noteLoading && noteError === null}
        error={noteError}
        onDeleted={onDeleted}
      />
    </Card>
  )
}
