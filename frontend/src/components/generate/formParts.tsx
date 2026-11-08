/**
 * Card Generator 兩種建卡流程（填表、預覽）共用的表單零件：牌組說明、由 schema 產生的欄位、建立成功的提示。
 * Form parts shared by the form and preview create flows.
 */

import { useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ChevronRight, Plus, X } from 'lucide-react'
import { Button } from '../ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '../ui/collapsible'
import { Input } from '../ui/input'
import { addQuickPick, addTags, type FormField, type RowsValue } from '../../lib/createForm'
import { cn } from '../../lib/utils'
import type { CreateNoteResponse } from '../../types/api'
import type { DeckStatus } from '../../hooks/useDeckStatus'

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
 * 牌組與筆記類型的說明；沒選牌組、牌組已不存在或還無法確認時說明原因（`id` 給送出按鈕的 aria-describedby）。
 * The deck and note type, or why no deck can be used.
 * @param props.id - 說明文字的 id。The id of the status text.
 * @param props.fixedDeck - 固定的牌組；null 表示用牌組樹選的。The fixed deck, or null.
 * @param props.selectedDeck - 牌組樹選的牌組。The deck picked in the tree.
 * @param props.deckStatus - 牌組樹所選牌組的狀態（見 `useDeckStatus`）。The selected deck's status.
 * @param props.noteType - 建出的筆記類型。The note type.
 * @returns 說明 JSX。The summary JSX.
 */
export function DeckSummary({
  id,
  fixedDeck,
  selectedDeck,
  deckStatus,
  noteType,
}: {
  id: string
  fixedDeck: string | null
  selectedDeck: string | null
  deckStatus: DeckStatus
  noteType: string | null
}) {
  return (
    <div className="space-y-1 text-sm">
      <p className="font-medium">牌組</p>
      {fixedDeck ? (
        <p className="break-all">{fixedDeck}（這種卡片固定建在這裡）</p>
      ) : deckStatus === 'all' || selectedDeck === null ? (
        <p id={id} role="status" className="text-muted-foreground">
          請先在牌組樹選擇一個牌組（選「全部牌組」時無法建立卡片）。
        </p>
      ) : deckStatus === 'loading' ? (
        <p id={id} role="status" className="break-all text-muted-foreground">
          {selectedDeck}（正在確認牌組是否存在…）
        </p>
      ) : deckStatus === 'error' ? (
        <p id={id} role="status" className="break-all text-destructive">
          無法載入牌組清單，暫時無法確認牌組「{selectedDeck}」是否存在；請在牌組樹重新整理。
        </p>
      ) : deckStatus === 'missing' ? (
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
 * 一列一筆的欄位（`x-ui: list`）：每列一個輸入框（職位這種物件列每格一個），右邊可刪除，下面「+ 新增一列」；
 * 有快速選項時列在上面，點一下加一列（已有的不重複）。還沒有任何列時顯示一列空白，打字就成為第一筆。
 * A field typed one entry per row, with "+" to add rows and quick picks.
 * @param props.id - 第一個輸入框的 id（label 指向它）。The first input's id.
 * @param props.field - 欄位（`rows` 不是 null）。The field.
 * @param props.value - 目前的列。The rows.
 * @param props.error - 錯誤訊息。The error.
 * @param props.disabled - 停用。Disabled.
 * @param props.onChange - 列有變動時。Called with the new rows.
 * @returns 欄位 JSX。The field JSX.
 */
export function RowsFieldInput({
  id,
  field,
  value,
  error,
  disabled,
  onChange,
}: {
  id: string
  field: FormField
  value: RowsValue
  error?: string
  disabled: boolean
  onChange: (value: RowsValue) => void
}) {
  const spec = field.rows!
  const cells = spec.cells
  const empty = (): string | Record<string, string> =>
    cells ? Object.fromEntries(cells.map((cell) => [cell.key, ''])) : ''
  // 沒有任何列時先給一列空白，不必先按「+」
  const rows = ((value as unknown[]).length > 0 ? value : [empty()]) as (string | Record<string, string>)[]
  const set = (next: (string | Record<string, string>)[]) => onChange(next as RowsValue)
  const describedBy = error ? `${id}-error` : field.description ? `${id}-desc` : undefined

  return (
    <div className="space-y-1" role="group" aria-labelledby={`${id}-label`}>
      <label id={`${id}-label`} htmlFor={id} className="text-sm font-medium">
        {field.title}
        {field.required && <span className="ml-1 text-destructive">*</span>}
      </label>
      {spec.quick.length > 0 && !cells && (
        <div className="flex flex-wrap gap-1">
          {spec.quick.map((option) => {
            const added = (rows as string[]).some((row) => row.trim() === option)
            return (
              <Button
                key={option}
                type="button"
                size="sm"
                variant={added ? 'secondary' : 'outline'}
                className="h-7 px-2 text-xs"
                disabled={disabled || added}
                aria-pressed={added}
                onClick={() => set(addQuickPick(rows as string[], option))}
              >
                {added ? option : `+ ${option}`}
              </Button>
            )
          })}
        </div>
      )}
      <ol className="space-y-1">
        {rows.map((row, index) => {
          const rowId = index === 0 ? id : `${id}-${index}`
          const update = (next: string | Record<string, string>) => set(rows.map((r, i) => (i === index ? next : r)))
          return (
            <li key={index} className="flex items-start gap-1">
              {cells ? (
                <div className="grid flex-1 gap-1 sm:grid-cols-2">
                  {cells.map((cell, cellIndex) => (
                    <Input
                      key={cell.key}
                      id={cellIndex === 0 ? rowId : `${rowId}-${cell.key}`}
                      value={(row as Record<string, string>)[cell.key] ?? ''}
                      placeholder={cell.title}
                      aria-label={`${field.title} ${index + 1} 的${cell.title}`}
                      aria-invalid={error ? true : undefined}
                      aria-describedby={describedBy}
                      disabled={disabled}
                      onChange={(e) => update({ ...(row as Record<string, string>), [cell.key]: e.target.value })}
                    />
                  ))}
                </div>
              ) : spec.multiline ? (
                <textarea
                  id={rowId}
                  value={row as string}
                  aria-label={`${field.title} ${index + 1}`}
                  aria-invalid={error ? true : undefined}
                  aria-describedby={describedBy}
                  disabled={disabled}
                  className={cn(TEXTAREA_CLASS, 'min-h-[60px] flex-1', error && 'border-destructive')}
                  onChange={(e) => update(e.target.value)}
                />
              ) : (
                <Input
                  id={rowId}
                  value={row as string}
                  aria-label={`${field.title} ${index + 1}`}
                  aria-invalid={error ? true : undefined}
                  aria-describedby={describedBy}
                  disabled={disabled}
                  className={cn('flex-1', error && 'border-destructive')}
                  onChange={(e) => update(e.target.value)}
                />
              )}
              <Button
                type="button"
                size="icon"
                variant="ghost"
                className="h-9 w-9 shrink-0 text-muted-foreground"
                aria-label={`刪除${field.title}第 ${index + 1} 列`}
                disabled={disabled}
                onClick={() => set(rows.filter((_, i) => i !== index))}
              >
                <X className="h-4 w-4" />
              </Button>
            </li>
          )
        })}
      </ol>
      <Button
        type="button"
        size="sm"
        variant="outline"
        className="h-7 px-2 text-xs"
        disabled={disabled}
        onClick={() => set([...rows, empty()])}
      >
        <Plus className="mr-1 h-3.5 w-3.5" />
        新增一列
      </Button>
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
 * 短值的一列一筆欄位（`x-item: tag`，語言、興趣這種）畫成一個一個小標籤：已加入的橫向排、可換行，每個
 * 有 × 可移除；還沒加入的快速選項是同一排的小按鈕；最後一個小輸入框打其他的，按 Enter 或打逗號加入
 * （頓號、逗號分隔的一次切成多個），離開輸入框時也加入，打了沒按 Enter 就送出也不會漏掉。
 * A short-value list field shown as removable tags, quick picks and a small input.
 * @param props.id - 輸入框的 id（label 指向它）。The input id.
 * @param props.field - 欄位（`rows.tags` 為 true）。The field.
 * @param props.value - 目前的標籤。The tags.
 * @param props.error - 錯誤訊息。The error.
 * @param props.disabled - 停用。Disabled.
 * @param props.onChange - 標籤有變動時。Called with the new tags.
 * @returns 欄位 JSX。The field JSX.
 */
export function TagsFieldInput({
  id,
  field,
  value,
  error,
  disabled,
  onChange,
}: {
  id: string
  field: FormField
  value: string[]
  error?: string
  disabled: boolean
  onChange: (value: string[]) => void
}) {
  const [text, setText] = useState('')
  const tags = value.filter((tag) => tag.trim() !== '')
  const quick = field.rows!.quick.filter((option) => !tags.includes(option))
  const describedBy = error ? `${id}-error` : field.description ? `${id}-desc` : undefined
  const commit = (typed: string) => {
    const next = addTags(tags, typed)
    if (next.length !== tags.length) onChange(next)
    setText('')
  }

  return (
    <div className="space-y-1" role="group" aria-labelledby={`${id}-label`}>
      <label id={`${id}-label`} htmlFor={id} className="text-sm font-medium">
        {field.title}
        {field.required && <span className="ml-1 text-destructive">*</span>}
      </label>
      <div className="flex flex-wrap items-center gap-1.5">
        {tags.length > 0 && (
          <ul role="list" className="contents" aria-label={`已加入的${field.title}`}>
            {tags.map((tag) => (
              <li key={tag} className="inline-flex items-center gap-1 rounded-full border bg-secondary px-2.5 py-0.5 text-xs">
                {tag}
                <button
                  type="button"
                  className="rounded-full p-0.5 hover:bg-accent disabled:opacity-50"
                  disabled={disabled}
                  aria-label={`移除${field.title} ${tag}`}
                  onClick={() => onChange(tags.filter((t) => t !== tag))}
                >
                  <X className="h-3 w-3" />
                </button>
              </li>
            ))}
          </ul>
        )}
        {quick.map((option) => (
          <button
            key={option}
            type="button"
            className="inline-flex items-center rounded-full border border-dashed px-2.5 py-0.5 text-xs text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-50"
            disabled={disabled}
            aria-label={`加入${field.title} ${option}`}
            onClick={() => onChange(addTags(tags, option))}
          >
            <Plus className="mr-0.5 h-3 w-3" />
            {option}
          </button>
        ))}
        <Input
          id={id}
          value={text}
          disabled={disabled}
          placeholder={quick.length > 0 ? '其他…' : '輸入後按 Enter'}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          className={cn('h-7 w-32 rounded-full px-2.5 text-xs', error && 'border-destructive')}
          onChange={(e) => {
            const typed = e.target.value
            // 打出逗號、頓號時把前面的加入
            if (/[、,，]$/.test(typed)) commit(typed)
            else setText(typed)
          }}
          onKeyDown={(e) => {
            // 輸入法選字中的 Enter 不算；Enter 不送出整張表單
            if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
              e.preventDefault()
              commit(text)
            }
          }}
          onBlur={() => commit(text)}
        />
      </div>
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
 * 牌組不能用時取代整個表單的提示：說明原因與怎麼做；填到一半的內容或草稿還在時一併說明，使用者
 * 知道沒有遺失。
 * Replaces the form while the deck rules out creating.
 * @param props.reason - 原因（見 `deckBlockedReason`）。The reason.
 * @param props.kept - 保留著的內容說明；沒有時省略。What is kept, if anything.
 * @returns 提示 JSX。The notice JSX.
 */
export function DeckRequiredNotice({ reason, kept }: { reason: string; kept?: string | null }) {
  return (
    <div role="status" className="space-y-1 rounded-md border border-amber-500/50 bg-amber-500/10 px-3 py-3 text-sm">
      <p className="font-medium">這種卡片要建在牌組樹選的牌組</p>
      <p>{reason}</p>
      {kept && <p className="text-xs text-muted-foreground">{kept}</p>}
    </div>
  )
}

/**
 * 建立成功的提示：筆記數、牌組、同步提醒，加上查看新卡片的方式——有 `onView` 時是按鈕（卡片瀏覽頁的
 * 建卡畫面：在右欄打開新卡片），否則是到 Cards 頁這個牌組的連結（Card Generator）。
 * The success notice after creating notes, with a way to view them.
 * @param props.result - 建卡結果。The create result.
 * @param props.onView - 查看新卡片；省略時改顯示到 Cards 頁的連結。Views the new card.
 * @returns 提示 JSX。The notice JSX.
 */
export function CreatedNotice({ result, onView }: { result: CreateNoteResponse; onView?: () => void }) {
  return (
    <div role="status" className="space-y-1 rounded-md border border-emerald-500/50 bg-emerald-500/10 px-3 py-2 text-sm">
      <p className="font-medium">
        已建立 {result.note_ids.length} 張筆記（牌組：{result.deck}）
      </p>
      {/* 加標籤失敗與同步失敗可能同時發生，後端以換行分開 */}
      {result.sync_warning && <p className="whitespace-pre-line text-amber-900">{result.sync_warning}</p>}
      {onView ? (
        <button type="button" className="text-primary underline" onClick={onView}>
          {result.note_ids.length > 1 ? '查看新卡片（第一張）' : '查看新卡片'}
        </button>
      ) : (
        <Link className="text-primary underline" to={`/cards?deck=${encodeURIComponent(result.deck)}`}>
          到 Cards 頁查看這個牌組
        </Link>
      )}
    </div>
  )
}
