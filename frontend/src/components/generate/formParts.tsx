/**
 * Card Generator 兩種建卡流程（填表、預覽）共用的表單零件：牌組說明、由 schema 產生的欄位、建立成功的提示。
 * Form parts shared by the form and preview create flows.
 */

import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ChevronRight } from 'lucide-react'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '../ui/collapsible'
import { Input } from '../ui/input'
import type { FormField } from '../../lib/createForm'
import { cn } from '../../lib/utils'
import type { CreateNoteResponse } from '../../types/api'

/** 多行輸入框的樣式（與 Input 一致）。The textarea style, matching Input. */
export const TEXTAREA_CLASS =
  'flex min-h-[80px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2'

/**
 * 可折疊的區塊：標題列顯示名稱與目前的值（折起來也看得到選了什麼），點標題列展開或收合，高度有過渡動畫。
 * A collapsible section whose header shows the current value.
 * @param props.title - 區塊名稱。The section name.
 * @param props.summary - 標題列右側的目前值。The current value shown in the header.
 * @param props.open - 是否展開。Whether open.
 * @param props.onOpenChange - 使用者展開或收合時。Called when the user toggles it.
 * @param props.children - 內容。The content.
 * @returns 區塊 JSX。The section JSX.
 */
export function CollapsibleSection({
  title,
  summary,
  open,
  onOpenChange,
  children,
}: {
  title: ReactNode
  summary?: ReactNode
  open: boolean
  onOpenChange: (open: boolean) => void
  children: ReactNode
}) {
  return (
    <Collapsible open={open} onOpenChange={onOpenChange} className="group/collapsible rounded-md border">
      <CollapsibleTrigger className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm hover:bg-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <ChevronRight className="h-4 w-4 shrink-0 transition-transform duration-200 group-data-[state=open]/collapsible:rotate-90" />
        <span className="shrink-0 font-medium">{title}</span>
        {summary && <span className="min-w-0 truncate text-muted-foreground">{summary}</span>}
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="border-t px-3 py-3">{children}</div>
      </CollapsibleContent>
    </Collapsible>
  )
}

/**
 * 牌組與筆記類型的說明；沒選牌組或牌組已不存在時說明原因（`id` 給送出按鈕的 aria-describedby）。
 * The deck and note type, or why no deck can be used.
 * @param props.id - 說明文字的 id。The id of the status text.
 * @param props.fixedDeck - 固定的牌組；null 表示用牌組樹選的。The fixed deck, or null.
 * @param props.selectedDeck - 牌組樹選的牌組。The deck picked in the tree.
 * @param props.deckMissing - 選的牌組是否已不存在。Whether that deck is gone.
 * @param props.noteType - 建出的筆記類型。The note type.
 * @returns 說明 JSX。The summary JSX.
 */
export function DeckSummary({
  id,
  fixedDeck,
  selectedDeck,
  deckMissing,
  noteType,
}: {
  id: string
  fixedDeck: string | null
  selectedDeck: string | null
  deckMissing: boolean
  noteType: string | null
}) {
  return (
    <div className="space-y-1 text-sm">
      <p className="font-medium">牌組</p>
      {fixedDeck ? (
        <p className="break-all">{fixedDeck}（這種卡片固定建在這裡）</p>
      ) : selectedDeck === null ? (
        <p id={id} role="status" className="text-muted-foreground">
          請先在牌組樹選擇一個牌組（選「全部牌組」時無法建立卡片）。
        </p>
      ) : deckMissing ? (
        <p id={id} role="status" className="break-all text-destructive">
          牌組「{selectedDeck}」已不存在，請在牌組樹重新選擇。
        </p>
      ) : (
        <p className="break-all">{selectedDeck}</p>
      )}
      <p className="text-xs text-muted-foreground">筆記類型：{noteType}</p>
    </div>
  )
}

/**
 * 由 schema 產生的一個欄位：多行、只能選的下拉（`enum`）、可輸入的下拉（`examples`）或一般文字。
 * One schema-generated field.
 * @param props.id - 輸入框的 id。The input id.
 * @param props.field - 欄位。The field.
 * @param props.value - 目前的值。The value.
 * @param props.error - 錯誤訊息。The error, if any.
 * @param props.disabled - 是否停用。Whether disabled.
 * @param props.onChange - 值改變時。Called with the new value.
 * @returns 欄位 JSX。The field JSX.
 */
export function SchemaFieldInput({
  id,
  field,
  value,
  error,
  disabled,
  onChange,
}: {
  id: string
  field: FormField
  value: string
  error?: string
  disabled: boolean
  onChange: (value: string) => void
}) {
  const listId = field.examples.length > 0 ? `${id}-examples` : undefined
  const common = {
    id,
    value,
    disabled,
    'aria-invalid': error ? true : undefined,
    'aria-describedby': error ? `${id}-error` : field.description ? `${id}-desc` : undefined,
    onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
      onChange(e.target.value),
  }
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="text-sm font-medium">
        {field.title}
        {field.required && <span className="ml-1 text-destructive">*</span>}
      </label>
      {field.options.length > 0 ? (
        <select {...common} className={cn(TEXTAREA_CLASS, 'min-h-0 h-10 py-0', error && 'border-destructive')}>
          {field.options.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      ) : field.multiline ? (
        <textarea {...common} className={cn(TEXTAREA_CLASS, error && 'border-destructive')} />
      ) : (
        <Input {...common} list={listId} className={cn(error && 'border-destructive')} />
      )}
      {listId && (
        <datalist id={listId}>
          {field.examples.map((example) => (
            <option key={example} value={example} />
          ))}
        </datalist>
      )}
      {field.description && !error && (
        <p id={`${id}-desc`} className="text-xs text-muted-foreground">
          {field.description}
        </p>
      )}
      {error && (
        <p id={`${id}-error`} role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  )
}

/**
 * 建立成功的提示：筆記數、牌組、同步提醒與到 Cards 頁的連結。
 * The success notice after creating notes.
 * @param props.result - 建卡結果。The create result.
 * @returns 提示 JSX。The notice JSX.
 */
export function CreatedNotice({ result }: { result: CreateNoteResponse }) {
  return (
    <div role="status" className="space-y-1 rounded-md border border-emerald-500/50 bg-emerald-500/10 px-3 py-2 text-sm">
      <p className="font-medium">
        已建立 {result.note_ids.length} 張筆記（牌組：{result.deck}）
      </p>
      {/* 加標籤失敗與同步失敗可能同時發生，後端以換行分開 */}
      {result.sync_warning && <p className="whitespace-pre-line text-amber-900">{result.sync_warning}</p>}
      <Link className="text-primary underline" to={`/cards?deck=${encodeURIComponent(result.deck)}`}>
        到 Cards 頁查看這個牌組
      </Link>
    </div>
  )
}
