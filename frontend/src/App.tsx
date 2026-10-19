import { Routes, Route, Link, Navigate, useLocation } from 'react-router-dom'
import { LayoutDashboard, PlusCircle, Network, Bot, Layers, SlidersHorizontal } from 'lucide-react'
import { Toaster } from './components/ui/sonner'
import {
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
} from './components/ui/sidebar'

// Pages (will create these next)
import Dashboard from './pages/Dashboard'
import CardGenerator from './pages/CardGenerator'
import KnowledgeGraph from './pages/KnowledgeGraph'
import ToolSettings from './pages/ToolSettings'
import Cards from './pages/Cards'
import Preferences from './pages/Preferences'

const navItems = [
  { name: 'Dashboard', path: '/', icon: LayoutDashboard },
  { name: 'Cards', path: '/cards', icon: Layers },
  { name: 'Card Generator', path: '/generate', icon: PlusCircle },
  { name: 'Knowledge Graph', path: '/graph', icon: Network },
  { name: 'Tool Settings', path: '/tools', icon: Bot },
  { name: 'Preferences', path: '/preferences', icon: SlidersHorizontal },
]

/**
 * 左側的頁面選單（可收合；手機上是滑出的 Sheet）。
 * The page menu on the left (collapsible; a sliding sheet on mobile).
 * @returns 側邊欄 JSX。Sidebar JSX.
 */
function AppSidebar() {
  const location = useLocation()
  const { isMobile, setOpenMobile } = useSidebar()

  return (
    <Sidebar>
      <SidebarHeader className="p-6">
        <h1 className="text-2xl font-bold tracking-tight text-foreground">FluencyTides</h1>
        <p className="text-sm text-muted-foreground">Intelligent Anki</p>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup className="px-3">
          <SidebarMenu>
            {navItems.map((item) => (
              <SidebarMenuItem key={item.path}>
                <SidebarMenuButton asChild isActive={location.pathname === item.path}>
                  {/* 手機上選了頁面就收起選單 */}
                  {/* 已經在這頁時連到目前的網址：不會清掉這頁記住的位置（例如卡片瀏覽的牌組與卡片） */}
                  <Link
                    to={location.pathname === item.path ? location.pathname + location.search : item.path}
                    onClick={() => isMobile && setOpenMobile(false)}
                  >
                    <item.icon />
                    <span>{item.name}</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
            ))}
          </SidebarMenu>
        </SidebarGroup>
      </SidebarContent>
      <SidebarRail />
    </Sidebar>
  )
}

/**
 * 應用程式根元件：提供側邊欄導覽、路由切換與全域 Toast。
 * Application root component: provides sidebar navigation, routing, and global toasts.
 * @returns 應用程式版面 JSX。Application layout JSX.
 */
function App() {
  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset className="min-h-screen">
        <header className="sticky top-0 z-20 flex h-12 shrink-0 items-center gap-2 border-b bg-background/95 px-4 backdrop-blur">
          <SidebarTrigger title="展開／收起選單（Ctrl+B）" />
          <span className="font-bold md:hidden">FluencyTides</span>
        </header>
        <div className="flex-1 p-4 md:p-6">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/cards" element={<Cards />} />
            <Route path="/generate" element={<CardGenerator />} />
            <Route path="/graph" element={<KnowledgeGraph />} />
            <Route path="/tools" element={<ToolSettings />} />
            <Route path="/preferences" element={<Preferences />} />
            {/* 舊網址（LLM 模型設定頁）。The former LLM settings URL. */}
            <Route path="/llm-models" element={<Navigate to="/tools" replace />} />
          </Routes>
        </div>
      </SidebarInset>

      {/* Global Toast Provider */}
      <Toaster />
    </SidebarProvider>
  )
}

export default App
