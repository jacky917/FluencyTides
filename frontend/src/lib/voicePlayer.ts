/**
 * 台詞語音的播放控制：同一時間只播一句；再按一次同一句或改播別句都會先停掉目前這句。
 * 不依賴 DOM（音訊元素由呼叫端提供），所以可以在 node 環境測試。
 *
 * Voice playback controller: at most one clip at a time; toggling the same
 * key stops it, starting another key stops the current one first. The audio
 * element is injected, so this runs (and is tested) without a DOM.
 */

/** 事件處理器（參數不用；寫成這樣 `HTMLAudioElement` 的 `(ev: Event) => any` 也能指定）。An event handler. */
export type VoiceHandler = (...args: never[]) => unknown

/** 播放需要的音訊元素介面（`HTMLAudioElement` 的子集）。The subset of `HTMLAudioElement` used. */
export interface VoiceAudio {
  play(): Promise<void>
  pause(): void
  onended: VoiceHandler | null
  onerror: VoiceHandler | null
}

/** {@link VoicePlayer} 的設定。Options of {@link VoicePlayer}. */
export interface VoicePlayerOptions<K> {
  /** 為一個網址建立音訊元素（瀏覽器用 `new Audio(url)`）。Creates an audio element for a URL. */
  createAudio: (url: string) => VoiceAudio
  /** 正在播放的鍵改變時呼叫（停止時為 null）。Called when the playing key changes (null when stopped). */
  onChange: (key: K | null) => void
  /** 播放失敗時呼叫一次（之後狀態已重設成停止）。Called once when playback fails (state already reset). */
  onError: (key: K, url: string, cause: unknown) => void
}

/**
 * 一次只播一句的播放器。Plays one clip at a time.
 *
 * 每次播放各有自己的音訊元素；事件與 `play()` 的結果只在它仍是目前這次播放時才處理，
 * 所以停止後才抵達的 `AbortError`、`ended` 不會影響下一句。同一次失敗可能同時觸發
 * `error` 事件與 `play()` 拒絕，只回報一次。
 * Each playback owns its element; late events from a stopped clip are
 * ignored, and a failure reported by both `error` and `play()` is reported once.
 */
export class VoicePlayer<K> {
  private audio: VoiceAudio | null = null
  private key: K | null = null
  private readonly opts: VoicePlayerOptions<K>

  constructor(opts: VoicePlayerOptions<K>) {
    this.opts = opts
  }

  /** 正在播放的鍵。The key being played, or null. */
  get playing(): K | null {
    return this.key
  }

  /**
   * 播放或停止：正在播同一個鍵就停止，否則停掉目前這句再播新的。
   * Stop if `key` is playing, otherwise switch to it.
   * @param key - 這句的鍵（例 line_id）。The clip key.
   * @param url - 音訊網址。The audio URL.
   */
  toggle(key: K, url: string): void {
    if (this.audio && this.key === key) {
      this.stop()
      return
    }
    this.start(key, url)
  }

  /** 停止播放（沒在播時什麼都不做）。Stop playback, if any. */
  stop(): void {
    if (!this.release()) return
    this.opts.onChange(null)
  }

  private start(key: K, url: string): void {
    this.release()
    const audio = this.opts.createAudio(url)
    this.audio = audio
    this.key = key
    audio.onended = () => {
      if (this.audio === audio) this.stop()
    }
    audio.onerror = () => this.fail(audio, key, url, new Error('audio element error'))
    this.opts.onChange(key)
    let started: Promise<void> | undefined
    try {
      started = audio.play()
    } catch (e) {
      this.fail(audio, key, url, e)
      return
    }
    // 舊瀏覽器的 play() 不回 Promise
    started?.catch((e: unknown) => this.fail(audio, key, url, e))
  }

  private fail(audio: VoiceAudio, key: K, url: string, cause: unknown): void {
    if (this.audio !== audio) return // 已停止或已換句：不是目前這次播放的錯
    this.release()
    this.opts.onChange(null)
    this.opts.onError(key, url, cause)
  }

  /** 放掉目前的音訊元素；回傳原本是否在播。Drop the current element; whether one was playing. */
  private release(): boolean {
    const audio = this.audio
    if (!audio) return false
    this.audio = null
    this.key = null
    audio.onended = null
    audio.onerror = null
    try {
      audio.pause()
    } catch {
      // 元素已失效時 pause 可能丟例外；反正要丟掉它
    }
    return true
  }
}

/**
 * 把播放失敗整理成可顯示的訊息。音訊元素拿不到 HTTP 狀態，所以用 `probe` 重新請求一次語音網址
 * （走 API client，可以取得後端的錯誤訊息，例如「語音 xxx 不存在」）；請求成功代表檔案在，
 * 是瀏覽器播不了或被擋下。
 *
 * Describes a playback failure. The audio element hides the HTTP status, so
 * `probe` re-requests the URL through the API client to surface the backend
 * message; if that succeeds the file exists and the browser could not play it.
 *
 * @param cause - `play()` 的拒絕原因或元素錯誤。The rejection or element error.
 * @param probe - 重新請求語音；失敗時拒絕並帶 API 錯誤。Re-requests the voice.
 * @param describe - 把 API 錯誤整理成訊息（`describeApiError(e).message`）。Formats an API error.
 * @returns 訊息。The message.
 */
export async function describeVoiceFailure(
  cause: unknown,
  probe: () => Promise<unknown>,
  describe: (error: unknown) => string,
): Promise<string> {
  // DOMException 不一定繼承 Error（舊瀏覽器），只看 name
  if (typeof cause === 'object' && cause !== null && (cause as { name?: unknown }).name === 'NotAllowedError') {
    return '瀏覽器擋下了播放（請再按一次播放鍵）'
  }
  try {
    await probe()
  } catch (e) {
    return describe(e)
  }
  return '瀏覽器無法播放這個音訊檔'
}
