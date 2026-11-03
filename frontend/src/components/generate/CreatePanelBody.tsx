/**
 * 建卡面板的內容：卡片類型選單，加上依建卡流程切換的表單（填表或預覽）。Card Generator 頁與卡片
 * 瀏覽頁的建卡畫面共用（`docs/wip/card_list_create_FEAT_2026-10-30.md` D3）。
 *
 * - 卡片類型依 `capabilities.create.channels` 呈現：含 web 可選；只能在 TG、本機腳本建立的反灰並
 *   顯示原因；沒有任何管道的不顯示。前端不自行判斷，只看宣告（`lib/handlerCapabilities.ts`）。
 * - 填表流程（`flow="form"`）：表單由 `create_schema` 產生（`lib/createForm.ts`），不呼叫 LLM。
 * - 預覽流程（`flow="preview"`，目前只有三語對話卡）：見 `PreviewCreate.tsx`。
 * - 選的卡片類型、選單是否展開記在 localStorage，填到一半的內容存在 sessionStorage；鍵與頁面無關，
 *   兩個入口看到的是同一份。
 * - 後端寫入後會同步 AnkiWeb 並等它完成，所以送出期間顯示「建立中、同步中」；422 的欄位錯誤標在欄位旁。
 *
 * The body of a create panel: the card type picker and the form for the
 * handler's create flow. Shared by the Card Generator page and the card
 * browsing page.
 */

import { useId, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Eraser, Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { FluencyTidesAPI } from '../../api/client'
import { removeStored, useLocalStorage, useSessionStorage } from '../../hooks/useStoredState'
import type { DeckStatus } from '../../hooks/useDeckStatus'
import { describeApiError, failureTitle, isBackendError, retryTransient } from '../../lib/apiError'
import { formErrorsFrom, initialValues, missingRequired, schemaFields, type FormErrors } from '../../lib/createForm'
import { createAvailability, type CreateAvailability } from '../../lib/handlerCapabilities'
import { cn } from '../../lib/utils'
import type { CreateNoteResponse, HandlerInfo } from '../../types/api'
import { ErrorNote } from '../shared/ErrorNote'
import { Button } from '../ui/button'
import { Skeleton } from '../ui/skeleton'
import { CollapsibleSection, CreatedNotice, DeckSummary, SchemaFieldInput } from './formParts'
import { PreviewCreate } from './PreviewCreate'
import { TagInput } from './TagInput'

/** 選單上的一種卡片。A handler on the picker. */
interface PickerEntry {
  handler: HandlerInfo
  availability: Exclude<CreateAvailability, { kind: 'hidden' }>
}

/** {@link CreatePanelBody} 的 props。Props of {@link CreatePanelBody}. */
export interface CreatePanelBodyProps {
  /** 新卡建在哪個牌組；null 為全部牌組（不能建立）。The deck to create in. */
  selectedDeck: string | null
  /** 所選牌組的狀態（見 `useDeckStatus`）。The selected deck's status. */
  deckStatus: DeckStatus
  /**
   * 建卡成功、通知與重新整理都做完後呼叫；元件卸載後仍可能被呼叫（請求在背景完成）。
   * Called after a successful create, possibly after unmounting.
   */
  onCreated?: (result: CreateNoteResponse) => void
}

/**
 * 卡片類型選單與建卡表單。The card type picker and the create form.
 * @returns 面板內容 JSX。The panel body JSX.
 */
export function CreatePanelBody({ selectedDeck, deckStatus, onCreated }: CreatePanelBodyProps) {
  const [storedHandler, setStoredHandler] = useLocalStorage<string | null>('generate_handler', null)
  // 卡片類型選單是否展開；選好類型後自動收起，標題列仍看得到選了哪一種
  const [pickerOpen, setPickerOpen] = useLocalStorage<boolean>('generate_picker_open', true)
  const {
    data: handlers,
    isPending: handlersPending,
    error: handlersError,
  } = useQuery({ queryKey: ['handlers'], queryFn: FluencyTidesAPI.listHandlers, retry: retryTransient })

  const entries: PickerEntry[] = (handlers ?? []).flatMap((handler) => {
    const availability = createAvailability(handler.capabilities.create)
    return availability.kind === 'hidden' ? [] : [{ handler, availability }]
  })
  const selectable = entries.filter((entry) => entry.availability.kind === 'selectable').map((entry) => entry.handler)
  // 記住的那種還能從網頁建立就用它，否則用第一個可選的
  const current = selectable.find((handler) => handler.name === storedHandler) ?? selectable[0]

  if (handlersPending) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    )
  }
  if (handlersError) {
    return <ErrorNote title="無法載入卡片類型，暫時無法建立卡片" error={handlersError} />
  }
  return (
    <>
      <CollapsibleSection
        title="卡片類型"
        summary={current?.title ?? '未選擇'}
        open={pickerOpen}
        onOpenChange={setPickerOpen}
      >
        <HandlerPicker
          entries={entries}
          selected={current?.name ?? null}
          onSelect={(name) => {
            setStoredHandler(name)
            setPickerOpen(false)
          }}
        />
      </CollapsibleSection>
      {current ? (
        // 換卡片類型時重新掛載，表單內容與錯誤一起重設
        current.capabilities.create.flow === 'preview' ? (
          <PreviewCreate
            key={current.name}
            handler={current}
            selectedDeck={selectedDeck}
            deckStatus={deckStatus}
            onCreated={onCreated}
          />
        ) : (
          <CreateForm
            key={current.name}
            handler={current}
            selectedDeck={selectedDeck}
            deckStatus={deckStatus}
            onCreated={onCreated}
          />
        )
      ) : (
        <p className="text-sm text-muted-foreground">目前沒有能在網頁建立的卡片類型。</p>
      )}
    </>
  )
}

/**
 * 卡片類型的選單：可選的能點，不能從網頁建立的反灰並顯示原因。
 * The card type picker; greyed-out entries show why.
 * @param props.entries - 要顯示的卡片類型。The entries.
 * @param props.selected - 選取的 handler 名稱。The selected handler.
 * @param props.onSelect - 選取時。Called on selection.
 * @returns 選單 JSX。Picker JSX.
 */
function HandlerPicker({
  entries,
  selected,
  onSelect,
}: {
  entries: PickerEntry[]
  selected: string | null
  onSelect: (name: string) => void
}) {
  return (
    <fieldset className="space-y-2">
      <legend className="sr-only">卡片類型</legend>
      {entries.map(({ handler, availability }) => {
        const disabled = availability.kind === 'disabled'
        return (
          <label
            key={handler.name}
            className={cn(
              'flex items-start gap-3 rounded-md border px-3 py-2 text-sm',
              disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer hover:bg-accent',
              selected === handler.name && 'border-primary bg-primary/5',
            )}
          >
            <input
              type="radio"
              name="create-handler"
              className="mt-1"
              value={handler.name}
              checked={selected === handler.name}
              disabled={disabled}
              onChange={() => onSelect(handler.name)}
            />
            <span className="min-w-0">
              <span className="font-medium">{handler.title}</span>
              {availability.kind === 'disabled' && (
                <span className="block break-words text-xs text-muted-foreground">{availability.reason}</span>
              )}
            </span>
          </label>
        )
      })}
    </fieldset>
  )
}

/**
 * 一種卡片的建卡表單（由 `create_schema` 產生）。
 * The create form of one handler, generated from its schema.
 * @param props.handler - 可從網頁建立的 handler。The handler.
 * @param props.selectedDeck - 牌組樹選的牌組。The deck picked in the tree.
 * @param props.deckStatus - 牌組樹所選牌組的狀態（見 `useDeckStatus`）。The selected deck's status.
 * @param props.onCreated - 建卡成功後（見 {@link CreatePanelBodyProps}）。Called after creating.
 * @returns 表單 JSX。Form JSX.
 */
function CreateForm({
  handler,
  selectedDeck,
  deckStatus,
  onCreated,
}: {
  handler: HandlerInfo
  selectedDeck: string | null
  deckStatus: DeckStatus
  onCreated?: (result: CreateNoteResponse) => void
}) {
  const formId = useId()
  const queryClient = useQueryClient()
  const create = handler.capabilities.create
  const { fields, skipped } = schemaFields(handler.create_schema)
  // 填到一半的內容存在這個分頁的 sessionStorage：切換頁面、重新整理都還在，建立成功或按「清除」才清掉
  const formKey = `generate_form:${handler.name}`
  const tagsKey = `generate_tags:${handler.name}`
  const [storedValues, setStoredValues] = useSessionStorage<Record<string, string>>(formKey, {})
  const [tags, setTags] = useSessionStorage<string[]>(tagsKey, [])
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const values = useMemo(() => ({ ...initialValues(fields), ...storedValues }), [storedValues])
  const [errors, setErrors] = useState<FormErrors | null>(null)
  const [result, setResult] = useState<CreateNoteResponse | null>(null)

  // 牌組固定的卡片不看牌組樹
  const fixedDeck = create.deck === 'fixed' ? create.fixed_deck : null
  const deck = fixedDeck ?? (deckStatus === 'ok' ? selectedDeck : null)
  const missing = missingRequired(fields, values)

  const mutation = useMutation({
    mutationFn: () =>
      FluencyTidesAPI.createNote(handler.name, { deck: deck!, note_type: create.note_type!, params: values, tags }),
    onSuccess: (data) => {
      setResult(data)
      setErrors(null)
      removeStored('session', formKey)
      removeStored('session', tagsKey)
      toast.success(`已建立 ${data.note_ids.length} 張筆記`, { description: `牌組：${data.deck}` })
      if (data.sync_warning) toast.warning('同步 AnkiWeb 失敗', { description: data.sync_warning })
      for (const queryKey of [['graph'], ['cards'], ['decks'], ['tags']]) queryClient.invalidateQueries({ queryKey })
      onCreated?.(data)
    },
    onError: (err) => {
      setResult(null)
      const mapped = formErrorsFrom(err, fields.map((field) => field.name))
      if (mapped) {
        setErrors(mapped)
        return
      }
      setErrors(null)
      if (!isBackendError(err)) console.error('[CardGenerator] 建立失敗', err)
      const info = describeApiError(err)
      toast.error(failureTitle(info.status, '建立'), { description: info.message })
    },
  })

  const formReady = handler.create_schema !== null && create.note_type !== null
  const canSubmit = formReady && deck !== null && missing.length === 0 && !mutation.isPending

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (canSubmit) mutation.mutate()
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4" aria-busy={mutation.isPending}>
      <DeckSummary
        id={`${formId}-deck`}
        fixedDeck={fixedDeck}
        selectedDeck={selectedDeck}
        deckStatus={deckStatus}
        noteType={create.note_type}
      />

      {!formReady && (
        <ErrorNote title="暫時無法建立" error={{ error_code: 'UNAVAILABLE', message: create.reason ?? '沒有建卡表單' }} />
      )}

      {fields.map((field) => (
        <SchemaFieldInput
          key={field.name}
          id={`${formId}-${field.name}`}
          field={field}
          value={values[field.name] ?? ''}
          error={errors?.fields[field.name]}
          disabled={mutation.isPending}
          onChange={(value) => setStoredValues((prev) => ({ ...prev, [field.name]: value }))}
        />
      ))}

      <TagInput handlerName={handler.name} tags={tags} onChange={setTags} disabled={mutation.isPending} />

      {skipped.length > 0 && (
        <p className="text-xs text-muted-foreground">網頁表單不提供這些參數：{skipped.join('、')}</p>
      )}
      {errors && errors.other.length > 0 && (
        <ErrorNote title="建立失敗" error={{ error_code: 'VALIDATION', message: errors.other.join('；') }} />
      )}

      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="submit"
          disabled={!canSubmit}
          aria-describedby={deck === null && !fixedDeck ? `${formId}-deck` : undefined}
        >
          {mutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          {mutation.isPending ? '建立中、同步中…' : '建立'}
        </Button>
        <Button
          type="button"
          variant="ghost"
          disabled={
            mutation.isPending || (!Object.values(storedValues).some((value) => value.trim() !== '') && tags.length === 0)
          }
          onClick={() => {
            removeStored('session', formKey)
            removeStored('session', tagsKey)
            setErrors(null)
            setResult(null)
          }}
        >
          <Eraser className="mr-2 h-4 w-4" />
          清除
        </Button>
      </div>
      {mutation.isPending && (
        <p role="status" className="text-xs text-muted-foreground">
          寫入 Anki 後會同步 AnkiWeb，最久可能要等一分鐘。
        </p>
      )}

      {result && <CreatedNotice result={result} />}
    </form>
  )
}
