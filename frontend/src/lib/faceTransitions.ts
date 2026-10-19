/**
 * 卡面正反切換的動畫：有哪些、叫什麼、怎麼套用。與 React 無關，方便測試。
 * Card face switch animations: the catalogue and how to apply one. React-free.
 *
 * 每種動畫分兩段：舊的一面先「離開」（`out`），換上新的一面再「進來」（`in`），各佔總時長的
 * 一半。方向：翻到背面為 1、翻回正面為 -1，動畫依方向反過來（例如滑動改從另一邊進來）。
 * Keyframes 在 `index.css`（`face-<id>-out` / `face-<id>-in`），用 `--face-dir` 讀方向。
 *
 * Each animation has an `out` half (old face leaves) and an `in` half (new
 * face enters). The direction (+1 to the back, -1 to the front) mirrors it.
 * Keyframes live in `index.css` and read `--face-dir`.
 */

import type { CSSProperties } from 'react'

/** 動畫代號。Animation identifiers. */
export type FaceTransitionId = 'none' | 'fade' | 'flip' | 'flip-vertical' | 'slide' | 'zoom'

/** 速度代號。Speed identifiers. */
export type FaceTransitionSpeed = 'fast' | 'normal' | 'slow'

/** 動畫的一段。One half of an animation. */
export type FacePhase = 'out' | 'in'

/** 一種動畫的說明。An animation in the catalogue. */
export interface FaceTransitionOption {
  id: FaceTransitionId
  label: string
  description: string
  /** 需要 3D 透視（翻轉類）。Needs a 3D perspective. */
  perspective?: boolean
}

/** 所有動畫，依設定頁的顯示順序。The catalogue, in display order. */
export const FACE_TRANSITIONS: readonly FaceTransitionOption[] = [
  { id: 'flip', label: '左右翻轉', description: '像翻開實體卡片一樣繞直軸翻面', perspective: true },
  { id: 'flip-vertical', label: '上下翻轉', description: '繞橫軸翻面，像翻筆記本', perspective: true },
  { id: 'fade', label: '淡入淡出', description: '舊的一面淡出，新的一面淡入' },
  { id: 'slide', label: '滑動', description: '往前翻時從右邊滑入，翻回時從左邊' },
  { id: 'zoom', label: '縮放', description: '輕微縮小淡出，再從稍大處收回' },
  { id: 'none', label: '無動畫', description: '立即切換' },
]

/** 各速度的總時長（ms，兩段合計）。Total duration per speed, both halves. */
export const FACE_SPEEDS: Readonly<Record<FaceTransitionSpeed, { label: string; ms: number }>> = {
  fast: { label: '快', ms: 220 },
  normal: { label: '標準', ms: 360 },
  slow: { label: '慢', ms: 560 },
}

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
  const speed = typeof value.speed === 'string' && value.speed in FACE_SPEEDS
    ? (value.speed as FaceTransitionSpeed)
    : DEFAULT_FLIP_PREFERENCE.speed
  return { animation, speed }
}

/**
 * 某一段動畫要套在卡面外框上的樣式；沒有動畫時為 undefined。
 * The style for one half of an animation, or undefined for none.
 * @param preference - 動畫偏好。The preference.
 * @param phase - 哪一段。Which half.
 * @param direction - 1 為翻到背面，-1 為翻回正面。+1 to the back, -1 to the front.
 * @returns 樣式。The style.
 */
export function faceTransitionStyle(
  preference: FlipPreference,
  phase: FacePhase,
  direction: 1 | -1,
): CSSProperties | undefined {
  if (preference.animation === 'none') return undefined
  const half = FACE_SPEEDS[preference.speed].ms / 2
  // 離開加速、進來減速，兩段接起來才順
  const easing = phase === 'out' ? 'cubic-bezier(0.4, 0, 1, 1)' : 'cubic-bezier(0, 0, 0.2, 1)'
  return {
    animation: `face-${preference.animation}-${phase} ${half}ms ${easing} both`,
    ['--face-dir' as string]: direction,
  }
}

/**
 * 這種動畫的外框是否需要 3D 透視。Whether the animation needs a perspective.
 * @param animation - 動畫代號。The animation.
 * @returns 是否需要。Whether it does.
 */
export function needsPerspective(animation: FaceTransitionId): boolean {
  return FACE_TRANSITIONS.some((option) => option.id === animation && option.perspective)
}
