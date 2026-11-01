/**
 * 台詞語音播放的 React 包裝：{@link VoicePlayer} 加上「正在播哪一句」的狀態，卸載時自動停止。
 * BGM 播放也用它（另開一個實例、`loop` 打開），所以語音和 BGM 各自一次只播一個、彼此可以同時播。
 * React wrapper around {@link VoicePlayer}: exposes the playing key and stops
 * on unmount. BGM uses a separate instance (looping), so a voice and a BGM
 * track can play together.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { VoicePlayer } from '../lib/voicePlayer'

/** {@link useVoicePlayer} 的音訊設定（每次建立音訊元素時套用）。Audio options applied to each new element. */
export interface VoicePlayerAudioOptions {
  /** 循環播放（BGM 用；循環時不會觸發 `ended`，只能手動停止）。Loop the clip (BGM). */
  loop?: boolean
  /** 音量 0–1（預設 1）。Volume 0–1. */
  volume?: number
}

/** {@link useVoicePlayer} 的回傳值。Return value of {@link useVoicePlayer}. */
export interface VoicePlayerControls<K> {
  /** 正在播放的鍵（沒有時為 null）。The playing key, or null. */
  playing: K | null
  /** 播放或停止這一句。Play or stop a clip. */
  toggle: (key: K, url: string) => void
  /** 停止播放。Stop playback. */
  stop: () => void
}

/**
 * 一次只播一句的語音播放器。A one-at-a-time voice player.
 * @param onError - 播放失敗時呼叫（狀態已重設成停止）。Called on failure (state already reset).
 * @param audioOptions - 循環、音量（只採用第一次 render 傳入的值）。Loop/volume (first render wins).
 * @returns 播放狀態與控制函式。Playback state and controls.
 */
export function useVoicePlayer<K>(
  onError: (key: K, url: string, cause: unknown) => void,
  audioOptions: VoicePlayerAudioOptions = {},
): VoicePlayerControls<K> {
  const [playing, setPlaying] = useState<K | null>(null)
  const onErrorRef = useRef(onError)
  useEffect(() => {
    onErrorRef.current = onError
  }, [onError])
  const optionsRef = useRef(audioOptions)
  const playerRef = useRef<VoicePlayer<K> | null>(null)
  const player = useCallback((): VoicePlayer<K> => {
    playerRef.current ??= new VoicePlayer<K>({
      createAudio: (url) => {
        const audio = new Audio(url)
        audio.preload = 'auto'
        const { loop, volume } = optionsRef.current
        if (loop) audio.loop = true
        if (volume !== undefined) audio.volume = volume
        return audio
      },
      onChange: setPlaying,
      onError: (key, url, cause) => onErrorRef.current(key, url, cause),
    })
    return playerRef.current
  }, [])

  useEffect(() => () => playerRef.current?.stop(), [])

  const toggle = useCallback((key: K, url: string) => player().toggle(key, url), [player])
  const stop = useCallback(() => playerRef.current?.stop(), [])
  return { playing, toggle, stop }
}
