import type { ReactNode } from 'react'
import { Button } from '../ui/button'

/** {@link ConfirmDialog} 的 props。Props of {@link ConfirmDialog}. */
export interface ConfirmDialogProps {
  title: string
  /** 說明；可多行。The message; may span lines. */
  message: ReactNode
  /** 確認按鈕的文字。Label of the confirm button. */
  confirmLabel: string
  /** 確認的是破壞性操作（例如刪除）時按鈕用紅色。Whether the action is destructive. */
  destructive?: boolean
  onConfirm: () => void
  onCancel: () => void
}

/**
 * 頁面內的確認視窗，取代 `window.confirm`。它渲染在呼叫端所在的位置，所以放在全螢幕元素裡時
 * 全螢幕中看得到；瀏覽器顯示 `confirm`／`alert` 這類原生對話框前會先退出全螢幕。
 * An in-page confirmation dialog replacing `window.confirm`. It renders where
 * it is placed, so it stays visible inside a fullscreen element; browsers
 * leave fullscreen before showing native dialogs.
 * @returns 確認視窗 JSX。Dialog JSX.
 */
export function ConfirmDialog({ title, message, confirmLabel, destructive, onConfirm, onCancel }: ConfirmDialogProps) {
  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
      <div role="alertdialog" aria-modal="true" aria-label={title} className="bg-background border shadow-lg rounded-xl w-full max-w-sm p-6 space-y-4">
        <h3 className="text-lg font-bold">{title}</h3>
        <div className="text-sm text-muted-foreground whitespace-pre-line break-words">{message}</div>
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onCancel}>
            取消
          </Button>
          <Button variant={destructive ? 'destructive' : 'default'} onClick={onConfirm} autoFocus>
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  )
}
