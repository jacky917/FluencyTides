/**
 * 卡面正反切換的動畫：有哪些、叫什麼、怎麼套用。與 React 無關，方便測試。
 * Card face switch animations: the catalogue and how to apply one. React-free.
 *
 * 舊的一面（`leave`）與新的一面（`enter`）疊在同一格、同時播放，所以動作是連續的一段；
 * 外框高度同時從舊的一面平滑變成新的一面（見 `components/FaceTransition.tsx`）。方向：翻到
 * 背面為 1、翻回正面為 -1，動畫依方向鏡像。Keyframes 在 `index.css`
 * （`face-<id>-leave` / `face-<id>-enter`），用 `--face-dir` 讀方向。
 *
 * The leaving and entering faces are stacked and animate together, while
 * the wrapper height tweens between them. The direction (+1 to the back,
 * -1 to the front) mirrors the motion. Keyframes live in `index.css`.
 */

import type { CSSProperties } from 'react'

/** 動畫代號。Animation identifiers. */
export type FaceTransitionId = 'none' | 'fade' | 'flip' | 'flip-vertical' | 'slide' | 'zoom'

/** 速度代號。Speed identifiers. */
export type FaceTransitionSpeed = 'fast' | 'normal' | 'slow'

/** 播放中的角色：離開的舊面、進來的新面。The role of a face during a switch. */
export type FaceRole = 'leave' | 'enter'

/** 一種動畫的說明。An animation in the catalogue. */
export interface FaceTransitionOption {
  id: FaceTransitionId
  label: string
  description: string
  /** 3D 翻轉：需要透視，兩面背對背（背面不可見）。A 3D flip: perspective, hidden backfaces. */
  flip?: boolean
}

/** 所有動畫，依設定頁的顯示順序。The catalogue, in display order. */
export const FACE_TRANSITIONS: readonly FaceTransitionOption[] = [
  { id: 'flip', label: '左右翻轉', description: '正反兩面背對背，像翻開實體卡片一樣繞直軸翻轉', flip: true },
  { id: 'flip-vertical', label: '上下翻轉', description: '繞橫軸翻轉，像翻筆記本', flip: true },
  { id: 'slide', label: '滑動', description: '新的一面從前進方向滑入，舊的一面往反方向退出' },
  { id: 'fade', label: '淡入淡出', description: '兩面交叉淡化' },
  { id: 'zoom', label: '縮放', description: '舊的一面退後淡出，新的一面由近處落定' },
  { id: 'none', label: '無動畫', description: '立即切換' },
]

/** 各速度的時長（ms）。Duration per speed. */
export const FACE_SPEEDS: Readonly<Record<FaceTransitionSpeed, { label: string; ms: number }>> = {
  fast: { label: '快', ms: 280 },
  normal: { label: '標準', ms: 460 },
  slow: { label: '慢', ms: 700 },
}

/** 翻轉用前後加減速的曲線，其他用快速起步、柔和落定的曲線。Easing curves. */
const FLIP_EASING = 'cubic-bezier(0.45, 0.05, 0.25, 1)'
const SETTLE_EASING = 'cubic-bezier(0.22, 0.8, 0.24, 1)'

/** 使用者的動畫偏好。The user's animation preference. */
export interface FlipPreference {
  animation: FaceTransitionId
  speed: FaceTransitionSpeed
}

export const DEFAULT_FLIP_PREFERENCE: FlipPreference = { animation: 'flip', speed: 'normal' }

/**
 * 整理存下來的偏好：不認得的值退回預設。
 * Sanitizes a stored preference, defaulting unknown values.
 * @param raw - localStorage 讀出的值。The stored value.
 * @returns 可用的偏好。A usable preference.
 */
export function sanitizeFlipPreference(raw: unknown): FlipPreference {
  const value = (typeof raw === 'object' && raw !== null ? raw : {}) as Record<string, unknown>
  const animation = FACE_TRANSITIONS.some((option) => option.id === value.animation)
    ? (value.animation as FaceTransitionId)
    : DEFAULT_FLIP_PREFERENCE.animation
  const speed =
    typeof value.speed === 'string' && value.speed in FACE_SPEEDS
      ? (value.speed as FaceTransitionSpeed)
      : DEFAULT_FLIP_PREFERENCE.speed
  return { animation, speed }
}

/**
 * 是否為 3D 翻轉（外框要透視、兩面背對背）。Whether the animation is a 3D flip.
 * @param animation - 動畫代號。The animation.
 * @returns 是否為翻轉。Whether it flips.
 */
export function isFlip(animation: FaceTransitionId): boolean {
  return FACE_TRANSITIONS.some((option) => option.id === animation && option.flip)
}

/**
 * 動畫的時長與曲線（高度變化也用同一組，才會與動作同步）。
 * Duration and easing of a switch; the height tween uses the same.
 * @param preference - 動畫偏好。The preference.
 * @returns 時長（ms）與曲線。Duration and easing.
 */
export function faceTiming(preference: FlipPreference): { ms: number; easing: string } {
  return { ms: FACE_SPEEDS[preference.speed].ms, easing: isFlip(preference.animation) ? FLIP_EASING : SETTLE_EASING }
}

/**
 * 播放中某一面要套的樣式；沒有動畫時為 undefined。
 * The style of one face during a switch, or undefined for none.
 * @param preference - 動畫偏好。The preference.
 * @param role - 離開或進來。Leaving or entering.
 * @param direction - 1 為翻到背面，-1 為翻回正面。+1 to the back, -1 to the front.
 * @returns 樣式。The style.
 */
export function faceTransitionStyle(
  preference: FlipPreference,
  role: FaceRole,
  direction: 1 | -1,
): CSSProperties | undefined {
  if (preference.animation === 'none') return undefined
  const { ms, easing } = faceTiming(preference)
  return {
    animation: `face-${preference.animation}-${role} ${ms}ms ${easing} both`,
    ['--face-dir' as string]: direction,
    ...(isFlip(preference.animation) ? { backfaceVisibility: 'hidden' as const, transformOrigin: 'center center' } : {}),
  }
}
