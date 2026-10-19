import { useCallback, useEffect, useRef, type RefObject } from 'react'
import type { ScrollGesture } from '../lib/ankiFrame'
import { createElasticOverscroll, type ElasticOptions, type ElasticOverscroll } from '../lib/elasticOverscroll'

/**
 * 在捲動容器上加上彈性回饋（見 `lib/elasticOverscroll.ts`）：接上容器自己的滾輪與觸控事件，
 * 並回傳處理轉送手勢的函式——iframe 內的事件不會傳到網頁，要由 iframe 內的腳本轉送過來。
 * 一般情況直接用 `components/ElasticScrollArea`；需要自己排版時才用這個 hook。
 *
 * Adds rubber-band feedback to a scroll container and returns a handler for
 * gestures forwarded from iframes. Prefer `ElasticScrollArea`.
 *
 * @param scrollerRef - 捲動容器。The scroll container.
 * @param contentRef - 要位移的內容。The element to displace.
 * @param options - 是否啟用、位移上限與位移回呼。Enabled flag, limit and callback.
 * @returns 處理轉送手勢的函式（未啟用時不做事）。A handler for forwarded gestures.
 */
export function useElasticOverscroll(
  scrollerRef: RefObject<HTMLElement | null>,
  contentRef: RefObject<HTMLElement | null>,
  { enabled, maxPull, onPull }: ElasticOptions & { enabled: boolean },
): (gesture: ScrollGesture) => void {
  const controllerRef = useRef<ElasticOverscroll | null>(null)
  // 用 ref 取最新的回呼：回呼換了不必重建控制器
  const onPullRef = useRef(onPull)
  useEffect(() => {
    onPullRef.current = onPull
  }, [onPull])

  useEffect(() => {
    const scroller = scrollerRef.current
    const content = contentRef.current
    if (!enabled || !scroller || !content) return
    const controller = createElasticOverscroll(scroller, content, {
      maxPull,
      onPull: (displacement, animated) => onPullRef.current?.(displacement, animated),
    })
    controllerRef.current = controller

    const onWheel = (event: WheelEvent) => controller.wheel(event.deltaY)
    const onTouchStart = (event: TouchEvent) => controller.touch('start', event.touches[0]?.screenY)
    const onTouchMove = (event: TouchEvent) => {
      // 拉動中取消原生捲動，否則瀏覽器會同時捲動或觸發整頁的回彈
      if (controller.touch('move', event.touches[0]?.screenY) && event.cancelable) event.preventDefault()
    }
    const onTouchEnd = () => controller.touch('end')
    scroller.addEventListener('wheel', onWheel, { passive: true })
    scroller.addEventListener('touchstart', onTouchStart, { passive: true })
    scroller.addEventListener('touchmove', onTouchMove, { passive: false })
    scroller.addEventListener('touchend', onTouchEnd)
    scroller.addEventListener('touchcancel', onTouchEnd)
    return () => {
      scroller.removeEventListener('wheel', onWheel)
      scroller.removeEventListener('touchstart', onTouchStart)
      scroller.removeEventListener('touchmove', onTouchMove)
      scroller.removeEventListener('touchend', onTouchEnd)
      scroller.removeEventListener('touchcancel', onTouchEnd)
      controller.dispose()
      controllerRef.current = null
    }
  }, [scrollerRef, contentRef, enabled, maxPull])

  return useCallback((gesture: ScrollGesture) => {
    const controller = controllerRef.current
    if (!controller) return
    if (gesture.kind === 'wheel') controller.wheel(gesture.deltaY)
    else controller.touch(gesture.phase, gesture.y)
  }, [])
}
