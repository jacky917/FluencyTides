import { useEffect, useRef, type MutableRefObject } from 'react'
import { PlusCircle, X } from 'lucide-react'
import { useDeckStatus } from '../../hooks/useDeckStatus'
import { cn } from '../../lib/utils'
import { CreatePanelBody } from '../generate/CreatePanelBody'
import { Panel } from '../shared/Panel'
import { STACKED_VIEWER_MAX_WIDTH } from '../shared/panelStyles'
import { Button } from '../ui/button'

/** {@link CardCreatePanel} 的 props。Props of {@link CardCreatePanel}. */
interface CardCreatePanelProps {
  /** 與清單並排：填滿高度、自己捲動。Sits beside the list and scrolls itself. */
  sideBySide: boolean
  className?: string
  /**
   * 這一輪建卡畫面是否已經捲動過（由呼叫端保存，退出建卡畫面時重設）：版面模式改變而重新掛載時
   * 不再捲一次。Whether this create screen was already scrolled to.
   */
  scrolledRef: MutableRefObject<boolean>
  /** 左欄選的牌組，新卡建在這裡；null 為全部牌組（不能建立）。The deck to create in. */
  deck: string | null
  /** 關閉建卡畫面。Closes the create screen. */
  onClose: () => void
  /**
   * 成功提示上的「查看新卡片」：切到新筆記的卡片（見 `useCardNavigation` 的 `showCreated`）。
   * Opens the created note's card.
   */
  onViewCreated: (noteIds: number[]) => void
}

/**
 * 卡片瀏覽頁右欄的建卡面板（`docs/wip/card_list_create_FEAT_2026-10-30.md` D2、D5）：外框、關閉按鈕
 * 與捲動，內容是與 Card Generator 共用的 `CreatePanelBody`。卡片類型與草稿存在瀏覽器儲存空間，
 * 切換版面而重新掛載時都還在。
 *
 * 建卡成功後留在這裡、表單清空回預設值，可以接著建下一張；要看新卡片時按成功提示上的「查看新卡片」。
 *
 * The create panel in the viewer column of the card browsing page; its body
 * is shared with the Card Generator page.
 * @returns 面板 JSX。Panel JSX.
 */
export function CardCreatePanel({ sideBySide, className, scrolledRef, deck, onClose, onViewCreated }: CardCreatePanelProps) {
  const panelRef = useRef<HTMLDivElement>(null)
  const deckStatus = useDeckStatus(deck)

  // 不並排時面板在清單下方：打開建卡畫面時捲過去；版面模式改變而重新掛載時不再捲
  useEffect(() => {
    if (scrolledRef.current) return
    scrolledRef.current = true
    if (!sideBySide) panelRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    // 只在打開建卡畫面時捲動
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <Panel
      ref={panelRef}
      scrollable={sideBySide}
      className={cn(!sideBySide && STACKED_VIEWER_MAX_WIDTH, className)}
      title={
        <>
          <PlusCircle className="h-4 w-4" />
          建立卡片
        </>
      }
      actions={
        <Button
          type="button"
          size="icon"
          variant="ghost"
          className="h-8 w-8"
          aria-label="關閉建卡表單"
          title="關閉建卡表單"
          onClick={onClose}
        >
          <X className="h-4 w-4" />
        </Button>
      }
      contentClassName="space-y-6"
    >
      <CreatePanelBody
        selectedDeck={deck}
        deckStatus={deckStatus}
        onViewCreated={(result) => onViewCreated(result.note_ids)}
      />
    </Panel>
  )
}
