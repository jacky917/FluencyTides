import { useEffect, useState } from 'react'

/** 小於這個寬度視為手機（與 Tailwind `md` 一致）。Below this width counts as mobile (Tailwind `md`). */
const MOBILE_BREAKPOINT = 768

/**
 * 視窗是否比 `md` 窄；視窗大小改變時更新。
 * Whether the viewport is narrower than `md`, updated on resize.
 * @returns 是否為手機寬度。Whether the viewport is mobile-sized.
 */
export function useIsMobile(): boolean {
  const query = `(max-width: ${MOBILE_BREAKPOINT - 1}px)`
  const [isMobile, setIsMobile] = useState(() => typeof window !== 'undefined' && window.matchMedia(query).matches)

  useEffect(() => {
    const media = window.matchMedia(query)
    const onChange = () => setIsMobile(media.matches)
    onChange()
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [query])

  return isMobile
}
