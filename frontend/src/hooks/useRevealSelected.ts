import { useEffect, useRef, type RefObject } from 'react'

/**
 * 面板第一次載入好時，把選取的項目（標了 `aria-current="true"` 的元素）捲到面板中間，回到頁面
 * 時不用再自己找。只捲動這個面板，不會連帶捲動整頁；項目已經看得到時不動。
 * Scrolls the selected item (`aria-current="true"`) to the middle of its
 * panel once the panel has loaded, without scrolling the page.
 * @param containerRef - 自己捲動的面板。The self-scrolling panel.
 * @param ready - 內容是否已載入。Whether the content has loaded.
 * @param enabled - 面板是否自己捲動（不自己捲動時不處理）。Whether the panel scrolls itself.
 *
 * 每次渲染後檢查一次（只是一次 querySelector），直到找到選取的項目為止。
 */
export function useRevealSelected(containerRef: RefObject<HTMLElement | null>, ready: boolean, enabled: boolean): void {
  const revealed = useRef(false)

  useEffect(() => {
    const container = containerRef.current
    if (revealed.current || !ready || !enabled || !container) return
    // 找到選取的項目才算處理過：從側邊欄回來時，網址可能晚一步才恢復成上次的位置
    const item = container.querySelector<HTMLElement>('[aria-current="true"]')
    if (!item) return
    revealed.current = true
    const box = container.getBoundingClientRect()
    const rect = item.getBoundingClientRect()
    // 上方有固定的標題列，靠近上緣也算看不到
    const visible = rect.top >= box.top + box.height * 0.25 && rect.bottom <= box.bottom
    if (!visible) container.scrollTop += rect.top - box.top - box.height / 2 + rect.height / 2
  })
}
