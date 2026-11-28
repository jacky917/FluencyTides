/**
 * 工具設定頁面（能力路由）。
 * Tool settings page (capability routes).
 *
 * 網頁版的 TG `/setconfig`：列出每個用途鍵（含文字全域 `*` 與語音全域 `*voice`）在各能力上的生效設定
 * （文字 LLM、錄音評分策略、聽音檔的模型、語音轉文字），可為某個用途的某個能力選 provider →（需要時）
 * 模型 →（有 effort 時）effort 並儲存，或恢復預設。選項只來自後端回傳的白名單（`GET /api/v1/capability-routes`
 * 的 `providers`；評分策略另依 `strategies` 的限縮過濾）。每個用途依「自己的設定 → 它的全域 → `.env`」解析；
 * 兩個全域各有「全局生效」開關，打開時那一邊的用途都改用全域（自己的設定保留但不生效）。
 *
 * The web counterpart of TG `/setconfig`: each route key's per-capability
 * setting, with a provider → model → (effort) picker and a restore action.
 * Options come only from the backend's whitelist. Each key resolves to itself,
 * then its global, then `.env`; each global has a "global switch".
 *
 * 頁面最下方另有唯讀的「Handler 能力」區塊（{@link HandlerCapabilitiesSection}），列出每種卡片開放到哪裡。
 */

import { useState } from 'react'
import { useIsMutating, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, RefreshCw } from 'lucide-react'
import { toast } from 'sonner'
import { FluencyTidesAPI } from '../api/client'
import { HandlerCapabilitiesSection } from '../components/settings/HandlerCapabilitiesSection'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../components/ui/card'
import { Button } from '../components/ui/button'
import { Skeleton } from '../components/ui/skeleton'
import { cn } from '../lib/utils'
import { describeApiError, failureTitle, isBackendError } from '../lib/apiError'
import {
  CAPABILITY_AUDIO_EVAL,
  CAPABILITY_LLM,
  GLOBAL_TITLES,
  buildChoice,
  capabilitiesInUse,
  capabilityLabel,
  defaultEffort,
  emptyProviderHint,
  formatChoice,
  initialSelection,
  isGlobalKey,
  previewLabel,
  providerLabel,
  providersFor,
  sourceLabel,
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
  // 帶 routeKey：全域鍵自己的設定生效時要顯示「自己設定」，不是「沿用全域」
  const lines = [`${routeKey}：${state.label}（${sourceLabel(state, routeKey)}）`]
  if (state.forced && state.stored !== null) {
    lines.push(`${GLOBAL_TITLES[state.global_key] ?? '全域'}的全局生效開著，這組設定已儲存，但目前不會生效。`)
  }
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

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div className="space-y-2">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-2xl font-bold tracking-tight sm:text-3xl">工具設定</h2>
          <Button variant="outline" size="sm" onClick={() => refetch()} disabled={isFetching}>
            <RefreshCw className={cn('h-4 w-4 mr-2', isFetching && 'animate-spin')} />
            重新整理
          </Button>
        </div>
        <p className="text-sm text-muted-foreground sm:text-base">
          每個用途可以各自選要用的模型，儲存後下一次使用就生效。沒有自己設定的項目沿用它的全域（文字用途看「全域（文字）」、
          錄音評分看「全域（語音）」），最後是部署時 <code>.env</code> 的預設。全域打開「全局生效」時，那一邊的用途都改用全域，
          自己的設定保留但不生效。
        </p>
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

      <HandlerCapabilitiesSection />
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
          <p key={index} className="text-amber-900 [overflow-wrap:anywhere]">
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
  providers,
  strategies,
  readOnly,
  editingSlot,
  onEdit,
  notices,
  onNotice,
}: RouteCardProps) {
  const isGlobal = isGlobalKey(route.route_key)
  const states = Object.entries(route.capabilities)
  // 只標出會用到的能力的問題（例如策略是 listen 時，STT 沒設定不影響評分）；各能力自己的警示照常顯示
  const inUse = capabilitiesInUse(route)
  const flagged =
    !route.declared ||
    states.some(
      ([capability, state]) =>
        inUse.has(capability) && (!state.declared || state.rejected.length > 0 || state.problem !== null),
    )

  // 標題用說明（人看得懂的用途），用途鍵放旁邊小字
  const title = isGlobal ? GLOBAL_TITLES[route.route_key] : (route.description ?? '（程式裡已沒有這個用途）')
  const subtitle = isGlobal ? route.description : null
  // 被全局生效蓋過的全域（這個用途自己的設定目前不生效）
  const forcedBy = [...new Set(states.filter(([, state]) => state.forced).map(([, state]) => state.global_key))]

  return (
    <Card className={cn(flagged && 'border-amber-500/60')}>
      <CardHeader className="space-y-1 px-4 pb-1 pt-4 sm:px-5">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
          {flagged && <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" aria-label="警示" />}
          <CardTitle className="text-base">{title}</CardTitle>
          <code className="text-xs text-muted-foreground">{route.route_key}</code>
          {isGlobal && route.switch !== undefined && route.switch !== null && (
            <GlobalSwitch globalKey={route.route_key} enabled={route.switch} readOnly={readOnly} />
          )}
        </div>
        {subtitle && <CardDescription className="text-xs">{subtitle}</CardDescription>}
        {forcedBy.map((globalKey) => (
          <CardDescription key={globalKey} className="text-xs text-amber-700">
            {GLOBAL_TITLES[globalKey] ?? globalKey}的全局生效開著：這個用途自己的設定目前不生效。
          </CardDescription>
        ))}
      </CardHeader>
      <CardContent className="divide-y p-0 text-sm">
        {!route.declared && (
          <div className="px-4 py-3 sm:px-5">
            <Warning title="孤兒設定" lines={['程式裡已沒有這個用途，只能恢復預設來刪除。']} />
          </div>
        )}
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
    <section className="space-y-3 px-4 py-3 sm:px-5" aria-label={capabilityLabel(capability)}>
      <div className="grid grid-cols-[1fr_auto] items-center gap-x-3 gap-y-1.5 sm:grid-cols-[7.5rem_1fr_auto]">
        <h3 className="font-medium">{capabilityLabel(capability)}</h3>
        <div className="col-span-2 row-start-2 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 sm:col-span-1 sm:col-start-2 sm:row-start-1">
          <code className="min-w-0 break-all rounded bg-muted px-2 py-0.5 text-xs sm:text-sm">{state.label}</code>
          <SourceBadge state={state} routeKey={routeKey} />
        </div>
        <div className="col-start-2 row-start-1 flex justify-end gap-2 sm:col-start-3">
          {declared && !readOnly && (
            <Button
              size="sm"
              variant={editing ? 'secondary' : 'outline'}
              onClick={() => {
                onNotice(null)
                onEdit(!editing)
              }}
              disabled={busy}
            >
              {editing ? '取消' : '更改'}
            </Button>
          )}
          {state.stored !== null && !readOnly && (
            <Button
              size="sm"
              variant="ghost"
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
      {/* 自己有設定、但生效的不是它（被略過，或全局生效開著）時才另外列出；生效的就是它時上面已經看得到 */}
      {state.stored !== null && sourceLabel(state, routeKey) !== '自己設定' && (
        <div className="text-muted-foreground">
          自己的設定（{state.forced ? '全局生效開著，目前不生效' : '沒有生效'}）：
          <code className="break-all">{formatChoice(state.stored)}</code>
        </div>
      )}

      {declared === false && state.declared === false && (
        <Warning title="孤兒設定" lines={['程式裡這個用途已經不用這項，只能恢復預設來刪除。']} />
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
 * @param props.state - 能力的狀態。The capability state.
 * @param props.routeKey - 這一列的用途鍵。The row's key.
 * @returns 標記 JSX。Badge JSX.
 */
function SourceBadge({ state, routeKey }: { state: CapabilityState; routeKey: string }) {
  const { source, forced } = state
  const own = source === 'route' || (source === 'global' && routeKey === state.global_key)
  return (
    <span
      className={cn(
        'shrink-0 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium',
        own && 'bg-primary text-primary-foreground',
        source === 'global' && !own && !forced && 'bg-secondary text-secondary-foreground border',
        forced && 'border border-amber-500/60 bg-amber-500/10 text-amber-800',
        source === 'env' && !forced && 'bg-muted text-muted-foreground border',
      )}
    >
      {sourceLabel(state, routeKey)}
    </span>
  )
}

/**
 * 全域的「全局生效」開關：打開時那一邊的用途都改用全域，各用途自己的設定保留但不生效。
 * A global's "global switch": while on, every route on that side uses the global.
 * @param props.globalKey - 全域鍵（`*` 或 `*voice`）。The global key.
 * @param props.enabled - 目前是否開著。Whether it is on.
 * @param props.readOnly - 讀不到資料庫時不能切換。Disabled while the DB is unreadable.
 * @returns 開關 JSX。Switch JSX.
 */
function GlobalSwitch({ globalKey, enabled, readOnly }: { globalKey: string; enabled: boolean; readOnly: boolean }) {
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationKey: ['capability-route-switch', globalKey],
    mutationFn: (next: boolean) => FluencyTidesAPI.putGlobalSwitch(globalKey, next),
    onSuccess: (_route, next) => {
      toast.success(`${GLOBAL_TITLES[globalKey] ?? globalKey}：已${next ? '打開' : '關閉'}全局生效`, {
        description: next ? '這一邊的用途都改用全域的設定，各自的設定保留但不生效。' : '各用途有自己的設定就用自己的。',
      })
    },
    onError: (error) => {
      if (!isBackendError(error)) console.error(`[ToolSettings] 切換 ${globalKey} 全局生效失敗`, error)
      const { status, message } = describeApiError(error)
      toast.error(failureTitle(status, '切換全局生效'), { description: message })
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: QUERY_KEY }),
  })
  return (
    <label className="ml-auto flex items-center gap-2 text-xs font-medium">
      <input
        type="checkbox"
        className="h-4 w-4 accent-amber-600"
        checked={mutation.isPending ? mutation.variables : enabled}
        disabled={readOnly || mutation.isPending}
        onChange={(e) => mutation.mutate(e.target.checked)}
      />
      全局生效
    </label>
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
        <p key={index} className="[overflow-wrap:anywhere]">
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
  // 還沒選 provider 時，只有這個能力有要選模型的 provider 才先顯示模型欄（評分策略永遠不顯示）
  const showModel = needsModel || (!provider && providers.some((item) => item.models.length > 0))
  const model = provider?.models.find((item) => item.name === modelName)
  const needsEffort = (model?.efforts.length ?? 0) > 0
  const choice: RouteUpdate | null = buildChoice(providers, { provider: providerId, model: modelName, effort })
  const columns = 1 + (showModel ? 1 : 0) + (needsEffort ? 1 : 0)

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
        {showModel && (
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
          <p className="[overflow-wrap:anywhere]">
            <b>{routeKey}</b> 的{capabilityLabel(capability)}改用：
            <code className="break-all rounded bg-background px-2 py-0.5 border">
              {previewLabel(provider, choice.model, choice.effort)}
            </code>
            {capability === CAPABILITY_LLM && <span className="text-muted-foreground">（將寫進卡片的標籤）</span>}
          </p>
          {provider.title && <p className="text-muted-foreground">{provider.title}</p>}
          <p className="text-muted-foreground">
            {capability === CAPABILITY_AUDIO_EVAL
              ? '策略用到的模型在「聽音檔的模型」「語音轉文字」「文字 LLM」設定。儲存後下一次評分就生效。'
              : '儲存時會先試連一次（檢查 CLI、金鑰、伺服器設定），成功才存，之後馬上生效。'}
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
