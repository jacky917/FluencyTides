import type { ReactNode } from 'react'
import { ChevronDown, FolderTree } from 'lucide-react'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '../ui/collapsible'

/** {@link StackedDeckPicker} 的 props。Props of {@link StackedDeckPicker}. */
interface StackedDeckPickerProps {
  /** 選取的牌組；null 為全部牌組。The selected deck, or null for all. */
  deck: string | null
  /** 是否展開（由呼叫端保存：版面模式改變會重新掛載）。Whether it is open. */
  open: boolean
  onOpen: (open: boolean) => void
  /** 展開後顯示的牌組樹。The deck tree shown when open. */
  children: ReactNode
}

/**
 * 疊成一欄（手機）時的牌組選擇（`docs/wip/cards_page_revamp_FEAT_2026-11-23.md` D7）：平常收成一列，只顯示
 * 目前的牌組；點開才出現牌組樹（有展開動畫），選了牌組由呼叫端收起。這樣卡片清單在第一個畫面就看得到。
 * The deck picker of the stacked layout: one row showing the deck, opening into the deck tree.
 * @returns 牌組選擇 JSX。The picker.
 */
export function StackedDeckPicker({ deck, open, onOpen, children }: StackedDeckPickerProps) {
  return (
    <Collapsible open={open} onOpenChange={onOpen}>
      <CollapsibleTrigger className="group flex w-full items-center gap-2 rounded-xl border bg-card px-4 py-3 text-left text-card-foreground shadow-sm transition-colors hover:bg-accent">
        <FolderTree className="h-4 w-4 shrink-0 text-muted-foreground" />
        <span className="shrink-0 text-sm text-muted-foreground">牌組</span>
        <span className="min-w-0 flex-1 truncate text-sm font-medium" title={deck ?? undefined}>
          {deck ?? '全部牌組'}
        </span>
        <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground transition-transform group-data-[state=open]:rotate-180 motion-reduce:transition-none" />
      </CollapsibleTrigger>
      {/* 內距讓牌組樹的外框與陰影不被收合動畫的 overflow 裁掉 */}
      <CollapsibleContent className="motion-reduce:animate-none">
        <div className="p-0.5 pt-2">{children}</div>
      </CollapsibleContent>
    </Collapsible>
  )
}
