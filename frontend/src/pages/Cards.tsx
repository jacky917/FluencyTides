/**
 * 卡片瀏覽頁面。
 * Card browsing page.
 *
 * 左：牌組樹；中：所選牌組（含子牌組）的卡片清單，可加 Anki 搜尋語句；右：Anki 渲染好的卡面
 * 與筆記欄位。不經過 task handler，任何筆記類型都能看。
 *
 * 分工：這個頁面只負責版面——依內容區實際寬度（左側選單收合後會變寬，見 `lib/cardLayout.ts`）
 * 排成三欄並排、牌組與清單並排而卡面在下方、或全部疊成一欄，並排的面板之間可以拖曳調整寬度。
 * - 網址中的狀態與卡面跳轉、返回：`hooks/useCardNavigation.ts`
 * - 面板寬度的預設與記憶：`hooks/usePanelWidths.ts`
 * - 三個面板：`components/cards/`
 * 切換版面模式會重新掛載面板，所以面板的狀態（牌組展開、搜尋草稿、筆記欄位視窗）放在這一層。
 * 三欄時頁面剛好填滿視窗、外層不捲動，各欄有自己的捲軸。
 *
 * This page only lays out the panels; navigation, panel widths and the
 * panels themselves live in their own hooks and components.
 */

import { useRef, useState } from 'react'
import { CardListPanel } from '../components/cards/CardListPanel'
import { CardViewPanel } from '../components/cards/CardViewPanel'
import { DeckPanel } from '../components/cards/DeckPanel'
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '../components/ui/resizable'
import { useCardNavigation } from '../hooks/useCardNavigation'
import { useElementWidth } from '../hooks/useElementWidth'
import { usePanelWidths, type PanelKey } from '../hooks/usePanelWidths'
import { DECK_MAX, DECK_MIN, LIST_MIN, VIEWER_MAX, VIEWER_MIN, layoutMode } from '../lib/cardLayout'

/**
 * 卡片瀏覽頁面。Card browsing page.
 * @returns 頁面 JSX。Page JSX.
 */
export default function Cards() {
  const nav = useCardNavigation()
  const panels = usePanelWidths()
  const layoutRef = useRef<HTMLDivElement>(null)
  const mode = layoutMode(useElementWidth(layoutRef))

  // 版面模式改變（例如收合選單、縮放視窗）會重新掛載面板；面板的狀態放在這裡才不會遺失
  const [deckToggled, setDeckToggled] = useState<Record<string, boolean>>({})
  const [searchDraft, setSearchDraft] = useState(nav.search)
  const [fieldsOpen, setFieldsOpen] = useState(false)
  const scrolledCardRef = useRef<number | null>(null)

  const deckPanel = (scrollable: boolean) => (
    <DeckPanel
      scrollable={scrollable}
      toggled={deckToggled}
      onToggled={setDeckToggled}
      selected={nav.deck}
      onSelect={nav.selectDeck}
    />
  )
  const listPanel = (scrollable: boolean) => (
    <CardListPanel
      scrollable={scrollable}
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
  )
  const viewerPanel = (sideBySide: boolean, className?: string) => (
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
      onSide={nav.setSide}
      onBrowse={nav.openNotes}
      onBack={nav.goBack}
      onDeleted={nav.clearCard}
    />
  )
  /** 調整某個面板寬度的分隔線。The separator that resizes one panel. */
  const handleFor = (key: PanelKey) => (
    <ResizableHandle
      withHandle
      className="mx-2"
      title="拖曳調整寬度，雙擊恢復預設"
      disableDoubleClick
      onPointerDown={() => panels.grab(key)}
      onKeyDown={() => panels.grab(key)}
      onDoubleClick={() => panels.reset(key)}
    />
  )
  // 牌組與卡面拖曳後保持像素寬度，清單吃剩下的寬度
  const deckResizable = (
    <ResizablePanel
      id="deck"
      panelRef={panels.deckRef}
      defaultSize={panels.widths.deck}
      minSize={DECK_MIN}
      maxSize={DECK_MAX}
      groupResizeBehavior="preserve-pixel-size"
      onResize={panels.trackWidth('deck')}
    >
      {deckPanel(true)}
    </ResizablePanel>
  )

  // 三欄時剛好填滿視窗（扣掉頂列 3rem 與內容區上下 padding 3rem），外層不捲動
  return (
    <div className={mode === 'three' ? 'flex h-[calc(100svh-6rem)] min-h-[28rem] flex-col gap-6' : 'space-y-6'}>
      <div className="shrink-0">
        <h2 className="text-3xl font-bold tracking-tight">卡片瀏覽</h2>
        <p className="text-muted-foreground mt-2">
          選一個牌組查看其中（含子牌組）的卡片；卡面由 Anki 渲染，與 Anki 中看到的大致相同。
        </p>
      </div>

      <div ref={layoutRef} className={mode === 'three' ? 'min-h-0 flex-1' : undefined}>
        {mode === 'three' ? (
          <ResizablePanelGroup className="h-full" onLayoutChanged={panels.save}>
            {deckResizable}
            {handleFor('deck')}
            <ResizablePanel id="list" minSize={LIST_MIN}>
              {listPanel(true)}
            </ResizablePanel>
            {handleFor('viewer')}
            <ResizablePanel
              id="viewer"
              panelRef={panels.viewerRef}
              defaultSize={panels.widths.viewer}
              minSize={VIEWER_MIN}
              maxSize={VIEWER_MAX}
              groupResizeBehavior="preserve-pixel-size"
              onResize={panels.trackWidth('viewer')}
            >
              {viewerPanel(true)}
            </ResizablePanel>
          </ResizablePanelGroup>
        ) : mode === 'two' ? (
          <div className="space-y-4">
            <ResizablePanelGroup className="h-[min(65svh,36rem)]" onLayoutChanged={panels.save}>
              {deckResizable}
              {handleFor('deck')}
              <ResizablePanel id="list" minSize={LIST_MIN}>
                {listPanel(true)}
              </ResizablePanel>
            </ResizablePanelGroup>
            {viewerPanel(false, 'mx-auto w-full')}
          </div>
        ) : (
          <div className="space-y-4">
            {deckPanel(false)}
            {listPanel(false)}
            {viewerPanel(false)}
          </div>
        )}
      </div>
    </div>
  )
}
