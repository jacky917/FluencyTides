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
  /**
   * 條件列選的卡片類型（已確認開放欄位搜尋）；它的欄位就是欄位按鈕。
   * The card type chosen in the scope bar, known to offer field search.
   */
  handler: HandlerInfo
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
  'inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full border px-2.5 py-1 text-xs transition-colors hover:bg-accent disabled:opacity-50 pointer-coarse:h-9 pointer-coarse:px-3 pointer-coarse:text-sm'

/** 輸入框與下拉選單：觸控時加高、16px 字。Inputs: taller with 16px text on touch. */
const FIELD_CLASS = 'h-8 pointer-coarse:h-10 pointer-coarse:text-base'

/**
 * 新加的條件從高度 0 展開（`index.css` 的 `expand-in`）；系統設定減少動態效果時不播。移除條件直接拿掉，
 * 不做收合動畫（移除後還佔位的話，焦點與後面的條件會錯位）。
 * New conditions expand in; removal is immediate.
 */
const CONDITION_ENTER = 'motion-safe:animate-expand-in'

/**
 * 欄位搜尋：卡片類型由條件列決定（`docs/wip/cards_page_revamp_FEAT_2026-11-23.md` D4），這裡點欄位按鈕加條件、
 * 輸入關鍵字；條件之間是「且」，可以任意疊加（同一個欄位也能加好幾次）。按「搜尋」時由後端組成 Anki 查詢，
 * 交給清單搜尋。「標籤」條件比對整個標籤（含子標籤），輸入時從這種卡片用過的標籤自動完成。
 * Field search for the card type chosen in the scope bar: add field conditions (ANDed) and search.
 * @returns 面板 JSX。Panel JSX.
 */
export function FieldSearchPanel({ handler, state, onChange, onQuery }: FieldSearchPanelProps) {
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
      {/* 觸控裝置（手機、平板）上排成一行左右滑動，不換成好幾行把清單往下擠；右緣淡出提示還有更多 */}
      <div
        className="flex flex-wrap gap-1.5 pointer-coarse:flex-nowrap pointer-coarse:gap-2 pointer-coarse:overflow-x-auto pointer-coarse:pb-1 pointer-coarse:[mask-image:linear-gradient(to_right,black_88%,transparent)] pointer-coarse:[scrollbar-width:none] pointer-coarse:[&::-webkit-scrollbar]:hidden"
        role="group"
        aria-label="加入條件的欄位"
      >
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
                className={cn(
                  'grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-1 gap-y-0.5 @[17rem]:grid-cols-[5.5rem_minmax(0,1fr)_auto] @[17rem]:gap-x-2',
                  // 只有剛按欄位按鈕加的那個條件展開；回到頁面時還原的條件直接顯示
                  c.id === focusId && CONDITION_ENTER,
                )}
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
      {build.isError && (
        <p role="alert" className="text-xs text-destructive">
          搜尋失敗：{describeApiError(build.error).message}
        </p>
      )}
    </form>
  )
}
