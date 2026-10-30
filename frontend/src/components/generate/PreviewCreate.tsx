/**
 * Card Generator 的預覽流程（`create.flow="preview"`）：以 `draft_schema` 的表單請 LLM 生成草稿 → 預覽、
 * 勾選 → 建立或廢棄（計畫書 `docs/wip/trilingual_web_create_FEAT_2026-10-30.md` §3）。
 *
 * 草稿不存在後端：生成結果只在這個元件裡，建立時把勾選的內容送到一般的建卡端點；廢棄就是丟掉草稿、
 * 回到輸入（輸入的內容保留）。預覽畫面依卡片類型而定，目前只有三語對話卡。
 *
 * The preview create flow: draft form → LLM draft → select → create or discard.
 */

import { useId, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Loader2, Sparkles } from 'lucide-react'
import { toast } from 'sonner'
import { FluencyTidesAPI } from '../../api/client'
import { describeApiError, failureTitle, isBackendError } from '../../lib/apiError'
import { formErrorsFrom, initialValues, missingRequired, schemaFields, type FormErrors } from '../../lib/createForm'
import {
  readDraft,
  selectAll,
  selectedCount,
  selectedParams,
  type DraftSelection,
} from '../../lib/trilingualDraft'
import type { CreateNoteResponse, HandlerInfo, TrilingualCreateParams } from '../../types/api'
import { ErrorNote } from '../shared/ErrorNote'
import { Button } from '../ui/button'
import { CreatedNotice, DeckSummary, SchemaFieldInput } from './formParts'
import { TrilingualDraftPreview } from './TrilingualDraftPreview'

/** 有預覽畫面的 handler。Handlers with a preview screen. */
const PREVIEW_HANDLERS = new Set(['speaking_trilingual'])

/** 一份草稿與它的編輯狀態。A draft and its edits. */
interface DraftState {
  draft: TrilingualCreateParams
  selection: DraftSelection
  prompt: string
  context: string
}

/**
 * 預覽流程的建卡。The preview create flow.
 * @param props.handler - 預覽流程的 handler。The handler.
 * @param props.selectedDeck - 牌組樹選的牌組。The deck picked in the tree.
 * @param props.deckMissing - 選的牌組是否已不存在。Whether that deck is gone.
 * @returns 建卡畫面 JSX。The create screen JSX.
 */
export function PreviewCreate({
  handler,
  selectedDeck,
  deckMissing,
}: {
  handler: HandlerInfo
  selectedDeck: string | null
  deckMissing: boolean
}) {
  const formId = useId()
  const queryClient = useQueryClient()
  const create = handler.capabilities.create
  const { fields } = schemaFields(handler.draft_schema ?? null)
  const fieldNames = fields.map((field) => field.name)
  const [values, setValues] = useState(() => initialValues(fields))
  const [inputErrors, setInputErrors] = useState<FormErrors | null>(null)
  const [state, setState] = useState<DraftState | null>(null)
  const [createError, setCreateError] = useState<string | null>(null)
  const [result, setResult] = useState<CreateNoteResponse | null>(null)

  const fixedDeck = create.deck === 'fixed' ? create.fixed_deck : null
  const deck = fixedDeck ?? (deckMissing ? null : selectedDeck)
  const ready = handler.draft_schema && create.note_type !== null && PREVIEW_HANDLERS.has(handler.name)

  const draftMutation = useMutation({
    mutationFn: () => FluencyTidesAPI.createDraft(handler.name, values),
    onSuccess: (data) => {
      const draft = readDraft(data.params)
      setState({ draft, selection: selectAll(draft), prompt: draft.prompt, context: draft.context })
      setInputErrors(null)
      setCreateError(null)
      setResult(null)
    },
    onError: (err) => {
      const mapped = formErrorsFrom(err, fieldNames)
      if (mapped) {
        setInputErrors(mapped)
        return
      }
      setInputErrors(null)
      if (!isBackendError(err)) console.error('[PreviewCreate] 生成失敗', err)
      // 草稿沒有寫入任何東西，失敗一律是「無法生成」
      toast.error('無法生成', { description: describeApiError(err).message })
    },
  })

  const createMutation = useMutation({
    mutationFn: (params: TrilingualCreateParams) =>
      FluencyTidesAPI.createNote(handler.name, {
        deck: deck!,
        note_type: create.note_type!,
        params: params as unknown as Record<string, unknown>,
      }),
    onSuccess: (data) => {
      setResult(data)
      setState(null)
      setCreateError(null)
      setValues(initialValues(fields))
      toast.success(`已建立 ${data.note_ids.length} 張筆記`, { description: `牌組：${data.deck}` })
      if (data.sync_warning) toast.warning('同步 AnkiWeb 失敗', { description: data.sync_warning })
      for (const queryKey of [['graph'], ['cards'], ['decks']]) queryClient.invalidateQueries({ queryKey })
    },
    onError: (err) => {
      const mapped = formErrorsFrom(err, [])
      if (mapped) {
        setCreateError(mapped.other.join('；'))
        return
      }
      setCreateError(null)
      if (!isBackendError(err)) console.error('[PreviewCreate] 建立失敗', err)
      const info = describeApiError(err)
      toast.error(failureTitle(info.status, '建立'), { description: info.message })
    },
  })

  const busy = draftMutation.isPending || createMutation.isPending
  const missing = missingRequired(fields, values)
  const canGenerate = Boolean(ready) && missing.length === 0 && !busy
  const chosen = state ? selectedCount(state.selection) : 0
  const canCreate = state !== null && deck !== null && chosen > 0 && state.prompt.trim() !== '' && !busy

  const handleGenerate = (e: React.FormEvent) => {
    e.preventDefault()
    if (canGenerate) draftMutation.mutate()
  }

  const handleCreate = () => {
    if (state && canCreate) {
      createMutation.mutate(selectedParams(state.draft, state.selection, state.prompt.trim(), state.context.trim()))
    }
  }

  const deckSummary = (
    <DeckSummary
      id={`${formId}-deck`}
      fixedDeck={fixedDeck}
      selectedDeck={selectedDeck}
      deckMissing={deckMissing}
      noteType={create.note_type}
    />
  )

  if (!ready) {
    return (
      <div className="space-y-4">
        {deckSummary}
        <ErrorNote
          title="暫時無法建立"
          error={{ error_code: 'UNAVAILABLE', message: create.reason ?? '網頁還沒有這種卡片的預覽畫面' }}
        />
      </div>
    )
  }

  if (state) {
    return (
      <div className="space-y-4" aria-busy={createMutation.isPending}>
        {deckSummary}
        <p className="break-words rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
          背景：{values.background}
        </p>
        <TrilingualDraftPreview
          draft={state.draft}
          selection={state.selection}
          onSelectionChange={(selection) => setState({ ...state, selection })}
          prompt={state.prompt}
          onPromptChange={(prompt) => setState({ ...state, prompt })}
          context={state.context}
          onContextChange={(context) => setState({ ...state, context })}
          disabled={createMutation.isPending}
        />
        {createError && <ErrorNote title="建立失敗" error={{ error_code: 'VALIDATION', message: createError }} />}
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            disabled={!canCreate}
            onClick={handleCreate}
            aria-describedby={deck === null && !fixedDeck ? `${formId}-deck` : undefined}
          >
            {createMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {createMutation.isPending ? '建立中、同步中…' : `建立（${chosen} 句）`}
          </Button>
          <Button type="button" variant="outline" disabled={createMutation.isPending} onClick={() => setState(null)}>
            廢棄
          </Button>
        </div>
        {createMutation.isPending && (
          <p role="status" className="text-xs text-muted-foreground">
            寫入 Anki 後會同步 AnkiWeb，最久可能要等一分鐘。
          </p>
        )}
        {chosen === 0 && <p className="text-xs text-muted-foreground">至少勾選一句才能建立。</p>}
      </div>
    )
  }

  return (
    <form onSubmit={handleGenerate} className="space-y-4" aria-busy={draftMutation.isPending}>
      {deckSummary}
      {fields.map((field) => (
        <SchemaFieldInput
          key={field.name}
          id={`${formId}-${field.name}`}
          field={field}
          value={values[field.name] ?? ''}
          error={inputErrors?.fields[field.name]}
          disabled={draftMutation.isPending}
          onChange={(value) => setValues((prev) => ({ ...prev, [field.name]: value }))}
        />
      ))}
      {inputErrors && inputErrors.other.length > 0 && (
        <ErrorNote title="無法生成" error={{ error_code: 'VALIDATION', message: inputErrors.other.join('；') }} />
      )}
      <Button type="submit" disabled={!canGenerate}>
        {draftMutation.isPending ? (
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        ) : (
          <Sparkles className="mr-2 h-4 w-4" />
        )}
        {draftMutation.isPending ? '生成中…' : '生成'}
      </Button>
      {draftMutation.isPending && (
        <p role="status" className="text-xs text-muted-foreground">
          LLM 正在寫三種語言的說法，通常要半分鐘到一分鐘。
        </p>
      )}
      {result && <CreatedNotice result={result} />}
    </form>
  )
}
