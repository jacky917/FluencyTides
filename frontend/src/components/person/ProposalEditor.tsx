/**
 * 「AI 整理」的一項建議：勾選、依種類編輯內容，並附上「依據」（`docs/wip/person_llm_fill_FEAT_2026-11-23.md` D8）。
 * 已寫入的項目只能看；上次沒寫入的顯示原因。
 *
 * One "AI 整理" proposal: tick it, edit it by kind, and see what it is based on.
 */

import type { ReactNode } from 'react'
import { CheckCircle2 } from 'lucide-react'
import type { FillProposal } from '../../types/api'
import { FOLLOWUP_TYPES, HISTORY_ITEM_LABELS, historyItemKeys } from '../../lib/personFill'
import { cn } from '../../lib/utils'
import { Input } from '../ui/input'
import { TEXTAREA_CLASS } from '../generate/formParts'

/** 欄位旁的小標。A field label. */
function Labeled({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="text-xs text-muted-foreground">{label}</span>
      {children}
    </label>
  )
}

/** 觸控時 16px 字（iOS 才不會放大整頁）。Inputs get 16px text on touch. */
const FIELD_CLASS = 'h-9 pointer-coarse:h-10 pointer-coarse:text-base'
const SELECT_CLASS =
  'h-9 w-full rounded-md border border-input bg-background px-2 text-sm pointer-coarse:h-10 pointer-coarse:text-base'
const AREA_CLASS = cn(TEXTAREA_CLASS, 'min-h-16 pointer-coarse:text-base')

/** {@link ProposalEditor} 的 props。Props of {@link ProposalEditor}. */
interface ProposalEditorProps {
  proposal: FillProposal
  /** 標題（文字欄位、會變的資料用欄位的中文名稱）。The heading. */
  heading: string
  /** 畫面上的標題（段標題已經是欄位名稱，這裡只說是 AI 的建議與動作）；省略時用 heading。The visible label. */
  label?: string
  selected: boolean
  /** 建新卡的稱呼一定要寫入，不能取消勾選。Whether unticking is not allowed. */
  required?: boolean
  /** 已經寫入了（只能看）。Already written. */
  saved: boolean
  /** 上次沒寫入的原因。Why it was not written last time. */
  error?: string
  /** 寫入中、別處改過等不能編輯的時候。Whether editing is disabled. */
  disabled: boolean
  onSelect: (selected: boolean) => void
  onChange: (data: Record<string, unknown>) => void
}

/**
 * 一項建議的卡片。One proposal card.
 * @param props - 見 {@link ProposalEditorProps}。See {@link ProposalEditorProps}.
 * @returns 卡片 JSX。The card.
 */
export function ProposalEditor({
  proposal,
  heading,
  label,
  selected,
  required = false,
  saved,
  error,
  disabled,
  onSelect,
  onChange,
}: ProposalEditorProps) {
  const data = proposal.data
  const locked = disabled || saved
  const set = (key: string, value: unknown) => onChange({ ...data, [key]: value })
  const text = (key: string) => (typeof data[key] === 'string' ? (data[key] as string) : '')
  const reason = typeof data.reason === 'string' && data.reason ? data.reason : null

  let body: ReactNode = null
  if (proposal.kind === 'display_name') {
    body = (
      <Input
        value={text('value')}
        onChange={(e) => set('value', e.target.value)}
        disabled={locked}
        aria-label="稱呼"
        placeholder="平常怎麼稱呼他，例如「田中さん」"
        className={FIELD_CLASS}
      />
    )
  } else if (proposal.kind === 'interaction') {
    body = (
      <div className="grid gap-2 @[28rem]:grid-cols-2">
        <Labeled label="日期">
          <Input type="date" value={text('date')} onChange={(e) => set('date', e.target.value)} disabled={locked}
            className={FIELD_CLASS} />
        </Labeled>
        <Labeled label="地點">
          <Input value={text('place')} onChange={(e) => set('place', e.target.value)} disabled={locked}
            className={FIELD_CLASS} />
        </Labeled>
        <div className="@[28rem]:col-span-2">
          <Labeled label="聊了什麼">
            <textarea value={text('topics')} onChange={(e) => set('topics', e.target.value)} disabled={locked}
              className={AREA_CLASS} />
          </Labeled>
        </div>
        <div className="@[28rem]:col-span-2">
          <Labeled label="對方的近況">
            <textarea value={text('updates')} onChange={(e) => set('updates', e.target.value)} disabled={locked}
              className={AREA_CLASS} />
          </Labeled>
        </div>
        <div className="@[28rem]:col-span-2">
          <Labeled label="記住的一句話">
            <Input value={text('quote')} onChange={(e) => set('quote', e.target.value)} disabled={locked}
              className={FIELD_CLASS} />
          </Labeled>
        </div>
      </div>
    )
  } else if (proposal.kind === 'followup') {
    body = (
      <div className="grid gap-2 @[28rem]:grid-cols-[1fr_9rem_10rem]">
        <Labeled label="內容">
          <Input value={text('item')} onChange={(e) => set('item', e.target.value)} disabled={locked}
            className={FIELD_CLASS} />
        </Labeled>
        <Labeled label="類型">
          <select value={text('type') || 'ask'} onChange={(e) => set('type', e.target.value)} disabled={locked}
            className={SELECT_CLASS}>
            {FOLLOWUP_TYPES.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </Labeled>
        <Labeled label="期限">
          <Input type="date" value={text('due')} onChange={(e) => set('due', e.target.value)} disabled={locked}
            className={FIELD_CLASS} />
        </Labeled>
      </div>
    )
  } else if (proposal.kind === 'goal') {
    body = (
      <div className="grid gap-2 @[28rem]:grid-cols-[8rem_1fr_8rem]">
        <Labeled label="類型">
          <Input value={text('kind')} onChange={(e) => set('kind', e.target.value)} disabled={locked}
            className={FIELD_CLASS} />
        </Labeled>
        <Labeled label="內容">
          <Input value={text('item')} onChange={(e) => set('item', e.target.value)} disabled={locked}
            className={FIELD_CLASS} />
        </Labeled>
        <Labeled label="目標時間">
          <Input value={text('target')} onChange={(e) => set('target', e.target.value)} disabled={locked}
            placeholder="YYYY-MM" className={FIELD_CLASS} />
        </Labeled>
      </div>
    )
  } else if (proposal.kind === 'family') {
    body = (
      <div className="grid gap-2 @[28rem]:grid-cols-[8rem_1fr]">
        <Labeled label="關係">
          <Input value={text('relation')} onChange={(e) => set('relation', e.target.value)} disabled={locked}
            placeholder="例如「弟弟」" className={FIELD_CLASS} />
        </Labeled>
        <Labeled label="名字">
          <Input value={text('name')} onChange={(e) => set('name', e.target.value)} disabled={locked}
            className={FIELD_CLASS} />
        </Labeled>
        <div className="@[28rem]:col-span-2">
          <Labeled label="說明">
            <textarea value={text('note')} onChange={(e) => set('note', e.target.value)} disabled={locked}
              className={AREA_CLASS} />
          </Labeled>
        </div>
      </div>
    )
  } else if (proposal.kind === 'field') {
    body = (
      <div className="space-y-2">
        <textarea value={text('value')} onChange={(e) => set('value', e.target.value)} disabled={locked}
          aria-label={`${heading}的內容`} className={AREA_CLASS} />
        <select value={text('mode') || 'replace'} onChange={(e) => set('mode', e.target.value)} disabled={locked}
          aria-label={`${heading}的寫法`} className={cn(SELECT_CLASS, 'w-auto')}>
          <option value="replace">取代原本的</option>
          <option value="append">接在後面</option>
        </select>
      </div>
    )
  } else if (proposal.kind === 'history_add') {
    const item = (data.item && typeof data.item === 'object' ? data.item : {}) as Record<string, unknown>
    body = (
      <div className="grid gap-2 @[28rem]:grid-cols-2">
        {historyItemKeys(item).map((key) => (
          <div key={key} className={cn((key === 'value' || key === 'note') && '@[28rem]:col-span-2')}>
            <Labeled label={HISTORY_ITEM_LABELS[key]}>
              <Input
                value={typeof item[key] === 'string' ? (item[key] as string) : ''}
                onChange={(e) => set('item', { ...item, [key]: e.target.value })}
                disabled={locked}
                className={FIELD_CLASS}
              />
            </Labeled>
          </div>
        ))}
      </div>
    )
  } else if (proposal.kind === 'history_end') {
    body = <p className="text-sm">劃掉：{text('text')}</p>
  }

  return (
    <div
      className={cn(
        '@container space-y-2 rounded-md border p-3 transition-colors',
        selected && !saved ? 'border-primary/40' : 'opacity-80',
        error && 'border-destructive/60',
      )}
    >
      <div className="flex items-center gap-2">
        <input
          type="checkbox"
          className="h-4 w-4 shrink-0 accent-primary pointer-coarse:h-5 pointer-coarse:w-5"
          checked={selected || required}
          disabled={locked || required}
          onChange={(e) => onSelect(e.target.checked)}
          aria-label={`寫入「${heading}」`}
        />
        <span className="min-w-0 flex-1 truncate text-sm font-medium">{label ?? heading}</span>
        {saved && (
          <span className="inline-flex shrink-0 items-center gap-1 text-xs text-emerald-600">
            <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
            已存
          </span>
        )}
      </div>
      {body}
      {reason && <p className="text-xs text-muted-foreground">依據：{reason}</p>}
      {error && (
        <p role="alert" className="text-xs text-destructive">
          沒有寫入：{error}
        </p>
      )}
    </div>
  )
}
