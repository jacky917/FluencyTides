/**
 * 建卡面板的內容：卡片類型選單，加上依建卡流程切換的表單（填表或預覽）。Card Generator 頁與卡片
 * 瀏覽頁的建卡畫面共用（`docs/archive/card_list_create_FEAT_2026-10-30.md` D3）。
 *
 * - 卡片類型依 `capabilities.create.channels` 呈現：含 web 可選；只能在 TG、本機腳本建立的反灰並
 *   顯示原因；沒有任何管道的不顯示。前端不自行判斷，只看宣告（`lib/handlerCapabilities.ts`）。
 * - 填表流程（`flow="form"`）：表單由 `create_schema` 產生（`lib/createForm.ts`），不呼叫 LLM。媒體欄位
 *   （`x-ui: audio`、`image`）是上傳／錄音元件：上傳只暫存在後端，建卡成功時才存進 Anki、失敗時 Anki 不變
 *   （`docs/wip/media_upload_FEAT_2026-10-20.md`）；上傳中、等待麥克風授權、錄音中、錄好還沒決定時不能送出。
 * - 預覽流程（`flow="preview"`，三語對話卡與外語糾錯）：見 `PreviewCreate.tsx`。
 * - 選的卡片類型、選單是否展開、填到一半的內容都記在 localStorage（關掉分頁再開還在）；鍵與頁面無關，
 *   兩個入口看到的是同一份。
 * - 全站同一時間只有一份建卡草稿（`docs/wip/server_drafts_FEAT_2026-11-04.md`）：有別種卡片的草稿時，
 *   不顯示表單，改顯示那份草稿的提示（前往、廢棄）；填表建卡也要等那份處理完。
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
import { useCreatedNotice } from '../../hooks/useCreatedNotice'
import { useCurrentDraft, useSetCurrentDraft } from '../../hooks/useServerDraft'
import { removeStored, useLocalStorage } from '../../hooks/useStoredState'
import type { DeckStatus } from '../../hooks/useDeckStatus'
import { describeApiError, failureTitle, isBackendError, retryTransient } from '../../lib/apiError'
import {
  deckBlockedReason,
  deckBlocksForm,
  formErrorsFrom,
  formParams,
  hasInput,
  initialRows,
  initialValues,
  missingRequired,
  normalizeRows,
  schemaFields,
  type FormValue,
  type RowsValue,
  type FormErrors,
} from '../../lib/createForm'
import { createAvailability, type CreateAvailability } from '../../lib/handlerCapabilities'
import { mediaFieldState } from '../../lib/mediaUpload'
import { CURRENT_DRAFT_QUERY_KEY, draftFromError, timeLabel } from '../../lib/serverDraft'
import { cn } from '../../lib/utils'
import type { CreateNoteResponse, HandlerInfo, ServerDraft } from '../../types/api'
import { ErrorNote } from '../shared/ErrorNote'
import { MediaField } from '../shared/MediaField'
import { Button } from '../ui/button'
import { Skeleton } from '../ui/skeleton'
import { CollapsibleSection, CreatedNotice, DeckRequiredNotice, DeckSummary, RowsFieldInput, SchemaFieldInput, TagsFieldInput } from './formParts'
import { PreviewCreate } from './PreviewCreate'
import { useDiscardDraft } from './useDiscardDraft'
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
   * 建立成功的提示上「查看新卡片」按下時；省略時提示改顯示到 Cards 頁的連結。建卡成功後表單一律留在
   * 原地、清空回預設值，方便接著建下一張。有傳時提示存在 sessionStorage（見 `useCreatedNotice`），
   * 面板重新掛載也還在。
   * Views a created note from the success notice; without it the notice links
   * to the Cards page. The form always stays, reset, after creating. When given,
   * the notice survives remounts.
   */
  onViewCreated?: (result: CreateNoteResponse) => void
}

/**
 * 卡片類型選單與建卡表單。The card type picker and the create form.
 * @returns 面板內容 JSX。The panel body JSX.
 */
export function CreatePanelBody({ selectedDeck, deckStatus, onViewCreated }: CreatePanelBodyProps) {
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
  // 全站唯一的建卡草稿；是別種卡片的就先處理它
  const { data: draft } = useCurrentDraft()
  const otherDraft = draft && current && draft.handler !== current.name ? draft : null

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
      {current && otherDraft ? (
        <OtherDraftNotice
          draft={otherDraft}
          title={(handlers ?? []).find((handler) => handler.name === otherDraft.handler)?.title ?? otherDraft.handler}
          canGo={selectable.some((handler) => handler.name === otherDraft.handler)}
          onGo={() => {
            setStoredHandler(otherDraft.handler)
            setPickerOpen(false)
          }}
        />
      ) : current ? (
        // 換卡片類型時重新掛載，表單內容與錯誤一起重設
        current.capabilities.create.flow === 'preview' ? (
          <PreviewCreate
            key={current.name}
            handler={current}
            selectedDeck={selectedDeck}
            deckStatus={deckStatus}
            onViewCreated={onViewCreated}
          />
        ) : (
          <CreateForm
            key={current.name}
            handler={current}
            selectedDeck={selectedDeck}
            deckStatus={deckStatus}
            onViewCreated={onViewCreated}
          />
        )
      ) : (
        <p className="text-sm text-muted-foreground">目前沒有能在網頁建立的卡片類型。</p>
      )}
    </>
  )
}

/**
 * 有別種卡片的草稿時，取代表單的提示：網頁可建立的類型都能「前往」；可建立、失敗時可以廢棄。
 * 填表建卡佔用中（`form_creating`，另一個分頁正在建立）時只說明，等它結束。
 * Shown instead of the form while another card type's draft exists.
 * @param props.draft - 那份草稿。The draft.
 * @param props.title - 那種卡片的名稱。Its card type title.
 * @param props.canGo - 能不能在這裡切過去（網頁可建立的類型）。Whether it can be switched to.
 * @param props.onGo - 切到那種卡片。Switches to it.
 * @returns 提示 JSX。Notice JSX.
 */
function OtherDraftNotice({
  draft,
  title,
  canGo,
  onGo,
}: {
  draft: ServerDraft
  title: string
  canGo: boolean
  onGo: () => void
}) {
  const discard = useDiscardDraft()
  if (draft.status === 'form_creating') {
    return (
      <p role="status" className="flex items-center gap-2 rounded-md border px-3 py-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        正在建立一張「{title}」，請稍候。一次只能建一張卡片。
      </p>
    )
  }
  const statusText = {
    generating: `生成中（${timeLabel(draft.generation_started_at)} 開始）`,
    ready: '已生成，等你建立或廢棄',
    failed: '生成失敗',
    creating: '建立中',
    form_creating: '建立中',
  }[draft.status]
  const canDiscard = draft.status === 'ready' || draft.status === 'failed'
  return (
    <div role="status" className="space-y-3 rounded-md border px-3 py-3 text-sm">
      <p>
        目前有一份「{title}」的草稿（{statusText}）。一次只能建一張卡片：請先建立或廢棄那份草稿，才能建其他卡片。
      </p>
      <div className="flex flex-wrap gap-2">
        {canGo && (
          <Button type="button" size="sm" onClick={onGo}>
            前往那份草稿
          </Button>
        )}
        {canDiscard && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={discard.isPending}
            onClick={() => discard.discard(draft)}
          >
            廢棄
          </Button>
        )}
      </div>
      {discard.dialog}
    </div>
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
 * @param props.onViewCreated - 提示上的「查看新卡片」（見 {@link CreatePanelBodyProps}）。Views the created note.
 * @returns 表單 JSX。Form JSX.
 */
function CreateForm({
  handler,
  selectedDeck,
  deckStatus,
  onViewCreated,
}: {
  handler: HandlerInfo
  selectedDeck: string | null
  deckStatus: DeckStatus
  onViewCreated?: (result: CreateNoteResponse) => void
}) {
  const formId = useId()
  const queryClient = useQueryClient()
  const create = handler.capabilities.create
  const { fields, skipped } = schemaFields(handler.create_schema)
  // 填到一半的內容存在 localStorage：關掉分頁再開還在，建立成功或按「清除」才清掉
  const formKey = `generate_form:${handler.name}`
  const tagsKey = `generate_tags:${handler.name}`
  const [storedValues, setStoredValues] = useLocalStorage<Record<string, FormValue>>(formKey, {})
  const [tags, setTags] = useLocalStorage<string[]>(tagsKey, [])
  const setCurrentDraft = useSetCurrentDraft()
  const values = useMemo<Record<string, FormValue>>(
    () => normalizeRows(fields, { ...initialValues(fields), ...initialRows(fields), ...storedValues }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [storedValues],
  )
  const [errors, setErrors] = useState<FormErrors | null>(null)
  const [result, setResult] = useCreatedNotice(handler.name, onViewCreated !== undefined)
  // 媒體欄位還沒做完的事（上傳中、等待麥克風授權、錄音中、錄好還沒決定）：欄位名 → 原因；有任何一個就先不能送出
  const [mediaBusy, setMediaBusy] = useState<Record<string, string>>({})
  const mediaBusyReason = Object.values(mediaBusy)[0] ?? null

  // 牌組固定的卡片不看牌組樹
  const fixedDeck = create.deck === 'fixed' ? create.fixed_deck : null
  const deck = fixedDeck ?? (deckStatus === 'ok' ? selectedDeck : null)
  const missing = missingRequired(fields, values)

  const mutation = useMutation({
    mutationFn: () =>
      FluencyTidesAPI.createNote(handler.name, {
        deck: deck!,
        note_type: create.note_type!,
        params: formParams(fields, values),
        tags,
      }),
    onSuccess: (data) => {
      setResult(data)
      setErrors(null)
      removeStored('local', formKey)
      removeStored('local', tagsKey)
      toast.success(`已建立 ${data.note_ids.length} 張筆記`, { description: `牌組：${data.deck}` })
      if (data.sync_warning) toast.warning('同步 AnkiWeb 失敗', { description: data.sync_warning })
      for (const queryKey of [['graph'], ['cards'], ['decks'], ['tags']]) queryClient.invalidateQueries({ queryKey })
    },
    // 建卡期間佔用了全站的建卡槽位；結束後重讀，其他畫面才不會停在「建立中」
    onSettled: () => queryClient.invalidateQueries({ queryKey: CURRENT_DRAFT_QUERY_KEY }),
    onError: (err) => {
      setResult(null)
      // 另一個地方剛開始了一份草稿或正在建卡：放進快取（是別種卡片的就改顯示那份草稿的提示），並說明原因
      const latest = draftFromError(err)
      if (latest !== undefined) {
        setCurrentDraft(latest)
        toast.error('現在不能建立', { description: describeApiError(err).message })
        return
      }
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
  const canSubmit =
    formReady && deck !== null && missing.length === 0 && mediaBusyReason === null && !mutation.isPending
  // 不能送出的原因寫在按鈕旁：牌組說明在表單最上方，欄位多時看不到
  const blockedReason = !formReady
    ? null
    : (deckBlockedReason(fixedDeck, selectedDeck, deckStatus) ??
      (missing.length > 0
        ? `還沒填：${missing.map((name) => fields.find((field) => field.name === name)?.title ?? name).join('、')}。`
        : mediaBusyReason !== null
          ? `${mediaBusyReason}，完成後才能建立。`
          : null))

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (canSubmit) mutation.mutate()
  }

  // 牌組不能用：一開始就不顯示表單（建立進行中照常顯示）。填到一半的內容還在 sessionStorage。
  // 媒體欄位還在上傳或錄音時也照常顯示：換掉表單會卸載元件、靜默取消上傳；做完後檔名存進表單，才換成提示
  if (
    formReady &&
    !mutation.isPending &&
    mediaBusyReason === null &&
    deckBlocksForm(fixedDeck, selectedDeck, deckStatus)
  ) {
    const kept = Object.values(storedValues).some(hasInput) || tags.length > 0
    return (
      <DeckRequiredNotice
        reason={deckBlockedReason(fixedDeck, selectedDeck, deckStatus) ?? ''}
        kept={kept ? '填到一半的內容還在，選好牌組就會出現。' : null}
      />
    )
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

      {fields.map((field) => {
        const value = values[field.name]
        const onChange = (next: FormValue) => setStoredValues((prev) => ({ ...prev, [field.name]: next }))
        return field.media ? (
          <MediaField
            key={field.name}
            id={`${formId}-${field.name}`}
            kind={field.media}
            label={field.title}
            required={field.required}
            description={field.description}
            state={mediaFieldState(field.media, '', typeof value === 'string' ? value : '')}
            error={errors?.fields[field.name]}
            disabled={mutation.isPending}
            onUploaded={onChange}
            onClear={() => onChange('')}
            onBusyChange={(reason) =>
              setMediaBusy((prev) => {
                if ((prev[field.name] ?? null) === reason) return prev
                const next = { ...prev }
                if (reason === null) delete next[field.name]
                else next[field.name] = reason
                return next
              })
            }
          />
        ) : field.rows?.tags ? (
          <TagsFieldInput
            key={field.name}
            id={`${formId}-${field.name}`}
            field={field}
            value={Array.isArray(value) ? (value as string[]) : []}
            error={errors?.fields[field.name]}
            disabled={mutation.isPending}
            onChange={onChange}
          />
        ) : field.rows ? (
          <RowsFieldInput
            key={field.name}
            id={`${formId}-${field.name}`}
            field={field}
            value={Array.isArray(value) ? (value as RowsValue) : []}
            error={errors?.fields[field.name]}
            disabled={mutation.isPending}
            onChange={onChange}
          />
        ) : (
          <SchemaFieldInput
            key={field.name}
            id={`${formId}-${field.name}`}
            field={field}
            value={typeof value === 'string' ? value : ''}
            error={errors?.fields[field.name]}
            disabled={mutation.isPending}
            onChange={onChange}
          />
        )
      })}

      <TagInput handlerName={handler.name} tags={tags} onChange={setTags} disabled={mutation.isPending} />

      {skipped.length > 0 && (
        <p className="text-xs text-muted-foreground">網頁表單不提供這些參數：{skipped.join('、')}</p>
      )}
      {errors && errors.other.length > 0 && (
        <ErrorNote title="建立失敗" error={{ error_code: 'VALIDATION', message: errors.other.join('；') }} />
      )}

      {!mutation.isPending && blockedReason && (
        <p id={`${formId}-blocked`} role="status" className="text-xs text-muted-foreground">
          {blockedReason}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="submit"
          disabled={!canSubmit}
          aria-describedby={!mutation.isPending && blockedReason ? `${formId}-blocked` : undefined}
        >
          {mutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          {mutation.isPending ? '建立中、同步中…' : '建立'}
        </Button>
        <Button
          type="button"
          variant="ghost"
          disabled={
            mutation.isPending || (!Object.values(storedValues).some(hasInput) && tags.length === 0)
          }
          onClick={() => {
            removeStored('local', formKey)
            removeStored('local', tagsKey)
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

      {result && <CreatedNotice result={result} onView={onViewCreated && (() => onViewCreated(result))} />}
    </form>
  )
}
