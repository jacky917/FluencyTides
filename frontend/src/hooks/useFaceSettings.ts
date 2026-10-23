import { useEffect, useState } from 'react'
import { useLocalStorage } from './useLocalStorage'
import { DEFAULT_FACE_SETTINGS, sanitizeFaceSettings, type FaceSettings } from '../lib/faceTransitions'

/**
 * 存動畫設定的 localStorage 鍵（沿用舊名：舊的設定讀取時自動轉成新的格式，下次存設定時才寫回）。
 * localStorage key, kept from the old format.
 */
const FACE_SETTINGS_KEY = 'pref_card_flip'

/** 系統「減少動態效果」的媒體查詢。The reduced-motion media query. */
const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)'

/**
 * 卡面切換的動畫設定：翻面與換卡各一種動畫，速度共用（存在 localStorage，其他分頁改了也會同步）。
 * 系統開啟「減少動態效果」時，實際套用的動畫一律為無。
 * The face animation settings; reduced motion forces no animation.
 * @returns 設定值、實際套用的值、是否因系統設定停用、設定函式。The settings and their setter.
 */
export function useFaceSettings() {
  const [stored, setStored] = useLocalStorage<FaceSettings>(FACE_SETTINGS_KEY, DEFAULT_FACE_SETTINGS)
  const settings = sanitizeFaceSettings(stored)
  const [reducedMotion, setReducedMotion] = useState(
    () => typeof window !== 'undefined' && window.matchMedia(REDUCED_MOTION_QUERY).matches,
  )

  useEffect(() => {
    const media = window.matchMedia(REDUCED_MOTION_QUERY)
    const onChange = () => setReducedMotion(media.matches)
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [])

  const effective: FaceSettings = reducedMotion ? { ...settings, flip: 'none', switch: 'none' } : settings
  const setSettings = (changes: Partial<FaceSettings>) => setStored({ ...settings, ...changes })

  return { settings, effective, reducedMotion, setSettings }
}
