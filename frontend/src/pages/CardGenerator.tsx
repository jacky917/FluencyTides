/**
 * Card Generator 頁面（handlers 計畫書 §6、D9、D13）。
 * Card Generator page.
 *
 * 版面與 Cards 相同：牌組樹（並排時在左、窄螢幕時在上方）決定新卡建在哪個牌組，選「全部牌組」
 * 或已不存在的牌組時無法建立；另一側是建卡表單。
 *
 * - 卡片類型依 `capabilities.create.channels` 呈現：含 web 可選；只能在 TG、本機腳本建立的反灰並
 *   顯示原因；沒有任何管道的不顯示。前端不自行判斷，只看宣告（`lib/handlerCapabilities.ts`）。
 * - 填表流程（`flow="form"`）：表單由 `create_schema` 產生（`lib/createForm.ts`），不呼叫 LLM。
 * - 預覽流程（`flow="preview"`，目前只有三語對話卡）：先以 `draft_schema` 的表單請 LLM 生成草稿，勾選後
 *   建立或廢棄（`components/generate/PreviewCreate.tsx`）。
 * - 填到一半的內容（與預覽流程的草稿）存在分頁的 sessionStorage，切換頁面、重新整理都還在；「清除」一鍵清空。
 * - 後端寫入後會同步 AnkiWeb 並等它完成，所以送出期間顯示「建立中、同步中」；422 的欄位錯誤標在
 *   欄位旁。
 *
 * The same layout as Cards. Handlers appear per their declared channels; the
 * form comes from `create_schema`; field errors are shown next to fields.
 */

import { useId, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Eraser, Loader2, PlusCircle } from 'lucide-react'
import { toast } from 'sonner'
import { FluencyTidesAPI } from '../api/client'
import { DeckTreePanel } from '../components/shared/DeckTreePanel'
import { ErrorNote } from '../components/shared/ErrorNote'
import { CollapsibleSection, CreatedNotice, DeckSummary, SchemaFieldInput } from '../components/generate/formParts'
import { PreviewCreate } from '../components/generate/PreviewCreate'
import { TagInput } from '../components/generate/TagInput'
import { PageHeader } from '../components/shared/PageHeader'
import { Panel } from '../components/shared/Panel'
import { SplitLayout } from '../components/shared/SplitLayout'
import { Button } from '../components/ui/button'
import { Skeleton } from '../components/ui/skeleton'
import { removeStored, useLocalStorage, useSessionStorage } from '../hooks/useStoredState'
import { useDeckStatus, type DeckStatus } from '../hooks/useDeckStatus'
import { usePanelWidths } from '../hooks/usePanelWidths'
import { describeApiError, failureTitle, isBackendError, retryTransient } from '../lib/apiError'
import { GENERATE_LAYOUT } from '../lib/pageLayouts'
import { formErrorsFrom, initialValues, missingRequired, schemaFields, type FormErrors } from '../lib/createForm'
import { createAvailability, type CreateAvailability } from '../lib/handlerCapabilities'
import { cn } from '../lib/utils'
import type { CreateNoteResponse, HandlerInfo } from '../types/api'

/** 選單上的一種卡片。A handler on the picker. */
interface PickerEntry {
  handler: HandlerInfo
  availability: Exclude<CreateAvailability, { kind: 'hidden' }>
}

/**
 * 卡片產生器頁面：在牌組樹選牌組，再選卡片類型、填表建立。
 * Card generator page: pick the deck, then a card type, and fill in the form.
 * @returns 卡片產生器頁面 JSX。Card generator page JSX.
 */
export default function CardGenerator() {
  const [selectedDeck, setSelectedDeck] = useLocalStorage<string | null>(
    'generate_selectedDeck',
    import.meta.env.VITE_DEFAULT_DECK || null,
  )
  const [deckToggled, setDeckToggled] = useLocalStorage<Record<string, boolean>>('generate_deck_toggled', {})
  const [storedHandler, setStoredHandler] = useLocalStorage<string | null>('generate_handler', null)
  // 卡片類型選單是否展開；選好類型後自動收起，標題列仍看得到選了哪一種
  const [pickerOpen, setPickerOpen] = useLocalStorage<boolean>('generate_picker_open', true)
  const panels = usePanelWidths(GENERATE_LAYOUT)

  const {
    data: handlers,
    isPending: handlersPending,
    error: handlersError,
  } = useQuery({ queryKey: ['handlers'], queryFn: FluencyTidesAPI.listHandlers, retry: retryTransient })
  // 記住的牌組可能已在 Anki 被刪除或改名，後端遇到不存在的牌組會自動建立，所以要確認牌組還在才能
  // 建立；清單還沒載入或載入失敗時無法確認，也不能建立
  const deckStatus = useDeckStatus(selectedDeck)

  const entries: PickerEntry[] = (handlers ?? []).flatMap((handler) => {
    const availability = createAvailability(handler.capabilities.create)
    return availability.kind === 'hidden' ? [] : [{ handler, availability }]
  })
  const selectable = entries.filter((entry) => entry.availability.kind === 'selectable').map((entry) => entry.handler)
  // 記住的那種還能從網頁建立就用它，否則用第一個可選的
  const current = selectable.find((handler) => handler.name === storedHandler) ?? selectable[0]

  const formPanel = (sideBySide: boolean) => (
    <Panel
      scrollable={sideBySide}
      title={
        <>
          <PlusCircle className="h-4 w-4" />
          建立卡片
        </>
      }
      contentClassName="space-y-6"
    >
      {handlersPending ? (
        <div className="space-y-3">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : handlersError ? (
        <ErrorNote title="無法載入卡片類型，暫時無法建立卡片" error={handlersError} />
      ) : (
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
              />
            ) : (
              <CreateForm
                key={current.name}
                handler={current}
                selectedDeck={selectedDeck}
                deckStatus={deckStatus}
              />
            )
          ) : (
            <p className="text-sm text-muted-foreground">目前沒有能在網頁建立的卡片類型。</p>
          )}
        </>
      )}
    </Panel>
  )

  return (
    <SplitLayout
      header={
        <PageHeader
          title="Card Generator"
          description="在牌組樹選牌組，再選卡片類型建立。三語對話卡由 LLM 生成說法、勾選後建立；其他卡片手動填表。"
        />
      }
      panels={panels}
      columns={[
        {
          id: 'deck',
          resizable: 'deck',
          render: (sideBySide) => (
            <DeckTreePanel
              scrollable={sideBySide}
              toggled={deckToggled}
              onToggled={setDeckToggled}
              selected={selectedDeck}
              onSelect={setSelectedDeck}
            />
          ),
        },
        { id: 'main', render: formPanel },
      ]}
    />
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
 * @returns 表單 JSX。Form JSX.
 */
function CreateForm({
  handler,
  selectedDeck,
  deckStatus,
}: {
  handler: HandlerInfo
  selectedDeck: string | null
  deckStatus: DeckStatus
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
