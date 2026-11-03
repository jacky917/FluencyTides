/**
 * 分頁按鈕：オーバービュー（總覽）＋各分頁，依 `order` 排列；目前分頁有 `data-active`。
 * Tab buttons: overview plus each tab in order.
 */

import type { ReactNode } from 'react'
import type { GraphTheme } from '../../../core/theme'
import { localizedTitle, type StoryTab } from '../core/types'

/** {@link TabBar} 的 props。Props of {@link TabBar}. */
export interface TabBarProps {
  tabs: StoryTab[]
  active: string | null
  theme: GraphTheme
  lang?: string
  badges?: Record<string, ReactNode>
  overviewLabel?: string
  onChange?: (tab: string | null) => void
}

/**
 * 分頁按鈕。The tab bar.
 * @param props - 見 {@link TabBarProps}。See {@link TabBarProps}.
 * @returns 按鈕列。The buttons.
 */
export function TabBar({ tabs, active, theme, lang, badges, overviewLabel = 'オーバービュー', onChange }: TabBarProps) {
  const ordered = [...tabs].sort((a, b) => a.order - b.order)
  const button = (key: string | null, title: string, avatar?: string) => (
    <button
      key={key ?? '__overview'}
      type="button"
      className="sf-tab"
      data-tab={key ?? 'overview'}
      data-active={active === key || undefined}
      aria-pressed={active === key}
      onClick={() => onChange?.(key)}
    >
      {avatar && <img className="sf-tab__avatar" src={avatar} alt="" draggable={false} />}
      <span className="sf-tab__title">{title}</span>
      {key && badges?.[key] != null && <span className="sf-tab__badge">{badges[key]}</span>}
    </button>
  )
  return (
    <nav className="sf-tabs" aria-label="分頁">
      {button(null, overviewLabel)}
      {ordered.map((t) => button(t.key, localizedTitle(t, lang), t.avatar ? theme.assets.tabAvatars?.[t.avatar] : undefined))}
    </nav>
  )
}
