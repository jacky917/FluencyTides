import { useEffect, useId, useRef, useState, type FormEvent } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Loader2, Plus, Search, X } from 'lucide-react'
import { FluencyTidesAPI } from '../../api/client'
import { describeApiError } from '../../lib/apiError'
import {
  ANY_FIELD,
  TAG_FIELD,
  addCondition,
  fieldTitle,
  removeCondition,
  requestConditions,
  selectHandler,
  setConditionValue,
  type FieldSearchState,
} from '../../lib/fieldSearch'
import type { HandlerInfo } from '../../types/api'
import { Button } from '../ui/button'
import { cn } from '../../lib/utils'
import { Input } from '../ui/input'

/** {@link FieldSearchPanel} 的 props。Props of {@link FieldSearchPanel}. */
interface FieldSearchPanelProps {
  /** 開放欄位搜尋的 handler。The searchable handlers. */
  handlers: HandlerInfo[]
  state: FieldSearchState
  onChange: (state: FieldSearchState) => void
  /** 組好查詢後交給卡片清單搜尋。Runs the built query. */
  onQuery: (query: string) => void
}

/*
 * 排版分兩個方向調整：
 * - 版面看面板自己的寬度（container query，`@container` 在 form 上）：電腦三欄時清單欄很窄，條件的欄位名
 *   放在輸入框上方；寬度夠（手機直向、兩欄）時欄位名與輸入框同一列。
 * - 尺寸看輸入裝置（`pointer-coarse`，手機、平板）：按鈕與輸入框加高到好點的大小，文字 16px（iOS Safari
 *   在小於 16px 的輸入框聚焦時會放大整頁）。
 * Layout follows the panel width (container queries); sizes follow the pointer:
 * touch gets larger targets and 16px text (iOS zooms on smaller inputs).
 */

const CHIP_CLASS =
  'inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs transition-colors hover:bg-accent disabled:opacity-50 pointer-coarse:h-9 pointer-coarse:px-3 pointer-coarse:text-sm'

/** 輸入框與下拉選單：觸控時加高、16px 字。Inputs: taller with 16px text on touch. */
const FIELD_CLASS = 'h-8 pointer-coarse:h-10 pointer-coarse:text-base'

/**
 * 欄位搜尋：選筆記類型、點欄位按鈕加條件、輸入關鍵字；條件之間是「且」，可以任意疊加（同一個欄位
 * 也能加好幾次）。按「搜尋」時由後端組成 Anki 查詢，交給清單搜尋。「標籤」條件比對整個標籤（含子標籤），
 * 輸入時從這種卡片用過的標籤自動完成。
 * Field search: pick a note type, add field conditions (ANDed) and search.
 * @returns 面板 JSX。Panel JSX.
 */
export function FieldSearchPanel({ handlers, state, onChange, onQuery }: FieldSearchPanelProps) {
  const handler = handlers.find((h) => h.name === state.handler)
  const tagListId = useId()
  // 有標籤條件時才查這種卡片用過的標籤（自動完成用）
  const hasTagCondition = state.conditions.some((c) => c.field === TAG_FIELD)
  const { data: knownTags } = useQuery({
    queryKey: ['tags', state.handler],
    queryFn: () => FluencyTidesAPI.listTags(state.handler ?? ''),
    enabled: Boolean(state.handler) && hasTagCondition,
    staleTime: 60_000,
  })
  // 新加的條件自動聚焦
  const [focusId, setFocusId] = useState<number | null>(null)
  const inputs = useRef(new Map<number, HTMLInputElement>())
  useEffect(() => {
    if (focusId !== null) inputs.current.get(focusId)?.focus()
  }, [focusId])

  const build = useMutation({
    mutationFn: () => FluencyTidesAPI.buildSearchQuery(state.handler ?? '', requestConditions(state)),
    onSuccess: ({ query }) => onQuery(query),
  })

  const add = (field: string) => {
    const next = addCondition(state, field)
    onChange(next.state)
    setFocusId(next.id)
  }
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (state.handler) build.mutate()
  }

  return (
    <form onSubmit={submit} className="@container space-y-2" aria-label="欄位搜尋">
      {/* 窄欄時標籤在上、選單佔滿整列，選項文字才不會被截斷 */}
      <div className="flex flex-col gap-1 @[17rem]:flex-row @[17rem]:items-center @[17rem]:gap-2">
        <label htmlFor="field-search-type" className="shrink-0 text-xs text-muted-foreground">
          欄位搜尋
        </label>
        <select
          id="field-search-type"
          className={cn('w-full min-w-0 rounded-md border border-input bg-background px-2 text-sm @[17rem]:flex-1', FIELD_CLASS)}
          value={state.handler ?? ''}
          onChange={(e) => onChange(selectHandler(e.target.value || null))}
        >
          <option value="">不使用（Anki 語法）</option>
          {handlers.map((h) => (
            <option key={h.name} value={h.name}>
              {h.title}
            </option>
          ))}
        </select>
      </div>

      {handler && (
        <>
          <div className="flex flex-wrap gap-1.5 pointer-coarse:gap-2" role="group" aria-label="加入條件的欄位">
            {[
              { key: ANY_FIELD, title: '全部欄位' },
              ...(handler.search_fields ?? []),
              { key: TAG_FIELD, title: '標籤' },
            ].map((f) => (
              <button key={f.key} type="button" className={CHIP_CLASS} onClick={() => add(f.key)}>
                <Plus className="h-3 w-3" />
                {f.title}
              </button>
            ))}
          </div>

          <datalist id={tagListId}>
            {(knownTags ?? []).map((item) => (
              <option key={item.tag} value={item.tag} label={`${item.count} 張`} />
            ))}
          </datalist>

          {state.conditions.length > 0 && (
            <ul className="space-y-2 @[17rem]:space-y-1.5">
              {state.conditions.map((c, index) => {
                const title = fieldTitle(handler, c.field)
                return (
                  // 窄欄：欄位名一列、輸入框與移除鈕一列；夠寬：同一列，欄位名固定寬度對齊
                  <li
                    key={c.id}
                    className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-1 gap-y-0.5 @[17rem]:grid-cols-[5.5rem_minmax(0,1fr)_auto] @[17rem]:gap-x-2"
                  >
                    <span className="col-span-2 truncate text-xs font-medium @[17rem]:col-span-1" title={title}>
                      {index > 0 && <span className="mr-1 font-normal text-muted-foreground">且</span>}
                      {title}
                    </span>
                    <Input
                      ref={(el) => {
                        if (el) inputs.current.set(c.id, el)
                        else inputs.current.delete(c.id)
                      }}
                      value={c.value}
                      onChange={(e) => onChange(setConditionValue(state, c.id, e.target.value))}
                      placeholder={c.field === TAG_FIELD ? '標籤（從清單挑）' : '包含的文字'}
                      list={c.field === TAG_FIELD ? tagListId : undefined}
                      aria-label={c.field === TAG_FIELD ? '標籤是' : `${title}包含`}
                      className={cn('px-2', FIELD_CLASS)}
                    />
                    <Button
                      type="button"
                      size="icon"
                      variant="ghost"
                      className="h-8 w-8 shrink-0 pointer-coarse:h-10 pointer-coarse:w-10"
                      aria-label={`移除「${title}」條件`}
                      onClick={() => onChange(removeCondition(state, c.id))}
                    >
                      <X className="h-4 w-4" />
                    </Button>
                  </li>
                )
              })}
            </ul>
          )}

          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" size="sm" className="pointer-coarse:h-10 pointer-coarse:px-4" disabled={build.isPending}>
              {build.isPending ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Search className="mr-1 h-4 w-4" />}
              搜尋
            </Button>
            {state.conditions.length > 0 && (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="pointer-coarse:h-10"
                onClick={() => onChange(selectHandler(state.handler))}
              >
                清除條件
              </Button>
            )}
          </div>
          {/* 每個條件前已標「且」，這裡只說明沒有條件時的行為 */}
          {state.conditions.length === 0 && (
            <p className="text-xs text-muted-foreground">沒有條件時列出全部{handler.title}</p>
          )}
          {build.isError && (
            <p role="alert" className="text-xs text-destructive">
              搜尋失敗：{describeApiError(build.error).message}
            </p>
          )}
        </>
      )}
    </form>
  )
}
