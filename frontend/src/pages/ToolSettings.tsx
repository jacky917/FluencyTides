/**
 * 工具設定頁面（能力路由）。
 * Tool settings page (capability routes).
 *
 * 網頁版的 TG `/setconfig`：列出每個用途鍵（含全域 `*`）在各能力上的生效設定（文字 LLM、錄音評分
 * 策略、聽音檔的模型、語音轉文字），可為某個用途的某個能力選 provider →（需要時）模型 →（有 effort
 * 時）effort 並儲存，或恢復預設。選項只來自後端回傳的白名單（`GET /api/v1/capability-routes` 的
 * `providers`；評分策略另依 `strategies` 的限縮過濾）。分支鍵（`<流程>.<分支>`）縮排在流程鍵之下。
 *
 * The web counterpart of TG `/setconfig`: each route key's per-capability
 * setting, with a provider → model → (effort) picker and a restore action.
 * Options come only from the backend's whitelist; branch keys are indented
 * under their flow key.
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
import { describeApiError, failureTitle, isBackendError } from '../lib/apiError'
import {
  CAPABILITY_AUDIO_EVAL,
  CAPABILITY_LLM,
  GLOBAL_ROUTE_KEY,
  SOURCE_LABELS,
  buildChoice,
  capabilitiesInUse,
  capabilityLabel,
  defaultEffort,
  emptyProviderHint,
  formatChoice,
  initialSelection,
  parentKey,
  previewLabel,
  providerLabel,
  providersFor,
} from '../lib/capabilityRoutes'
import type { CapabilityRoute, CapabilityState, ProviderOption, RouteUpdate } from '../types/api'

const QUERY_KEY = ['capability-routes']

const SELECT_CLASS =
  'flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50'

/** 寫入後顯示在該能力下的結果。The outcome shown under a capability after a write. */
interface Notice {
  kind: 'success' | 'error'
  title: string
  lines: string[]
}

/** 一個「用途鍵 × 能力」的識別字串。Identifies one key and capability. */
const slotId = (routeKey: string, capability: string) => `${routeKey}\u0000${capability}`

/**
 * 同一個「用途鍵 × 能力」的寫入（PUT / DELETE）共用的 mutation key，讓它同時只能有一個寫入。
 * Mutation key shared by a slot's writes so only one runs at a time.
 * @param routeKey - 用途鍵。Route key.
 * @param capability - 能力。Capability.
 * @returns mutation key。The mutation key.
 */
const writeKey = (routeKey: string, capability: string) => ['capability-route-write', routeKey, capability]

/**
 * 寫入失敗的共用處理：非業務錯誤留在 console，結果顯示在該能力下並跳出 toast。
 * Shared write-failure handling: log non-business errors, show the outcome.
 * @param error - 捕捉到的錯誤。The caught error.
 * @param action - 動作名稱。Action name.
 * @param target - 用途鍵與能力（只用於 log）。Key and capability, for logging.
 * @param onNotice - 顯示結果。Shows the outcome.
 */
function reportWriteFailure(error: unknown, action: string, target: string, onNotice: (notice: Notice) => void) {
  if (!isBackendError(error)) {
    console.error(`[ToolSettings] ${action} ${target} 失敗`, error)
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
 * 寫入後一個能力狀態的說明（生效設定與警示），與 TG 結果畫面相同。
 * Lines describing a capability after a write, as on the TG result screen.
 * @param routeKey - 用途鍵。Route key.
 * @param state - 寫入後的狀態。The state afterwards.
 * @returns 說明文字。The lines.
 */
function describeAfterWrite(routeKey: string, state: CapabilityState): string[] {
  const lines = [`${routeKey}：${state.label}（來源：${SOURCE_LABELS[state.source]}）`]
  if (state.db_unavailable) {
    lines.push('讀不到資料庫，無法確認退回後的設定，上面顯示的是 .env 預設。')
  }
  if (state.problem) {
    lines.push(state.problem)
  }
  return lines
}

/**
 * 工具設定頁面。Tool settings page.
 * @returns 頁面 JSX。Page JSX.
 */
export default function ToolSettings() {
  const { data, isPending, isError, error, fetchStatus, refetch, isFetching } = useQuery({
    queryKey: QUERY_KEY,
    queryFn: FluencyTidesAPI.listCapabilityRoutes,
  })
  const [editingSlot, setEditingSlot] = useState<string | null>(null)
  const [notices, setNotices] = useState<Record<string, Notice>>({})

  const setNotice = (slot: string, notice: Notice | null) =>
    setNotices((prev) => {
      const next = { ...prev }
      if (notice) next[slot] = notice
      else delete next[slot]
      return next
    })

  const readOnly = data?.db_unavailable ?? true
  const listedKeys = new Set(data?.routes.map((route) => route.route_key) ?? [])

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-3xl font-bold tracking-tight">工具設定</h2>
          <p className="text-muted-foreground mt-2">
            為每個用途選擇使用的工具與模型（文字 LLM、錄音評分策略、聽音檔的模型、語音轉文字），下一次使用即生效、
            重啟後仍在。來源：<b>用途</b>＝該用途自己的設定、<b>流程</b>＝分支沿用流程鍵的設定、<b>全域</b>＝
            <code>*</code> 的設定、<b>.env</b>＝部署預設。
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
          title="無法載入工具設定"
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
              <RouteCard
                key={route.route_key}
                route={route}
                isBranch={parentKey(route.route_key) !== null && listedKeys.has(parentKey(route.route_key) ?? '')}
                providers={data.providers}
                strategies={data.strategies ?? {}}
                readOnly={readOnly}
                editingSlot={editingSlot}
                onEdit={(slot) => setEditingSlot(slot)}
                notices={notices}
                onNotice={setNotice}
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

/** {@link RouteCard} 的 props。Props of {@link RouteCard}. */
interface RouteCardProps {
  route: CapabilityRoute
  /** 分支鍵（流程鍵也在清單中）：縮排顯示。A branch whose flow key is listed. */
  isBranch: boolean
  providers: Record<string, ProviderOption[]>
  strategies: Record<string, string[]>
  readOnly: boolean
  editingSlot: string | null
  onEdit: (slot: string | null) => void
  notices: Record<string, Notice>
  onNotice: (slot: string, notice: Notice | null) => void
}

/**
 * 一個用途鍵的卡片：鍵、說明，以及它各能力的設定。
 * One route key's card: the key, its description, and one panel per capability.
 * @returns 卡片 JSX。Card JSX.
 */
function RouteCard({
  route,
  isBranch,
  providers,
  strategies,
  readOnly,
  editingSlot,
  onEdit,
  notices,
  onNotice,
}: RouteCardProps) {
  const isGlobal = route.route_key === GLOBAL_ROUTE_KEY
  const states = Object.entries(route.capabilities)
  // 只標出會用到的能力的問題（例如策略是 listen 時，STT 沒設定不影響評分）；各能力自己的警示照常顯示
  const inUse = capabilitiesInUse(route)
  const flagged =
    !route.declared ||
    states.some(
      ([capability, state]) =>
        inUse.has(capability) && (!state.declared || state.rejected.length > 0 || state.problem !== null),
    )

  return (
    <Card className={cn(flagged && 'border-amber-500/60', isBranch && 'ml-6 sm:ml-10')}>
      <CardHeader className="pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          {flagged && <AlertTriangle className="h-4 w-4 text-amber-600" aria-label="警示" />}
          {isBranch && <span className="text-muted-foreground" aria-hidden>└</span>}
          <code>{route.route_key}</code>
          {isGlobal && <span className="text-xs font-normal text-muted-foreground">全域</span>}
          {isBranch && <span className="text-xs font-normal text-muted-foreground">分支，沒設定的能力沿用流程鍵</span>}
        </CardTitle>
        <CardDescription>{route.description ?? '（程式中已沒有這個用途）'}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        {!route.declared && <Warning title="孤兒設定" lines={['程式中已沒有這個用途，只能恢復預設以刪除。']} />}
        {states.map(([capability, state]) => {
          const slot = slotId(route.route_key, capability)
          return (
            <CapabilityPanel
              key={capability}
              routeKey={route.route_key}
              declared={route.declared && state.declared}
              capability={capability}
              state={state}
              providers={providersFor(capability, route.route_key, providers, strategies)}
              readOnly={readOnly}
              editing={editingSlot === slot}
              onEdit={(editing) => onEdit(editing ? slot : null)}
              notice={notices[slot]}
              onNotice={(notice) => onNotice(slot, notice)}
            />
          )
        })}
      </CardContent>
    </Card>
  )
}

/** {@link CapabilityPanel} 的 props。Props of {@link CapabilityPanel}. */
interface CapabilityPanelProps {
  routeKey: string
  declared: boolean
  capability: string
  state: CapabilityState
  providers: ProviderOption[]
  readOnly: boolean
  editing: boolean
  onEdit: (editing: boolean) => void
  notice: Notice | undefined
  onNotice: (notice: Notice | null) => void
}

/**
 * 一個用途鍵在一種能力上的設定：狀態、警示、設定與恢復預設。
 * One key's setting for one capability: state, warnings, edit and restore.
 * @returns 面板 JSX。Panel JSX.
 */
function CapabilityPanel({
  routeKey,
  declared,
  capability,
  state,
  providers,
  readOnly,
  editing,
  onEdit,
  notice,
  onNotice,
}: CapabilityPanelProps) {
  const queryClient = useQueryClient()
  const target = `${routeKey} / ${capability}`

  const deleteMutation = useMutation({
    mutationKey: writeKey(routeKey, capability),
    mutationFn: () => FluencyTidesAPI.deleteCapabilityRoute(routeKey, capability),
    onSuccess: (result) => {
      const title = result.deleted ? '已恢復預設' : '這個用途本來就沒有設定'
      const after = result.route?.capabilities[capability]
      let lines: string[]
      if (after) {
        lines = describeAfterWrite(routeKey, after)
      } else if (result.route_unavailable) {
        lines = ['無法顯示目前生效的設定（詳見後端 log），請重新整理查看。']
      } else {
        lines = [`${target}：孤兒設定${result.deleted ? '已刪除' : '已不存在'}。`]
      }
      onNotice({ kind: 'success', title, lines })
      toast.success(title, { description: toastLines(lines) })
    },
    onError: (error) => reportWriteFailure(error, '恢復預設', target, onNotice),
    onSettled: () => queryClient.invalidateQueries({ queryKey: QUERY_KEY }),
  })

  // 儲存（編輯區）與恢復預設共用同一個 key：任一進行中時兩邊都停用
  const busy = useIsMutating({ mutationKey: writeKey(routeKey, capability) }) > 0

  return (
    <section className="space-y-3 rounded-lg border p-3" aria-label={capabilityLabel(capability)}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-medium">{capabilityLabel(capability)}</h3>
        <div className="flex gap-2">
          {declared && !readOnly && (
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
          {state.stored !== null && !readOnly && (
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
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-muted-foreground">目前生效：</span>
        <code className="rounded bg-muted px-2 py-0.5">{state.label}</code>
        <SourceBadge source={state.source} />
      </div>
      {state.stored !== null && (
        <div className="text-muted-foreground">
          本用途的設定：<code>{formatChoice(state.stored)}</code>
        </div>
      )}

      {declared === false && state.declared === false && (
        <Warning title="孤兒設定" lines={['程式中這個用途已不用這個能力，只能恢復預設以刪除。']} />
      )}
      {state.problem && <Warning title="目前生效的設定無法使用" lines={[state.problem]} />}
      {state.rejected.length > 0 && (
        <Warning
          title="解析時略過了設定"
          lines={state.rejected.map((layer) => `已略過 ${layer.route_key}：${layer.reason}`)}
        />
      )}

      {notice && <NoticeBox notice={notice} />}

      {/* 寫入失敗後重新整理可能變成唯讀（讀不到資料庫），編輯區要跟著收起 */}
      {editing && !readOnly && (
        <CapabilityEditor
          routeKey={routeKey}
          capability={capability}
          state={state}
          providers={providers}
          busy={busy}
          onCancel={() => onEdit(false)}
          onSubmit={() => onNotice(null)}
          onSaved={(saved) => {
            onEdit(false)
            const after = saved.capabilities[capability]
            onNotice({ kind: 'success', title: '已儲存', lines: after ? describeAfterWrite(routeKey, after) : [] })
          }}
          onFailed={onNotice}
        />
      )}
    </section>
  )
}

/**
 * 來源標記。Source badge.
 * @param props.source - 生效的層。The winning layer.
 * @returns 標記 JSX。Badge JSX.
 */
function SourceBadge({ source }: { source: CapabilityState['source'] }) {
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

/** {@link CapabilityEditor} 的 props。Props of {@link CapabilityEditor}. */
interface CapabilityEditorProps {
  routeKey: string
  capability: string
  state: CapabilityState
  providers: ProviderOption[]
  /** 這個能力有寫入進行中。A write for this slot is in flight. */
  busy: boolean
  onCancel: () => void
  /** 送出前（清掉上一次的結果）。Called before submitting. */
  onSubmit: () => void
  onSaved: (route: CapabilityRoute) => void
  /** 寫入失敗；結果顯示在該能力下，編輯區收起後仍看得到。Write failed; shown on the panel. */
  onFailed: (notice: Notice) => void
}

/**
 * provider → 模型 → effort 的三層選擇與儲存。
 * The provider → model → effort picker and save action.
 *
 * 選項只來自白名單；若本用途已有設定且仍在白名單內則預先帶入。模型不使用 effort 時不顯示
 * effort，只有一個 effort 時自動選上。
 * Options come only from the whitelist; an existing stored choice is
 * pre-filled when still whitelisted. The effort picker is hidden when the
 * model takes none, and a single effort is selected automatically.
 *
 * @returns 編輯區 JSX。Editor JSX.
 */
function CapabilityEditor({
  routeKey,
  capability,
  state,
  providers,
  busy,
  onCancel,
  onSubmit,
  onSaved,
  onFailed,
}: CapabilityEditorProps) {
  const queryClient = useQueryClient()
  const initial = initialSelection(state.stored, providers)
  const [providerId, setProviderId] = useState(initial.provider)
  const [modelName, setModelName] = useState(initial.model)
  const [effort, setEffort] = useState(initial.effort)
  const idPrefix = `${capability}-${routeKey}`

  const provider = providers.find((item) => item.id === providerId)
  const needsModel = (provider?.models.length ?? 0) > 0
  const model = provider?.models.find((item) => item.name === modelName)
  const needsEffort = (model?.efforts.length ?? 0) > 0
  const choice: RouteUpdate | null = buildChoice(providers, { provider: providerId, model: modelName, effort })
  const columns = 1 + (needsModel || !provider ? 1 : 0) + (needsEffort ? 1 : 0)

  const selectModel = (name: string, from: ProviderOption | undefined) => {
    setModelName(name)
    setEffort(defaultEffort(from?.models.find((item) => item.name === name)?.efforts ?? []))
  }

  const saveMutation = useMutation({
    mutationKey: writeKey(routeKey, capability),
    mutationFn: (value: RouteUpdate) => FluencyTidesAPI.putCapabilityRoute(routeKey, capability, value),
    onSuccess: (saved) => {
      const label = saved.capabilities[capability]?.label ?? ''
      toast.success('已儲存', { description: `${saved.route_key}：${label}` })
      onSaved(saved)
    },
    onError: (err) => reportWriteFailure(err, '儲存', `${routeKey} / ${capability}`, onFailed),
    onSettled: () => queryClient.invalidateQueries({ queryKey: QUERY_KEY }),
  })

  if (providers.length === 0) {
    return <Warning title={`沒有可選的${providerLabel(capability)}`} lines={[emptyProviderHint(capability)]} />
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
      <div className={cn('grid gap-4', columns === 3 ? 'md:grid-cols-3' : columns === 2 ? 'md:grid-cols-2' : '')}>
        <div className="space-y-2">
          <label className="text-sm font-medium" htmlFor={`provider-${idPrefix}`}>
            {providerLabel(capability)}
          </label>
          <select
            id={`provider-${idPrefix}`}
            className={SELECT_CLASS}
            value={providerId}
            onChange={(e) => {
              const next = providers.find((item) => item.id === e.target.value)
              setProviderId(e.target.value)
              // 只有一個模型時直接帶入，與 effort 的處理一致
              selectModel(next?.models.length === 1 ? next.models[0].name : '', next)
            }}
          >
            <option value="" disabled>
              選擇{providerLabel(capability)}
            </option>
            {providers.map((item) => (
              <option key={item.id} value={item.id}>
                {providerOptionText(item)}
              </option>
            ))}
          </select>
        </div>
        {(needsModel || !provider) && (
          <div className="space-y-2">
            <label className="text-sm font-medium" htmlFor={`model-${idPrefix}`}>
              模型
            </label>
            <select
              id={`model-${idPrefix}`}
              className={SELECT_CLASS}
              value={modelName}
              disabled={!provider}
              onChange={(e) => selectModel(e.target.value, provider)}
            >
              <option value="" disabled>
                選擇模型
              </option>
              {provider?.models.map((item) => (
                <option key={item.name} value={item.name}>
                  {item.name}
                </option>
              ))}
            </select>
          </div>
        )}
        {needsEffort && model && (
          <div className="space-y-2">
            <label className="text-sm font-medium" htmlFor={`effort-${idPrefix}`}>
              思考深度（effort）
            </label>
            <select
              id={`effort-${idPrefix}`}
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

      {choice && provider && (
        <div className="space-y-1">
          <p>
            <b>{routeKey}</b> 的{capabilityLabel(capability)}改用：
            <code className="rounded bg-background px-2 py-0.5 border">
              {previewLabel(provider, choice.model, choice.effort)}
            </code>
            {capability === CAPABILITY_LLM && <span className="text-muted-foreground">（將寫進卡片的標籤）</span>}
          </p>
          {provider.title && <p className="text-muted-foreground">{provider.title}</p>}
          <p className="text-muted-foreground">
            {capability === CAPABILITY_AUDIO_EVAL
              ? '策略用到的模型在「聽音檔的模型」「語音轉文字」「文字 LLM」設定。儲存後下一次評分即生效、重啟後仍在。'
              : '儲存時會先試建一次（檢查 CLI、金鑰、伺服器設定），之後立即生效、重啟後仍在。'}
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

/**
 * provider 選項的文字：策略附說明，端點附類型（與 ID 相同時省略）。
 * The text of a provider option.
 * @param option - provider。The provider.
 * @returns 顯示文字。Display text.
 */
function providerOptionText(option: ProviderOption): string {
  if (option.title) return `${option.id} — ${option.title}`
  return option.type === option.id ? option.id : `${option.id}（${option.type}）`
}
