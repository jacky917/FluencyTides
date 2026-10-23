/**
 * 卡面切換（翻面、換卡）的動畫目錄與使用者設定：有哪些、叫什麼、多長、怎麼標記。與 React 無關，方便測試。
 * The card face animation catalogue and settings: names, timing and markup. React-free.
 *
 * 分工：這裡只決定「哪一面播哪一段、正著播還是倒著播、多久、什麼曲線」，輸出 data 屬性與
 * CSS 變數；動畫長什麼樣子（keyframes、陰影、圖層順序）全部在 `components/FaceTransition.css`。
 *
 * 每種動畫只定義往前（例如翻到背面、換到新的卡片）的兩段：舊的一面播 `leave`、新的一面播
 * `enter`。往回（翻回正面、回到上一張）就是往前那段倒著播、兩面角色互換——舊的一面播 `enter`
 * 倒放、新的一面播 `leave` 倒放。所以曲線都用對稱的，倒放時手感才一樣。
 *
 * This module decides which part each face plays, in which direction, for
 * how long; the visuals live in `FaceTransition.css`. Each animation defines
 * only the forward parts; going back plays them reversed with roles swapped.
 */

/** 動畫代號。Animation identifiers. */
export type FaceTransitionId = 'none' | 'page' | 'slide' | 'morph' | 'zoom'

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
  { id: 'morph', label: '伸縮漸變', description: '外框隨內容平滑伸長或縮短，兩面在框內交叉漸變', durationScale: 1 },
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

/** 一次切換要播的動畫與速度。The animation and speed of one switch. */
export interface FacePreference {
  animation: FaceTransitionId
  speed: FaceTransitionSpeed
}

/** 播動畫的兩種情境：同一張卡片翻面、換到另一張卡片。The two occasions for an animation. */
export type FaceOccasion = 'flip' | 'switch'

/**
 * 使用者的動畫設定：每種情境各選一種動畫（欄位就是 {@link FaceOccasion}，新增情境時編譯器會要求
 * 補上），速度共用。The user's settings: one animation per occasion, a shared speed.
 */
export interface FaceSettings extends Record<FaceOccasion, FaceTransitionId> {
  speed: FaceTransitionSpeed
}

/**
 * 預設設定：翻面像翻書，換卡用水平推移，像換到下一張。
 * Defaults: a page turn for flips, a horizontal push for switching cards.
 */
export const DEFAULT_FACE_SETTINGS: FaceSettings = { flip: 'page', switch: 'slide', speed: 'normal' }

/**
 * 舊版的動畫代號 → 現在的（設定存在使用者的瀏覽器，改名後要接得上）。這裡的 `flip` 是舊的動畫名
 * 「翻轉」，與翻面情境無關。Renamed animation ids; `flip` here is an old animation, not the occasion.
 */
const LEGACY_ANIMATION_IDS: Record<string, FaceTransitionId> = { flip: 'page', 'flip-vertical': 'page', fade: 'morph' }

/** 物件自己有這個鍵（`in` 會找到原型上的 `constructor` 等）。Own-key check, unlike `in`. */
const hasOwn = (object: object, key: string): boolean => Object.prototype.hasOwnProperty.call(object, key)

/**
 * 某種動畫的說明；不認得時為「無動畫」。The catalogue entry of an animation.
 * @param id - 動畫代號。The animation.
 * @returns 說明。The entry.
 */
export function faceTransitionOption(id: FaceTransitionId): FaceTransitionOption {
  return FACE_TRANSITIONS.find((option) => option.id === id) ?? FACE_TRANSITIONS[FACE_TRANSITIONS.length - 1]
}

/**
 * 存下來的動畫代號：舊代號換成新的，不認得時用預設。
 * A stored animation id, migrating renamed ids.
 * @param raw - 存下來的值。The stored value.
 * @param fallback - 不認得時的預設。The default.
 * @returns 可用的動畫代號。A usable id.
 */
function sanitizeAnimation(raw: unknown, fallback: FaceTransitionId): FaceTransitionId {
  const id = typeof raw === 'string' ? (hasOwn(LEGACY_ANIMATION_IDS, raw) ? LEGACY_ANIMATION_IDS[raw] : raw) : undefined
  return FACE_TRANSITIONS.some((option) => option.id === id) ? (id as FaceTransitionId) : fallback
}

/**
 * 整理存下來的設定：舊代號換成新的，不認得的值退回預設。舊版只有一種動畫（`animation`），
 * 當成翻面的設定沿用，換卡用預設。
 * Sanitizes stored settings; the old single `animation` becomes the flip one.
 * @param raw - localStorage 讀出的值。The stored value.
 * @returns 可用的設定。Usable settings.
 */
export function sanitizeFaceSettings(raw: unknown): FaceSettings {
  const value = (typeof raw === 'object' && raw !== null ? raw : {}) as Record<string, unknown>
  const speed =
    typeof value.speed === 'string' && hasOwn(FACE_SPEEDS, value.speed)
      ? (value.speed as FaceTransitionSpeed)
      : DEFAULT_FACE_SETTINGS.speed
  return {
    flip: sanitizeAnimation(value.flip ?? value.animation, DEFAULT_FACE_SETTINGS.flip),
    switch: sanitizeAnimation(value.switch, DEFAULT_FACE_SETTINGS.switch),
    speed,
  }
}

/**
 * 某種情境要播的動畫與速度。The animation and speed for an occasion.
 * @param settings - 動畫設定。The settings.
 * @param occasion - 翻面或換卡。Flip or switch.
 * @returns 這次切換的動畫與速度。The preference for the switch.
 */
export function facePreference(settings: FaceSettings, occasion: FaceOccasion): FacePreference {
  return { animation: settings[occasion], speed: settings.speed }
}

/**
 * 動畫的時長與曲線（外框高度的過渡也用同一組，才會與動作同步）。
 * Duration and easing of a switch; the height tween uses the same.
 * @param preference - 這次切換的動畫與速度。The animation and speed of the switch.
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
  /** 伸縮漸變時兩面不一樣高：較高的是 long、較矮的是 short。Which face is taller (morph only). */
  'data-face-span'?: 'long' | 'short'
  style: Record<string, string>
}

/** 兩面的高度：舊的一面（離開）與新的一面（進來）。Heights of the leaving and entering faces. */
export interface FaceHeights {
  from?: number
  to?: number
}

/**
 * 播放中某一面的標記：播哪一段（往回時角色互換）、正放或倒放、時長與曲線。沒有動畫時為 null。
 *
 * 伸縮漸變時，較高的一面另外標成 long，並帶上較矮那一面的高度（`--face-split`）：
 * 這條線以上是兩面重疊、交叉漸變的部分，以下是較高那一面多出來的部分，不漸變、只由外框高度露出或收起。
 *
 * The markup of one face during a switch, or null for no animation. For a
 * morph between faces of different heights, the taller one also carries the
 * shorter height: above it the faces cross-fade, below it the taller face
 * stays opaque and is revealed or hidden by the wrapper height alone.
 * @param preference - 這次切換的動畫與速度。The animation and speed of the switch.
 * @param role - 這一面是離開還是進來。Leaving or entering.
 * @param direction - 1 為往前（翻到背面、換到新的卡片），-1 為往回（翻回正面、回到上一張）。+1 forward, -1 back.
 * @param heights - 兩面的高度（伸縮漸變用）。Heights of both faces, for morph.
 * @returns data 屬性與 CSS 變數。Attributes and CSS variables.
 */
export function faceMarkup(
  preference: FacePreference,
  role: FaceRole,
  direction: 1 | -1,
  heights: FaceHeights = {},
): FaceMarkup | null {
  if (preference.animation === 'none') return null
  const forward = direction === 1
  const part: FacePart = (role === 'leaving') === forward ? 'leave' : 'enter'
  const { ms, easing } = faceTiming(preference)
  const markup: FaceMarkup = {
    'data-face-anim': preference.animation,
    'data-face-part': part,
    style: { '--face-duration': `${ms}ms`, '--face-easing': easing, '--face-play': forward ? 'normal' : 'reverse' },
  }
  const { from, to } = heights
  if (preference.animation === 'morph' && from !== undefined && to !== undefined && from !== to) {
    const own = role === 'leaving' ? from : to
    const split = Math.min(from, to)
    markup['data-face-span'] = own > split ? 'long' : 'short'
    if (own > split) markup.style['--face-split'] = `${split}px`
  }
  return markup
}
