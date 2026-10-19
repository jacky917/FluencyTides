import { useLayoutEffect, useState, type RefObject } from 'react'

/**
 * 元素目前的寬度（px），大小改變時更新；第一次在繪製前就量好，不會先閃一次錯的版面。
 * The element's current width, measured before the first paint and kept up to date.
 * @param ref - 要量的元素。The element to measure.
 * @returns 寬度；元素還不存在時為 0。The width, or 0 before the element exists.
 */
export function useElementWidth(ref: RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(0)

  useLayoutEffect(() => {
    const element = ref.current
    if (!element) return
    setWidth(element.getBoundingClientRect().width)
    const observer = new ResizeObserver((entries) => {
      const entry = entries[entries.length - 1]
      if (entry) setWidth(entry.contentRect.width)
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [ref])

  return width
}
