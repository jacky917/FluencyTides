/**
 * 牌組條件列（`docs/wip/cards_page_revamp_FEAT_2026-11-23.md` D2）：卡片類型、筆記類型、層數三個選單，加上
 * 牌組路徑的麵包屑。知識圖譜的工具列與卡片頁的清單共用。
 *
 * 分成兩個元件，讓呼叫端各自排版：`DeckScopeSelects` 回傳一組選單（Fragment，直接排進呼叫端的 flex 列），
 * `DeckCrumbs` 是麵包屑。選項由呼叫端給（知識圖譜只列開放圖譜的卡片類型、層數只在樹狀圖譜出現；卡片頁多一個
 * 「全部卡片類型」、層數一直都有）。層數的選項與名稱兩邊一樣（`DEPTH_OPTIONS`），不由呼叫端給。
 *
 * The deck scope bar shared by the knowledge graph toolbar and the card list: card type, note type and depth
 * selects, plus the deck breadcrumb.
 */

import type { ReactNode } from 'react'
import { deckCrumbs } from '@/features/graph-views/engines/topic-tree'
import { DEPTH_OPTIONS, type ScopeOption } from '../../lib/deckScope'
import { cn } from '../../lib/utils'

/** 條件列選單的外觀（觸控時加高、16px 字，iOS 才不會放大整頁）。The select style. */
export const SCOPE_SELECT_CLASS =
  'h-9 min-w-0 max-w-full rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring pointer-coarse:h-10 pointer-coarse:text-base'

/** {@link DeckScopeSelects} 的 props。Props of {@link DeckScopeSelects}. */
export interface DeckScopeSelectsProps {
  /** 卡片類型的選項；空的時不顯示這個選單。The card type options; hidden when empty. */
  cardTypes: ScopeOption[]
  cardType: string
  onCardType: (value: string) => void
  /** 目前卡片類型的筆記類型；兩種以上才顯示選單。Note types of the card type; shown when two or more. */
  noteTypes: string[]
  /** 選的筆記類型；null 為全部。The chosen note type, or null for all. */
  noteType: string | null
  onNoteType: (value: string | null) => void
  /** 插在筆記類型與層數之間的額外選單（知識圖譜的名稱欄位）。Extra selects after the note type. */
  children?: ReactNode
  /** 層數；undefined 時不顯示層數選單。The depth; the select is hidden when undefined. */
  depth?: number
  onDepth?: (depth: number) => void
}

/**
 * 卡片類型、筆記類型、層數三個選單（Fragment，排進呼叫端的 flex 列）。
 * The card type, note type and depth selects, as a fragment.
 * @param props - 見 {@link DeckScopeSelectsProps}。See {@link DeckScopeSelectsProps}.
 * @returns 選單 JSX。The selects.
 */
export function DeckScopeSelects({
  cardTypes,
  cardType,
  onCardType,
  noteTypes,
  noteType,
  onNoteType,
  children,
  depth,
  onDepth,
}: DeckScopeSelectsProps) {
  return (
    <>
      {cardTypes.length > 0 && (
        <select
          className={SCOPE_SELECT_CLASS}
          aria-label="卡片類型"
          value={cardType}
          onChange={(e) => onCardType(e.target.value)}
        >
          {cardTypes.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      )}
      {noteTypes.length > 1 && (
        <select
          className={SCOPE_SELECT_CLASS}
          aria-label="筆記類型"
          value={noteType ?? ''}
          onChange={(e) => onNoteType(e.target.value || null)}
        >
          <option value="">全部筆記類型</option>
          {noteTypes.map((type) => (
            <option key={type} value={type}>
              {type}
            </option>
          ))}
        </select>
      )}
      {children}
      {depth !== undefined && onDepth && (
        <select
          className={SCOPE_SELECT_CLASS}
          aria-label="展開層數"
          value={depth}
          onChange={(e) => onDepth(Number(e.target.value))}
        >
          {DEPTH_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      )}
    </>
  )
}

/** {@link DeckCrumbs} 的 props。Props of {@link DeckCrumbs}. */
export interface DeckCrumbsProps {
  /** 牌組完整名稱；null 為全部牌組。The deck, or null for all decks. */
  deck: string | null
  /** 點上層的牌組（null＝全部牌組）。Called with the deck to go to. */
  onDeck: (deck: string | null) => void
  /** 牌組已不存在（在最後加上提示）。Whether the deck no longer exists. */
  missing?: boolean
  className?: string
}

/**
 * 牌組路徑的麵包屑：每一段都能點（最後一段是目前的牌組），長名稱會換行。
 * The deck breadcrumb; every level but the last can be clicked.
 * @param props - 見 {@link DeckCrumbsProps}。See {@link DeckCrumbsProps}.
 * @returns 麵包屑 JSX。The breadcrumb.
 */
export function DeckCrumbs({ deck, onDeck, missing = false, className }: DeckCrumbsProps) {
  const crumbs = deckCrumbs(deck)
  return (
    <nav aria-label="牌組路徑" className={cn('flex min-w-0 flex-wrap items-center gap-1 text-sm', className)}>
      {crumbs.map((crumb, index) => (
        <span key={crumb.deck ?? ''} className="flex min-w-0 items-center gap-1">
          {index > 0 && <span className="text-muted-foreground">›</span>}
          {index === crumbs.length - 1 ? (
            <span className="font-medium break-all" aria-current="page">
              {crumb.label}
            </span>
          ) : (
            <button
              type="button"
              className="break-all text-left text-muted-foreground hover:text-foreground hover:underline pointer-coarse:py-1"
              onClick={() => onDeck(crumb.deck)}
            >
              {crumb.label}
            </button>
          )}
        </span>
      ))}
      {missing && <span className="text-destructive">（已不存在，請重新選擇）</span>}
    </nav>
  )
}
