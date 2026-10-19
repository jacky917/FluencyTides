/**
 * 捲到頂或底之後繼續拉時的彈性回饋（iOS 的橡皮筋效果）：內容跟著拉動但越拉越緊，放開後彈回。
 * 只處理「已經捲到邊界」的那一段；一般捲動仍交給瀏覽器。與 React 無關，方便測試。
 *
 * Rubber-band feedback when pulling past the top or bottom of a scroll
 * container: the content follows with increasing resistance and springs
 * back on release. Normal scrolling stays native. React-free.
 */

/** 放開後彈回的動畫。The spring-back transition. */
export const SPRING_BACK = 'transform 520ms cubic-bezier(0.18, 0.89, 0.32, 1.12)'
/** 滾輪停下多久算放開（滾輪沒有「放開」事件）。Idle time that ends a wheel pull. */
export const WHEEL_RELEASE_MS = 140
/** 預設的位移上限（px，上下都是）。Default displacement limit, both ways. */
export const DEFAULT_MAX_PULL = 150
/** 阻力係數（與 iOS 相同）。Resistance constant, as on iOS. */
const RUBBER_BAND_CONSTANT = 0.55

/**
 * 橡皮筋公式：拉動距離換成內容實際位移，越拉越接近 `dimension` 但永遠到不了。
 * The rubber-band curve: displacement approaches `dimension` but never reaches it.
 * @param distance - 手指或滾輪累積的拉動距離（正為往下拉）。Raw pull distance.
 * @param dimension - 位移上限（通常取容器高度的一部分）。The asymptotic limit.
 * @returns 內容位移。The displacement.
 */
export function rubberBand(distance: number, dimension: number): number {
  if (distance === 0 || dimension <= 0) return 0
  const magnitude = (1 - 1 / ((Math.abs(distance) * RUBBER_BAND_CONSTANT) / dimension + 1)) * dimension
  return Math.sign(distance) * magnitude
}

/** 捲動容器的位置。The scroll position of a container. */
export interface ScrollEdges {
  atTop: boolean
  atBottom: boolean
}

/**
 * 容器是否在頂或底（差 1px 內算到了，避免小數點誤差）。
 * Whether the container is at its top or bottom edge.
 * @param el - 捲動容器。The scroll container.
 * @returns 邊界狀態。Edge state.
 */
export function scrollEdges(el: Pick<HTMLElement, 'scrollTop' | 'scrollHeight' | 'clientHeight'>): ScrollEdges {
  return { atTop: el.scrollTop <= 0, atBottom: el.scrollTop + el.clientHeight >= el.scrollHeight - 1 }
}

/**
 * 依目前的拉動量與這次的移動量算出新的拉動量：只在已到邊界、往外拉時累積；往回推時先抵銷
 * 拉動量，抵到 0 為止（之後交給一般捲動）。
 * The next pull amount: accumulates only when pushing past an edge; moving
 * back first cancels the pull down to zero.
 * @param pull - 目前的拉動量（正為頂端往下拉，負為底端往上拉）。Current pull.
 * @param delta - 這次內容要移動的量（正為往下拉）。Movement this step.
 * @param edges - 容器邊界狀態。Edge state.
 * @returns 新的拉動量。The new pull.
 */
export function nextPull(pull: number, delta: number, edges: ScrollEdges): number {
  if (pull > 0) return Math.max(0, pull + delta)
  if (pull < 0) return Math.min(0, pull + delta)
  if (delta > 0 && edges.atTop) return delta
  if (delta < 0 && edges.atBottom) return delta
  return 0
}

/** 彈性效果的控制器。Controller of the effect. */
export interface ElasticOverscroll {
  /** 滾輪（`deltaY` 正為往下捲）。A wheel step; positive scrolls down. */
  wheel: (deltaY: number) => void
  /** 觸控（`y` 為手指的畫面座標）。A touch event at screen coordinate `y`. */
  touch: (phase: 'start' | 'move' | 'end', y?: number) => boolean
  /** 停止並復原。Stops and resets. */
  dispose: () => void
}

/** {@link createElasticOverscroll} 的選項。Options of the effect. */
export interface ElasticOptions {
  /** 位移上限（px，上下都是；橡皮筋曲線的漸近值，永遠到不了）。Displacement limit. */
  maxPull?: number
  /**
   * 位移改變時（正為頂端往下拉、負為底端往上拉，0 為放開）；`animated` 為正在彈回。
   * Called with each displacement; `animated` while springing back.
   */
  onPull?: (displacement: number, animated: boolean) => void
}

/**
 * 建立彈性效果：拉動時位移 `content`，依 `scroller` 的捲動位置判斷是否到邊界。
 * Creates the effect, moving `content` based on `scroller`'s edges.
 * @param scroller - 捲動容器。The scroll container.
 * @param content - 要位移的內容。The element to displace.
 * @param options - 上限與回呼。Limit and callback.
 * @returns 控制器。The controller.
 */
export function createElasticOverscroll(
  scroller: HTMLElement,
  content: HTMLElement,
  { maxPull = DEFAULT_MAX_PULL, onPull }: ElasticOptions = {},
): ElasticOverscroll {
  let pull = 0
  let lastY: number | null = null
  let releaseTimer: number | undefined

  const apply = (animated: boolean) => {
    const displacement = rubberBand(pull, maxPull)
    content.style.transition = animated ? SPRING_BACK : 'none'
    content.style.transform = pull === 0 ? '' : `translate3d(0, ${displacement.toFixed(2)}px, 0)`
    onPull?.(displacement, animated)
  }
  // 拉動中（含往回推、還沒抵銷完）把捲動位置固定在邊界：滾輪與 iframe 轉送的觸控無法取消
  // 原生捲動，不固定的話內容會同時被捲動又被位移，移動兩倍的距離
  const pinToEdge = () => {
    if (pull > 0) scroller.scrollTop = 0
    else if (pull < 0) scroller.scrollTop = scroller.scrollHeight - scroller.clientHeight
  }
  const release = () => {
    window.clearTimeout(releaseTimer)
    if (pull === 0) return
    pull = 0
    apply(true)
  }

  return {
    wheel(deltaY) {
      const next = nextPull(pull, -deltaY, scrollEdges(scroller))
      if (next === pull && next === 0) return
      pull = next
      apply(false)
      pinToEdge()
      window.clearTimeout(releaseTimer)
      releaseTimer = window.setTimeout(release, WHEEL_RELEASE_MS)
    },
    touch(phase, y) {
      if (phase === 'start') {
        lastY = y ?? null
        return false
      }
      if (phase === 'end' || y === undefined || lastY === null) {
        lastY = null
        release()
        return false
      }
      const delta = y - lastY
      lastY = y
      const next = nextPull(pull, delta, scrollEdges(scroller))
      const pulling = next !== 0
      if (pulling || pull !== 0) {
        pull = next
        apply(false)
        pinToEdge()
      }
      // 拉動中：呼叫端應取消原生捲動
      return pulling
    },
    dispose() {
      window.clearTimeout(releaseTimer)
      pull = 0
      content.style.transition = ''
      content.style.transform = ''
      onPull?.(0, false)
    },
  }
}
