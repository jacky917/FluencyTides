/**
 * 建卡表單的預覽流程（`create.flow="preview"`，Card Generator 與卡片瀏覽頁共用）：以 `draft_schema` 的表單
 * 請 LLM 生成草稿 → 預覽、勾選 → 建立或廢棄（計畫書 `docs/archive/trilingual_web_create_FEAT_2026-10-30.md` §3）。
 *
 * 草稿存在後端記憶體（`docs/wip/server_drafts_FEAT_2026-11-04.md`）：全站同一時間只有一份，生成在後端背景跑，
 * 網頁只是去讀它，所以重新整理、關掉分頁、換裝置都不會遺失（後端重啟除外）。草稿只會因「建立」成功或
 * 「廢棄」消失；生成中不能廢棄，要等結果回來。預覽中的修改自動存回後端（`useDraftEditor`）。生成前的輸入
 * 存在 localStorage，建立成功才清掉。預覽畫面依卡片類型而定，目前只有三語對話卡。
 *
 * The preview create flow, backed by the server-side draft.
 */

import { useEffect, useId, useMemo, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Eraser, Loader2, RotateCw, Sparkles } from 'lucide-react'
import { toast } from 'sonner'
import { FluencyTidesAPI } from '../../api/client'
import { useCreatedNotice } from '../../hooks/useCreatedNotice'
import { useCurrentDraft, useDraftEditor, useSetCurrentDraft, type SaveStatus } from '../../hooks/useServerDraft'
import { readStored, removeStored, useLocalStorage, writeStored } from '../../hooks/useStoredState'
import { describeApiError, failureTitle, isBackendError } from '../../lib/apiError'
import {
  deckBlockedReason,
  deckBlocksForm,
  formErrorsFrom,
  initialValues,
  missingRequired,
  schemaFields,
  type FormErrors,
} from '../../lib/createForm'
import { draftBackupKey, draftFromError, inputAsValues, timeLabel } from '../../lib/serverDraft'
import {
  readDraft,
  selectAll,
  selectedCount,
  selectedParams,
  type DraftSelection,
} from '../../lib/trilingualDraft'
import type { CreateNoteResponse, HandlerInfo, ServerDraft, TrilingualCreateParams } from '../../types/api'
import type { DeckStatus } from '../../hooks/useDeckStatus'
import { ErrorNote } from '../shared/ErrorNote'
import { Button } from '../ui/button'
import { Skeleton } from '../ui/skeleton'
import { CreatedNotice, DeckRequiredNotice, DeckSummary, SchemaFieldInput } from './formParts'
import { TagInput } from './TagInput'
import { TrilingualDraftPreview } from './TrilingualDraftPreview'
import { useDiscardDraft } from './useDiscardDraft'

/** 有預覽畫面的 handler。Handlers with a preview screen. */
const PREVIEW_HANDLERS = new Set(['speaking_trilingual'])

/**
 * 一份草稿的編輯狀態（存回後端的 `state`，是 JSON 物件，所以用 type 而不是 interface）。
 * The editing state saved as the draft's `state`.
 */
type DraftState = {
  draft: TrilingualCreateParams
  selection: DraftSelection
  prompt: string
  context: string
  /** 要加的標籤（LLM 建議的預設全加，可增減）。The tags to add. */
  tags: string[]
}

/** 存檔狀態的文字。Labels of the save status. */
const SAVE_LABELS: Record<SaveStatus, string> = {
  saved: '已儲存',
  saving: '儲存中…',
  unsaved: '尚未儲存',
  offline: '尚未儲存（連不上伺服器，恢復後會自動存好）',
  conflict: '這份草稿在其他地方改過了',
}

/** 預覽流程的參數。Props of the preview flow. */
interface FlowProps {
  handler: HandlerInfo
  selectedDeck: string | null
  deckStatus: DeckStatus
  onViewCreated?: (result: CreateNoteResponse) => void
}

/** 各畫面共用的參數：建立成功的提示放在上層，換畫面（預覽 → 輸入）時還在。Props shared by the screens. */
interface ScreenProps extends FlowProps {
  deckSummary: React.ReactNode
  result: CreateNoteResponse | null
  setResult: (result: CreateNoteResponse | null) => void
}

/**
 * 預覽流程的建卡。The preview create flow.
 * @param props.handler - 預覽流程的 handler。The handler.
 * @param props.selectedDeck - 牌組樹選的牌組。The deck picked in the tree.
 * @param props.deckStatus - 牌組樹所選牌組的狀態（見 `useDeckStatus`）。The selected deck's status.
 * @param props.onViewCreated - 建立成功的提示上「查看新卡片」按下時；省略時提示改顯示到 Cards 頁的連結。
 *   Views a created note from the success notice.
 * @returns 建卡畫面 JSX。The create screen JSX.
 */
export function PreviewCreate(props: FlowProps) {
  const { handler, selectedDeck, deckStatus } = props
  const formId = useId()
  const create = handler.capabilities.create
  const { data: current, isPending, error } = useCurrentDraft()
  // 建立成功後預覽畫面換成輸入畫面：提示放在這一層才不會跟著消失
  const [result, setResult] = useCreatedNotice(handler.name, props.onViewCreated !== undefined)
  const ready = handler.draft_schema && create.note_type !== null && PREVIEW_HANDLERS.has(handler.name)
  const fixedDeck = create.deck === 'fixed' ? create.fixed_deck : null

  const deckSummary = (
    <DeckSummary
      id={`${formId}-deck`}
      fixedDeck={fixedDeck}
      selectedDeck={selectedDeck}
      deckStatus={deckStatus}
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
  if (isPending) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    )
  }
  // 從沒讀到過才擋住整個畫面；已有資料時背景重讀失敗一次（例如手機切回分頁時剛好斷線），只提示一行
  if (error && current === undefined) {
    return <ErrorNote title="無法讀取建卡草稿，暫時無法建立" error={error} />
  }
  const staleNote = error && (
    <p role="status" className="text-xs text-muted-foreground">
      暫時讀不到最新的草稿狀態，稍後會自動重試。
    </p>
  )
  // 別種卡片的草稿由 CreatePanelBody 顯示提示，不會進到這裡
  const mine = current && current.handler === handler.name ? current : null
  if (mine && (mine.status === 'ready' || mine.status === 'creating') && mine.result) {
    return (
      <>
        {staleNote}
        <ReadyPreview
          key={mine.id}
          {...props}
          draft={mine}
          deckSummary={deckSummary}
          result={result}
          setResult={setResult}
        />
      </>
    )
  }
  return (
    <>
      {staleNote}
      <InputScreen {...props} draft={mine} deckSummary={deckSummary} result={result} setResult={setResult} />
    </>
  )
}

/**
 * 輸入畫面：沒有草稿、生成中、生成失敗。The input screen: no draft, generating, or failed.
 * @returns 輸入畫面 JSX。The input screen JSX.
 */
function InputScreen({
  handler,
  selectedDeck,
  deckStatus,
  onViewCreated,
  draft,
  deckSummary,
  result,
  setResult,
}: ScreenProps & { draft: ServerDraft | null }) {
  const formId = useId()
  const setCurrent = useSetCurrentDraft()
  const create = handler.capabilities.create
  const { fields } = schemaFields(handler.draft_schema ?? null)
  const fieldNames = fields.map((field) => field.name)
  // 生成前的輸入存在 localStorage：關掉分頁再開還在，建立成功才清掉（D11）
  const formKey = `generate_form:${handler.name}`
  const [storedValues, setStoredValues] = useLocalStorage<Record<string, string>>(formKey, {})
  const [inputErrors, setInputErrors] = useState<FormErrors | null>(null)
  const generating = draft?.status === 'generating'
  const failed = draft?.status === 'failed' ? draft : null
  // 生成中顯示送出的輸入；其餘用本機存的
  const values = useMemo(
    () => ({ ...initialValues(fields), ...(generating ? inputAsValues(draft?.input ?? null) : storedValues) }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [storedValues, generating, draft?.input],
  )

  // 生成失敗時本機沒有輸入（例如換了裝置）：帶入當時的輸入，可以直接重試或改了再試
  useEffect(() => {
    if (!failed) return
    const local = readStored<Record<string, string>>('local', formKey, {})
    if (!Object.values(local).some((value) => value.trim() !== '')) {
      writeStored('local', formKey, inputAsValues(failed.input))
    }
  }, [failed, formKey])

  const onDraftError = (err: unknown, action: string) => {
    const latest = draftFromError(err)
    if (latest !== undefined) {
      setCurrent(latest)
      toast.error(`無法${action}`, { description: describeApiError(err).message })
      return
    }
    const mapped = formErrorsFrom(err, fieldNames)
    if (mapped) {
      setInputErrors(mapped)
      return
    }
    setInputErrors(null)
    if (!isBackendError(err)) console.error(`[PreviewCreate] ${action}失敗`, err)
    toast.error(`無法${action}`, { description: describeApiError(err).message })
  }

  const startMutation = useMutation({
    mutationFn: (input: Record<string, string>) => FluencyTidesAPI.startDraft(handler.name, input),
    onSuccess: (data) => {
      setCurrent(data)
      setInputErrors(null)
      setResult(null)
    },
    onError: (err) => onDraftError(err, '生成'),
  })
  const retryMutation = useMutation({
    mutationFn: ({ id, version, input }: { id: string; version: number; input: Record<string, string> }) =>
      FluencyTidesAPI.retryDraft(id, version, input),
    onSuccess: (data) => {
      setCurrent(data)
      setInputErrors(null)
    },
    onError: (err) => onDraftError(err, '重試'),
  })
  const discardMutation = useDiscardDraft()

  const sending = startMutation.isPending || retryMutation.isPending
  const busy = generating || sending || discardMutation.isPending
  const missing = missingRequired(fields, values)
  const canGenerate = missing.length === 0 && !busy

  const handleGenerate = (e: React.FormEvent) => {
    e.preventDefault()
    if (!canGenerate) return
    if (failed) retryMutation.mutate({ id: failed.id, version: failed.version, input: values })
    else startMutation.mutate(values)
  }

  /** 清除輸入（草稿不動）。Clears the inputs only. */
  const clearInputs = () => {
    removeStored('local', formKey)
    setInputErrors(null)
    setResult(null)
  }
  const hasInput = Object.values(storedValues).some((value) => value.trim() !== '')
  const fixedDeck = create.deck === 'fixed' ? create.fixed_deck : null

  // 牌組不能用：一開始就不給生成（生成中、失敗的草稿照常顯示，不打斷）。輸入還在 localStorage
  if (!draft && !sending && deckBlocksForm(fixedDeck, selectedDeck, deckStatus)) {
    return (
      <DeckRequiredNotice
        reason={deckBlockedReason(fixedDeck, selectedDeck, deckStatus) ?? ''}
        kept={hasInput ? '填到一半的內容還在，選好牌組就會出現。' : null}
      />
    )
  }

  return (
    <form onSubmit={handleGenerate} className="space-y-4" aria-busy={busy}>
      {deckSummary}
      {failed && (
        <ErrorNote
          title="生成失敗"
          error={{
            error_code: failed.error?.error_code ?? 'DRAFT_GENERATION_FAILED',
            message: `${failed.error?.message ?? '原因不明'}（可以直接重試，或改了輸入再試）`,
          }}
        />
      )}
      {fields.map((field) => (
        <SchemaFieldInput
          key={field.name}
          id={`${formId}-${field.name}`}
          field={field}
          value={values[field.name] ?? ''}
          error={inputErrors?.fields[field.name]}
          disabled={busy}
          onChange={(value) => setStoredValues((prev) => ({ ...prev, [field.name]: value }))}
        />
      ))}
      {inputErrors && inputErrors.other.length > 0 && (
        <ErrorNote title="無法生成" error={{ error_code: 'VALIDATION', message: inputErrors.other.join('；') }} />
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" disabled={!canGenerate}>
          {failed ? <RotateCw className="mr-2 h-4 w-4" /> : <Sparkles className="mr-2 h-4 w-4" />}
          {failed ? '重試' : '生成'}
        </Button>
        {(generating || failed) && (
          <Button
            type="button"
            variant="outline"
            disabled={generating || busy}
            onClick={() => draft && discardMutation.discard(draft)}
          >
            廢棄
          </Button>
        )}
        <Button type="button" variant="ghost" disabled={busy || !hasInput} onClick={clearInputs}>
          <Eraser className="mr-2 h-4 w-4" />
          清除
        </Button>
        {(generating || sending) && (
          <span role="status" className="inline-flex items-center gap-1 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            生成中…
          </span>
        )}
      </div>
      {generating && (
        <p className="text-xs text-muted-foreground">
          {timeLabel(draft?.generation_started_at ?? null) && `${timeLabel(draft?.generation_started_at ?? null)} 開始。`}
          LLM 正在寫三種語言的說法，通常要半分鐘到一分鐘。可以先去別頁或關掉分頁，生成好會通知，回來就看得到。
          結果回來前不能廢棄。
        </p>
      )}
      {result && <CreatedNotice result={result} onView={onViewCreated && (() => onViewCreated(result))} />}
      {discardMutation.dialog}
    </form>
  )
}

/**
 * 預覽畫面：可建立（與建立中）的草稿，修改自動存回後端。The preview screen of a ready draft.
 * @returns 預覽畫面 JSX。The preview screen JSX.
 */
function ReadyPreview({
  handler,
  selectedDeck,
  deckStatus,
  draft,
  deckSummary,
  setResult,
}: ScreenProps & { draft: ServerDraft }) {
  const formId = useId()
  const queryClient = useQueryClient()
  const setCurrent = useSetCurrentDraft()
  const create = handler.capabilities.create
  const formKey = `generate_form:${handler.name}`
  const [createError, setCreateError] = useState<string | null>(null)
  const editor = useDraftEditor<DraftState>(draft, () => {
    const parsed = readDraft(draft.result!.params)
    return {
      draft: parsed,
      selection: selectAll(parsed),
      prompt: parsed.prompt,
      context: parsed.context,
      tags: draft.result!.suggested_tags.map((item) => item.tag),
    }
  })
  const state = editor.state
  // 廢棄、建立送出前暫停自動存檔；沒成功時一定要恢復，否則之後的修改都不會存回後端
  const discardMutation = useDiscardDraft({ beforeDiscard: editor.pause, afterFailure: () => editor.resume() })

  const fixedDeck = create.deck === 'fixed' ? create.fixed_deck : null
  const deck = fixedDeck ?? (deckStatus === 'ok' ? selectedDeck : null)

  const createMutation = useMutation({
    mutationFn: () => {
      editor.pause()
      return FluencyTidesAPI.createNote(handler.name, {
        deck: deck!,
        note_type: create.note_type!,
        params: selectedParams(state.draft, state.selection, state.prompt.trim(), state.context.trim()) as unknown as Record<
          string,
          unknown
        >,
        tags: state.tags,
        draft: { id: draft.id, version: editor.version },
      })
    },
    onSuccess: (data) => {
      setResult(data)
      removeStored('local', formKey)
      removeStored('local', draftBackupKey(draft.id))
      setCurrent(null)
      setCreateError(null)
      toast.success(`已建立 ${data.note_ids.length} 張筆記`, { description: `牌組：${data.deck}` })
      if (data.sync_warning) toast.warning('同步 AnkiWeb 失敗', { description: data.sync_warning })
      for (const queryKey of [['graph'], ['cards'], ['decks'], ['tags']]) queryClient.invalidateQueries({ queryKey })
    },
    onError: async (err) => {
      const latest = draftFromError(err)
      if (latest !== undefined) {
        setCurrent(latest)
        toast.error('無法建立', { description: describeApiError(err).message })
        if (latest) editor.resume(latest)
        else removeStored('local', draftBackupKey(draft.id))
        return
      }
      // 建立失敗時草稿退回可建立（版本加了）：讀最新的，接著自動存檔。三種結果要分開處理
      let refreshed: ServerDraft | null | undefined
      try {
        refreshed = await FluencyTidesAPI.getCurrentDraft()
      } catch (refreshErr) {
        console.error('[PreviewCreate] 建立失敗後重讀草稿也失敗', refreshErr)
        refreshed = undefined
      }
      if (refreshed === null) {
        // 槽位已空：最可能是其實建好了，只是回應沒回來（例如逾時）。不要讓人以為失敗而再建一次
        removeStored('local', draftBackupKey(draft.id))
        setCurrent(null)
        toast.warning('建立結果未知，草稿已不在', {
          description: '卡片可能已經建立，請先到卡片瀏覽頁確認，再決定要不要重建。',
        })
        return
      }
      if (refreshed) {
        setCurrent(refreshed)
        editor.resume(refreshed)
      } else {
        // 讀不到最新狀態：保留畫面與修改，恢復自動存檔（版本不符時會走衝突讓使用者選）
        editor.resume()
      }
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

  const creating = createMutation.isPending || draft.status === 'creating'
  const busy = creating || discardMutation.isPending
  const chosen = selectedCount(state.selection)
  // 存檔送出中不給建立：存檔先到會讓建立拿到過時的版本，誤報「在其他地方改過了」
  const canCreate =
    deck !== null &&
    chosen > 0 &&
    state.prompt.trim() !== '' &&
    !busy &&
    !editor.conflict &&
    editor.saveStatus !== 'saving'
  // 不能建立的原因寫在按鈕旁：牌組說明在最上方，草稿很長時看不到
  const blockedReason =
    deckBlockedReason(fixedDeck, selectedDeck, deckStatus) ??
    (chosen === 0 ? '至少勾選一句才能建立。' : !state.prompt.trim() ? '卡面情境不能空白（在上方填寫）。' : null)

  // 牌組不能用：草稿還在，選好牌組就會出現（建立中照常顯示）
  if (!creating && deckBlocksForm(fixedDeck, selectedDeck, deckStatus)) {
    return (
      <DeckRequiredNotice
        reason={deckBlockedReason(fixedDeck, selectedDeck, deckStatus) ?? ''}
        kept="已生成的草稿還在，選好牌組就會出現。"
      />
    )
  }

  const update = (patch: Partial<DraftState>) => editor.setState({ ...state, ...patch })
  const input = inputAsValues(draft.input)

  return (
    <div className="space-y-4" aria-busy={creating}>
      {deckSummary}
      {input.background && (
        <p className="break-words rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">背景：{input.background}</p>
      )}
      <TrilingualDraftPreview
        draft={state.draft}
        selection={state.selection}
        onSelectionChange={(selection) => update({ selection })}
        prompt={state.prompt}
        onPromptChange={(prompt) => update({ prompt })}
        context={state.context}
        onContextChange={(context) => update({ context })}
        disabled={busy}
      />
      <TagInput handlerName={handler.name} tags={state.tags} onChange={(tags) => update({ tags })} disabled={busy} />
      {editor.conflict && (
        <div role="alert" className="space-y-2 rounded-md border border-destructive/50 px-3 py-2 text-sm">
          <p>這份草稿在其他地方改過了。要放棄這裡的修改、載入最新的，還是用這裡的修改覆蓋？</p>
          <div className="flex flex-wrap gap-2">
            <Button type="button" size="sm" variant="outline" onClick={editor.loadLatest}>
              載入最新
            </Button>
            <Button type="button" size="sm" variant="outline" onClick={editor.overwrite}>
              用這裡的覆蓋
            </Button>
          </div>
        </div>
      )}
      {createError && <ErrorNote title="建立失敗" error={{ error_code: 'VALIDATION', message: createError }} />}
      {!busy && blockedReason && (
        <p id={`${formId}-blocked`} role="status" className="text-xs text-destructive">
          {blockedReason}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          disabled={!canCreate}
          onClick={() => canCreate && createMutation.mutate()}
          aria-describedby={!busy && blockedReason ? `${formId}-blocked` : undefined}
        >
          {creating && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          {creating ? '建立中、同步中…' : `建立（${chosen} 句）`}
        </Button>
        <Button type="button" variant="outline" disabled={busy} onClick={() => discardMutation.discard(draft)}>
          廢棄
        </Button>
        <span className="text-xs text-muted-foreground" aria-live="polite">
          {creating ? '' : SAVE_LABELS[editor.saveStatus]}
        </span>
      </div>
      {creating && (
        <p role="status" className="text-xs text-muted-foreground">
          寫入 Anki 後會同步 AnkiWeb，最久可能要等一分鐘。
        </p>
      )}
      {discardMutation.dialog}
    </div>
  )
}
