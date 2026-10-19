import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type AnimationEvent,
  type CSSProperties,
  type ReactNode,
} from 'react'
import { cn } from '../lib/utils'
import { faceTiming, faceTransitionStyle, isFlip, type FlipPreference } from '../lib/faceTransitions'

/** 給每一面內容的控制。Controls handed to each face. */
export interface FaceControls {
  /**
   * 內容排版好了（例如卡面回報了高度）；`waitForReady` 時，新的一面要等這個才開始播放。
   * The content is laid out; with `waitForReady` the switch waits for it.
   */
  onReady: () => void
}

/** 切換的階段。Stage of a switch. */
type Stage = 'idle' | 'preparing' | 'animating'

/** 切換的狀態。The transition state. */
interface TransitionState<T> {
  /** 對應的重設鍵（換了就直接切換，不播動畫）。The reset key it belongs to. */
  key: unknown
  /** 目前顯示的那一面。The face on screen. */
  current: T
  /** 正在進來的那一面；null 為沒有在切換。The incoming face, or null. */
  incoming: T | null
  stage: Stage
  direction: 1 | -1
  /** 切換開始時外框的高度。Wrapper height when the switch started. */
  fromHeight?: number
  /** 新的一面的高度。Height of the incoming face. */
  toHeight?: number
}

/** 新的一面回報排版好之後再等這麼久沒有變化才開始（圖片等陸續載入）。Settle time after a ready report. */
const READY_SETTLE_MS = 120
/** 最多等新的一面這麼久，之後不管有沒有回報都開始。Max wait for the incoming face. */
const READY_TIMEOUT_MS = 900

/**
 * 切換兩種內容（卡片的正面與背面）時播放動畫：
 * 1. 準備：新的一面先疊在舊的一面下方、透明地渲染，外框維持原本高度（`waitForReady` 時等它
 *    回報排版好，例如卡面 iframe 載入並回報高度），所以播放中不會出現空白或閃爍；
 * 2. 播放：兩面同時播放離開／進來的動畫，外框高度同時從舊的一面平滑變成新的一面；
 * 3. 結束：移除舊的一面，外框高度回到自動。新的一面的元素沿用準備時的那一個，不會重新掛載。
 * `resetKey` 改變時（換了一張卡片）直接切換、不播動畫；準備時又切回原本那一面則取消。
 *
 * Animates switching between two contents (card faces): the incoming face is
 * pre-rendered invisibly (optionally until it reports ready), then both faces
 * animate together while the wrapper height tweens; the incoming element is
 * kept, not remounted. A changed `resetKey` switches immediately.
 *
 * @param props.value - 要顯示的那一面。The face to show.
 * @param props.resetKey - 換了就不播動畫。Changing it skips the animation.
 * @param props.direction - 1 為往前（翻到背面），-1 為往回。Direction of the switch.
 * @param props.preference - 動畫與速度。Animation and speed.
 * @param props.waitForReady - 新的一面要等 `onReady` 才播放。Wait for the incoming face's `onReady`.
 * @param props.faceClassName - 每一面外框的 class（例如圓角，讓陰影貼合卡面）。Class of each face wrapper.
 * @param props.children - 依某一面畫出內容。Renders a face.
 * @returns 包著內容的外框。The animated wrapper.
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
}: {
  value: T
  resetKey: unknown
  direction: 1 | -1
  preference: FlipPreference
  waitForReady?: boolean
  className?: string
  faceClassName?: string
  children: (face: T, controls: FaceControls) => ReactNode
}) {
  const [state, setState] = useState<TransitionState<T>>({
    key: resetKey,
    current: value,
    incoming: null,
    stage: 'idle',
    direction,
  })
  const containerRef = useRef<HTMLDivElement>(null)
  const incomingRef = useRef<HTMLDivElement>(null)

  // 在 render 中直接更新（React 會在提交前立刻重畫），換卡片時不會先閃出舊的一面
  if (state.key !== resetKey) {
    setState({ key: resetKey, current: value, incoming: null, stage: 'idle', direction })
  } else if (state.stage === 'idle' && value !== state.current) {
    setState(
      preference.animation === 'none'
        ? { ...state, current: value }
        : { ...state, incoming: value, stage: 'preparing', direction, fromHeight: containerRef.current?.offsetHeight },
    )
  } else if (state.stage !== 'idle' && preference.animation === 'none') {
    // 切換中動畫被關掉（系統減少動態效果、其他分頁改了設定）：直接切到結果
    setState({ ...state, current: value, incoming: null, stage: 'idle', fromHeight: undefined, toHeight: undefined })
  } else if (state.stage === 'preparing' && value === state.current) {
    // 還沒開始播就切回原本那一面：取消
    setState({ ...state, incoming: null, stage: 'idle', fromHeight: undefined })
  }

  /** 開始播放：量新的一面的高度，外框高度從舊的變到新的。Starts the animation. */
  const start = useCallback(() => {
    setState((prev) =>
      prev.stage === 'preparing' ? { ...prev, stage: 'animating', toHeight: incomingRef.current?.offsetHeight } : prev,
    )
  }, [])

  /** 播完：新的一面成為目前的一面。Finishes the switch. */
  const finish = useCallback(() => {
    setState((prev) =>
      prev.stage === 'animating' && prev.incoming !== null
        ? { ...prev, current: prev.incoming, incoming: null, stage: 'idle', fromHeight: undefined, toHeight: undefined }
        : prev,
    )
  }, [])

  // 準備階段：等新的一面排版好（回報後再穩定一下），或最多等 READY_TIMEOUT_MS
  const settleTimer = useRef<number | undefined>(undefined)
  useEffect(() => {
    if (state.stage !== 'preparing') return
    const timeout = window.setTimeout(start, waitForReady ? READY_TIMEOUT_MS : 0)
    return () => {
      window.clearTimeout(timeout)
      window.clearTimeout(settleTimer.current)
    }
  }, [state.stage, state.incoming, waitForReady, start])

  const onIncomingReady = useCallback(() => {
    window.clearTimeout(settleTimer.current)
    settleTimer.current = window.setTimeout(start, READY_SETTLE_MS)
  }, [start])

  // 播完以動畫自己的 finished 為準（瀏覽器可能延後才真正開始，用計時會提早截斷）；
  // 計時只是保險：分頁在背景時動畫不會前進
  const { ms, easing } = faceTiming(preference)
  useEffect(() => {
    if (state.stage !== 'animating') return
    let cancelled = false
    const animations = incomingRef.current?.getAnimations?.() ?? []
    if (animations.length > 0) {
      Promise.all(animations.map((animation) => animation.finished)).then(
        () => !cancelled && finish(),
        () => undefined,
      )
    }
    const timer = window.setTimeout(finish, ms * 2 + 1000)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [state.stage, ms, finish])

  // 播放開始的那一刻外框還是舊的高度，下一個畫格才換成新的高度，height 的 transition 才接得上
  const [heightArmed, setHeightArmed] = useState(false)
  useLayoutEffect(() => {
    if (state.stage !== 'animating') {
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
  }, [state.stage])

  const onEnterEnd = (event: AnimationEvent<HTMLDivElement>) => {
    if (event.target === event.currentTarget) finish()
  }

  let containerStyle: CSSProperties | undefined
  if (state.stage === 'preparing' && state.fromHeight !== undefined) {
    containerStyle = { height: state.fromHeight }
  } else if (state.stage === 'animating' && state.fromHeight !== undefined && state.toHeight !== undefined) {
    containerStyle = {
      height: heightArmed ? state.toHeight : state.fromHeight,
      transition: `height ${ms}ms ${easing}`,
    }
  }

  const layer: CSSProperties = { gridArea: '1 / 1', alignSelf: 'start', minWidth: 0 }
  const noop = () => undefined
  const shown = state.key === resetKey ? state.current : value
  const incoming = state.key === resetKey ? state.incoming : null

  return (
    <div
      ref={containerRef}
      // 翻轉要透視（近側邊緣會略為突出，不裁切）；其他動畫橫向裁切，滑入時才不會撐出橫向捲動
      className={cn('grid', isFlip(preference.animation) ? '[perspective:1800px]' : 'overflow-x-clip', className)}
      style={containerStyle}
    >
      <div
        key={shown}
        className={faceClassName}
        aria-hidden={state.stage === 'animating' || undefined}
        style={{
          ...layer,
          ...(state.stage !== 'idle' ? { willChange: 'opacity, transform' } : {}),
          ...(state.stage === 'animating' ? faceTransitionStyle(preference, 'leave', state.direction) : {}),
        }}
      >
        {children(shown, { onReady: noop })}
      </div>
      {incoming !== null && (
        <div
          key={incoming}
          ref={incomingRef}
          className={faceClassName}
          aria-hidden={state.stage === 'preparing' || undefined}
          // 準備中的那一面不能被 Tab 到（React 18 不認得 inert，直接當屬性傳）
          {...(state.stage === 'preparing' ? ({ inert: '' } as Record<string, string>) : {})}
          style={{
            ...layer,
            // 準備時就先合成成獨立圖層並畫好（瀏覽器不畫完全透明、沒有 will-change 的元素），
            // 開始播放時才不會因為第一次繪製整張卡面而卡住
            willChange: 'opacity, transform',
            ...(state.stage === 'preparing'
              ? { opacity: 0.01, pointerEvents: 'none' }
              : faceTransitionStyle(preference, 'enter', state.direction)),
          }}
          onAnimationEnd={onEnterEnd}
        >
          {children(incoming, { onReady: onIncomingReady })}
        </div>
      )}
    </div>
  )
}
