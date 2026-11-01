import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type HTMLAttributes,
  type ReactNode,
} from 'react'
import { cn } from '../lib/utils'
import { faceMarkup, faceTiming, faceTransitionOption, type FacePreference, type FaceRole } from '../lib/faceTransitions'
import { initialFaceState, reduceFace, type FaceEvent, type FaceState } from '../lib/faceTransitionMachine'
import './FaceTransition.css'

/** 給每一面內容的控制。Controls handed to each face. */
export interface FaceControls {
  /**
   * 內容排版好了（例如卡面回報了高度）；`waitForReady` 時，新的一面要等這個才開始播放。
   * The content is laid out; with `waitForReady` the switch waits for it.
   */
  onReady: () => void
}

/** 新的一面回報排版好之後再等這麼久沒有變化才開始（圖片等陸續載入）。Settle time after a ready report. */
const READY_SETTLE_MS = 120
/** 最多等新的一面這麼久，之後不管有沒有回報都開始。Max wait for the incoming face. */
const READY_TIMEOUT_MS = 900

/**
 * 切換內容（卡片的正反面，或換到另一張卡片）時播放動畫。
 * Animates switching between contents (card faces, or cards).
 *
 * 分工：狀態怎麼轉移在 `lib/faceTransitionMachine.ts`，播哪一段、多久在
 * `lib/faceTransitions.ts`，長什麼樣子在 `FaceTransition.css`；這個元件只處理 DOM 的部分——
 * 量高度、等新的一面排版好、判斷動畫播完，以及外框高度的過渡。新的一面的元素以它的值為 key，
 * 準備時渲染的那一個會一路沿用，不會重新掛載。
 *
 * The state machine, the catalogue and the visuals live elsewhere; this
 * component measures heights, waits for readiness, detects the end and
 * tweens the wrapper height. Faces are keyed by value, so the incoming one
 * is never remounted.
 *
 * @param props.value - 要顯示的那一面。The face to show.
 * @param props.resetKey - 換了就不播動畫，直接顯示新的值（內容整個換掉、不該有過渡時用）。
 *   Changing it skips the animation.
 * @param props.direction - 1 為往前（例如翻到背面、換到新的卡片），-1 為往回（翻回正面、回到上一張）。
 *   Direction of the switch.
 * @param props.preference - 下一次切換要播的動畫與速度；切換開始時記下，播放中改了不影響這一次
 *   （改成無動畫時立即切到結果）。Animation and speed of the next switch, fixed when it starts.
 * @param props.waitForReady - 新的一面要等 `onReady` 才播放。Wait for the incoming face's `onReady`.
 * @param props.faceClassName - 每一面外框的 class（例如圓角，讓陰影貼合卡面）。Class of each face wrapper.
 * @param props.children - 依某一面畫出內容。Renders a face.
 * @param props.footer - 畫在外框下方的內容（例如卡面的提示），隨外框高度的過渡一起移動；參數是
 *   正在顯示的那一面，播放中為正在進來的那一面。Content below the wrapper, moving with its height;
 *   receives the face on screen, or the incoming one while animating.
 * @returns 包著內容的外框（與下方的內容）。The animated wrapper and the footer.
 */
export function FaceTransition<T extends string>({
  value,
  resetKey,
  direction,
  preference,
  waitForReady = false,
  className,
  faceClassName,
  children,
  footer,
}: {
  value: T
  resetKey: unknown
  direction: 1 | -1
  preference: FacePreference
  waitForReady?: boolean
  className?: string
  faceClassName?: string
  children: (face: T, controls: FaceControls) => ReactNode
  footer?: (face: T) => ReactNode
}) {
  const [state, setState] = useState<FaceState<T>>(() => initialFaceState(resetKey, value, direction))
  const containerRef = useRef<HTMLDivElement>(null)
  const incomingRef = useRef<HTMLDivElement>(null)
  const dispatch = useCallback((event: FaceEvent<T>) => setState((prev) => reduceFace(prev, event)), [])

  // 在 render 中對齊外部的值（React 會在提交前立刻重畫）：換了 resetKey 時不會先閃出舊的值；
  // 開始切換時也能在新的一面畫上去之前量到外框原本的高度
  const synced = reduceFace(state, {
    type: 'sync',
    key: resetKey,
    value,
    direction,
    preference,
    measure: () => exactHeight(containerRef.current),
  })
  if (synced !== state) setState(synced)
  const view = synced
  // 切換中（準備與播放）用切換開始時記下的動畫，閒置時用外部給的
  const active = view.preference ?? preference

  /** 新的一面排版好了：量它的高度，開始播放。Starts the animation. */
  const start = useCallback(() => dispatch({ type: 'ready', height: exactHeight(incomingRef.current) }), [dispatch])

  // 準備階段：等新的一面回報排版好（再穩定一下），或最多等 READY_TIMEOUT_MS
  const settleTimer = useRef<number | undefined>(undefined)
  useEffect(() => {
    if (view.stage !== 'preparing') return
    const timeout = window.setTimeout(start, waitForReady ? READY_TIMEOUT_MS : 0)
    return () => {
      window.clearTimeout(timeout)
      window.clearTimeout(settleTimer.current)
    }
  }, [view.stage, view.incoming, waitForReady, start])

  const onIncomingReady = useCallback(() => {
    window.clearTimeout(settleTimer.current)
    settleTimer.current = window.setTimeout(start, READY_SETTLE_MS)
  }, [start])

  // 播完以兩面動畫自己的 finished 為準（瀏覽器可能延後才真正開始，用計時會提早截斷）；
  // 計時只是保險：分頁在背景時動畫不會前進
  const { ms, easing } = faceTiming(active)
  useEffect(() => {
    if (view.stage !== 'animating') return
    let cancelled = false
    // 只等有限次數的動畫（內容裡若有無限循環的動畫，例如載入中的轉圈，finished 永遠不會來）
    const animations = (containerRef.current?.getAnimations?.({ subtree: true }) ?? []).filter(
      (animation) => animation.effect?.getComputedTiming().iterations !== Infinity,
    )
    if (animations.length > 0) {
      Promise.all(animations.map((animation) => animation.finished)).then(
        () => !cancelled && dispatch({ type: 'finished' }),
        () => undefined,
      )
    }
    const timer = window.setTimeout(() => dispatch({ type: 'finished' }), ms * 2 + 1000)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [view.stage, ms, dispatch])

  // 播放開始的那一刻外框還是舊的高度，下一個畫格才換成新的高度，height 的 transition 才接得上
  const [heightArmed, setHeightArmed] = useState(false)
  useLayoutEffect(() => {
    if (view.stage !== 'animating') {
      setHeightArmed(false)
      return
    }
    const frame = requestAnimationFrame(() => setHeightArmed(true))
    // 畫格沒來（分頁在背景）也要換上新的高度
    const fallback = window.setTimeout(() => setHeightArmed(true), 50)
    return () => {
      cancelAnimationFrame(frame)
      window.clearTimeout(fallback)
    }
  }, [view.stage])

  let containerStyle: CSSProperties | undefined
  if (view.stage === 'preparing' && view.fromHeight !== undefined) {
    containerStyle = { height: view.fromHeight }
  } else if (view.stage === 'animating' && view.fromHeight !== undefined && view.toHeight !== undefined) {
    containerStyle = { height: heightArmed ? view.toHeight : view.fromHeight, transition: `height ${ms}ms ${easing}` }
  }

  /** 一面在播放中的標記。Markup of one face. */
  const layerProps = (role: FaceRole): HTMLAttributes<HTMLDivElement> => {
    const markup =
      view.stage === 'animating'
        ? faceMarkup(active, role, view.direction, { from: view.fromHeight, to: view.toHeight })
        : null
    if (!markup) return {}
    const { style, ...data } = markup
    return { ...data, style: style as CSSProperties }
  }
  const option = faceTransitionOption(active.animation)

  const footerFace = view.stage === 'animating' && view.incoming !== null ? view.incoming : view.current

  return (
    <>
      <div
        ref={containerRef}
        className={cn('face-stage', className)}
        data-face-anim={view.stage === 'animating' ? active.animation : undefined}
        data-face-busy={view.stage !== 'idle' ? '' : undefined}
        data-face-clip={option.overflows ? undefined : ''}
        style={containerStyle}
      >
        <div
          key={view.current}
          className={cn('face-layer', faceClassName)}
          aria-hidden={view.stage === 'animating' || undefined}
          {...layerProps('leaving')}
        >
          {children(view.current, { onReady: noop })}
        </div>
        {view.incoming !== null && (
          <div
            key={view.incoming}
            ref={incomingRef}
            className={cn('face-layer', faceClassName)}
            data-face-stage={view.stage === 'preparing' ? 'preparing' : undefined}
            aria-hidden={view.stage === 'preparing' || undefined}
            // 準備中的那一面不能被 Tab 到（React 18 不認得 inert，直接當屬性傳）
            {...(view.stage === 'preparing' ? ({ inert: '' } as Record<string, string>) : {})}
            {...layerProps('entering')}
          >
            {children(view.incoming, { onReady: onIncomingReady })}
          </div>
        )}
      </div>
      {footer?.(footerFace)}
    </>
  )
}

const noop = () => undefined

/**
 * 元素不取整的高度（伸縮漸變的分界要和較矮那一面的下緣完全對齊，差一點就會多出一條亮線或縫）。
 * The unrounded height, so the morph split lines up with the shorter face exactly.
 * @param element - 要量的元素。The element.
 * @returns 高度（px），沒有元素時為 undefined。The height, or undefined.
 */
function exactHeight(element: HTMLElement | null): number | undefined {
  return element?.getBoundingClientRect().height
}
