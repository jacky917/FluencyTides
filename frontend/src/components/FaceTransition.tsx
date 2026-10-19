import { useCallback, useEffect, useRef, useState, type AnimationEvent, type ReactNode } from 'react'
import { cn } from '../lib/utils'
import {
  FACE_SPEEDS,
  faceTransitionStyle,
  needsPerspective,
  type FacePhase,
  type FlipPreference,
} from '../lib/faceTransitions'

/** 動畫的狀態。The transition state. */
interface TransitionState<T> {
  /** 對應的重設鍵（換了就直接切換，不播動畫）。The reset key it belongs to. */
  key: unknown
  /** 目前畫面上的那一面。The face on screen. */
  shown: T
  /** 正在播的那一段；null 為靜止。The half being played, or null. */
  phase: FacePhase | null
  direction: 1 | -1
  /** 開始播放時外框的高度（px）。The wrapper height when the switch started. */
  holdHeight?: number
}

/**
 * 切換兩種內容（卡片的正面與背面）時播放動畫：舊內容先播「離開」，換成新內容再播「進來」。
 * `resetKey` 改變時（換了一張卡片）直接切換、不播動畫。動畫播放中又切換時，播完這一段後
 * 接著切到最新的內容。播放期間外框維持開始時的高度（`min-height`），新內容還在載入、高度
 * 還小時不會先縮再長；播完就放開，新內容比較矮時照常縮回。
 *
 * Animates switching between two contents (card faces): the old content
 * plays the `out` half, then the new one plays `in`. A changed `resetKey`
 * (another card) switches immediately.
 *
 * @param props.value - 要顯示的那一面。The face to show.
 * @param props.resetKey - 換了就不播動畫。Changing it skips the animation.
 * @param props.direction - 1 為往前（翻到背面），-1 為往回。Direction of the switch.
 * @param props.preference - 動畫與速度。Animation and speed.
 * @param props.children - 依某一面畫出內容。Renders a face.
 * @returns 包著內容的外框。The animated wrapper.
 */
export function FaceTransition<T>({
  value,
  resetKey,
  direction,
  preference,
  className,
  children,
}: {
  value: T
  resetKey: unknown
  direction: 1 | -1
  preference: FlipPreference
  className?: string
  children: (shown: T) => ReactNode
}) {
  const [state, setState] = useState<TransitionState<T>>({ key: resetKey, shown: value, phase: null, direction })
  const valueRef = useRef(value)
  valueRef.current = value
  const directionRef = useRef(direction)
  directionRef.current = direction
  const wrapperRef = useRef<HTMLDivElement>(null)

  // 在 render 中直接更新（React 會在提交前立刻重畫），換卡片時不會先閃出舊的一面
  if (state.key !== resetKey) {
    setState({ key: resetKey, shown: value, phase: null, direction })
  } else if (state.phase === null && state.shown !== value) {
    setState(
      preference.animation === 'none'
        ? { ...state, shown: value }
        : { ...state, phase: 'out', direction, holdHeight: wrapperRef.current?.offsetHeight },
    )
  }

  /** 這一段播完：離開 → 換成最新的內容並進來；進來 → 靜止。Advances to the next half. */
  const advance = useCallback(
    (phase: FacePhase) =>
      setState((prev) => {
        if (prev.phase !== phase) return prev
        // 進來時用最新的方向（播放中來回切換過）
        return phase === 'out'
          ? { ...prev, shown: valueRef.current, phase: 'in', direction: directionRef.current }
          : { ...prev, phase: null, holdHeight: undefined }
      }),
    [],
  )

  // 保險：animationend 沒來（分頁在背景、元素被隱藏）也不會卡在半途
  const half = FACE_SPEEDS[preference.speed].ms / 2
  useEffect(() => {
    if (state.phase === null) return
    const phase = state.phase
    const timer = window.setTimeout(() => advance(phase), half + 150)
    return () => window.clearTimeout(timer)
  }, [state.phase, state.shown, half, advance])

  const onAnimationEnd = (event: AnimationEvent<HTMLDivElement>) => {
    if (event.target === event.currentTarget && state.phase) advance(state.phase)
  }

  const shown = state.key === resetKey ? state.shown : value
  return (
    <div
      ref={wrapperRef}
      className={cn(needsPerspective(preference.animation) && '[perspective:1600px]', className)}
      style={state.phase && state.holdHeight ? { minHeight: state.holdHeight } : undefined}
    >
      <div
        style={state.phase ? faceTransitionStyle(preference, state.phase, state.direction) : undefined}
        onAnimationEnd={onAnimationEnd}
      >
        {children(shown)}
      </div>
    </div>
  )
}
