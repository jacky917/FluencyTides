import { useCallback, useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, Trash2, X } from 'lucide-react'
import { toast } from 'sonner'
import { FluencyTidesAPI } from '../api/client'
import { describeApiError, failureTitle, isBackendError, retryTransient } from '../lib/apiError'
import { changedFields, fieldValues, noteEditPolicy } from '../lib/cardFields'
import type { GenericNoteDetail } from '../types/api'
import { JsonFieldEditor } from './JsonFieldEditor'
import { ConfirmDialog } from './shared/ConfirmDialog'
import { Button } from './ui/button'
import { Input } from './ui/input'

/**
 * CardDetailModal 元件的 Props。
 * Props for the CardDetailModal component.
 */
interface CardDetailModalProps {
  isOpen: boolean
  onClose: () => void
  /** 筆記、負責的 handler 與權限（`GET /notes/{note_id}`）。The note with its handler and permissions. */
  note: GenericNoteDetail | null
  isLoading: boolean
  /** 讀取筆記失敗的錯誤；沒有時省略。The load error, if any. */
  error?: unknown
  /** 刪除成功後呼叫（例如清掉頁面上的選取）。Called after a successful delete. */
  onDeleted?: () => void
}

/** 寫入的對象；有它才能送出寫入。A write target; writes need one. */
interface WriteTarget {
  handlerName: string
  noteId: number
}

/** 欄位輸入框的 id；Anki 欄位名可以有空白，id 不行。An element id for a field. */
const fieldId = (name: string) => `field-${encodeURIComponent(name)}`

/** 寫入後要重新整理的查詢：知識圖譜、卡片瀏覽與筆記。Queries refreshed after a write. */
const REFRESH_KEYS = [['graph'], ['cards'], ['card-view'], ['note']]

/** 後端寫入後會同步 AnkiWeb；同步失敗時資料已寫入，只提醒。Shows the backend's sync warning. */
function showSyncWarning(warning: string | null) {
  if (warning) toast.warning('同步 AnkiWeb 失敗', { description: warning })
}

/**
 * 卡片詳情彈窗：檢視、編輯與刪除單一 Anki 筆記的欄位。
 * Card detail modal: view, edit, and delete a single Anki note's fields.
 *
 * 能否編輯、刪除看後端回傳的 `permissions`（不允許時顯示原因）；寫入送給負責的 handler
 * （`note.handler`）；handler 宣告的媒體欄位、沒有格式的 JSON 欄位只顯示不編輯；儲存時只送出有變的欄位。
 * handler 宣告了格式的 JSON 欄位（`json_field_specs`）改用清單表單編輯，各欄自己儲存（`JsonFieldEditor`）。
 * 後端寫入後會同步 AnkiWeb 並等它完成，所以送出期間顯示「同步中」。
 * Permissions come from the backend; writes go to the owning handler;
 * declared JSON and media fields are read-only; only changed fields are sent.
 *
 * @param isOpen - 是否開啟彈窗。Whether the modal is open.
 * @param onClose - 關閉彈窗的回呼。Callback to close the modal.
 * @param note - 筆記與權限。The note.
 * @param isLoading - 是否正在載入。Whether the note is loading.
 * @param error - 讀取筆記失敗的錯誤。The load error.
 * @param onDeleted - 刪除成功後的回呼。Called after a delete.
 * @returns 彈窗 JSX 或 null。Modal JSX or null.
 */
export function CardDetailModal({ isOpen, onClose, note, isLoading, error, onDeleted }: CardDetailModalProps) {
  const queryClient = useQueryClient()
  // 唯讀欄位與 JSON 欄位的格式看 handler 宣告；只有允許修改時才需要
  const needsHandlers =
    isOpen && (note?.permissions.update_fields.allowed === true || note?.permissions.update_json?.allowed === true)
  const {
    data: handlers,
    error: handlersError,
    refetch: refetchHandlers,
    isFetching: handlersFetching,
  } = useQuery({
    queryKey: ['handlers'],
    queryFn: FluencyTidesAPI.listHandlers,
    enabled: needsHandlers,
    retry: retryTransient,
  })
  const original = useMemo(() => (note ? fieldValues(note) : {}), [note])
  const [fields, setFields] = useState<Record<string, string>>({})
  // 刪除前的確認視窗（頁面內的，不用 window.confirm：瀏覽器顯示原生對話框前會退出全螢幕）
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  // JSON 清單有未儲存的修改時，關閉前確認
  const [dirtyJson, setDirtyJson] = useState<ReadonlySet<string>>(new Set())
  const [confirmingClose, setConfirmingClose] = useState(false)
  useEffect(() => {
    if (!isOpen) {
      setConfirmingDelete(false)
      setConfirmingClose(false)
      setDirtyJson(new Set())
    }
  }, [isOpen])
  const onJsonDirty = useCallback((field: string, dirty: boolean) => {
    setDirtyJson((prev) => {
      if (prev.has(field) === dirty) return prev
      const next = new Set(prev)
      if (dirty) next.add(field)
      else next.delete(field)
      return next
    })
  }, [])

  useEffect(() => {
    setFields(original)
  }, [original])

  const handler = note ? handlers?.find((h) => h.name === note.handler) : undefined
  const policy = note
    ? noteEditPolicy({
        note,
        handler,
        handlersError: needsHandlers && handlersError ? describeApiError(handlersError).message : null,
      })
    : null
  // 能以清單表單編輯的 JSON 欄位：handler 宣告了格式、讀取也帶回了內容
  const jsonSpecs = handler?.json_field_specs ?? {}
  const jsonStates = note?.json_fields ?? {}
  const jsonEditable = Object.keys(jsonSpecs).filter((name) => jsonStates[name] !== undefined)
  const jsonPermission = note?.permissions.update_json
  const reloadNote = () => queryClient.invalidateQueries({ queryKey: ['note'] })
  const target: WriteTarget | null =
    note && policy?.handlerName ? { handlerName: policy.handlerName, noteId: note.note_id } : null
  const changes = changedFields(original, fields)
  const hasChanges = Object.keys(changes).length > 0

  const refresh = () => REFRESH_KEYS.forEach((queryKey) => queryClient.invalidateQueries({ queryKey }))
  const reportError = (action: string, err: unknown) => {
    if (!isBackendError(err)) console.error(`[CardDetailModal] ${action}失敗`, err)
    const info = describeApiError(err)
    toast.error(failureTitle(info.status, action), { description: info.message })
  }

  const updateMutation = useMutation({
    mutationFn: ({ handlerName, noteId, fields: updated }: WriteTarget & { fields: Record<string, string> }) =>
      FluencyTidesAPI.updateNoteFields(handlerName, noteId, updated),
    onSuccess: (result, variables) => {
      toast.success('已儲存', { description: Object.keys(variables.fields).join('、') })
      showSyncWarning(result.sync_warning)
      onClose()
    },
    onError: (err) => reportError('儲存', err),
    onSettled: refresh,
  })

  const deleteMutation = useMutation({
    mutationFn: ({ handlerName, noteId }: WriteTarget) => FluencyTidesAPI.deleteNote(handlerName, noteId),
    onSuccess: (result) => {
      toast.success('已刪除筆記')
      showSyncWarning(result.sync_warning)
      onDeleted?.()
      onClose()
    },
    onError: (err) => reportError('刪除', err),
    onSettled: refresh,
  })

  if (!isOpen) return null

  const requestClose = () => {
    if (dirtyJson.size > 0) setConfirmingClose(true)
    else onClose()
  }

  const busy = updateMutation.isPending || deleteMutation.isPending
  const canSave = Boolean(target && policy?.canEdit && hasChanges && !busy && !isLoading)
  const canDelete = Boolean(target && policy?.canDelete && !busy && !isLoading)
  const notices = [policy?.editNotice, policy?.deleteNotice && `無法刪除：${policy.deleteNotice}`].filter(
    (notice): notice is string => Boolean(notice),
  )

  const handleDelete = () => {
    if (target) setConfirmingDelete(true)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
      <div className="bg-background border shadow-lg rounded-xl w-full max-w-2xl max-h-[90vh] flex flex-col">
        <div className="flex justify-between items-center px-6 py-4 border-b">
          <div className="min-w-0">
            <h2 className="text-xl font-semibold">筆記欄位</h2>
            {note && (
              <p className="text-sm text-muted-foreground mt-1 break-all">
                {note.note_type} · Note {note.note_id}
              </p>
            )}
          </div>
          <button
            onClick={requestClose}
            aria-label="關閉"
            className="p-2 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-full transition-colors"
          >
            <X className="w-5 h-5 text-muted-foreground" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-4">
          {isLoading ? (
            <div className="flex justify-center items-center h-32">
              <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
            </div>
          ) : note ? (
            <div className="space-y-4">
              {notices.length > 0 && (
                <div className="flex flex-wrap items-center gap-2 rounded-md border border-amber-500/50 bg-amber-500/10 px-3 py-2 text-sm text-amber-900">
                  <div className="flex-1 space-y-1">
                    {notices.map((notice) => (
                      <p key={notice} className="break-words">
                        {notice}
                      </p>
                    ))}
                  </div>
                  {needsHandlers && handlersError !== null && (
                    <Button size="sm" variant="outline" onClick={() => refetchHandlers()} disabled={handlersFetching}>
                      重試
                    </Button>
                  )}
                </div>
              )}
              {Object.entries(fields).map(([key, value]) => {
                if (jsonEditable.includes(key)) return null
                const locked = policy?.lockedFields.get(key)
                const readOnly = !policy?.canEdit || locked !== undefined
                const isLongText = locked === 'JSON' || key === 'ExampleSentence' || value.length > 50
                return (
                  <div key={key} className="space-y-1">
                    <label className="text-sm font-medium leading-none" htmlFor={fieldId(key)}>
                      {key}
                      {locked && <span className="ml-2 text-xs text-muted-foreground">{locked}，僅供檢視</span>}
                    </label>
                    {isLongText ? (
                      <textarea
                        id={fieldId(key)}
                        className="flex min-h-[80px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm font-mono ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 read-only:bg-muted/40"
                        value={value}
                        readOnly={readOnly}
                        onChange={(e) => setFields((prev) => ({ ...prev, [key]: e.target.value }))}
                      />
                    ) : (
                      <Input
                        id={fieldId(key)}
                        value={value}
                        readOnly={readOnly}
                        className="read-only:bg-muted/40"
                        onChange={(e) => setFields((prev) => ({ ...prev, [key]: e.target.value }))}
                      />
                    )}
                  </div>
                )
              })}
              {target &&
                jsonEditable.map((name) => (
                  <JsonFieldEditor
                    key={name}
                    handlerName={target.handlerName}
                    noteId={target.noteId}
                    field={name}
                    spec={jsonSpecs[name]}
                    state={jsonStates[name]}
                    canEdit={jsonPermission?.allowed === true}
                    notice={jsonPermission?.allowed ? null : jsonPermission?.reason}
                    onSaved={refresh}
                    onReload={reloadNote}
                    onDirtyChange={onJsonDirty}
                  />
                ))}
            </div>
          ) : (
            <div role="alert" className="text-center text-sm">
              <p className="font-semibold text-destructive">無法載入筆記內容</p>
              {error !== undefined && error !== null && (
                <p className="mt-1 break-words text-muted-foreground">{describeApiError(error).message}</p>
              )}
            </div>
          )}
        </div>

        <div className="flex justify-between items-center px-6 py-4 border-t bg-slate-50/50 dark:bg-slate-900/50 rounded-b-xl">
          <Button variant="destructive" onClick={handleDelete} disabled={!canDelete}>
            {deleteMutation.isPending ? (
              <Loader2 className="w-4 h-4 mr-2 animate-spin" />
            ) : (
              <Trash2 className="w-4 h-4 mr-2" />
            )}
            {deleteMutation.isPending ? '刪除並同步中…' : '刪除筆記'}
          </Button>
          <div className="space-x-2">
            <Button variant="outline" onClick={requestClose} disabled={busy}>
              取消
            </Button>
            <Button onClick={() => target && updateMutation.mutate({ ...target, fields: changes })} disabled={!canSave}>
              {updateMutation.isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
              {updateMutation.isPending ? '儲存並同步中…' : '儲存變更'}
            </Button>
          </div>
        </div>
      </div>
      {confirmingClose && (
        <ConfirmDialog
          title="還有未儲存的清單"
          message="有清單的修改還沒儲存，關閉後會遺失。確定要關閉嗎？"
          confirmLabel="關閉"
          destructive
          onCancel={() => setConfirmingClose(false)}
          onConfirm={() => {
            setConfirmingClose(false)
            onClose()
          }}
        />
      )}
      {confirmingDelete && target && (
        <ConfirmDialog
          title="刪除筆記"
          message="確定要刪除這則筆記嗎？此操作無法復原，相關的圖譜連線也會移除。"
          confirmLabel="刪除"
          destructive
          onCancel={() => setConfirmingDelete(false)}
          onConfirm={() => {
            setConfirmingDelete(false)
            deleteMutation.mutate(target)
          }}
        />
      )}
    </div>
  )
}
