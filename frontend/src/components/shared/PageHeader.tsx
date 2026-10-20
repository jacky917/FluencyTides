import type { ReactNode } from 'react'

/**
 * 頁面頂部的標題與說明。The title and description at the top of a page.
 * @param props.title - 標題。Title.
 * @param props.description - 說明。Description.
 * @returns 標題區 JSX。Header JSX.
 */
export function PageHeader({ title, description }: { title: ReactNode; description?: ReactNode }) {
  return (
    <div className="shrink-0">
      <h2 className="text-3xl font-bold tracking-tight">{title}</h2>
      {description && <p className="text-muted-foreground mt-2">{description}</p>}
    </div>
  )
}
