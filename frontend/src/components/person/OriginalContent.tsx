/**
 * 「AI 整理」每一段裡的「卡片原本」（`docs/wip/person_llm_fill_FEAT_2026-11-23.md` D9 第 8 點）：這個欄位卡片上
 * 原本的內容，和 AI 的建議分開顯示，使用者才判斷得出建議是不是重複。平常只能看，點 ✏️ 才修改；改過的在寫入時
 * 一起送出（文字欄位整段取代、清單的一筆以 `edit_item` 修改），也可以還原。
 *
 * The card's original content of a field, shown apart from the AI proposals; edited with the pencil.
 */

import { useState, type ReactNode } from 'react'
import { CheckCircle2, Pencil, Undo2 } from 'lucide-react'
import type { FillKnownItem } from '../../types/api'
import { keyLabel } from '../../lib/personFill'
import { cn } from '../../lib/utils'
import { Button } from '../ui/button'
import { Input } from '../ui/input'
import { TEXTAREA_CLASS } from '../create/formParts'

const FIELD_CLASS = 'h-9 pointer-coarse:h-10 pointer-coarse:text-base'
const AREA_CLASS = cn(TEXTAREA_CLASS, 'min-h-16 pointer-coarse:text-base')
/** ✏️、還原：觸控時放大到好點的大小（列高跟著變高，但對齊文字第一行）。Icon buttons, larger on touch. */
const ICON_BUTTON_CLASS = '-my-1 h-7 w-7 shrink-0 pointer-coarse:h-9 pointer-coarse:w-9'

/** 一項修改目前的狀態（`findEdit` 找到的那一項）。The state of one edit. */
export interface EditState {
  /** 改過的值：文字欄位是字串，清單的一筆是各鍵的值。The edited value. */
  value: unknown
  /** 還勾著（沒有還原）。Whether it is still ticked. */
  active: boolean
  saved: boolean
  error?: string
}

/** {@link OriginalContent} 的 props。Props of {@link OriginalContent}. */
interface OriginalContentProps {
  /** 文字欄位原本的內容（純文字）；清單時省略。The text field's value. */
  text?: string
  /** 清單原本的項目；文字欄位時省略。The list's original entries. */
  items?: FillKnownItem[]
  /** 這個欄位各鍵的名稱（後端帶來的）。The names of the field's keys. */
  labels?: Record<string, string>
  /** 找這個欄位（或那一筆）的修改。Looks up the edit of the field or an entry. */
  editOf: (itemIndex?: number) => EditState | null
  /** 送出修改。Records an edit. */
  onEditText: (value: string) => void
  onEditItem: (item: FillKnownItem, values: Record<string, unknown>) => void
  /** 還原修改。Reverts an edit. */
  onRevert: (itemIndex?: number) => void
  disabled: boolean
}

/**
 * 卡片原本的內容。The original content of a field.
 * @param props - 見 {@link OriginalContentProps}。See {@link OriginalContentProps}.
 * @returns 原本內容的 JSX；沒有內容時為 null。The block, or null.
 */
export function OriginalContent({
  text,
  items,
  labels,
  editOf,
  onEditText,
  onEditItem,
  onRevert,
  disabled,
}: OriginalContentProps) {
  if (!text && !(items && items.length > 0)) return null
  return (
    // 灰底虛線框與 ✏️ 已足夠表示「卡片原本、可修改」，不另外寫標題；讀屏仍讀得到
    <div role="group" aria-label="卡片原本的內容" className="space-y-1.5 rounded-md border border-dashed bg-muted/40 px-3 py-2">
      {text !== undefined && text !== '' && (
        <TextRow text={text} edit={editOf()} onSave={onEditText} onRevert={() => onRevert()} disabled={disabled} />
      )}
      {items?.map((item) => (
        <ItemRow
          key={item.index}
          item={item}
          labels={labels}
          edit={editOf(item.index)}
          onSave={(values) => onEditItem(item, values)}
          onRevert={() => onRevert(item.index)}
          disabled={disabled}
        />
      ))}
    </div>
  )
}

/** 一行原本的內容：文字、修改標記與按鈕。One original row with its marks and buttons. */
function RowFrame({
  children,
  edit,
  struck,
  onPencil,
  onRevert,
  disabled,
}: {
  children: ReactNode
  edit: EditState | null
  struck?: boolean
  onPencil: () => void
  onRevert: () => void
  disabled: boolean
}) {
  const edited = edit?.active
  return (
    <div className="space-y-0.5">
      <div className="flex items-start gap-2 text-sm">
        <div className={cn('min-w-0 flex-1 whitespace-pre-wrap [overflow-wrap:anywhere]', struck && 'text-muted-foreground line-through')}>
          {children}
        </div>
        {edit?.saved && (
          <span className="inline-flex shrink-0 items-center gap-1 text-xs text-emerald-600">
            <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
            已存
          </span>
        )}
        {edited && !edit?.saved && <span className="shrink-0 text-xs text-primary">已修改</span>}
        {edited && !edit?.saved && (
          <Button type="button" size="icon" variant="ghost" className={ICON_BUTTON_CLASS} disabled={disabled}
            aria-label="還原成原本的內容" title="還原成原本的內容" onClick={onRevert}>
            <Undo2 className="h-3.5 w-3.5 pointer-coarse:h-4 pointer-coarse:w-4" />
          </Button>
        )}
        {!edit?.saved && (
          <Button type="button" size="icon" variant="ghost" className={ICON_BUTTON_CLASS} disabled={disabled}
            aria-label="修改原本的內容" title="修改原本的內容" onClick={onPencil}>
            <Pencil className="h-3.5 w-3.5 pointer-coarse:h-4 pointer-coarse:w-4" />
          </Button>
        )}
      </div>
      {edit?.error && edit.active && (
        <p role="alert" className="text-xs text-destructive">
          沒有寫入：{edit.error}
        </p>
      )}
    </div>
  )
}

/** 文字欄位原本的內容。The original value of a text field. */
function TextRow({
  text,
  edit,
  onSave,
  onRevert,
  disabled,
}: {
  text: string
  edit: EditState | null
  onSave: (value: string) => void
  onRevert: () => void
  disabled: boolean
}) {
  const shown = edit?.active && typeof edit.value === 'string' ? edit.value : text
  const [draft, setDraft] = useState<string | null>(null)
  if (draft !== null) {
    return (
      <div className="space-y-1.5">
        <textarea value={draft} onChange={(e) => setDraft(e.target.value)} aria-label="修改原本的內容"
          className={AREA_CLASS} autoFocus />
        <EditButtons
          onDone={() => {
            if (draft !== text) onSave(draft)
            else if (edit?.active) onRevert()
            setDraft(null)
          }}
          onCancel={() => setDraft(null)}
        />
      </div>
    )
  }
  return (
    <RowFrame edit={edit} onPencil={() => setDraft(shown)} onRevert={onRevert} disabled={disabled}>
      {shown || <span className="text-muted-foreground">（清空）</span>}
    </RowFrame>
  )
}

/** 清單原本的一筆。One original entry of a list. */
function ItemRow({
  item,
  labels,
  edit,
  onSave,
  onRevert,
  disabled,
}: {
  item: FillKnownItem
  labels?: Record<string, string>
  edit: EditState | null
  onSave: (values: Record<string, unknown>) => void
  onRevert: () => void
  disabled: boolean
}) {
  const current =
    edit?.active && edit.value && typeof edit.value === 'object' ? (edit.value as Record<string, unknown>) : item.values
  const [draft, setDraft] = useState<Record<string, unknown> | null>(null)
  if (draft !== null) {
    return (
      <div className="@container space-y-1.5 rounded border bg-background p-2">
        <div className="grid gap-2 @[28rem]:grid-cols-2">
          {Object.keys(item.values).map((key) => (
            <label key={key} className={cn('block space-y-1', (key === 'note' || key === 'topics' || key === 'updates') && '@[28rem]:col-span-2')}>
              <span className="text-xs text-muted-foreground">{keyLabel(labels, key)}</span>
              <Input
                type={key === 'birth_year' ? 'number' : 'text'}
                value={draft[key] === null || draft[key] === undefined ? '' : String(draft[key])}
                onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
                className={FIELD_CLASS}
              />
            </label>
          ))}
        </div>
        <EditButtons
          onDone={() => {
            const values = normalizeValues(draft, item.values)
            if (JSON.stringify(values) !== JSON.stringify(item.values)) onSave(values)
            else if (edit?.active) onRevert()
            setDraft(null)
          }}
          onCancel={() => setDraft(null)}
        />
      </div>
    )
  }
  return (
    <RowFrame edit={edit} struck={!item.active} onPencil={() => setDraft({ ...current })} onRevert={onRevert}
      disabled={disabled}>
      {edit?.active ? summarize(current, labels) : item.text}
      {!item.active && <span className="ml-1 text-xs no-underline">（已劃掉）</span>}
    </RowFrame>
  )
}

/** 修改中的「完成」「取消」。The done / cancel buttons. */
function EditButtons({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  return (
    <div className="flex gap-2">
      <Button type="button" size="sm" className="pointer-coarse:h-10 pointer-coarse:px-4" onClick={onDone}>
        完成
      </Button>
      <Button type="button" size="sm" variant="ghost" className="pointer-coarse:h-10 pointer-coarse:px-4" onClick={onCancel}>
        取消
      </Button>
    </div>
  )
}

/**
 * 整理修改後的值：原本沒有、也沒填的鍵維持 null（不新增鍵）；出生年是數字。
 * Tidies the edited values: untouched empty keys stay null, the birth year is a number.
 */
function normalizeValues(draft: Record<string, unknown>, original: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(
    Object.keys(original).map((key) => {
      const raw = draft[key]
      const text = raw === null || raw === undefined ? '' : String(raw).trim()
      if (text === '') return [key, original[key] === null || original[key] === undefined ? null : '']
      if (key === 'birth_year') return [key, Number.isFinite(Number(text)) ? Number(text) : text]
      return [key, text]
    }),
  )
}

/** 修改後的一筆的一行文字（各鍵的值依序接起來）。A line for an edited entry. */
function summarize(values: Record<string, unknown>, labels?: Record<string, string>): string {
  const filled = Object.entries(values).filter(([, value]) => value !== null && value !== undefined && value !== '')
  // 只有一個鍵的清單（例如興趣）和原本的一行一樣，只寫值
  if (Object.keys(values).length === 1) return filled.map(([, value]) => String(value)).join('')
  return filled.map(([key, value]) => `${keyLabel(labels, key)}：${String(value)}`).join('　')
}
