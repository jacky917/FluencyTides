import { Routes, Route, Link, useLocation } from 'react-router-dom'
import { LayoutDashboard, PlusCircle, Network } from 'lucide-react'
import { cn } from './lib/utils'
import { Toaster } from './components/ui/sonner'

// Pages (will create these next)
import Dashboard from './pages/Dashboard'
import CardGenerator from './pages/CardGenerator'
import KnowledgeGraph from './pages/KnowledgeGraph'

function App() {
  const location = useLocation()

  const navItems = [
    { name: 'Dashboard', path: '/', icon: LayoutDashboard },
    { name: 'Card Generator', path: '/generate', icon: PlusCircle },
    { name: 'Knowledge Graph', path: '/graph', icon: Network },
  ]

  return (
    <div className="flex min-h-screen bg-background">
      {/* Sidebar */}
      <aside className="w-64 border-r bg-card hidden md:block">
        <div className="p-6">
          <h1 className="text-2xl font-bold tracking-tight text-foreground">
            FluencyTides
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Intelligent Anki
          </p>
        </div>
        <nav className="space-y-1 px-3 mt-4">
          {navItems.map((item) => {
            const isActive = location.pathname === item.path
            return (
              <Link
                key={item.path}
                to={item.path}
                className={cn(
                  "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                )}
              >
                <item.icon className="h-4 w-4" />
                {item.name}
              </Link>
            )
          })}
        </nav>
      </aside>

      {/* Main Content */}
      <main className="flex-1 flex flex-col min-h-screen overflow-hidden">
        <header className="h-14 border-b flex items-center px-6 md:hidden">
          <h1 className="font-bold">FluencyTides</h1>
        </header>
        <div className="flex-1 overflow-auto p-6">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/generate" element={<CardGenerator />} />
            <Route path="/graph" element={<KnowledgeGraph />} />
          </Routes>
        </div>
      </main>

      {/* Global Toast Provider */}
      <Toaster />
    </div>
  )
}

export default App

