/**
 * 卡面正反切換的動畫目錄：有哪些、叫什麼、多長、怎麼標記。與 React 無關，方便測試。
 * The card face animation catalogue: names, timing and markup. React-free.
 *
 * 分工：這裡只決定「哪一面播哪一段、正著播還是倒著播、多久、什麼曲線」，輸出 data 屬性與
 * CSS 變數；動畫長什麼樣子（keyframes、陰影、圖層順序）全部在 `components/FaceTransition.css`。
 *
 * 每種動畫只定義往前（翻到背面）的兩段：舊的一面播 `leave`、新的一面播 `enter`。往回（翻回
 * 正面）就是往前那段倒著播、兩面角色互換——舊的一面播 `enter` 倒放、新的一面播 `leave` 倒放。
 * 所以曲線都用對稱的，倒放時手感才一樣。
 *
 * This module decides which part each face plays, in which direction, for
 * how long; the visuals live in `FaceTransition.css`. Each animation defines
 * only the forward parts; going back plays them reversed with roles swapped.
 */

/** 動畫代號。Animation identifiers. */
export type FaceTransitionId = 'none' | 'page' | 'slide' | 'fade' | 'zoom'

/** 速度代號。Speed identifiers. */
export type FaceTransitionSpeed = 'fast' | 'normal' | 'slow'

/** 往前的兩段：舊的一面離開、新的一面進來。The forward parts. */
export type FacePart = 'leave' | 'enter'

/** 播放中的一面是舊的（離開）還是新的（進來）。Whether a face is leaving or entering. */
export type FaceRole = 'leaving' | 'entering'

/** 一種動畫的說明。An animation in the catalogue. */
export interface FaceTransitionOption {
  id: FaceTransitionId
  label: string
  description: string
  /** 相對於速度設定的時長倍數（翻頁動作大，給久一點）。Duration multiplier. */
  durationScale: number
  /** 會轉出卡面範圍（翻頁），外框不能裁切。Moves outside the card, so the wrapper must not clip. */
  overflows?: boolean
}

/** 所有動畫，依設定頁的顯示順序。The catalogue, in display order. */
export const FACE_TRANSITIONS: readonly FaceTransitionOption[] = [
  {
    id: 'page',
    label: '翻頁',
    description: '像翻書一樣，整頁沿左側書脊翻起、離開，露出下一頁',
    durationScale: 1.25,
    overflows: true,
  },
  { id: 'slide', label: '推移', description: '新的一面從右側推入，舊的一面退後變暗（iOS 頁面切換）', durationScale: 1 },
  { id: 'fade', label: '交叉淡化', description: '舊的一面先淡出，新的一面隨後淡入', durationScale: 0.85 },
  { id: 'zoom', label: '縮放', description: '舊的一面往後退去，新的一面由近處落定', durationScale: 0.9 },
  { id: 'none', label: '無動畫', description: '立即切換', durationScale: 0 },
]

/** 各速度的基準時長（ms），實際時長再乘上動畫的倍數。Base duration per speed. */
export const FACE_SPEEDS: Readonly<Record<FaceTransitionSpeed, { label: string; ms: number }>> = {
  fast: { label: '快', ms: 450 },
  normal: { label: '標準', ms: 700 },
  slow: { label: '慢', ms: 1000 },
}

/** 對稱的加減速曲線（倒放時手感相同）。A symmetric easing, so reversed playback feels the same. */
const EASING = 'cubic-bezier(0.65, 0, 0.35, 1)'

/** 使用者的動畫偏好。The user's animation preference. */
export interface FacePreference {
  animation: FaceTransitionId
  speed: FaceTransitionSpeed
}

export const DEFAULT_FACE_PREFERENCE: FacePreference = { animation: 'page', speed: 'normal' }

/** 舊版的動畫代號 → 現在的（設定存在使用者的瀏覽器，改名後要接得上）。Renamed animation ids. */
const LEGACY_IDS: Record<string, FaceTransitionId> = { flip: 'page', 'flip-vertical': 'page' }

/**
 * 某種動畫的說明；不認得時為「無動畫」。The catalogue entry of an animation.
 * @param id - 動畫代號。The animation.
 * @returns 說明。The entry.
 */
export function faceTransitionOption(id: FaceTransitionId): FaceTransitionOption {
  return FACE_TRANSITIONS.find((option) => option.id === id) ?? FACE_TRANSITIONS[FACE_TRANSITIONS.length - 1]
}

/**
 * 整理存下來的偏好：舊代號換成新的，不認得的值退回預設。
 * Sanitizes a stored preference, migrating renamed ids.
 * @param raw - localStorage 讀出的值。The stored value.
 * @returns 可用的偏好。A usable preference.
 */
export function sanitizeFacePreference(raw: unknown): FacePreference {
  const value = (typeof raw === 'object' && raw !== null ? raw : {}) as Record<string, unknown>
  const id = typeof value.animation === 'string' ? (LEGACY_IDS[value.animation] ?? value.animation) : undefined
  const animation = FACE_TRANSITIONS.some((option) => option.id === id)
    ? (id as FaceTransitionId)
    : DEFAULT_FACE_PREFERENCE.animation
  const speed =
    typeof value.speed === 'string' && value.speed in FACE_SPEEDS
      ? (value.speed as FaceTransitionSpeed)
      : DEFAULT_FACE_PREFERENCE.speed
  return { animation, speed }
}

/**
 * 動畫的時長與曲線（外框高度的過渡也用同一組，才會與動作同步）。
 * Duration and easing of a switch; the height tween uses the same.
 * @param preference - 動畫偏好。The preference.
 * @returns 時長（ms，無動畫為 0）與曲線。Duration and easing.
 */
export function faceTiming(preference: FacePreference): { ms: number; easing: string } {
  const scale = faceTransitionOption(preference.animation).durationScale
  return { ms: Math.round(FACE_SPEEDS[preference.speed].ms * scale), easing: EASING }
}

/** 一面在播放中要加上的屬性。The markup of one face during a switch. */
export interface FaceMarkup {
  'data-face-anim': FaceTransitionId
  'data-face-part': FacePart
  style: Record<string, string>
}

/**
 * 播放中某一面的標記：播哪一段（往回時角色互換）、正放或倒放、時長與曲線。沒有動畫時為 null。
 * The markup of one face during a switch, or null for no animation.
 * @param preference - 動畫偏好。The preference.
 * @param role - 這一面是離開還是進來。Leaving or entering.
 * @param direction - 1 為翻到背面，-1 為翻回正面。+1 to the back, -1 to the front.
 * @returns data 屬性與 CSS 變數。Attributes and CSS variables.
 */
export function faceMarkup(preference: FacePreference, role: FaceRole, direction: 1 | -1): FaceMarkup | null {
  if (preference.animation === 'none') return null
  const forward = direction === 1
  const part: FacePart = (role === 'leaving') === forward ? 'leave' : 'enter'
  const { ms, easing } = faceTiming(preference)
  return {
    'data-face-anim': preference.animation,
    'data-face-part': part,
    style: { '--face-duration': `${ms}ms`, '--face-easing': easing, '--face-play': forward ? 'normal' : 'reverse' },
  }
}
