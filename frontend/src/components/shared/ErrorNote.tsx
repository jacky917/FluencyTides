import { describeApiError } from '../../lib/apiError'

/**
 * 面板中的錯誤提示。An inline error in a panel.
 * @param props.title - 標題。Title.
 * @param props.error - 錯誤。The error.
 * @returns 提示 JSX。Error JSX.
 */
export function ErrorNote({ title, error }: { title: string; error: unknown }) {
  return (
    <div role="alert" className="rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm">
      <p className="font-semibold text-destructive">{title}</p>
      <p className="break-words">{describeApiError(error).message}</p>
    </div>
  )
}
