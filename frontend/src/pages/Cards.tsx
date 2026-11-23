/**
 * 卡片頁面（`docs/wip/cards_page_revamp_FEAT_2026-11-23.md`）。
 * Card page.
 *
 * 左：牌組樹；中：所選牌組的卡片清單，上方的牌組條件列（與知識圖譜共用）選卡片類型、筆記類型與搜尋幾層子
 * 牌組，可加 Anki 搜尋語句；右：Anki 渲染好的卡面與筆記欄位。清單標題的「＋」把右欄換成建卡表單（網頁唯一的建卡畫面，建在左欄選的牌組；建卡畫面開著時換牌組不會關掉），
 * 建好後留在表單（清空）可接著建，成功提示上可打開新卡片（`docs/archive/card_list_create_FEAT_2026-10-30.md`）。
 * 不經過 task handler，任何筆記類型都能看。條件列選的卡片類型開放欄位搜尋時（handler 宣告
 * `capabilities.search`）另有欄位搜尋：點欄位、輸入關鍵字，由後端組成 Anki 查詢（`FieldSearchPanel`）。
 *
 * 分工：這個頁面只提供三個面板，版面交給共用的 `SplitLayout`——依內容區實際寬度排成三欄並排、
 * 牌組與清單並排而卡面在下方、或全部疊成一欄（設定見 `lib/pageLayouts.ts` 的 `CARD_LAYOUT`），並排的面板之間可以拖曳
 * 調整寬度。
 * - 網址中的狀態與卡面跳轉、返回：`hooks/useCardNavigation.ts`
 * - 面板寬度的預設與記憶：`hooks/usePanelWidths.ts`
 * - 牌組樹：`components/shared/DeckTreePanel.tsx`；清單、卡面與建卡面板：`components/cards/`
 * 切換版面模式會重新掛載面板，所以面板的狀態（牌組展開、搜尋草稿、筆記欄位視窗）放在這一層；
 * 建卡表單的草稿本來就存在 sessionStorage，重新掛載也還在。
 *
 * This page only provides the panels; the shared SplitLayout arranges them,
 * and navigation and panel widths live in their own hooks.
 */

import { useEffect, useRef, useState } from 'react'
import { CardCreatePanel } from '../components/cards/CardCreatePanel'
import { CardListPanel } from '../components/cards/CardListPanel'
import { CardViewPanel } from '../components/cards/CardViewPanel'
import { StackedDeckPicker } from '../components/cards/StackedDeckPicker'
import { DeckTreePanel } from '../components/shared/DeckTreePanel'
import { PageHeader } from '../components/shared/PageHeader'
import { SplitLayout } from '../components/shared/SplitLayout'
import { useCardNavigation } from '../hooks/useCardNavigation'
import { useLocalStorage } from '../hooks/useStoredState'
import { usePanelWidths } from '../hooks/usePanelWidths'
import { CARD_LAYOUT } from '../lib/pageLayouts'
import { EMPTY_FIELD_SEARCH, restoreFieldSearch, type FieldSearchState } from '../lib/fieldSearch'
import { restoreCardDepth } from '../lib/cardBrowse'

/**
 * 卡片頁面。Card page.
 * @returns 頁面 JSX。Page JSX.
 */
export default function Cards() {
  const nav = useCardNavigation()
  const panels = usePanelWidths(CARD_LAYOUT)

  // 版面模式改變（例如收合選單、縮放視窗）會重新掛載面板；面板的狀態放在這裡才不會遺失
  // 牌組樹的展開狀態也記在 localStorage，下次回來維持原樣
  const [deckToggled, setDeckToggled] = useLocalStorage<Record<string, boolean>>('cards_deck_toggled', {})
  const [searchDraft, setSearchDraft] = useState(nav.search)
  // 欄位搜尋的類型與條件也記在 localStorage，回到頁面時還在
  const [storedFieldSearch, setFieldSearch] = useLocalStorage<FieldSearchState>('cards_field_search', EMPTY_FIELD_SEARCH)
  const fieldSearch = restoreFieldSearch(storedFieldSearch)
  // 條件列的筆記類型與層數（卡片類型就是欄位搜尋的 handler）
  const [storedNoteType, setNoteType] = useLocalStorage<string | null>('cards_note_type', null)
  const noteType = typeof storedNoteType === 'string' ? storedNoteType : null
  const [storedDepth, setDepth] = useLocalStorage<number>('cards_depth', 0)
  const depth = restoreCardDepth(storedDepth)
  const [fieldsOpen, setFieldsOpen] = useState(false)
  // 疊成一欄（手機）時牌組樹收成一列，點開才展開
  const [deckPickerOpen, setDeckPickerOpen] = useState(false)
  const scrolledCardRef = useRef<number | null>(null)
  // 這一輪建卡畫面是否已經捲到面板；退出建卡畫面後重設，下次打開再捲
  const scrolledCreateRef = useRef(false)
  useEffect(() => {
    if (!nav.creating) scrolledCreateRef.current = false
  }, [nav.creating])

  // 疊成一欄時卡面在清單下方：「回到清單」捲回剛才選的那張卡（不在這一頁時捲到清單頂端）
  const backToList = () => {
    const target =
      document.querySelector('[data-card-list] [aria-current="true"]') ?? document.querySelector('[data-card-list-panel]')
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    target?.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'center' })
  }

  return (
    <SplitLayout
      header={
        <PageHeader
          title="卡片"
          description="選一個牌組查看其中的卡片，可以篩卡片類型、指定搜尋幾層子牌組；卡面由 Anki 渲染，與 Anki 中看到的大致相同。"
        />
      }
      panels={panels}
      columns={[
        {
          id: 'deck',
          resizable: 'deck',
          render: (sideBySide) => {
            const tree = (
              <DeckTreePanel
                scrollable={sideBySide}
                toggled={deckToggled}
                onToggled={setDeckToggled}
                selected={nav.deck}
                onSelect={(deck) => {
                  nav.selectDeck(deck)
                  setDeckPickerOpen(false)
                }}
              />
            )
            return sideBySide ? (
              tree
            ) : (
              <StackedDeckPicker deck={nav.deck} open={deckPickerOpen} onOpen={setDeckPickerOpen}>
                {tree}
              </StackedDeckPicker>
            )
          },
        },
        {
          id: 'list',
          render: (sideBySide) => (
            <CardListPanel
              scrollable={sideBySide}
              draft={searchDraft}
              onDraft={setSearchDraft}
              fieldSearch={fieldSearch}
              onFieldSearch={setFieldSearch}
              noteType={noteType}
              onNoteType={setNoteType}
              depth={depth}
              onDepth={setDepth}
              deck={nav.deck}
              onDeck={nav.selectDeck}
              search={nav.search}
              page={nav.page}
              selectedCardId={nav.cardId}
              onSearch={nav.submitSearch}
              onPage={nav.setPage}
              onSelect={nav.selectCard}
              creating={nav.creating}
              onToggleCreate={nav.toggleCreate}
            />
          ),
        },
        {
          id: 'viewer',
          resizable: 'viewer',
          render: (sideBySide, className) =>
            nav.creating ? (
              <CardCreatePanel
                sideBySide={sideBySide}
                className={className}
                scrolledRef={scrolledCreateRef}
                deck={nav.deck}
                onClose={nav.closeCreate}
                onViewCreated={nav.showCreated}
              />
            ) : (
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
                onBackToList={sideBySide ? undefined : backToList}
                onDeleted={nav.clearCard}
              />
            ),
        },
      ]}
    />
  )
}
