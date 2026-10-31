import * as React from "react"
import * as CollapsiblePrimitive from "@radix-ui/react-collapsible"

import { cn } from "../../lib/utils"

/**
 * 可展開收合的區塊（shadcn/ui Collapsible，基於 Radix Collapsible）。
 * A collapsible region (shadcn/ui Collapsible on Radix Collapsible).
 */
function Collapsible(props: React.ComponentProps<typeof CollapsiblePrimitive.Root>) {
  return <CollapsiblePrimitive.Root data-slot="collapsible" {...props} />
}

/** 展開或收合的按鈕。Toggles the region. */
function CollapsibleTrigger(props: React.ComponentProps<typeof CollapsiblePrimitive.CollapsibleTrigger>) {
  return <CollapsiblePrimitive.CollapsibleTrigger data-slot="collapsible-trigger" {...props} />
}

/**
 * 收合的內容；展開與收合以高度動畫過渡（tw-animate-css 的 collapsible-down／up，高度取自 Radix 量到的
 * `--radix-collapsible-content-height`）。
 * The collapsible content, animating its height on open and close.
 */
function CollapsibleContent({ className, ...props }: React.ComponentProps<typeof CollapsiblePrimitive.CollapsibleContent>) {
  return (
    <CollapsiblePrimitive.CollapsibleContent
      data-slot="collapsible-content"
      className={cn(
        "overflow-hidden data-[state=closed]:animate-collapsible-up data-[state=open]:animate-collapsible-down",
        className,
      )}
      {...props}
    />
  )
}

export { Collapsible, CollapsibleTrigger, CollapsibleContent }
