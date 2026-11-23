/**
 * 知識圖譜的圖主題（`docs/wip/graph_themes_FEAT_2026-11-22.md` D4）：和劇情流程圖共用同一套主題與清單，
 * 但選擇分開記（`kg_theme`，全站一個；劇情流程圖是每款遊戲各記一個）。沒選過時，亮色外觀用平台預設、
 * 深色外觀用經典白（`fallbackTheme`），跟著系統外觀切換。
 *
 * 第一次讀的時候把單字卡舊的標籤樣式（`kg_fontStyleId`）換算成主題並刪掉舊鍵，看慣的樣子不變。
 *
 * The knowledge graph's theme: the same themes as the story flowchart, chosen separately; without a choice it
 * follows the system's light or dark appearance. The retired label style is converted once on first read.
 */

import { useState, useSyncExternalStore } from 'react'
import { useQuery } from '@tanstack/react-query'
import { DEFAULT_THEME, loadTheme, type GraphTheme } from '@/features/graph-views/core'
import { GRAPH_THEME_KEY, LEGACY_STYLE_KEY, fallbackTheme, themeForLegacyStyle } from '../lib/graphTheme'
import { readStored, removeStored, writeStored } from './useStoredState'

/** 系統深色外觀的媒體查詢（和 Tailwind `dark:` 用的同一個）。The dark-appearance media query. */
const DARK_QUERY = '(prefers-color-scheme: dark)'

/** 訂閱系統外觀的變化。Subscribes to appearance changes. */
function subscribeDark(listener: () => void): () => void {
  if (typeof window === 'undefined' || !window.matchMedia) return () => {}
  const media = window.matchMedia(DARK_QUERY)
  media.addEventListener('change', listener)
  return () => media.removeEventListener('change', listener)
}

/** 系統目前是否為深色外觀。Whether the system currently prefers dark. */
function prefersDark(): boolean {
  return typeof window !== 'undefined' && !!window.matchMedia?.(DARK_QUERY).matches
}

/** 讀出選過的主題；沒有時換算舊的標籤樣式（並刪掉舊鍵）。Reads the choice, converting the old style. */
function initialChoice(): string | null {
  const stored = readStored<string | null>('local', GRAPH_THEME_KEY, null)
  if (stored) return stored
  const legacy = themeForLegacyStyle(readStored<unknown>('local', LEGACY_STYLE_KEY, null))
  if (legacy) writeStored('local', GRAPH_THEME_KEY, legacy)
  removeStored('local', LEGACY_STYLE_KEY)
  return legacy
}

/**
 * 知識圖譜目前的主題與切換。The knowledge graph's theme and its setter.
 * @returns 主題（讀取中或讀不到時為平台預設）、主題 id、切換函式。The theme, its id and a setter.
 */
export function useGraphTheme(): { theme: GraphTheme; themeId: string; choose: (id: string) => void } {
  const [chosen, setChosen] = useState<string | null>(initialChoice)
  const dark = useSyncExternalStore(subscribeDark, prefersDark, () => false)
  const themeId = chosen ?? fallbackTheme(dark) ?? DEFAULT_THEME.id
  // 與劇情流程圖同一個查詢鍵：同一個主題只讀一次
  const query = useQuery({
    queryKey: ['story', 'theme', themeId],
    queryFn: () => loadTheme(themeId),
    staleTime: Infinity,
    retry: 1,
  })
  const choose = (id: string) => {
    setChosen(id)
    writeStored('local', GRAPH_THEME_KEY, id)
  }
  return { theme: query.data ?? DEFAULT_THEME, themeId, choose }
}
