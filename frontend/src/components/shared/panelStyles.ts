/**
 * 分欄頁面中各面板共用的樣式。Shared styles of the split-layout panels.
 */

/** 自己捲動的面板，標題列固定在上方。Sticky header of a self-scrolling panel. */
export const STICKY_HEADER = 'sticky top-0 z-10 bg-card rounded-t-lg'

/**
 * 面板自己捲動（並排時填滿高度、自己捲動）時的 class。
 * The class of a panel that scrolls itself.
 * @param scrollable - 是否自己捲動。Whether it scrolls itself.
 * @returns class，或 undefined。The class.
 */
export function panelScrollClass(scrollable: boolean): string | undefined {
  return scrollable ? 'h-full overflow-auto' : undefined
}
