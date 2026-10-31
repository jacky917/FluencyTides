/**
 * 卡面切換（翻面、換卡）的狀態機（純函式，與 React 與 DOM 無關，方便測試）。
 * The state machine of a face switch: pure, without React or the DOM.
 *
 * 階段：
 * - idle：只顯示目前那一面。
 * - preparing：新的一面已渲染在下方但看不見，等它排版好（`ready`）；外框固定在舊的高度。
 * - animating：兩面同時播放，外框高度從 `fromHeight` 過渡到 `toHeight`；播完（`finished`）
 *   新的一面成為目前那一面，回到 idle。
 * 換了 `key` 直接切換；準備中又切回原本那一面則取消，換成第三個值則改準備那一個（不先播已經
 * 不要的那一面）；播放中換了值，播完才往最新的值切換；切換中動畫被關掉則直接完成。
 * 動畫與速度在切換開始時記下（`preference`），播放中外部換了設定或情境（例如翻面接著換卡）
 * 也不會半途換成另一種動畫——唯一的例外是新的值不播動畫（無動畫），這時直接切到結果。
 *
 * Stages: idle → preparing (incoming rendered invisibly, waiting for
 * `ready`) → animating (both faces play, the height tweens) → idle.
 */

import type { FacePreference } from './faceTransitions'

/** 切換的階段。Stage of a switch. */
export type FaceStage = 'idle' | 'preparing' | 'animating'

/** 切換的狀態。The switch state. */
export interface FaceState<T> {
  /** 對應的重設鍵（換了就直接切換，不播動畫）。The reset key it belongs to. */
  key: unknown
  /** 目前顯示的那一面。The face on screen. */
  current: T
  /** 正在進來的那一面；null 為沒有在切換。The incoming face, or null. */
  incoming: T | null
  stage: FaceStage
  /** 1 為往前（例如翻到背面、換到新的卡片），-1 為往回（翻回正面、回到上一張）。Direction of the switch. */
  direction: 1 | -1
  /** 這次切換的動畫與速度（切換開始時記下）。The animation of the switch, fixed when it starts. */
  preference?: FacePreference
  /** 切換開始時外框的高度。Wrapper height when the switch started. */
  fromHeight?: number
  /** 新的一面的高度。Height of the incoming face. */
  toHeight?: number
}

/** 狀態機的事件。Events of the machine. */
export type FaceEvent<T> =
  /**
   * 每次 render 時對齊外部的值：要顯示哪一面、若要切換時播什麼動畫；`measure` 量外框目前高度，
   * 只在切換開始時才呼叫（量高度會強制排版）。Syncs with the props; `measure` runs only when a switch starts.
   */
  | {
      type: 'sync'
      key: unknown
      value: T
      direction: 1 | -1
      preference: FacePreference
      measure?: () => number | undefined
    }
  /** 新的一面排版好了，附上它的高度。The incoming face is laid out. */
  | { type: 'ready'; height?: number }
  /** 動畫播完。The animation finished. */
  | { type: 'finished' }

/**
 * 初始狀態。The initial state.
 * @param key - 重設鍵。The reset key.
 * @param value - 要顯示的那一面。The face to show.
 * @param direction - 方向。The direction.
 * @returns 靜止的狀態。An idle state.
 */
export function initialFaceState<T>(key: unknown, value: T, direction: 1 | -1): FaceState<T> {
  return { key, current: value, incoming: null, stage: 'idle', direction }
}

/**
 * 狀態轉移。沒有變化時回傳同一個物件（呼叫端據此判斷要不要更新，避免無窮重畫）。
 * The transition function; returns the same object when nothing changes.
 * @param state - 目前狀態。The state.
 * @param event - 事件。The event.
 * @returns 新的狀態。The next state.
 */
export function reduceFace<T>(state: FaceState<T>, event: FaceEvent<T>): FaceState<T> {
  switch (event.type) {
    case 'sync': {
      const animated = event.preference.animation !== 'none'
      if (state.key !== event.key) return initialFaceState(event.key, event.value, event.direction)
      if (state.stage !== 'idle' && !animated) {
        // 切換中動畫被關掉（系統減少動態效果、其他分頁改了設定，或換了情境而新情境設為無動畫）：
        // 直接切到結果
        return { ...settled(state), current: event.value }
      }
      if (state.stage === 'idle' && event.value !== state.current) {
        return animated
          ? {
              ...state,
              incoming: event.value,
              stage: 'preparing',
              direction: event.direction,
              preference: event.preference,
              fromHeight: event.measure?.(),
            }
          : { ...state, current: event.value }
      }
      if (state.stage === 'preparing' && event.value === state.current) {
        // 還沒開始播就切回原本那一面：取消
        return settled(state)
      }
      if (state.stage === 'preparing' && event.value !== state.incoming) {
        // 還沒開始播就又換了（例如新卡片還在排版時又點了另一張）：改準備最新的值，外框高度不變
        return { ...state, incoming: event.value, direction: event.direction, preference: event.preference }
      }
      return state
    }
    case 'ready':
      return state.stage === 'preparing' ? { ...state, stage: 'animating', toHeight: event.height } : state
    case 'finished':
      return state.stage === 'animating' && state.incoming !== null
        ? { ...settled(state), current: state.incoming }
        : state
  }
}

/**
 * 結束切換：回到 idle，清掉這次切換才有的欄位。Ends a switch, clearing its fields.
 * @param state - 目前狀態。The state.
 * @returns 停在目前那一面的狀態。An idle state on the current face.
 */
function settled<T>(state: FaceState<T>): FaceState<T> {
  return { ...state, incoming: null, stage: 'idle', preference: undefined, fromHeight: undefined, toHeight: undefined }
}
