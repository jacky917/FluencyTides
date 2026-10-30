/**
 * Card Generator 的預覽流程（`create.flow="preview"`）：以 `draft_schema` 的表單請 LLM 生成草稿 → 預覽、
 * 勾選 → 建立或廢棄（計畫書 `docs/wip/trilingual_web_create_FEAT_2026-10-30.md` §3）。
 *
 * 草稿不存在後端：輸入與草稿（含勾選、修改過的情境）存在這個分頁的 sessionStorage（`useSessionStorage`），
 * 切換頁面、重新整理都還在；生成中離開頁面，結果回來時照樣存下。建立時把勾選的內容送到一般的建卡端點，
 * 成功後清空；「廢棄」只丟掉草稿、回到輸入；「清除」把輸入與草稿一起清掉。預覽畫面依卡片類型而定，
 * 目前只有三語對話卡。
 *
 * The preview create flow: draft form → LLM draft → select → create or discard.
 */

import { useId, useMemo, useState } from 'react'
import { useIsMutating, useMutation, useQueryClient } from '@tanstack/react-query'
import { Eraser, Loader2, Sparkles } from 'lucide-react'
import { toast } from 'sonner'
import { FluencyTidesAPI } from '../../api/client'
import { removeStored, useSessionStorage, writeStored } from '../../hooks/useStoredState'
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
  // 輸入與草稿依卡片類型分開存，切換類型再回來也還在
  const formKey = `generate_form:${handler.name}`
  const draftKey = `generate_draft:${handler.name}`
  const [storedValues, setStoredValues] = useSessionStorage<Record<string, string>>(formKey, {})
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const values = useMemo(() => ({ ...initialValues(fields), ...storedValues }), [storedValues])
  const [inputErrors, setInputErrors] = useState<FormErrors | null>(null)
  const [state, setState] = useSessionStorage<DraftState | null>(draftKey, null)
  const [createError, setCreateError] = useState<string | null>(null)
  const [result, setResult] = useState<CreateNoteResponse | null>(null)

  const fixedDeck = create.deck === 'fixed' ? create.fixed_deck : null
  const deck = fixedDeck ?? (deckMissing ? null : selectedDeck)
  const ready = handler.draft_schema && create.note_type !== null && PREVIEW_HANDLERS.has(handler.name)

  // 生成中離開再回來時，新的元件看不到原本那次呼叫的 isPending：用 mutationKey 查有沒有還在跑的
  const draftKeyFilter = { mutationKey: ['draft', handler.name] }
  const generating = useIsMutating(draftKeyFilter) > 0
  const draftMutation = useMutation({
    ...draftKeyFilter,
    mutationFn: (input: Record<string, string>) => FluencyTidesAPI.createDraft(handler.name, input),
    // 這裡的回呼在元件卸載後也會執行（TanStack Query v5）：草稿直接寫進 sessionStorage，回到頁面就看得到
    onSuccess: (data) => {
      const draft = readDraft(data.params)
      writeStored<DraftState>('session', draftKey, {
        draft,
        selection: selectAll(draft),
        prompt: draft.prompt,
        context: draft.context,
      })
      toast.success('三語草稿已生成', { description: '在 Card Generator 勾選後建立' })
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
      removeStored('session', draftKey)
      removeStored('session', formKey)
      setCreateError(null)
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

  const busy = generating || createMutation.isPending
  const missing = missingRequired(fields, values)
  const canGenerate = Boolean(ready) && missing.length === 0 && !busy
  const chosen = state ? selectedCount(state.selection) : 0
  const canCreate = state !== null && deck !== null && chosen > 0 && state.prompt.trim() !== '' && !busy

  const handleGenerate = (e: React.FormEvent) => {
    e.preventDefault()
    if (canGenerate) draftMutation.mutate(values)
  }

  /** 一鍵清除：輸入、草稿與提示一起清掉（生成中仍會在完成時存下草稿）。Clears inputs and the draft. */
  const clearAll = () => {
    removeStored('session', draftKey)
    removeStored('session', formKey)
    setInputErrors(null)
    setCreateError(null)
    setResult(null)
  }
  const hasInput = Object.values(storedValues).some((value) => value.trim() !== '') || state !== null
  const clearButton = (
    <Button type="button" variant="ghost" disabled={busy || !hasInput} onClick={clearAll}>
      <Eraser className="mr-2 h-4 w-4" />
      清除
    </Button>
  )

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
          {clearButton}
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
    <form onSubmit={handleGenerate} className="space-y-4" aria-busy={generating}>
      {deckSummary}
      {fields.map((field) => (
        <SchemaFieldInput
          key={field.name}
          id={`${formId}-${field.name}`}
          field={field}
          value={values[field.name] ?? ''}
          error={inputErrors?.fields[field.name]}
          disabled={generating}
          onChange={(value) => setStoredValues((prev) => ({ ...prev, [field.name]: value }))}
        />
      ))}
      {inputErrors && inputErrors.other.length > 0 && (
        <ErrorNote title="無法生成" error={{ error_code: 'VALIDATION', message: inputErrors.other.join('；') }} />
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" disabled={!canGenerate}>
          {generating ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Sparkles className="mr-2 h-4 w-4" />}
          {generating ? '生成中…' : '生成'}
        </Button>
        {clearButton}
      </div>
      {generating && (
        <p role="status" className="text-xs text-muted-foreground">
          LLM 正在寫三種語言的說法，通常要半分鐘到一分鐘。可以先去別頁，生成好會通知，回來就看得到。
        </p>
      )}
      {result && <CreatedNotice result={result} />}
    </form>
  )
}
