import { cn } from "../../lib/utils"

/**
 * 骨架屏佔位元件，載入時顯示脈動動畫。
 * Skeleton placeholder component that shows a pulse animation while loading.
 * @param className - 額外的 class 名稱。Additional class names.
 * @returns 骨架屏 JSX。Skeleton JSX.
 */
function Skeleton({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("animate-pulse rounded-md bg-primary/10", className)}
      {...props}
    />
  )
}

export { Skeleton }
