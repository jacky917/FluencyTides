/**
 * 可收合的側邊欄（shadcn/ui Sidebar 的精簡版，只保留本專案用到的部分）。
 * A collapsible sidebar: a trimmed shadcn/ui Sidebar.
 *
 * - 桌面：收合時整條滑出畫面（`offcanvas`），主內容區同步加寬，兩者都是 CSS transition；
 *   展開狀態存在 localStorage，`Ctrl/⌘ + B` 切換。
 * - 手機（比 `md` 窄）：改成從左邊滑出的 Sheet。
 *
 * Desktop: slides off-canvas with a CSS transition, state kept in
 * localStorage, toggled with Ctrl/⌘+B. Mobile: a sheet sliding from the left.
 */

import * as React from "react"
import { Slot } from "@radix-ui/react-slot"
import { PanelLeftIcon } from "lucide-react"

import { useIsMobile } from "../../hooks/useIsMobile"
import { cn } from "../../lib/utils"
import { Button } from "./button"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "./sheet"

const SIDEBAR_STORAGE_KEY = "sidebar_state"
const SIDEBAR_WIDTH = "16rem"
const SIDEBAR_WIDTH_MOBILE = "18rem"
const SIDEBAR_KEYBOARD_SHORTCUT = "b"

/** 側邊欄狀態。The sidebar state shared through context. */
interface SidebarContextProps {
  state: "expanded" | "collapsed"
  open: boolean
  setOpen: (open: boolean) => void
  openMobile: boolean
  setOpenMobile: (open: boolean) => void
  isMobile: boolean
  toggleSidebar: () => void
}

const SidebarContext = React.createContext<SidebarContextProps | null>(null)

/**
 * 取得側邊欄狀態；必須在 {@link SidebarProvider} 之內使用。
 * Reads the sidebar state; must be used within {@link SidebarProvider}.
 * @returns 側邊欄狀態。The sidebar state.
 */
function useSidebar() {
  const context = React.useContext(SidebarContext)
  if (!context) {
    throw new Error("useSidebar must be used within a SidebarProvider.")
  }
  return context
}

/** 讀出上次的展開狀態；讀不到時為 null。Reads the stored open state. */
function readStoredOpen(): boolean | null {
  try {
    const value = window.localStorage.getItem(SIDEBAR_STORAGE_KEY)
    return value === null ? null : value === "true"
  } catch {
    return null
  }
}

/**
 * 側邊欄狀態的提供者，也是側邊欄與主內容區的外框。
 * Provides the sidebar state and wraps the sidebar and the main content.
 * @param props.defaultOpen - 沒有存過狀態時是否展開。Initial open state.
 */
function SidebarProvider({
  defaultOpen = true,
  className,
  style,
  children,
  ...props
}: React.ComponentProps<"div"> & { defaultOpen?: boolean }) {
  const isMobile = useIsMobile()
  const [openMobile, setOpenMobile] = React.useState(false)
  const [open, setOpenState] = React.useState(() => readStoredOpen() ?? defaultOpen)

  const setOpen = React.useCallback((value: boolean) => {
    setOpenState(value)
    try {
      window.localStorage.setItem(SIDEBAR_STORAGE_KEY, String(value))
    } catch {
      // 存不了（例如隱私模式）就只在這次有效
    }
  }, [])

  const toggleSidebar = React.useCallback(() => {
    if (isMobile) setOpenMobile((value) => !value)
    else setOpen(!open)
  }, [isMobile, open, setOpen])

  React.useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      // 在輸入框中不攔（Ctrl+B 可能是編輯器的粗體）
      const target = event.target as HTMLElement | null
      if (target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))) return
      if (event.key === SIDEBAR_KEYBOARD_SHORTCUT && (event.metaKey || event.ctrlKey)) {
        event.preventDefault()
        toggleSidebar()
      }
    }
    window.addEventListener("keydown", handleKeyDown)
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [toggleSidebar])

  const contextValue = React.useMemo<SidebarContextProps>(
    () => ({
      state: open ? "expanded" : "collapsed",
      open,
      setOpen,
      isMobile,
      openMobile,
      setOpenMobile,
      toggleSidebar,
    }),
    [open, setOpen, isMobile, openMobile, toggleSidebar],
  )

  return (
    <SidebarContext.Provider value={contextValue}>
      <div
        data-slot="sidebar-wrapper"
        style={{ "--sidebar-width": SIDEBAR_WIDTH, ...style } as React.CSSProperties}
        className={cn("group/sidebar-wrapper flex min-h-svh w-full", className)}
        {...props}
      >
        {children}
      </div>
    </SidebarContext.Provider>
  )
}

/**
 * 側邊欄本體。桌面：佔位的 gap 與 fixed 的內容一起以 transition 收合；手機：Sheet。
 * The sidebar itself: an animated gap plus a fixed container on desktop, a sheet on mobile.
 */
function Sidebar({ className, children, ...props }: React.ComponentProps<"div">) {
  const { isMobile, state, openMobile, setOpenMobile } = useSidebar()

  if (isMobile) {
    return (
      <Sheet open={openMobile} onOpenChange={setOpenMobile}>
        <SheetContent
          data-sidebar="sidebar"
          data-slot="sidebar"
          data-mobile="true"
          className="bg-sidebar text-sidebar-foreground w-(--sidebar-width) p-0 [&>button]:hidden"
          style={{ "--sidebar-width": SIDEBAR_WIDTH_MOBILE } as React.CSSProperties}
          side="left"
        >
          <SheetHeader className="sr-only">
            <SheetTitle>選單</SheetTitle>
            <SheetDescription>網站的頁面選單。</SheetDescription>
          </SheetHeader>
          <div className="flex h-full w-full flex-col">{children}</div>
        </SheetContent>
      </Sheet>
    )
  }

  return (
    <div
      className="group peer text-sidebar-foreground hidden md:block"
      data-state={state}
      data-collapsible={state === "collapsed" ? "offcanvas" : ""}
      data-slot="sidebar"
    >
      {/* 佔位：寬度跟著收合，主內容區因此平滑地加寬 */}
      <div
        data-slot="sidebar-gap"
        className="relative w-(--sidebar-width) bg-transparent transition-[width] duration-200 ease-linear group-data-[collapsible=offcanvas]:w-0"
      />
      <div
        data-slot="sidebar-container"
        className={cn(
          "fixed inset-y-0 left-0 z-10 hidden h-svh w-(--sidebar-width) border-r transition-[left,visibility] duration-200 ease-linear md:flex",
          // 收合後設為 invisible（動畫結束才生效）：連結不能再被 Tab 到，螢幕閱讀器也不會讀到
          "group-data-[collapsible=offcanvas]:invisible group-data-[collapsible=offcanvas]:left-[calc(var(--sidebar-width)*-1)]",
          className,
        )}
        {...props}
      >
        <div data-sidebar="sidebar" data-slot="sidebar-inner" className="bg-sidebar flex h-full w-full flex-col">
          {children}
        </div>
      </div>
    </div>
  )
}

/** 切換側邊欄的按鈕。Toggles the sidebar. */
function SidebarTrigger({ className, onClick, ...props }: React.ComponentProps<typeof Button>) {
  const { toggleSidebar, open, isMobile, openMobile } = useSidebar()
  const expanded = isMobile ? openMobile : open
  return (
    <Button
      data-sidebar="trigger"
      data-slot="sidebar-trigger"
      variant="ghost"
      size="icon"
      className={cn("size-8", className)}
      onClick={(event) => {
        onClick?.(event)
        toggleSidebar()
      }}
      aria-label={expanded ? "收起選單" : "展開選單"}
      aria-expanded={expanded}
      {...props}
    >
      <PanelLeftIcon className="h-4 w-4" />
    </Button>
  )
}

/** 側邊欄右緣的細長感應區：點一下切換。A thin rail on the edge that toggles the sidebar. */
function SidebarRail({ className, ...props }: React.ComponentProps<"button">) {
  const { toggleSidebar } = useSidebar()
  return (
    <button
      type="button"
      data-sidebar="rail"
      data-slot="sidebar-rail"
      aria-label="收起選單"
      tabIndex={-1}
      onClick={toggleSidebar}
      title="收起選單（Ctrl+B）"
      className={cn(
        "hover:after:bg-sidebar-border absolute inset-y-0 -right-4 z-20 hidden w-4 cursor-w-resize transition-all ease-linear after:absolute after:inset-y-0 after:left-1/2 after:w-[2px] sm:flex",
        className,
      )}
      {...props}
    />
  )
}

/** 主內容區（側邊欄旁邊）。The main content beside the sidebar. */
function SidebarInset({ className, ...props }: React.ComponentProps<"main">) {
  return (
    <main
      data-slot="sidebar-inset"
      className={cn("bg-background relative flex min-w-0 w-full flex-1 flex-col", className)}
      {...props}
    />
  )
}

/** 側邊欄頂部。The sidebar header. */
function SidebarHeader({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="sidebar-header" data-sidebar="header" className={cn("flex flex-col gap-2 p-2", className)} {...props} />
}

/** 側邊欄可捲動的內容區。The scrollable sidebar content. */
function SidebarContent({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="sidebar-content"
      data-sidebar="content"
      className={cn("flex min-h-0 flex-1 flex-col gap-2 overflow-auto", className)}
      {...props}
    />
  )
}

/** 側邊欄中的一組。A group in the sidebar. */
function SidebarGroup({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div data-slot="sidebar-group" data-sidebar="group" className={cn("relative flex w-full min-w-0 flex-col p-2", className)} {...props} />
  )
}

/** 選單清單。The menu list. */
function SidebarMenu({ className, ...props }: React.ComponentProps<"ul">) {
  return <ul data-slot="sidebar-menu" data-sidebar="menu" className={cn("flex w-full min-w-0 flex-col gap-1", className)} {...props} />
}

/** 選單項目。A menu item. */
function SidebarMenuItem({ className, ...props }: React.ComponentProps<"li">) {
  return <li data-slot="sidebar-menu-item" data-sidebar="menu-item" className={cn("group/menu-item relative", className)} {...props} />
}

/**
 * 選單按鈕；`asChild` 時套用到子元素（例如路由連結）。
 * A menu button; with `asChild` it styles its child (e.g. a router link).
 * @param props.isActive - 是否為目前頁面。Whether it is the current page.
 */
function SidebarMenuButton({
  asChild = false,
  isActive = false,
  className,
  ...props
}: React.ComponentProps<"button"> & { asChild?: boolean; isActive?: boolean }) {
  const Comp = asChild ? Slot : "button"
  return (
    <Comp
      data-slot="sidebar-menu-button"
      data-sidebar="menu-button"
      data-active={isActive}
      className={cn(
        "flex w-full items-center gap-3 overflow-hidden rounded-lg px-3 py-2 text-left text-sm font-medium outline-hidden transition-colors",
        "text-muted-foreground hover:bg-accent hover:text-accent-foreground focus-visible:ring-2 focus-visible:ring-ring",
        "data-[active=true]:bg-primary data-[active=true]:text-primary-foreground",
        "[&>svg]:size-4 [&>svg]:shrink-0 [&>span:last-child]:truncate",
        className,
      )}
      {...props}
    />
  )
}

export {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarRail,
  SidebarTrigger,
  useSidebar,
}
