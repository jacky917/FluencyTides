import { forwardRef, type ReactNode } from 'react'
import { cn } from '../../lib/utils'
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card'
import { STICKY_HEADER, panelScrollClass } from './panelStyles'

/** {@link Panel} 的 props。Props of {@link Panel}. */
export interface PanelProps {
  /** 面板自己捲動、標題列固定（並排時用）；否則跟著頁面捲動。The panel scrolls itself. */
  scrollable: boolean
  /** 標題（含圖示）。The title, with its icon. */
  title: ReactNode
  /** 標題列右側的按鈕。Buttons on the right of the header. */
  actions?: ReactNode
  className?: string
  contentClassName?: string
  children: ReactNode
}

/**
 * 分欄頁面中的一個面板：標題列加內容；並排時填滿高度、自己捲動，標題列固定在上方。
 * A panel of a split page: a header and content; when side by side it fills
 * the height and scrolls itself under a sticky header.
 * @returns 面板 JSX。Panel JSX.
 */
export const Panel = forwardRef<HTMLDivElement, PanelProps>(function Panel(
  { scrollable, title, actions, className, contentClassName, children },
  ref,
) {
  return (
    <Card ref={ref} className={cn(panelScrollClass(scrollable), className)}>
      <CardHeader className={cn('pb-2 flex-row items-center justify-between space-y-0', scrollable && STICKY_HEADER)}>
        <CardTitle className="text-base flex items-center gap-2">{title}</CardTitle>
        {actions}
      </CardHeader>
      <CardContent className={contentClassName}>{children}</CardContent>
    </Card>
  )
})
