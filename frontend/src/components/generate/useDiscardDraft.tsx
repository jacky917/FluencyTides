/**
 * 廢棄建卡草稿：先確認，成功後清空快取與本機的備份（`docs/wip/server_drafts_FEAT_2026-11-04.md` D8）。
 * 預覽流程的畫面與「有別種卡片的草稿」的提示共用。
 *
 * Discards a create draft after confirming.
 */

import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { toast } from 'sonner'
import { FluencyTidesAPI } from '../../api/client'
import { useSetCurrentDraft } from '../../hooks/useServerDraft'
import { removeStored } from '../../hooks/useStoredState'
import { describeApiError, isBackendError } from '../../lib/apiError'
import { draftBackupKey, draftFromError, errorCode } from '../../lib/serverDraft'
import type { ServerDraft } from '../../types/api'
import { ConfirmDialog } from '../shared/ConfirmDialog'

/** {@link useDiscardDraft} 的選項。Options. */
interface DiscardOptions {
  /** 確認後、送出前（例如暫停自動存檔）。Called before sending. */
  beforeDiscard?: () => void
  /** 沒有廢棄成功、草稿還在時（例如恢復自動存檔）。Called when the draft is still there. */
  afterFailure?: () => void
}

/**
 * 廢棄草稿的 mutation 與確認視窗。The discard mutation and its confirmation.
 * @param options - 送出前與失敗後的回呼。Callbacks.
 * @returns `discard(draft)` 開啟確認、`dialog` 要放進畫面、`isPending`。
 */
export function useDiscardDraft({ beforeDiscard, afterFailure }: DiscardOptions = {}) {
  const setCurrent = useSetCurrentDraft()
  const [confirming, setConfirming] = useState<ServerDraft | null>(null)
  const mutation = useMutation({
    mutationFn: (draft: ServerDraft) => {
      beforeDiscard?.()
      return FluencyTidesAPI.discardDraft(draft.id)
    },
    onSuccess: (_data, draft) => {
      removeStored('local', draftBackupKey(draft.id))
      setCurrent(null)
      toast.success('已廢棄草稿')
    },
    onError: (err, draft) => {
      const latest = draftFromError(err)
      if (latest === null) {
        // 已經不在了，目標其實達成：清掉備份，說明一下就好
        removeStored('local', draftBackupKey(draft.id))
        setCurrent(null)
        toast.info('這份草稿已不在', { description: '可能已在其他地方建立或廢棄，或伺服器重新啟動過。' })
        return
      }
      if (latest !== undefined) setCurrent(latest)
      else if (!isBackendError(err)) console.error('[useDiscardDraft] 廢棄失敗', err)
      afterFailure?.()
      toast.error(errorCode(err) === 'DRAFT_BUSY' ? '現在不能廢棄' : '無法廢棄', {
        description: describeApiError(err).message,
      })
    },
  })
  const dialog = confirming && (
    <ConfirmDialog
      title="廢棄這份草稿？"
      message="生成的內容會一起刪除，無法復原。"
      confirmLabel="廢棄"
      destructive
      onConfirm={() => {
        mutation.mutate(confirming)
        setConfirming(null)
      }}
      onCancel={() => setConfirming(null)}
    />
  )
  return { discard: setConfirming, dialog, isPending: mutation.isPending }
}
