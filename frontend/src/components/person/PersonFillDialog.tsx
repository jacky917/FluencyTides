/**
 * 人脈卡的「AI 整理」對話框（`docs/wip/person_llm_fill_FEAT_2026-11-23.md` D8），掛在版面層級、全站一個。
 *
 * 畫面跟著目前的草稿走：沒有草稿時輸入描述；整理中顯示進度（可以關掉對話框，整理好全站通知）；失敗時可重試或改
 * 描述再試；整理好時逐項編輯、勾選後寫入，編輯自動存回草稿（關掉、重新整理、換裝置都還在）。槽位裡是別種卡片的草稿
 * 時，提示先處理那一份（和預覽建卡共用全站一份草稿）。
 *
 * The site-wide "AI 整理" dialog; it follows the current draft.
 */

import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Loader2, Sparkles } from 'lucide-react'
import { toast } from 'sonner'
import { FluencyTidesAPI } from '../../api/client'
import { useCurrentDraft, useDraftEditor, useSetCurrentDraft } from '../../hooks/useServerDraft'
import { removeStored, writeStored } from '../../hooks/useStoredState'
import { describeApiError, retryTransient } from '../../lib/apiError'
import {
  LIST_TITLES,
  PERSON_FILL_HANDLER,
  closePersonFill,
  fieldSections,
  fillParams,
  findEdit,
  hasPending,
  initialFillState,
  isFillDraft,
  plainField,
  proposalField,
  revertEdit,
  selectedIndexes,
  upsertEdit,
  usePersonFillDialog,
  type FillEditState,
  type FillTarget,
} from '../../lib/personFill'
import { draftBackupKey, draftFromError, timeLabel } from '../../lib/serverDraft'
import type { FillProposal, FillResultParams, HandlerInfo, ServerDraft } from '../../types/api'
import { Button } from '../ui/button'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '../ui/sheet'
import { TEXTAREA_CLASS } from '../generate/formParts'
import { useDiscardDraft } from '../generate/useDiscardDraft'
import { OriginalContent, type EditState } from './OriginalContent'
import { ProposalEditor } from './ProposalEditor'

/** 描述的長度上限（同後端 `MAX_FILL_TEXT`）。The description limit. */
const MAX_TEXT = 4000

/** 寫入後要重新讀的查詢。Queries refreshed after writing. */
const REFRESHED = [['card-view'], ['note'], ['cards'], ['graph'], ['decks'], ['tags']]

/**
 * 全站的 AI 整理對話框。The site-wide "AI 整理" dialog.
 * @returns 對話框 JSX。The dialog.
 */
export function PersonFillDialog() {
  const { open, target } = usePersonFillDialog()
  // 打開時讀得勤一點：整理中要看到進度
  const { data: draft, refetch } = useCurrentDraft(open ? 2000 : 10_000)
  // 每次打開都重讀一次：草稿可能是在別的裝置或分頁開始的（沒有進行中的草稿時不會自動輪詢）
  useEffect(() => {
    if (open) void refetch()
  }, [open, refetch])
  const fillDraft = draft && isFillDraft(draft) ? draft : null
  const params = fillParams(fillDraft)

  let body
  if (draft && !fillDraft) {
    body = <OtherDraftNotice draft={draft} />
  } else if (!fillDraft) {
    body = <DescribeStep target={target} />
  } else if (fillDraft.status === 'generating') {
    body = <GeneratingStep draft={fillDraft} />
  } else if (fillDraft.status === 'failed') {
    body = <FailedStep draft={fillDraft} />
  } else if (params) {
    body = <ReviewStep key={fillDraft.id} draft={fillDraft} params={params} />
  }

  return (
    <Sheet open={open} onOpenChange={(next) => !next && closePersonFill()}>
      <SheetContent side="right" className="w-full gap-0 p-0 sm:max-w-xl">
        <SheetHeader className="border-b px-4 py-3 pr-12">
          <SheetTitle className="flex items-center gap-2">
            <Sparkles className="h-4 w-4" aria-hidden="true" />
            AI 整理
          </SheetTitle>
          <SheetDescription>
            <DialogSubtitle draft={fillDraft} params={params} target={target} />
          </SheetDescription>
        </SheetHeader>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">{body}</div>
      </SheetContent>
    </Sheet>
  )
}

/** 副標題：補哪張卡，或在哪個牌組建新卡。What the dialog is for. */
function DialogSubtitle({
  draft,
  params,
  target,
}: {
  draft: ServerDraft | null
  params: FillResultParams | null
  target: FillTarget | null
}) {
  const noteId = params?.note_id ?? (draft?.input?.note_id as number | null | undefined) ?? (draft ? null : target?.noteId)
  const deck = params?.deck ?? (draft?.input?.deck as string | null | undefined) ?? target?.deck
  const creating = (params?.mode ?? (noteId ? 'update' : 'create')) === 'create' && !params?.note_id
  const { data: note } = useQuery({
    queryKey: ['note', noteId],
    queryFn: () => FluencyTidesAPI.getNote(noteId!),
    enabled: !!noteId,
    retry: retryTransient,
  })
  const name = note?.fields.find((field) => field.name === 'Display_Name')?.value || target?.label
  // 沒有草稿也沒有目標（例如重新整理後從通知打開、剛丟棄）：只說明從哪裡打開
  if (!draft && !target?.noteId && !target?.deck) return <>從人脈卡的卡面，或在建卡畫面選人脈卡時打開。</>
  if (creating) return <>描述一個人，整理成一張新的人脈卡{deck ? `（牌組：${deck}）` : ''}。</>
  if (noteId) return <>描述今天聊了什麼、對{name ? `「${plainField(name)}」` : '他'}多了解了什麼，整理後放到對的欄位。</>
  return <>從人脈卡的卡面，或在建卡畫面選人脈卡時打開。</>
}

/** 槽位裡是別種卡片的草稿（共用全站一份）。Another kind of draft holds the slot. */
function OtherDraftNotice({ draft }: { draft: ServerDraft }) {
  const navigate = useNavigate()
  if (draft.status === 'form_creating') {
    return <p className="text-sm text-muted-foreground">正在建立另一張卡片，請稍候再試。</p>
  }
  return (
    <div className="space-y-3 text-sm">
      <p>目前有一份其他卡片的建卡草稿還沒處理完（全站同時只有一份草稿），請先建立或廢棄那一份。</p>
      <Button
        type="button"
        variant="outline"
        onClick={() => {
          writeStored('local', 'generate_handler', draft.handler)
          closePersonFill()
          navigate('/generate')
        }}
      >
        前往那份草稿
      </Button>
    </div>
  )
}

/** 輸入描述、開始整理。Type the description and start. */
function DescribeStep({ target }: { target: FillTarget | null }) {
  const setCurrent = useSetCurrentDraft()
  const [text, setText] = useState('')
  const start = useMutation({
    mutationFn: () =>
      FluencyTidesAPI.startFill(PERSON_FILL_HANDLER, { text: text.trim(), noteId: target?.noteId, deck: target?.deck }),
    onSuccess: (draft) => setCurrent(draft),
    onError: (err) => {
      const latest = draftFromError(err)
      if (latest !== undefined) setCurrent(latest)
      toast.error('無法開始整理', { description: describeApiError(err).message })
    },
  })
  if (!target || (!target.noteId && !target.deck)) {
    return (
      <p className="text-sm text-muted-foreground">
        請從人脈卡的卡面按「AI 整理」，或在建卡畫面選人脈卡後按「用一段話建立」。
      </p>
    )
  }
  return (
    <form
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault()
        if (text.trim()) start.mutate()
      }}
    >
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        maxLength={MAX_TEXT}
        rows={8}
        autoFocus
        aria-label="描述"
        placeholder={
          target.noteId
            ? '例如：今天在中野的咖啡廳見面，他說下個月要考 N1，最近開始週末去爬山……'
            : '例如：今天在 Geekly 的面談認識田中さん，他叫田中優樹，老家在福岡，現在在 Sony 當 PM……'
        }
        className={`${TEXTAREA_CLASS} pointer-coarse:text-base`}
      />
      <p className="text-xs text-muted-foreground">
        內容都由你提供，AI 只把它分到對的欄位、整理成簡潔的句子，不會補上你沒說的事。整理後可以逐項修改、決定要不要寫入。
      </p>
      <Button type="submit" disabled={!text.trim() || start.isPending}>
        {start.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
        整理
      </Button>
    </form>
  )
}

/** 整理中。Organizing in the background. */
function GeneratingStep({ draft }: { draft: ServerDraft }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])
  const started = draft.generation_started_at ? new Date(draft.generation_started_at).getTime() : now
  const seconds = Math.max(0, Math.round((now - started) / 1000))
  return (
    <div className="space-y-3 text-sm">
      <p role="status" className="inline-flex items-center gap-2">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        整理中…（{seconds} 秒）
      </p>
      <p className="text-xs text-muted-foreground">
        {timeLabel(draft.generation_started_at) && `${timeLabel(draft.generation_started_at)} 開始。`}
        可以先關掉對話框或去別頁，整理好會通知；結果回來前不能丟棄。
      </p>
      {typeof draft.input?.text === 'string' && (
        <p className="whitespace-pre-wrap rounded bg-muted px-3 py-2 text-xs text-muted-foreground">{draft.input.text}</p>
      )}
    </div>
  )
}

/** 整理失敗：重試或改描述再試，也可以丟棄。Organizing failed. */
function FailedStep({ draft }: { draft: ServerDraft }) {
  const setCurrent = useSetCurrentDraft()
  const original = typeof draft.input?.text === 'string' ? draft.input.text : ''
  const [text, setText] = useState(original)
  const discard = useDiscardDraft()
  const retry = useMutation({
    mutationFn: () =>
      FluencyTidesAPI.retryDraft(draft.id, draft.version, text.trim() !== original ? { text: text.trim() } : undefined),
    onSuccess: (next) => setCurrent(next),
    onError: (err) => {
      const latest = draftFromError(err)
      if (latest !== undefined) setCurrent(latest)
      toast.error('無法重試', { description: describeApiError(err).message })
    },
  })
  return (
    <div className="space-y-3">
      <p role="alert" className="text-sm text-destructive">
        整理失敗：{draft.error?.message ?? '不明的錯誤'}
      </p>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        maxLength={MAX_TEXT}
        rows={6}
        aria-label="描述"
        className={`${TEXTAREA_CLASS} pointer-coarse:text-base`}
      />
      <div className="flex flex-wrap gap-2">
        <Button type="button" disabled={!text.trim() || retry.isPending} onClick={() => retry.mutate()}>
          {retry.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          {text.trim() !== original ? '用新的描述重試' : '重試'}
        </Button>
        <Button type="button" variant="outline" disabled={discard.isPending} onClick={() => discard.discard(draft)}>
          丟棄
        </Button>
      </div>
      {discard.dialog}
    </div>
  )
}

/** 標題：文字欄位、會變的資料用欄位的中文名稱（取自建卡表單）。The heading of a proposal. */
function proposalHeading(proposal: FillProposal, titles: Record<string, string>): string {
  const field = typeof proposal.data.field === 'string' ? proposal.data.field : ''
  const title = titles[field] ?? field
  switch (proposal.kind) {
    case 'display_name':
      return '稱呼'
    case 'interaction':
      return '互動紀錄'
    case 'followup':
      return '待追蹤'
    case 'goal':
      return '目標'
    case 'family':
      return '家人'
    case 'field':
      return title
    case 'history_add':
      return `${title}：新增一筆`
    case 'history_end':
      return `${title}：劃掉`
    default:
      // 修改原本內容的項目（edit_*）顯示在「卡片原本」裡，不走這裡
      return title
  }
}

/** 建卡表單裡各欄位的中文名稱。The field titles from the create form. */
function fieldTitles(handlers: HandlerInfo[] | undefined): Record<string, string> {
  const schema = handlers?.find((handler) => handler.name === PERSON_FILL_HANDLER)?.create_schema
  const properties = (schema?.properties ?? {}) as Record<string, { title?: string }>
  return Object.fromEntries(Object.entries(properties).map(([name, spec]) => [name, spec.title ?? name]))
}

/** 整理好了：逐項編輯、勾選後寫入。Edit, tick and write. */
function ReviewStep({ draft, params }: { draft: ServerDraft; params: FillResultParams }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const setCurrent = useSetCurrentDraft()
  const editor = useDraftEditor<FillEditState>(draft, () => initialFillState(params))
  const discard = useDiscardDraft({ beforeDiscard: editor.pause, afterFailure: () => editor.resume() })
  const { data: handlers } = useQuery({ queryKey: ['handlers'], queryFn: FluencyTidesAPI.listHandlers, retry: retryTransient })
  const titles = fieldTitles(handlers)
  const { data: note } = useQuery({
    queryKey: ['note', params.note_id],
    queryFn: () => FluencyTidesAPI.getNote(params.note_id!),
    enabled: params.mode === 'update' && params.note_id !== null,
    retry: retryTransient,
  })
  // 文字欄位原本的內容：讀得到這張卡時用最新的（寫到一半後也對），讀不到時用整理當時的
  const currentValue = (field: string) => {
    const live = note?.fields.find((item) => item.name === field)
    return live ? plainField(live.value) : (params.known?.fields[field] ?? '')
  }
  const state = editor.state
  const writing = draft.status === 'creating'

  const apply = useMutation({
    mutationFn: () => {
      editor.pause()
      return FluencyTidesAPI.applyFill(draft.id, editor.version, state.proposals, selectedIndexes(state, params))
    },
    onSuccess: (result) => {
      for (const queryKey of REFRESHED) queryClient.invalidateQueries({ queryKey })
      if (result.sync_warning) toast.warning('寫入後有需要注意的事', { description: result.sync_warning })
      if (result.done) {
        removeStored('local', draftBackupKey(draft.id))
        setCurrent(null)
        closePersonFill()
        toast.success(`已寫入 ${result.saved.length} 項`, {
          action:
            params.mode === 'create' && result.note_id
              ? { label: '查看', onClick: () => navigate(`/cards?q=${encodeURIComponent(`nid:${result.note_id}`)}`) }
              : undefined,
        })
        return
      }
      if (result.draft) {
        setCurrent(result.draft)
        editor.resume(result.draft)
      } else {
        editor.resume()
      }
      toast.warning(`有 ${Object.keys(result.errors).length} 項沒寫入`, { description: '原因標在各項下面；改好後可以再送一次。' })
    },
    onError: (err) => {
      const latest = draftFromError(err)
      if (latest) {
        setCurrent(latest)
        editor.resume(latest)
      } else {
        editor.resume()
      }
      toast.error('寫入失敗', { description: `${describeApiError(err).message}（已寫入的項目會留著，可以再送一次）` })
    },
  })

  const busy = writing || apply.isPending
  const sections = fieldSections(state.proposals)
  // 一項修改（改卡片原本的內容）目前的狀態
  const editOf = (field: string, itemIndex?: number): EditState | null => {
    const index = findEdit(state, field, itemIndex)
    if (index < 0) return null
    const data = state.proposals[index].data
    return {
      value: itemIndex === undefined ? data.value : data.values,
      active: state.selected[index] ?? false,
      saved: params.saved.includes(index),
      error: params.errors[String(index)],
    }
  }
  const pendingCount = selectedIndexes(state, params).filter((index) => !params.saved.includes(index)).length
  const update = (index: number, next: Partial<FillProposal> | null, selected?: boolean) =>
    editor.setState({
      proposals: next
        ? state.proposals.map((proposal, i) => (i === index ? { ...proposal, ...next } : proposal))
        : state.proposals,
      selected: selected === undefined ? state.selected : state.selected.map((value, i) => (i === index ? selected : value)),
    })

  if (state.proposals.length === 0) {
    return (
      <div className="space-y-3 text-sm">
        <p>這段描述裡沒有整理出可以記下的內容。可以說得具體一點再試一次。</p>
        <Button type="button" variant="outline" onClick={() => discard.discard(draft)}>
          丟棄，重新描述
        </Button>
        {discard.dialog}
      </div>
    )
  }

  return (
    <div className="space-y-5">
      {sections.map((section) => (
        <section key={section.field} className="space-y-2">
          <h3 className="text-xs font-semibold text-muted-foreground">
            {LIST_TITLES[section.field] ?? titles[section.field] ?? section.field}
          </h3>
          {/* 補舊卡：這個欄位卡片上原本的內容（不是 LLM 給的），和下面 AI 的建議分開 */}
          {params.mode === 'update' && (
            <OriginalContent
              text={
                // 只有文字欄位（建議是「寫進欄位」的）才給文字；清單看 items
                section.indexes.some((index) => state.proposals[index].kind === 'field')
                  ? currentValue(section.field)
                  : undefined
              }
              items={params.known?.items?.[section.field]}
              labels={params.labels[section.field]}
              editOf={(itemIndex) => editOf(section.field, itemIndex)}
              onEditText={(value) =>
                editor.setState(upsertEdit(state, { kind: 'edit_field', data: { field: section.field, value } }))
              }
              onEditItem={(item, values) =>
                editor.setState(
                  upsertEdit(state, {
                    kind: 'edit_item',
                    data: {
                      field: section.field,
                      index: item.index,
                      fingerprint: item.fingerprint,
                      values,
                      expected: item.values,
                    },
                  }),
                )
              }
              onRevert={(itemIndex) => {
                const index = findEdit(state, section.field, itemIndex)
                if (index >= 0) editor.setState(revertEdit(state, index))
              }}
              disabled={busy || !!editor.conflict}
            />
          )}
          {section.indexes.map((index) => {
            const proposal = state.proposals[index]
            return (
              <ProposalEditor
                key={index}
                proposal={proposal}
                heading={proposalHeading(proposal, titles)}
                labels={params.labels[proposalField(proposal)]}
                label={
                  proposal.kind === 'history_add'
                    ? 'AI 建議：新增一筆'
                    : proposal.kind === 'history_end'
                      ? 'AI 建議：劃掉'
                      : 'AI 建議'
                }
                selected={state.selected[index] ?? false}
                required={proposal.kind === 'display_name' && params.mode === 'create'}
                saved={params.saved.includes(index)}
                error={params.errors[String(index)]}
                disabled={busy || !!editor.conflict}
                onSelect={(selected) => update(index, null, selected)}
                onChange={(data) => update(index, { data })}
              />
            )
          })}
        </section>
      ))}

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

      {/* 捲動區有下內距：往下多貼一個內距（-bottom-4、-mb-4），按鈕列才會蓋住底部、不露出後面的內容 */}
      <div className="sticky -bottom-4 -mx-4 -mb-4 flex flex-wrap items-center gap-2 border-t bg-background px-4 pt-3 pb-4">

        <Button
          type="button"
          disabled={busy || !!editor.conflict || editor.saveStatus === 'saving' || !hasPending(state, params)}
          onClick={() => apply.mutate()}
        >
          {busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          {busy ? '寫入中、同步中…' : `寫入勾選的（${pendingCount}）`}
        </Button>
        <Button type="button" variant="outline" disabled={busy} onClick={() => discard.discard(draft)}>
          丟棄
        </Button>
        <span className="text-xs text-muted-foreground" aria-live="polite">
          {busy ? '寫入 Anki 後會同步 AnkiWeb，最久可能要等一分鐘。' : editor.saveStatus === 'saved' ? '已暫存' : ''}
        </span>
      </div>
      {discard.dialog}
    </div>
  )
}
