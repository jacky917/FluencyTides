import { useQuery } from '@tanstack/react-query'
import { Check, Minus } from 'lucide-react'
import { FluencyTidesAPI } from '../../api/client'
import { retryTransient } from '../../lib/apiError'
import { capabilityRows } from '../../lib/handlerCapabilities'
import { cn } from '../../lib/utils'
import type { HandlerInfo } from '../../types/api'
import { ErrorNote } from '../shared/ErrorNote'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../ui/card'
import { Skeleton } from '../ui/skeleton'

/**
 * 工具設定頁的「Handler 能力」區塊（handlers 計畫書 §6）：唯讀，資料來自 `GET /handlers`。
 * 每個 handler 一張卡片，列出建立、讀取、改欄位、刪除、圖譜、動作是否開放與原因，用來一眼看出
 * 目前開放到哪裡。沒有 handler 的筆記類型不在這裡。
 *
 * The read-only "handler capabilities" section of the settings page.
 * @returns 區塊 JSX。Section JSX.
 */
export function HandlerCapabilitiesSection() {
  const { data, isPending, error } = useQuery({
    queryKey: ['handlers'],
    queryFn: FluencyTidesAPI.listHandlers,
    retry: retryTransient,
  })

  return (
    <section className="space-y-3" aria-labelledby="handler-capabilities-title">
      <div>
        <h3 id="handler-capabilities-title" className="text-xl font-semibold tracking-tight">
          Handler 能力
        </h3>
        <p className="text-sm text-muted-foreground mt-1">
          每種卡片（handler）在網頁與 TG 能做什麼；不開放的項目附上原因。唯讀，由後端宣告。
        </p>
      </div>
      {isPending ? (
        <div className="grid gap-3 md:grid-cols-2">
          <Skeleton className="h-48 w-full" />
          <Skeleton className="h-48 w-full" />
        </div>
      ) : data ? (
        <div className="grid gap-3 md:grid-cols-2">
          {data.map((handler) => (
            <HandlerCard key={handler.name} handler={handler} />
          ))}
        </div>
      ) : (
        <ErrorNote title="無法載入 handler 能力" error={error} />
      )}
    </section>
  )
}

/**
 * 一個 handler 的能力卡片。One handler's capability card.
 * @param props.handler - handler 資訊。The handler.
 * @returns 卡片 JSX。Card JSX.
 */
function HandlerCard({ handler }: { handler: HandlerInfo }) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">
          {handler.title} <span className="font-mono text-xs font-normal text-muted-foreground">{handler.name}</span>
        </CardTitle>
        <CardDescription className="break-all">{handler.note_types.join('、')}</CardDescription>
      </CardHeader>
      <CardContent>
        <dl className="space-y-2 text-sm">
          {capabilityRows(handler).map((row) => (
            <div key={row.label} className="flex gap-2">
              <dt className="flex w-20 shrink-0 items-center gap-1 font-medium">
                {row.supported ? (
                  <Check className="h-4 w-4 text-emerald-600" aria-label="開放" />
                ) : (
                  <Minus className="h-4 w-4 text-muted-foreground" aria-label="不開放" />
                )}
                {row.label}
              </dt>
              <dd className={cn('min-w-0 break-words', !row.supported && 'text-muted-foreground')}>
                {row.detail ?? (row.supported ? '開放' : '不開放')}
              </dd>
            </div>
          ))}
        </dl>
      </CardContent>
    </Card>
  )
}
