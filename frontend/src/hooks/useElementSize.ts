import { useCallback, useLayoutEffect, useState } from 'react'

/** 元素的寬高（px）。An element's size in pixels. */
export interface ElementSize {
  width: number
  height: number
}

/**
 * 量元素目前的寬高，大小改變時更新。用 callback ref 接元素，所以元素被重新掛載（例如版面模式切換）
 * 時會改量新的元素。
 * Measures an element's size and keeps it up to date; the callback ref
 * follows the element across remounts.
 * @returns 要接到元素上的 ref、目前寬高（元素還不存在時為 0）、目前量的元素。The ref, the size and the element.
 */
export function useElementSize(): [(element: HTMLElement | null) => void, ElementSize, HTMLElement | null] {
  const [element, setElement] = useState<HTMLElement | null>(null)
  const [size, setSize] = useState<ElementSize>({ width: 0, height: 0 })
  const ref = useCallback((node: HTMLElement | null) => setElement(node), [])

  useLayoutEffect(() => {
    if (!element) return
    const measure = (width: number, height: number) =>
      setSize((prev) => (prev.width === width && prev.height === height ? prev : { width, height }))
    const box = element.getBoundingClientRect()
    measure(box.width, box.height)
    const observer = new ResizeObserver((entries) => {
      const entry = entries[entries.length - 1]
      if (entry) measure(entry.contentRect.width, entry.contentRect.height)
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [element])

  return [ref, size, element]
}
