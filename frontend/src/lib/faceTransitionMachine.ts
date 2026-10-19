/**
 * 卡面正反切換的狀態機（純函式，與 React 與 DOM 無關，方便測試）。
 * The state machine of a face switch: pure, without React or the DOM.
 *
 * 階段：
 * - idle：只顯示目前那一面。
 * - preparing：新的一面已渲染在下方但看不見，等它排版好（`ready`）；外框固定在舊的高度。
 * - animating：兩面同時播放，外框高度從 `fromHeight` 過渡到 `toHeight`；播完（`finished`）
 *   新的一面成為目前那一面，回到 idle。
 * 換了 `key`（另一張卡片）直接切換；準備中又切回原本那一面則取消；切換中動畫被關掉則直接完成。
 *
 * Stages: idle → preparing (incoming rendered invisibly, waiting for
 * `ready`) → animating (both faces play, the height tweens) → idle.
 */

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
  /** 1 為往前（翻到背面），-1 為往回。Direction of the switch. */
  direction: 1 | -1
  /** 切換開始時外框的高度。Wrapper height when the switch started. */
  fromHeight?: number
  /** 新的一面的高度。Height of the incoming face. */
  toHeight?: number
}

/** 狀態機的事件。Events of the machine. */
export type FaceEvent<T> =
  /**
   * 每次 render 時對齊外部的值：要顯示哪一面、動畫是否開啟；`measure` 量外框目前高度，只在切換
   * 開始時才呼叫（量高度會強制排版）。Syncs with the props; `measure` runs only when a switch starts.
   */
  | { type: 'sync'; key: unknown; value: T; direction: 1 | -1; animated: boolean; measure?: () => number | undefined }
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
      if (state.key !== event.key) return initialFaceState(event.key, event.value, event.direction)
      if (state.stage !== 'idle' && !event.animated) {
        // 切換中動畫被關掉（系統減少動態效果、其他分頁改了設定）：直接切到結果
        return { ...state, current: event.value, incoming: null, stage: 'idle', fromHeight: undefined, toHeight: undefined }
      }
      if (state.stage === 'idle' && event.value !== state.current) {
        return event.animated
          ? { ...state, incoming: event.value, stage: 'preparing', direction: event.direction, fromHeight: event.measure?.() }
          : { ...state, current: event.value }
      }
      if (state.stage === 'preparing' && event.value === state.current) {
        // 還沒開始播就切回原本那一面：取消
        return { ...state, incoming: null, stage: 'idle', fromHeight: undefined }
      }
      return state
    }
    case 'ready':
      return state.stage === 'preparing' ? { ...state, stage: 'animating', toHeight: event.height } : state
    case 'finished':
      return state.stage === 'animating' && state.incoming !== null
        ? { ...state, current: state.incoming, incoming: null, stage: 'idle', fromHeight: undefined, toHeight: undefined }
        : state
  }
}
