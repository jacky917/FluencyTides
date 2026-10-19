import { useEffect, useRef, type RefObject } from 'react'
import { createElasticOverscroll, type ElasticOverscroll } from '../lib/elasticOverscroll'

/**
 * 在捲動容器上加上彈性回饋（見 `lib/elasticOverscroll.ts`）：接上容器自己的滾輪與觸控事件，
 * 並回傳控制器，讓 iframe 內轉送出來的手勢也能用（iframe 內的事件不會傳到網頁）。
 * Adds rubber-band feedback to a scroll container and returns the controller
 * for gestures forwarded from iframes.
 * @param scrollerRef - 捲動容器。The scroll container.
 * @param contentRef - 要位移的內容。The element to displace.
 * @param enabled - 是否啟用（容器真的會自己捲動時）。Whether it is on.
 * @returns 控制器的 ref；未啟用時為 null。A ref to the controller.
 */
export function useElasticOverscroll(
  scrollerRef: RefObject<HTMLElement | null>,
  contentRef: RefObject<HTMLElement | null>,
  enabled: boolean,
): RefObject<ElasticOverscroll | null> {
  const controllerRef = useRef<ElasticOverscroll | null>(null)

  useEffect(() => {
    const scroller = scrollerRef.current
    const content = contentRef.current
    if (!enabled || !scroller || !content) return
    const controller = createElasticOverscroll(scroller, content)
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
  }, [scrollerRef, contentRef, enabled])

  return controllerRef
}
