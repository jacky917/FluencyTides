import { useQuery } from '@tanstack/react-query'
import { FluencyTidesAPI } from '../api/client'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../components/ui/card'
import { Skeleton } from '../components/ui/skeleton'

export default function Dashboard() {
  const { data: health, isLoading, isError } = useQuery({
    queryKey: ['health'],
    queryFn: FluencyTidesAPI.checkHealth,
    retry: 1,
  })

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-3xl font-bold tracking-tight">Dashboard</h2>
        <p className="text-muted-foreground mt-2">
          Overview of your FluencyTides system status.
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>System Health</CardTitle>
            <CardDescription>Backend API Connection</CardDescription>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <Skeleton className="h-8 w-24" />
            ) : isError ? (
              <div className="flex items-center gap-2 text-destructive">
                <div className="w-3 h-3 rounded-full bg-destructive animate-pulse" />
                <span className="font-semibold uppercase">Offline</span>
              </div>
            ) : (
              <div className="flex items-center gap-2 text-green-500">
                <div className="w-3 h-3 rounded-full bg-green-500" />
                <span className="font-semibold uppercase tracking-wider">{health?.status || 'OK'}</span>
              </div>
            )}
          </CardContent>
        </Card>

        {/* More cards can be added here for stats */}
      </div>
    </div>
  )
}
