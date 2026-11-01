import { Fragment, useRef, type ReactNode } from 'react'
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '../ui/resizable'
import { useElementWidth } from '../../hooks/useElementWidth'
import type { PanelWidthsState } from '../../hooks/usePanelWidths'
import { splitMode, type SplitMode } from '../../lib/splitLayout'

/** 分欄版面中的一欄。One column of a split layout. */
export interface SplitColumn<K extends string> {
  /** 面板與 React key 用的 ID，同一個版面內不可重複。Panel/React key; unique within the layout. */
  id: string
  /**
   * 畫出這一欄。The column's content.
   * @param sideBySide - 是否與其他欄並排（並排時面板填滿高度、自己捲動）。Whether side by side.
   * @param className - 只有「部分並排」時放在下方的最後一欄會收到（置中、填滿寬度），其餘為
   *   undefined。Only the last column below the others in the partial mode gets one.
   */
  render: (sideBySide: boolean, className?: string) => ReactNode
  /**
   * 面板規格（`panels.layout.specs`）的鍵；有給時這一欄固定像素寬、可拖曳，沒給就吃剩下的寬度，
   * 最窄寬度取版面設定的 `fluidMin[id]`。
   * A key of the layout's panel specs: the column is fixed-width and
   * resizable. Otherwise it takes the remaining width, at least `fluidMin[id]`.
   */
  resizable?: K
}

/** {@link SplitLayout} 的 props。Props of {@link SplitLayout}. */
export interface SplitLayoutProps<K extends string> {
  /** 頁面頂部的標題區。The page header. */
  header: ReactNode
  columns: SplitColumn<K>[]
  /** `usePanelWidths(layout)` 的回傳值；斷點與面板規格都從它的 `layout` 取。From `usePanelWidths`. */
  panels: PanelWidthsState<K>
  /**
   * 暫時固定目前的版面模式，不隨寬度切換。某一欄進入全螢幕時要用：全螢幕會改變視窗寬度，切換模式
   * 會重新掛載各欄，被放大的元素一被移除，瀏覽器就會退出全螢幕。
   * Keeps the current mode while true, e.g. while a column is fullscreen.
   */
  lockMode?: boolean
}

/**
 * 分欄頁面的版面：依內容區實際寬度（左側選單收合後會變寬）把各欄排成全部並排、前面幾欄並排而
 * 最後一欄在下方、或全部疊成一欄；並排的欄之間可以拖曳調整寬度。全部並排時頁面剛好填滿視窗、外層
 * 不捲動，各欄有自己的捲軸。
 *
 * 切換版面模式會重新掛載各欄，所以各欄的狀態（例如牌組展開、搜尋草稿）要放在呼叫端。
 *
 * The layout of a split page: columns side by side, all but the last side by
 * side, or stacked, depending on the content width; side-by-side columns are
 * separated by draggable handles.
 *
 * @returns 版面 JSX。Layout JSX.
 */
export function SplitLayout<K extends string>({
  header,
  columns,
  panels,
  lockMode = false,
}: SplitLayoutProps<K>) {
  const { specs, fluidMin, breakpoints } = panels.layout
  const layoutRef = useRef<HTMLDivElement>(null)
  const measured = splitMode(useElementWidth(layoutRef), breakpoints)
  const lockedMode = useRef<SplitMode>(measured)
  if (!lockMode) lockedMode.current = measured
  const mode = lockedMode.current

  /** 調整某個面板寬度的分隔線。The separator that resizes one panel. */
  const handleFor = (key: K) => (
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
  // 可拖曳的欄拖曳後保持像素寬度，其他欄吃剩下的寬度
  const panelFor = (column: SplitColumn<K>) =>
    column.resizable !== undefined ? (
      <ResizablePanel
        id={column.id}
        panelRef={panels.refFor(column.resizable)}
        defaultSize={panels.widths[column.resizable]}
        minSize={specs[column.resizable].min}
        maxSize={specs[column.resizable].max}
        groupResizeBehavior="preserve-pixel-size"
        onResize={panels.trackWidth(column.resizable)}
      >
        {column.render(true)}
      </ResizablePanel>
    ) : (
      <ResizablePanel id={column.id} minSize={fluidMin[column.id]}>
        {column.render(true)}
      </ResizablePanel>
    )
  /**
   * 一組並排的欄；兩欄之間的分隔線調整其中可拖曳的那一欄（左邊優先），兩欄都不可拖曳時沒有分隔線。
   * Side-by-side columns; a separator resizes the resizable one of its two
   * neighbours (left first), and there is none if neither is resizable.
   */
  const group = (items: SplitColumn<K>[], className: string) => (
    <ResizablePanelGroup className={className} onLayoutChanged={panels.save}>
      {items.map((column, index) => {
        const previous = items[index - 1]
        const handleKey = previous ? (previous.resizable ?? column.resizable) : undefined
        return (
          <Fragment key={column.id}>
            {handleKey !== undefined && handleFor(handleKey)}
            {panelFor(column)}
          </Fragment>
        )
      })}
    </ResizablePanelGroup>
  )
  const last = columns[columns.length - 1]

  // 全部並排時剛好填滿視窗（扣掉 App.tsx 的頂列 h-12 與內容區 md:p-6 的上下 padding，共 6rem；
  // 窄於 md 時 padding 只有 p-4，底部會多出 1rem），外層不捲動
  return (
    <div className={mode === 'full' ? 'flex h-[calc(100svh-6rem)] min-h-[28rem] flex-col gap-6' : 'space-y-6'}>
      {header}
      <div ref={layoutRef} className={mode === 'full' ? 'min-h-0 flex-1' : undefined}>
        {mode === 'full' ? (
          group(columns, 'h-full')
        ) : mode === 'partial' && last ? (
          <div className="space-y-4">
            {group(columns.slice(0, -1), 'h-[min(65svh,36rem)]')}
            {last.render(false, 'mx-auto w-full')}
          </div>
        ) : (
          <div className="space-y-4">
            {columns.map((column) => (
              <Fragment key={column.id}>{column.render(false)}</Fragment>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
