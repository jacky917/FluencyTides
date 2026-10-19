import { useEffect, useState } from 'react'
import { useLocalStorage } from './useLocalStorage'
import { DEFAULT_FLIP_PREFERENCE, sanitizeFlipPreference, type FlipPreference } from '../lib/faceTransitions'

/** 存動畫偏好的 localStorage 鍵。localStorage key of the preference. */
const FLIP_PREFERENCE_KEY = 'pref_card_flip'

/** 系統「減少動態效果」的媒體查詢。The reduced-motion media query. */
const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)'

/**
 * 卡面正反切換的動畫偏好（存在 localStorage，其他分頁改了也會同步）。系統開啟「減少動態效果」
 * 時，實際套用的動畫一律為無。
 * The flip animation preference; reduced motion forces no animation.
 * @returns 設定值、實際套用的值、是否因系統設定停用、設定函式。The preference and its setter.
 */
export function useFlipPreference() {
  const [stored, setStored] = useLocalStorage<FlipPreference>(FLIP_PREFERENCE_KEY, DEFAULT_FLIP_PREFERENCE)
  const preference = sanitizeFlipPreference(stored)
  const [reducedMotion, setReducedMotion] = useState(
    () => typeof window !== 'undefined' && window.matchMedia(REDUCED_MOTION_QUERY).matches,
  )

  useEffect(() => {
    const media = window.matchMedia(REDUCED_MOTION_QUERY)
    const onChange = () => setReducedMotion(media.matches)
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [])

  const effective: FlipPreference = reducedMotion ? { ...preference, animation: 'none' } : preference
  const setPreference = (changes: Partial<FlipPreference>) => setStored({ ...preference, ...changes })

  return { preference, effective, reducedMotion, setPreference }
}
