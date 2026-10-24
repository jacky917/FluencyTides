import { useEffect, useMemo, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, Loader2, Plus, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { FluencyTidesAPI } from '../api/client'
import { describeApiError, failureTitle, isBackendError } from '../lib/apiError'
import { inputValue, itemErrors, itemFields, itemSummary, newItem, type ItemField } from '../lib/jsonItemForm'
import type { JsonFieldSpec, JsonFieldState } from '../types/api'
import { Button } from './ui/button'
import { Input } from './ui/input'

/** {@link JsonFieldEditor} 的 props。Props of {@link JsonFieldEditor}. */
interface JsonFieldEditorProps {
  handlerName: string
  noteId: number
  /** JSON 欄位名稱。The field. */
  field: string
  spec: JsonFieldSpec
  /** 讀取時的內容與版本。The content and version read. */
  state: JsonFieldState
  /** 能不能編輯（權限）；不能時唯讀並顯示原因。Whether editing is allowed. */
  canEdit: boolean
  /** 不能編輯的原因。Why editing is unavailable. */
  notice?: string | null
  /** 存好之後（重新整理卡片與筆記）。Called after saving. */
  onSaved: () => void
  /** 衝突時重新載入筆記。Reloads the note after a conflict. */
  onReload: () => void
  /** 有未儲存的修改時通知外層（關閉彈窗前提醒）。Reports unsaved changes. */
  onDirtyChange?: (field: string, dirty: boolean) => void
}

const TEXTAREA_CLASS =
  'flex min-h-[64px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 read-only:bg-muted/40'

/** 所有物件項目都當成可編輯的 dict；不是物件的（壞資料）保留原樣但不能編輯。Items as editable records. */
function asRecords(items: unknown[]): Record<string, unknown>[] {
  return items.map((item) => (item && typeof item === 'object' && !Array.isArray(item) ? { ...(item as object) } : { value: item }))
}

/**
 * 一個 JSON 欄位的清單編輯：每一筆是一張小表單，可新增、刪除、調整順序，按「儲存」時才整欄送出
 * （json_field_editor 計畫 D5）。別處改過（409）時提示重新載入；某一筆格式不對（422）時把錯誤標在
 * 那一筆的欄位旁。
 * Edits one JSON field as a list of small forms, saved as a whole.
 * @returns 區塊 JSX。Section JSX.
 */
export function JsonFieldEditor({
  handlerName,
  noteId,
  field,
  spec,
  state,
  canEdit,
  notice,
  onSaved,
  onReload,
  onDirtyChange,
}: JsonFieldEditorProps) {
  const fields = useMemo(() => itemFields(spec.item_schema), [spec.item_schema])
  const [items, setItems] = useState<Record<string, unknown>[]>(() => asRecords(state.items))
  const [version, setVersion] = useState(state.version)
  const [dirty, setDirty] = useState(false)
  const [errors, setErrors] = useState<Map<number, Map<string, string>>>(new Map())
  const [conflict, setConflict] = useState(false)

  // 讀到新的版本（重新載入、別處寫入後重新整理）時換成新的內容
  useEffect(() => {
    setItems(asRecords(state.items))
    setVersion(state.version)
    setDirty(false)
    setErrors(new Map())
    setConflict(false)
  }, [state.items, state.version])

  useEffect(() => onDirtyChange?.(field, dirty), [field, dirty, onDirtyChange])

  const editable = canEdit && state.error === null
  const change = (next: Record<string, unknown>[]) => {
    setItems(next)
    setDirty(true)
    setErrors(new Map())
  }
  const setValue = (index: number, key: string, value: unknown) =>
    change(items.map((item, i) => (i === index ? { ...item, [key]: value } : item)))
  const move = (index: number, offset: number) => {
    const next = [...items]
    const [moved] = next.splice(index, 1)
    next.splice(index + offset, 0, moved)
    change(next)
  }

  const save = useMutation({
    mutationFn: () => FluencyTidesAPI.updateJsonField(handlerName, noteId, field, items, version),
    onSuccess: (result) => {
      setItems(asRecords(result.items))
      setVersion(result.version)
      setDirty(false)
      toast.success(`已儲存${spec.title}`)
      if (result.sync_warning) toast.warning('同步 AnkiWeb 失敗', { description: result.sync_warning })
      onSaved()
    },
    onError: (err) => {
      const info = describeApiError(err)
      if (info.status === 409) {
        setConflict(true)
        return
      }
      const detail = (err as { response?: { data?: { detail?: unknown } } }).response?.data?.detail
      const mapped = info.status === 422 ? itemErrors(detail) : new Map<number, Map<string, string>>()
      setErrors(mapped)
      if (!isBackendError(err) && info.status !== 422) console.error(`[JsonFieldEditor] 儲存${spec.title}失敗`, err)
      toast.error(failureTitle(info.status, `儲存${spec.title}`), {
        description: mapped.size > 0 ? '有幾筆格式不對，已標在欄位旁。' : info.message,
      })
    },
  })

  return (
    <section className="space-y-2 rounded-md border p-3" aria-label={spec.title}>
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-semibold">{spec.title}</h3>
        <span className="text-xs text-muted-foreground">
          {items.length} 筆 · {field}
        </span>
        {dirty && <span className="text-xs font-medium text-amber-600">未儲存</span>}
        {editable && (
          <Button
            size="sm"
            variant="outline"
            className="ml-auto h-7 px-2"
            onClick={() => change([...items, newItem(spec.item_schema)])}
            disabled={save.isPending}
          >
            <Plus className="h-3.5 w-3.5" />
            新增
          </Button>
        )}
      </div>

      {state.error !== null && (
        <p role="alert" className="text-sm text-destructive">
          這一欄的資料格式壞了，暫時不能編輯：{state.error}
        </p>
      )}
      {!canEdit && notice && <p className="text-xs text-muted-foreground">{notice}</p>}
      {conflict && (
        <div role="alert" className="flex flex-wrap items-center gap-2 rounded-md border border-amber-500/50 bg-amber-500/10 px-3 py-2 text-sm text-amber-900">
          <span className="flex-1">這一欄在別處被改過（TG、Anki 或另一個分頁），請重新載入後再改。</span>
          <Button size="sm" variant="outline" onClick={onReload}>
            重新載入（放棄這裡的修改）
          </Button>
        </div>
      )}

      {items.length === 0 && <p className="text-xs text-muted-foreground">還沒有資料。</p>}
      <ol className="space-y-2">
        {items.map((item, index) => {
          const itemError = errors.get(index)
          return (
            <li key={index} className="rounded-md border bg-muted/20 p-2">
              <div className="mb-2 flex items-center gap-1">
                <span className="min-w-0 flex-1 truncate text-xs font-medium text-muted-foreground">
                  {index + 1}. {itemSummary(item, fields) || '（新的一筆）'}
                </span>
                {editable && (
                  <>
                    <Button size="icon" variant="ghost" className="h-7 w-7" aria-label="上移" disabled={index === 0}
                      onClick={() => move(index, -1)}>
                      <ArrowUp className="h-3.5 w-3.5" />
                    </Button>
                    <Button size="icon" variant="ghost" className="h-7 w-7" aria-label="下移"
                      disabled={index === items.length - 1} onClick={() => move(index, 1)}>
                      <ArrowDown className="h-3.5 w-3.5" />
                    </Button>
                    <Button size="icon" variant="ghost" className="h-7 w-7 text-destructive" aria-label="刪除這一筆"
                      onClick={() => change(items.filter((_, i) => i !== index))}>
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </>
                )}
              </div>
              {itemError?.get('_') && <p className="mb-1 text-xs text-destructive">{itemError.get('_')}</p>}
              <div className="grid gap-2 sm:grid-cols-2">
                {fields.map((f) => (
                  <ItemInput
                    key={f.key}
                    id={`json-${field}-${index}-${f.key}`}
                    field={f}
                    value={item[f.key]}
                    readOnly={!editable}
                    error={itemError?.get(f.key)}
                    onChange={(value) => setValue(index, f.key, value)}
                  />
                ))}
              </div>
            </li>
          )
        })}
      </ol>

      {editable && dirty && (
        <div className="flex justify-end gap-2">
          <Button size="sm" variant="outline" onClick={onReload} disabled={save.isPending}>
            放棄修改
          </Button>
          <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending}>
            {save.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {save.isPending ? '儲存並同步中…' : `儲存${spec.title}`}
          </Button>
        </div>
      )}
    </section>
  )
}

/** 一個欄位的輸入框。One field's control. */
function ItemInput({
  id,
  field,
  value,
  readOnly,
  error,
  onChange,
}: {
  id: string
  field: ItemField
  value: unknown
  readOnly: boolean
  error?: string
  onChange: (value: unknown) => void
}) {
  const wide = field.kind === 'textarea' ? 'sm:col-span-2' : ''
  const text = value === null || value === undefined ? '' : String(value)
  return (
    <div className={`space-y-1 ${wide}`}>
      <label htmlFor={id} className="text-xs font-medium">
        {field.title}
        {field.required && <span className="ml-0.5 text-destructive">*</span>}
      </label>
      {field.kind === 'select' ? (
        <select
          id={id}
          className="flex h-9 w-full rounded-md border border-input bg-background px-2 text-sm disabled:bg-muted/40"
          value={text}
          disabled={readOnly}
          onChange={(e) => onChange(e.target.value)}
        >
          {field.options?.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      ) : field.kind === 'boolean' ? (
        <div className="flex h-9 items-center">
          <input
            id={id}
            type="checkbox"
            className="h-4 w-4"
            checked={Boolean(value)}
            disabled={readOnly}
            onChange={(e) => onChange(e.target.checked)}
          />
        </div>
      ) : field.kind === 'textarea' ? (
        <textarea
          id={id}
          className={TEXTAREA_CLASS}
          value={text}
          readOnly={readOnly}
          onChange={(e) => onChange(inputValue(field, e.target.value))}
        />
      ) : (
        <Input
          id={id}
          inputMode={field.kind === 'number' ? 'numeric' : undefined}
          value={text}
          readOnly={readOnly}
          className="h-9 read-only:bg-muted/40"
          onChange={(e) => onChange(inputValue(field, e.target.value))}
        />
      )}
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  )
}
