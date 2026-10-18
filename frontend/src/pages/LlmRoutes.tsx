/**
 * LLM 模型設定頁面。
 * LLM model settings page.
 *
 * 網頁版的 TG `/models`：列出每個用途（含全域 `*`）的生效設定，可為某個用途選
 * 端點 → 模型 →（有 effort 時）effort 並儲存，或恢復預設。選項只來自後端回傳的
 * `.env` 端點白名單（`GET /api/v1/llm-routes` 的 `endpoints`）。
 *
 * The web counterpart of TG `/models`: lists each route key's effective
 * setting and lets the user pick endpoint → model → (effort) and save, or
 * restore the default. Options come only from the backend's whitelist.
 */

import { useState } from 'react'
import { useIsMutating, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, RefreshCw } from 'lucide-react'
import { toast } from 'sonner'
import { FluencyTidesAPI } from '../api/client'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../components/ui/card'
import { Button } from '../components/ui/button'
import { Skeleton } from '../components/ui/skeleton'
import { cn } from '../lib/utils'
import {
  GLOBAL_ROUTE_KEY,
  SOURCE_LABELS,
  defaultEffort,
  describeApiError,
  failureTitle,
  formatChoice,
  initialSelection,
  isBackendError,
  previewLabel,
} from '../lib/llmRoutes'
import type { LLMEndpointOption, LLMRoute, LLMRouteChoice } from '../types/api'

const QUERY_KEY = ['llm-routes']

const SELECT_CLASS =
  'flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50'

/** 寫入後顯示在該列的結果。The outcome shown on a row after a write. */
interface Notice {
  kind: 'success' | 'error'
  title: string
  lines: string[]
}

/**
 * 一列的寫入（PUT / DELETE）共用的 mutation key，讓同一列同時只能有一個寫入。
 * Mutation key shared by a row's writes so only one runs at a time.
 * @param routeKey - 用途鍵。Route key.
 * @returns mutation key。The mutation key.
 */
const writeKey = (routeKey: string) => ['llm-route-write', routeKey]

/**
 * 寫入失敗的共用處理：非業務錯誤留在 console，結果顯示在該列並跳出 toast。
 * Shared write-failure handling: log non-business errors, show the outcome.
 * @param error - 捕捉到的錯誤。The caught error.
 * @param action - 動作名稱。Action name.
 * @param routeKey - 用途鍵。Route key.
 * @param onNotice - 顯示在該列。Shows the outcome on the row.
 */
function reportWriteFailure(
  error: unknown,
  action: string,
  routeKey: string,
  onNotice: (notice: Notice) => void,
) {
  if (!isBackendError(error)) {
    console.error(`[LlmRoutes] ${action} ${routeKey} 失敗`, error)
  }
  const { status, message } = describeApiError(error)
  const title = failureTitle(status, action)
  onNotice({ kind: 'error', title, lines: [message] })
  toast.error(title, { description: message })
}

/**
 * toast 的多行說明（sonner 預設不保留換行）。Multi-line toast description.
 * @param lines - 各行。Lines.
 * @returns 保留換行的說明。A description that keeps line breaks.
 */
const toastLines = (lines: string[]) => <span className="whitespace-pre-line">{lines.join('\n')}</span>

/**
 * 寫入後一列狀態的說明（生效設定與警示），與 TG 結果畫面相同。
 * Lines describing a route after a write, as on the TG result screen.
 * @param route - 寫入後的狀態。The state afterwards.
 * @returns 說明文字。The lines.
 */
function describeAfterWrite(route: LLMRoute): string[] {
  const lines = [`${route.route_key}：${route.label}（來源：${SOURCE_LABELS[route.source]}）`]
  if (route.db_unavailable) {
    lines.push('讀不到資料庫，無法確認退回後的設定，上面顯示的是 .env 預設。')
  }
  if (route.problem) {
    lines.push(route.problem)
  }
  return lines
}

/**
 * LLM 模型設定頁面。
 * LLM model settings page.
 * @returns 頁面 JSX。Page JSX.
 */
export default function LlmRoutes() {
  const { data, isPending, isError, error, fetchStatus, refetch, isFetching } = useQuery({
    queryKey: QUERY_KEY,
    queryFn: FluencyTidesAPI.listLLMRoutes,
  })
  const [editingKey, setEditingKey] = useState<string | null>(null)
  const [notices, setNotices] = useState<Record<string, Notice>>({})

  const setNotice = (key: string, notice: Notice | null) =>
    setNotices((prev) => {
      const next = { ...prev }
      if (notice) next[key] = notice
      else delete next[key]
      return next
    })

  const readOnly = data?.db_unavailable ?? true

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-3xl font-bold tracking-tight">LLM 模型設定</h2>
          <p className="text-muted-foreground mt-2">
            為每個用途選擇使用的模型，下一次生成即生效、重啟後仍在。來源：<b>用途</b>＝該用途自己的設定、
            <b>全域</b>＝<code>*</code> 的設定、<b>.env</b>＝部署預設。
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => refetch()} disabled={isFetching}>
          <RefreshCw className={cn('h-4 w-4 mr-2', isFetching && 'animate-spin')} />
          重新整理
        </Button>
      </div>

      {isPending && fetchStatus === 'paused' ? (
        <Warning title="目前離線" lines={['恢復網路後會自動載入，或按右上角「重新整理」。']} />
      ) : isPending ? (
        <div className="space-y-3">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : !data ? (
        <Warning
          title="無法載入 LLM 用途設定"
          lines={[describeApiError(error).message, '請按右上角「重新整理」重試；持續失敗請查看後端 log。']}
        />
      ) : (
        <>
          {/* 背景重新整理失敗時保留上次的資料（與編輯中的選擇），只加一條警示 */}
          {isError && (
            <Warning title="重新整理失敗，以下為上次載入的資料" lines={[describeApiError(error).message]} />
          )}
          {data.db_unavailable && (
            <Warning
              title="目前讀不到資料庫"
              lines={['以下皆為 .env 預設，實際存的設定未知，暫時無法修改（詳見後端 log）。']}
            />
          )}
          <div className="space-y-3">
            {data.routes.map((route) => (
              <RouteRow
                key={route.route_key}
                route={route}
                endpoints={data.endpoints}
                readOnly={readOnly}
                editing={editingKey === route.route_key}
                onEdit={(editing) => setEditingKey(editing ? route.route_key : null)}
                notice={notices[route.route_key]}
                onNotice={(notice) => setNotice(route.route_key, notice)}
              />
            ))}
          </div>
        </>
      )}
    </div>
  )
}

/**
 * 醒目的警示框。A prominent warning box.
 * @param props.title - 標題。Title.
 * @param props.lines - 說明。Lines.
 * @returns 警示框 JSX。Warning JSX.
 */
function Warning({ title, lines }: { title: string; lines: string[] }) {
  return (
    <div role="alert" className="flex gap-3 rounded-lg border border-amber-500/50 bg-amber-500/10 p-4 text-sm">
      <AlertTriangle className="h-5 w-5 shrink-0 text-amber-600" />
      <div className="space-y-1">
        <p className="font-semibold text-amber-800">{title}</p>
        {lines.map((line, index) => (
          <p key={index} className="text-amber-900 break-words">
            {line}
          </p>
        ))}
      </div>
    </div>
  )
}

/** {@link RouteRow} 的 props。Props of {@link RouteRow}. */
interface RouteRowProps {
  route: LLMRoute
  endpoints: LLMEndpointOption[]
  readOnly: boolean
  editing: boolean
  onEdit: (editing: boolean) => void
  notice: Notice | undefined
  onNotice: (notice: Notice | null) => void
}

/**
 * 一個用途的列：狀態、警示、設定與恢復預設。
 * One route's row: state, warnings, edit and restore actions.
 * @returns 列 JSX。Row JSX.
 */
function RouteRow({ route, endpoints, readOnly, editing, onEdit, notice, onNotice }: RouteRowProps) {
  const queryClient = useQueryClient()
  const isGlobal = route.route_key === GLOBAL_ROUTE_KEY
  const flagged = !route.declared || route.rejected.length > 0 || route.problem !== null

  const deleteMutation = useMutation({
    mutationKey: writeKey(route.route_key),
    mutationFn: () => FluencyTidesAPI.deleteLLMRoute(route.route_key),
    onSuccess: (result) => {
      const title = result.deleted ? '已恢復預設' : '這個用途本來就沒有設定'
      let lines: string[]
      if (result.route) {
        lines = describeAfterWrite(result.route)
      } else if (result.route_unavailable) {
        lines = ['無法顯示目前生效的設定（詳見後端 log），請重新整理查看。']
      } else {
        lines = [`${route.route_key}：孤兒設定${result.deleted ? '已刪除' : '已不存在'}。`]
      }
      onNotice({ kind: 'success', title, lines })
      toast.success(title, { description: toastLines(lines) })
    },
    onError: (error) => reportWriteFailure(error, '恢復預設', route.route_key, onNotice),
    onSettled: () => queryClient.invalidateQueries({ queryKey: QUERY_KEY }),
  })

  // 儲存（編輯區）與恢復預設共用同一個 key：任一進行中時兩邊都停用
  const busy = useIsMutating({ mutationKey: writeKey(route.route_key) }) > 0

  return (
    <Card className={cn(flagged && 'border-amber-500/60')}>
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1 min-w-0">
            <CardTitle className="text-base flex items-center gap-2">
              {flagged && <AlertTriangle className="h-4 w-4 text-amber-600" aria-label="警示" />}
              <code>{route.route_key}</code>
              {isGlobal && <span className="text-xs font-normal text-muted-foreground">全域</span>}
            </CardTitle>
            <CardDescription>{route.description ?? '（程式中已沒有這個用途）'}</CardDescription>
          </div>
          <div className="flex gap-2">
            {route.declared && !readOnly && (
              <Button
                size="sm"
                variant={editing ? 'secondary' : 'default'}
                onClick={() => {
                  onNotice(null)
                  onEdit(!editing)
                }}
                disabled={busy}
              >
                {editing ? '取消' : '設定'}
              </Button>
            )}
            {route.stored !== null && !readOnly && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  onNotice(null)
                  deleteMutation.mutate()
                }}
                disabled={busy}
              >
                {deleteMutation.isPending ? '處理中…' : '恢復預設'}
              </Button>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-muted-foreground">目前生效：</span>
          <code className="rounded bg-muted px-2 py-0.5">{route.label}</code>
          <SourceBadge source={route.source} />
        </div>
        {route.stored !== null && (
          <div className="text-muted-foreground">
            本用途的設定：<code>{formatChoice(route.stored)}</code>
          </div>
        )}

        {!route.declared && (
          <Warning title="孤兒設定" lines={['程式中已沒有這個用途，只能恢復預設以刪除。']} />
        )}
        {route.problem && <Warning title="目前生效的設定無法使用" lines={[route.problem]} />}
        {route.rejected.length > 0 && (
          <Warning
            title="解析時略過了設定"
            lines={route.rejected.map((layer) => `已略過 ${layer.route_key}：${layer.reason}`)}
          />
        )}

        {notice && <NoticeBox notice={notice} />}

        {/* 寫入失敗後重新整理可能變成唯讀（讀不到資料庫），編輯區要跟著收起 */}
        {editing && !readOnly && (
          <RouteEditor
            route={route}
            endpoints={endpoints}
            busy={busy}
            onCancel={() => onEdit(false)}
            onSubmit={() => onNotice(null)}
            onSaved={(saved) => {
              onEdit(false)
              onNotice({ kind: 'success', title: '已儲存', lines: describeAfterWrite(saved) })
            }}
            onFailed={onNotice}
          />
        )}
      </CardContent>
    </Card>
  )
}

/**
 * 來源標記。Source badge.
 * @param props.source - 生效的層。The winning layer.
 * @returns 標記 JSX。Badge JSX.
 */
function SourceBadge({ source }: { source: LLMRoute['source'] }) {
  return (
    <span
      className={cn(
        'rounded-full px-2 py-0.5 text-xs font-medium',
        source === 'route' && 'bg-primary text-primary-foreground',
        source === 'global' && 'bg-secondary text-secondary-foreground border',
        source === 'env' && 'bg-muted text-muted-foreground border',
      )}
    >
      來源：{SOURCE_LABELS[source]}
    </span>
  )
}

/**
 * 寫入結果框。Write-outcome box.
 * @param props.notice - 結果。The outcome.
 * @returns 結果框 JSX。Box JSX.
 */
function NoticeBox({ notice }: { notice: Notice }) {
  return (
    <div
      role="status"
      className={cn(
        'rounded-lg border p-3',
        notice.kind === 'success' ? 'border-green-500/50 bg-green-500/10' : 'border-destructive/50 bg-destructive/10',
      )}
    >
      <p className={cn('font-semibold', notice.kind === 'success' ? 'text-green-700' : 'text-destructive')}>
        {notice.title}
      </p>
      {notice.lines.map((line, index) => (
        <p key={index} className="break-words">
          {line}
        </p>
      ))}
    </div>
  )
}

/** {@link RouteEditor} 的 props。Props of {@link RouteEditor}. */
interface RouteEditorProps {
  route: LLMRoute
  endpoints: LLMEndpointOption[]
  /** 這一列有寫入進行中。A write for this row is in flight. */
  busy: boolean
  onCancel: () => void
  /** 送出前（清掉上一次的結果）。Called before submitting. */
  onSubmit: () => void
  onSaved: (route: LLMRoute) => void
  /** 寫入失敗；結果顯示在該列，編輯區收起後仍看得到。Write failed; shown on the row. */
  onFailed: (notice: Notice) => void
}

/**
 * 端點 → 模型 → effort 的三層選擇與儲存。
 * The endpoint → model → effort picker and save action.
 *
 * 選項只來自白名單；若本用途已有設定且仍在白名單內則預先帶入。模型不使用 effort 時不顯示
 * effort，只有一個 effort 時自動選上。
 * Options come only from the whitelist; an existing stored choice is
 * pre-filled when still whitelisted. The effort picker is hidden when the
 * model takes none, and a single effort is selected automatically.
 *
 * @returns 編輯區 JSX。Editor JSX.
 */
function RouteEditor({ route, endpoints, busy, onCancel, onSubmit, onSaved, onFailed }: RouteEditorProps) {
  const queryClient = useQueryClient()
  const initial = initialSelection(route.stored, endpoints)
  const [endpointId, setEndpointId] = useState(initial.endpoint)
  const [modelName, setModelName] = useState(initial.model)
  const [effort, setEffort] = useState(initial.effort)

  const endpoint = endpoints.find((item) => item.id === endpointId)
  const model = endpoint?.models.find((item) => item.name === modelName)
  const needsEffort = (model?.efforts.length ?? 0) > 0
  const choice: LLMRouteChoice | null =
    endpoint && model && (!needsEffort || model.efforts.includes(effort))
      ? { endpoint: endpoint.id, model: model.name, effort: needsEffort ? effort : null }
      : null

  const selectModel = (name: string, from: LLMEndpointOption | undefined) => {
    setModelName(name)
    setEffort(defaultEffort(from?.models.find((item) => item.name === name)?.efforts ?? []))
  }

  const saveMutation = useMutation({
    mutationKey: writeKey(route.route_key),
    mutationFn: (value: LLMRouteChoice) => FluencyTidesAPI.putLLMRoute(route.route_key, value),
    onSuccess: (saved) => {
      toast.success('已儲存', { description: `${saved.route_key}：${saved.label}` })
      onSaved(saved)
    },
    onError: (err) => reportWriteFailure(err, '儲存', route.route_key, onFailed),
    onSettled: () => queryClient.invalidateQueries({ queryKey: QUERY_KEY }),
  })

  if (endpoints.length === 0) {
    return (
      <Warning
        title="沒有可選的端點"
        lines={['.env 的白名單都是空的（LLM_ENDPOINTS__<ID>__MODELS、LLM_CLAUDE_CODE_MODELS…）。']}
      />
    )
  }

  return (
    <form
      className="space-y-4 rounded-lg border bg-muted/30 p-4"
      onSubmit={(e) => {
        e.preventDefault()
        if (choice && !busy) {
          onSubmit()
          saveMutation.mutate(choice)
        }
      }}
    >
      <div className={cn('grid gap-4', needsEffort ? 'md:grid-cols-3' : 'md:grid-cols-2')}>
        <div className="space-y-2">
          <label className="text-sm font-medium" htmlFor={`endpoint-${route.route_key}`}>
            端點
          </label>
          <select
            id={`endpoint-${route.route_key}`}
            className={SELECT_CLASS}
            value={endpointId}
            onChange={(e) => {
              const next = endpoints.find((item) => item.id === e.target.value)
              setEndpointId(e.target.value)
              // 只有一個模型時直接帶入，與 effort 的處理一致
              selectModel(next?.models.length === 1 ? next.models[0].name : '', next)
            }}
          >
            <option value="" disabled>
              選擇端點
            </option>
            {endpoints.map((item) => (
              <option key={item.id} value={item.id}>
                {item.id}（{item.type}）
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-2">
          <label className="text-sm font-medium" htmlFor={`model-${route.route_key}`}>
            模型
          </label>
          <select
            id={`model-${route.route_key}`}
            className={SELECT_CLASS}
            value={modelName}
            disabled={!endpoint}
            onChange={(e) => selectModel(e.target.value, endpoint)}
          >
            <option value="" disabled>
              選擇模型
            </option>
            {endpoint?.models.map((item) => (
              <option key={item.name} value={item.name}>
                {item.name}
              </option>
            ))}
          </select>
        </div>
        {needsEffort && model && (
          <div className="space-y-2">
            <label className="text-sm font-medium" htmlFor={`effort-${route.route_key}`}>
              思考深度（effort）
            </label>
            <select
              id={`effort-${route.route_key}`}
              className={SELECT_CLASS}
              value={effort}
              onChange={(e) => setEffort(e.target.value)}
            >
              <option value="" disabled>
                選擇 effort
              </option>
              {model.efforts.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      {choice && endpoint && (
        <div className="space-y-1">
          <p>
            <b>{route.route_key}</b> 改用：<code className="rounded bg-background px-2 py-0.5 border">
              {previewLabel(endpoint, choice.model, choice.effort)}
            </code>
            <span className="text-muted-foreground">（將寫進卡片的標籤）</span>
          </p>
          <p className="text-muted-foreground">
            儲存時會先試建一次客戶端（檢查 CLI、金鑰），之後的生成立即生效、重啟後仍在。
          </p>
        </div>
      )}

      <div className="flex gap-2">
        <Button type="submit" disabled={!choice || busy}>
          {saveMutation.isPending ? '儲存中…' : '儲存'}
        </Button>
        <Button type="button" variant="ghost" onClick={onCancel} disabled={saveMutation.isPending}>
          取消
        </Button>
      </div>
    </form>
  )
}
