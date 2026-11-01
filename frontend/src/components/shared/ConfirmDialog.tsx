import { useEffect, useRef, type KeyboardEvent, type MouseEvent, type ReactNode } from 'react'
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

/** 視窗內可取得焦點的元素。Focusable elements inside the dialog. */
const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

/**
 * 頁面內的確認視窗，取代 `window.confirm`。它渲染在呼叫端所在的位置，所以放在全螢幕元素裡時
 * 全螢幕中看得到；瀏覽器顯示 `confirm`／`alert` 這類原生對話框前會先退出全螢幕。
 *
 * 和原生對話框一樣是 modal：按 Esc 或點背景取消、Tab 只在視窗內循環，關閉後焦點回到開啟前的元素。
 * 全螢幕時 Esc 會被瀏覽器拿去退出全螢幕、不會送到頁面，這時要按「取消」。
 *
 * An in-page confirmation dialog replacing `window.confirm`. It renders where
 * it is placed, so it stays visible inside a fullscreen element; browsers
 * leave fullscreen before showing native dialogs. It is modal: Esc or a
 * backdrop click cancels, Tab stays inside, and focus returns on close.
 * @returns 確認視窗 JSX。Dialog JSX.
 */
export function ConfirmDialog({ title, message, confirmLabel, destructive, onConfirm, onCancel }: ConfirmDialogProps) {
  const dialogRef = useRef<HTMLDivElement>(null)

  // 關閉後把焦點還給開啟前的元素（例如觸發刪除的按鈕）
  useEffect(() => {
    const previous = document.activeElement
    return () => {
      if (previous instanceof HTMLElement && previous.isConnected) previous.focus()
    }
  }, [])

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    // 不讓按鍵傳到後面的頁面（例如外層彈窗或圖的快捷鍵）
    event.stopPropagation()
    if (event.key === 'Escape') {
      event.preventDefault()
      onCancel()
      return
    }
    if (event.key !== 'Tab') return
    const focusable = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? [])
    if (focusable.length === 0) return
    const first = focusable[0]
    const last = focusable[focusable.length - 1]
    const active = document.activeElement
    const inside = active instanceof Node && dialogRef.current?.contains(active) === true
    if (event.shiftKey && (active === first || !inside)) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && (active === last || !inside)) {
      event.preventDefault()
      first.focus()
    }
  }

  // 只有直接點在背景（不是視窗本身）才取消；也不讓點擊傳到外層彈窗的背景
  const onBackdropClick = (event: MouseEvent<HTMLDivElement>) => {
    event.stopPropagation()
    if (event.target === event.currentTarget) onCancel()
  }

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
      onClick={onBackdropClick}
      onKeyDown={onKeyDown}
    >
      <div
        ref={dialogRef}
        role="alertdialog"
        aria-modal="true"
        aria-label={title}
        className="bg-background border shadow-lg rounded-xl w-full max-w-sm p-6 space-y-4"
      >
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
