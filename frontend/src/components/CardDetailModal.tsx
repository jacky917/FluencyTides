import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, Trash2, X } from 'lucide-react'
import { toast } from 'sonner'
import { FluencyTidesAPI } from '../api/client'
import { describeApiError, failureTitle, isBackendError, retryTransient } from '../lib/apiError'
import { changedFields, fieldEditPolicy, isStructuredField, orderedFields } from '../lib/cardFields'
import type { CardDetail } from '../types/api'
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
  cardDetail: CardDetail | null
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

/** 寫入後要重新整理的查詢：知識圖譜與卡片瀏覽。Queries refreshed after a write. */
const REFRESH_KEYS = [['graph'], ['cards'], ['card-view'], ['card']]

/**
 * 卡片詳情彈窗：檢視、編輯與刪除單一 Anki 筆記的欄位。
 * Card detail modal: view, edit, and delete a single Anki note's fields.
 *
 * 寫入一律交給負責該筆記類型的 handler（依 `supported_models` 找），能否編輯與刪除見
 * {@link fieldEditPolicy}；JSON 欄位只顯示、不編輯；儲存時只送出有變的欄位。
 * Writes go to the handler owning the note type; JSON fields are read-only
 * and only changed fields are sent.
 *
 * @param isOpen - 是否開啟彈窗。Whether the modal is open.
 * @param onClose - 關閉彈窗的回呼。Callback to close the modal.
 * @param cardDetail - 筆記詳情。Note detail.
 * @param isLoading - 是否正在載入。Whether the note is loading.
 * @param error - 讀取筆記失敗的錯誤。The load error.
 * @param onDeleted - 刪除成功後的回呼。Called after a delete.
 * @returns 彈窗 JSX 或 null。Modal JSX or null.
 */
export function CardDetailModal({ isOpen, onClose, cardDetail, isLoading, error, onDeleted }: CardDetailModalProps) {
  const queryClient = useQueryClient()
  const {
    data: handlers,
    error: handlersError,
    refetch: refetchHandlers,
    isFetching: handlersFetching,
  } = useQuery({
    queryKey: ['handlers'],
    queryFn: FluencyTidesAPI.listHandlers,
    enabled: isOpen,
    retry: retryTransient,
  })
  const original = useMemo(
    () => (cardDetail ? Object.fromEntries(orderedFields(cardDetail)) : {}),
    [cardDetail],
  )
  const [fields, setFields] = useState<Record<string, string>>({})
  // 刪除前的確認視窗（頁面內的，不用 window.confirm：瀏覽器顯示原生對話框前會退出全螢幕）
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  useEffect(() => {
    if (!isOpen) setConfirmingDelete(false)
  }, [isOpen])

  useEffect(() => {
    setFields(original)
  }, [original])

  const policy = cardDetail
    ? fieldEditPolicy({
        handlers,
        handlersError: handlersError ? describeApiError(handlersError).message : null,
        modelName: cardDetail.modelName,
      })
    : null
  const target: WriteTarget | null =
    cardDetail && policy?.handlerName ? { handlerName: policy.handlerName, noteId: cardDetail.noteId } : null
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
      FluencyTidesAPI.updateCard(handlerName, noteId, { action: 'update_fields', fields: updated }),
    onSuccess: (_, variables) => {
      toast.success('已儲存', { description: Object.keys(variables.fields).join('、') })
      onClose()
    },
    onError: (err) => reportError('儲存', err),
    onSettled: refresh,
  })

  const deleteMutation = useMutation({
    mutationFn: ({ handlerName, noteId }: WriteTarget) => FluencyTidesAPI.deleteCard(handlerName, noteId),
    onSuccess: () => {
      toast.success('已刪除筆記')
      onDeleted?.()
      onClose()
    },
    onError: (err) => reportError('刪除', err),
    onSettled: refresh,
  })

  if (!isOpen) return null

  const busy = updateMutation.isPending || deleteMutation.isPending
  const canSave = Boolean(target && policy?.canEdit && hasChanges && !busy && !isLoading)
  const canDelete = Boolean(target && policy?.canDelete && !busy && !isLoading)

  const handleDelete = () => {
    if (target) setConfirmingDelete(true)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
      <div className="bg-background border shadow-lg rounded-xl w-full max-w-2xl max-h-[90vh] flex flex-col">
        <div className="flex justify-between items-center px-6 py-4 border-b">
          <div className="min-w-0">
            <h2 className="text-xl font-semibold">筆記欄位</h2>
            {cardDetail && (
              <p className="text-sm text-muted-foreground mt-1 break-all">
                {cardDetail.modelName} · Note {cardDetail.noteId}
              </p>
            )}
          </div>
          <button
            onClick={onClose}
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
          ) : cardDetail ? (
            <div className="space-y-4">
              {policy?.notice && (
                <div className="flex flex-wrap items-center gap-2 rounded-md border border-amber-500/50 bg-amber-500/10 px-3 py-2 text-sm text-amber-900">
                  <span className="flex-1">{policy.notice}</span>
                  {handlersError !== null && (
                    <Button size="sm" variant="outline" onClick={() => refetchHandlers()} disabled={handlersFetching}>
                      重試
                    </Button>
                  )}
                </div>
              )}
              {Object.entries(fields).map(([key, value]) => {
                const structured = isStructuredField(key, original[key] ?? '')
                const readOnly = !policy?.canEdit || structured
                const isLongText = structured || key === 'ExampleSentence' || value.length > 50
                return (
                  <div key={key} className="space-y-1">
                    <label className="text-sm font-medium leading-none" htmlFor={fieldId(key)}>
                      {key}
                      {structured && <span className="ml-2 text-xs text-muted-foreground">JSON，僅供檢視</span>}
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
            刪除筆記
          </Button>
          <div className="space-x-2">
            <Button variant="outline" onClick={onClose} disabled={busy}>
              取消
            </Button>
            <Button onClick={() => target && updateMutation.mutate({ ...target, fields: changes })} disabled={!canSave}>
              {updateMutation.isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
              儲存變更
            </Button>
          </div>
        </div>
      </div>
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
