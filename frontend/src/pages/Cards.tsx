/**
 * 卡片瀏覽頁面。
 * Card browsing page.
 *
 * 左：牌組樹；中：所選牌組（含子牌組）的卡片清單，可加 Anki 搜尋語句；右：Anki 渲染好的卡面
 * 與筆記欄位。不經過 task handler，任何筆記類型都能看。
 *
 * 分工：這個頁面只提供三個面板，版面交給共用的 `SplitLayout`——依內容區實際寬度排成三欄並排、
 * 牌組與清單並排而卡面在下方、或全部疊成一欄（設定見 `lib/cardLayout.ts` 的 `CARD_LAYOUT`），並排的面板之間可以拖曳
 * 調整寬度。
 * - 網址中的狀態與卡面跳轉、返回：`hooks/useCardNavigation.ts`
 * - 面板寬度的預設與記憶：`hooks/usePanelWidths.ts`
 * - 牌組樹：`components/shared/DeckTreePanel.tsx`；清單與卡面：`components/cards/`
 * 切換版面模式會重新掛載面板，所以面板的狀態（牌組展開、搜尋草稿、筆記欄位視窗）放在這一層。
 *
 * This page only provides the panels; the shared SplitLayout arranges them,
 * and navigation and panel widths live in their own hooks.
 */

import { useRef, useState } from 'react'
import { CardListPanel } from '../components/cards/CardListPanel'
import { CardViewPanel } from '../components/cards/CardViewPanel'
import { DeckTreePanel } from '../components/shared/DeckTreePanel'
import { PageHeader } from '../components/shared/PageHeader'
import { SplitLayout } from '../components/shared/SplitLayout'
import { useCardNavigation } from '../hooks/useCardNavigation'
import { useLocalStorage } from '../hooks/useLocalStorage'
import { usePanelWidths } from '../hooks/usePanelWidths'
import { CARD_LAYOUT, LIST_MIN } from '../lib/cardLayout'

/**
 * 卡片瀏覽頁面。Card browsing page.
 * @returns 頁面 JSX。Page JSX.
 */
export default function Cards() {
  const nav = useCardNavigation()
  const panels = usePanelWidths(CARD_LAYOUT)

  // 版面模式改變（例如收合選單、縮放視窗）會重新掛載面板；面板的狀態放在這裡才不會遺失
  // 牌組樹的展開狀態也記在 localStorage，下次回來維持原樣
  const [deckToggled, setDeckToggled] = useLocalStorage<Record<string, boolean>>('cards_deck_toggled', {})
  const [searchDraft, setSearchDraft] = useState(nav.search)
  const [fieldsOpen, setFieldsOpen] = useState(false)
  const scrolledCardRef = useRef<number | null>(null)

  return (
    <SplitLayout
      header={
        <PageHeader
          title="卡片瀏覽"
          description="選一個牌組查看其中（含子牌組）的卡片；卡面由 Anki 渲染，與 Anki 中看到的大致相同。"
        />
      }
      panels={panels}
      columns={[
        {
          id: 'deck',
          resizable: 'deck',
          render: (sideBySide) => (
            <DeckTreePanel
              scrollable={sideBySide}
              toggled={deckToggled}
              onToggled={setDeckToggled}
              selected={nav.deck}
              onSelect={nav.selectDeck}
            />
          ),
        },
        {
          id: 'list',
          minWidth: LIST_MIN,
          render: (sideBySide) => (
            <CardListPanel
              scrollable={sideBySide}
              draft={searchDraft}
              onDraft={setSearchDraft}
              deck={nav.deck}
              search={nav.search}
              page={nav.page}
              selectedCardId={nav.cardId}
              onSearch={nav.submitSearch}
              onPage={nav.setPage}
              onSelect={nav.selectCard}
            />
          ),
        },
        {
          id: 'viewer',
          resizable: 'viewer',
          render: (sideBySide, className) => (
            <CardViewPanel
              sideBySide={sideBySide}
              className={className}
              fieldsOpen={fieldsOpen}
              onFieldsOpen={setFieldsOpen}
              scrolledCardRef={scrolledCardRef}
              cardId={nav.cardId}
              deck={nav.deck}
              side={nav.side}
              trailLength={nav.trail.length}
              arrivedBack={nav.arrivedBack}
              onSide={nav.setSide}
              onBrowse={nav.openNotes}
              onBack={nav.goBack}
              onDeleted={nav.clearCard}
            />
          ),
        },
      ]}
    />
  )
}
