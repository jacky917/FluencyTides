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

const CHIP_CLASS =
  'inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs transition-colors hover:bg-accent disabled:opacity-50'

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
    <form onSubmit={submit} className="space-y-2" aria-label="欄位搜尋">
      <div className="flex items-center gap-2">
        <label htmlFor="field-search-type" className="shrink-0 text-xs text-muted-foreground">
          欄位搜尋
        </label>
        <select
          id="field-search-type"
          className="h-8 min-w-0 flex-1 rounded-md border border-input bg-background px-2 text-sm"
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
          <div className="flex flex-wrap gap-1.5" role="group" aria-label="加入條件的欄位">
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
            <ul className="space-y-1.5">
              {state.conditions.map((c, index) => (
                <li key={c.id} className="flex items-center gap-2">
                  <span className="w-6 shrink-0 text-right text-xs text-muted-foreground">{index > 0 ? '且' : ''}</span>
                  <span className="w-20 shrink-0 truncate text-xs font-medium" title={fieldTitle(handler, c.field)}>
                    {fieldTitle(handler, c.field)}
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
                    aria-label={c.field === TAG_FIELD ? '標籤是' : `${fieldTitle(handler, c.field)}包含`}
                    className="h-8"
                  />
                  <Button
                    type="button"
                    size="icon"
                    variant="ghost"
                    className="h-8 w-8 shrink-0"
                    aria-label={`移除「${fieldTitle(handler, c.field)}」條件`}
                    onClick={() => onChange(removeCondition(state, c.id))}
                  >
                    <X className="h-4 w-4" />
                  </Button>
                </li>
              ))}
            </ul>
          )}

          <div className="flex items-center gap-2">
            <Button type="submit" size="sm" disabled={build.isPending}>
              {build.isPending ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Search className="mr-1 h-4 w-4" />}
              搜尋
            </Button>
            {state.conditions.length > 0 && (
              <Button type="button" size="sm" variant="ghost" onClick={() => onChange(selectHandler(state.handler))}>
                清除條件
              </Button>
            )}
            <span className="text-xs text-muted-foreground">
              {state.conditions.length === 0 ? `沒有條件時列出全部${handler.title}` : '條件之間是「且」'}
            </span>
          </div>
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
