/**
 * 劇情流程圖的遊戲清單、manifest、註記與主題（React Query）。
 * Story game list, manifest, annotations and theme hooks.
 */

import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { StoryAPI } from '../api/story'
import { DEFAULT_THEME, loadTheme, type GraphTheme } from '@/features/graph-views/core'

/** 有流程圖的遊戲（卡片頁反查也用）。Games with a flowchart. */
export function useStoryGames(enabled = true) {
  return useQuery({ queryKey: ['story', 'games'], queryFn: StoryAPI.listGames, staleTime: 5 * 60 * 1000, enabled, retry: 1 })
}

/** 一款遊戲的 manifest；`build_id` 改變代表資料更新。The manifest. */
export function useStoryManifest(gameId: string) {
  return useQuery({ queryKey: ['story', 'manifest', gameId], queryFn: () => StoryAPI.getManifest(gameId), staleTime: 60 * 1000 })
}

/** 註記統計與搜尋索引（後端快取 60 秒，前端每 60 秒重抓）。Annotation stats. */
export function useStoryAnnotations(gameId: string, buildId: number | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ['story', 'annotations', gameId, buildId],
    queryFn: () => StoryAPI.getAnnotations(gameId),
    enabled: enabled && buildId !== undefined,
    staleTime: 60 * 1000,
    refetchInterval: 60 * 1000,
  })
}

const THEME_STORAGE = 'story-theme'

/** 內建主題清單（`public/story-themes/index.json`）。Built-in theme list. */
export function useStoryThemeList() {
  return useQuery({
    queryKey: ['story', 'themes'],
    queryFn: async () => {
      const res = await fetch('/story-themes/index.json')
      if (!res.ok) throw new Error('主題清單讀取失敗')
      return ((await res.json()) as { themes: { id: string; label: string }[] }).themes
    },
    staleTime: Infinity,
  })
}

function readStoredTheme(gameId: string): string | null {
  try {
    return localStorage.getItem(`${THEME_STORAGE}:${gameId}`)
  } catch {
    return null
  }
}

/**
 * 目前的主題：使用者選的（localStorage）→ manifest 的 `default_theme` → 平台預設（D15）。
 * 主題讀不到時退回平台預設。
 * The active theme: user choice → manifest default → platform default.
 * @param gameId - 遊戲。The game.
 * @param manifestDefault - manifest 的預設主題。The manifest default.
 * @returns 主題、主題 id 與切換函式。The theme, its id and a setter.
 */
export function useStoryTheme(gameId: string, manifestDefault: string | null | undefined) {
  const [chosen, setChosen] = useState<string | null>(() => readStoredTheme(gameId))
  const themeId = chosen ?? manifestDefault ?? DEFAULT_THEME.id
  const query = useQuery({ queryKey: ['story', 'theme', themeId], queryFn: () => loadTheme(themeId), staleTime: Infinity, retry: 1 })
  const [theme, setTheme] = useState<GraphTheme>(DEFAULT_THEME)
  useEffect(() => {
    if (query.data) setTheme(query.data)
    else if (query.isError) setTheme(DEFAULT_THEME)
  }, [query.data, query.isError])
  const choose = (id: string | null) => {
    setChosen(id)
    try {
      if (id) localStorage.setItem(`${THEME_STORAGE}:${gameId}`, id)
      else localStorage.removeItem(`${THEME_STORAGE}:${gameId}`)
    } catch {
      // localStorage 不能用時只在這次瀏覽有效
    }
  }
  return { theme, themeId, chosen, choose, isError: query.isError }
}
